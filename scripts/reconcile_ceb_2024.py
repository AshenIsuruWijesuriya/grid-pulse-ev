r"""Reconcile published CEB 2024 generation with existing PUCSL data.

Non-destructive: reads CSV files; writes only diagnostic outputs to --outdir.
All differences are comparisons of possibly different accounting boundaries.
Do not treat equality as proof of national demand or a difference as curtailment.

From project root:
    .\.venv\Scripts\python.exe scripts\reconcile_ceb_2024.py

Reference: CEB Sales and Generation Data Book 2024, printed pages 73-74.
"""
from __future__ import annotations

import argparse
import calendar
import json
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MW_COLUMNS = {
    "total_dispatch_reported_mw": "dispatch_integrated_all_gwh",
    "solar_mw": "solar_integrated_all_gwh",
    "wind_mw": "wind_integrated_all_gwh",
    "coal_mw": "coal_integrated_all_gwh",
    "major_hydro_mw": "hydro_integrated_all_gwh",
    "oil_ceb_mw": "ceb_oil_integrated_all_gwh",
    "oil_ipp_mw": "ipp_oil_integrated_all_gwh",
    "mini_hydro_mw": "mini_hydro_integrated_all_gwh",
}
DAILY_FIELDS = {
    "reported_daily_total_energy_mwh": "daily_reported_total_gwh",
    "reported_daily_solar_energy_mwh": "daily_reported_solar_gwh",
    "reported_daily_wind_energy_mwh": "daily_reported_wind_gwh",
    "integrated_total_energy_mwh": "daily_integrated_total_gwh",
    "integrated_solar_energy_mwh": "daily_integrated_solar_gwh",
}
QUALITY = {"no_interval_flags", "review_required"}


def load_reference(path: Path) -> pd.DataFrame:
    reference = pd.read_csv(path)
    needed = {
        "year", "month", "system_gross_generation_gwh", "system_net_generation_gwh",
        "ipp_gross_rooftop_solar_gwh", "ipp_gross_grid_solar_gwh",
        "ceb_net_coal_gwh", "ceb_gross_coal_gwh",
    }
    missing = needed - set(reference.columns)
    if missing:
        raise ValueError(f"Reference missing columns: {sorted(missing)}")
    if len(reference) != 12 or list(reference["month"]) != list(range(1, 13)):
        raise ValueError("CEB reference must have one ordered row for every month of 2024")
    if not reference["year"].eq(2024).all():
        raise ValueError("Reference data must be for 2024 only")
    checks = {
        "system_gross_generation_gwh": 17460.066,
        "system_net_generation_gwh": 16801.627,
        "ipp_gross_grid_solar_gwh": 256.554,
        "ipp_gross_rooftop_solar_gwh": 866.554,
    }
    for col, expected in checks.items():
        actual = reference[col].sum()
        if abs(actual - expected) > 0.02:
            raise ValueError(f"Annual source check failed for {col}: {actual} vs {expected}")
    return reference


def dispatch_monthly(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"Consolidated dispatch CSV not found: {path}")
    df = pd.read_csv(path)
    required = {"interval_start_local", "interval_quality_status", *MW_COLUMNS}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Dispatch CSV missing columns: {sorted(missing)}")
    ts = pd.to_datetime(df["interval_start_local"], utc=True, errors="raise")
    ts = ts.dt.tz_convert("Asia/Colombo")
    if not ts.dt.year.eq(2024).all():
        raise ValueError("Expected only 2024 local timestamps in dispatch")
    if ts.duplicated().any():
        raise ValueError("Duplicate 15-minute timestamps in dispatch")
    if not ts.dt.minute.isin([0, 15, 30, 45]).all() or not ts.dt.second.eq(0).all():
        raise ValueError("Interval timestamps must be aligned to 15 minutes")
    if not df["interval_quality_status"].isin(QUALITY).all():
        bad = df.loc[~df["interval_quality_status"].isin(QUALITY), "interval_quality_status"].unique()
        raise ValueError(f"Unexpected or legacy quality status: {bad}")
    df["month"] = ts.dt.month
    df["date"] = ts.dt.strftime("%Y-%m-%d")
    df["quality_clean"] = df["interval_quality_status"].eq("no_interval_flags")
    output = []
    for month in range(1, 13):
        m = df[df["month"] == month]
        expected = calendar.monthrange(2024, month)[1] * 96
        row = {
            "month": month,
            "expected_intervals": expected,
            "observed_intervals": len(m),
            "clean_intervals": int(m["quality_clean"].sum()),
            "review_required_intervals": int((~m["quality_clean"]).sum()),
            "observed_days": int(m["date"].nunique()),
            "expected_days": calendar.monthrange(2024, month)[1],
        }
        if len(m) > expected:
            raise ValueError(f"Too many records in month {month}")
        row["observed_fraction"] = len(m) / expected
        row["fully_observed"] = len(m) == expected
        for mw, out in MW_COLUMNS.items():
            row[out] = pd.to_numeric(m[mw], errors="raise").sum(min_count=1) * 0.25 / 1000
            row[out.replace("_all_gwh", "_quality_clean_gwh")] = (
                pd.to_numeric(m.loc[m["quality_clean"], mw], errors="raise").sum(min_count=1)
                * 0.25 / 1000
            )
        output.append(row)
    return pd.DataFrame(output)


def load_daily_reconciliations(directory: Path) -> pd.DataFrame:
    # Include partial-month reconciliation files (e.g. June 2024: June 30 is missing).
    # Do not infer or impute the missing day's energy.
    paths = sorted([
        *directory.glob("pucsl_2024_??_reconciliation.csv"),
        *directory.glob("pucsl_2024_??_partial_reconciliation.csv"),
    ])
    if not paths:
        print(f"WARNING: no 2024 monthly reconciliation CSVs in {directory}")
        return pd.DataFrame(columns=["operating_date", "month", *DAILY_FIELDS.values()])
    parts = []
    for path in paths:
        df = pd.read_csv(path)
        need = {"operating_date", *DAILY_FIELDS}
        if not need.issubset(df.columns):
            raise ValueError(f"Missing daily columns in {path.name}: {sorted(need - set(df.columns))}")
        d = pd.to_datetime(df["operating_date"], errors="raise")
        if not d.dt.year.eq(2024).all():
            raise ValueError(f"Non-2024 dates in {path.name}")
        df["operating_date"] = d.dt.strftime("%Y-%m-%d")
        df["month"] = d.dt.month
        for original, renamed in DAILY_FIELDS.items():
            df[renamed] = pd.to_numeric(df[original], errors="coerce") / 1000
        parts.append(df[["operating_date", "month", *DAILY_FIELDS.values()]])
    days = pd.concat(parts, ignore_index=True).sort_values("operating_date")
    if days["operating_date"].duplicated().any():
        raise ValueError("Duplicate operating dates among daily reconciliation CSVs")
    days["daily_total_gap_gwh"] = days["daily_reported_total_gwh"] - days["daily_integrated_total_gwh"]
    days["daily_solar_gap_gwh"] = days["daily_reported_solar_gwh"] - days["daily_integrated_solar_gwh"]
    days["daily_non_solar_residual_gap_gwh"] = days["daily_total_gap_gwh"] - days["daily_solar_gap_gwh"]
    return days


def daily_monthly(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame(columns=["month", "daily_reconciliation_days", *DAILY_FIELDS.values(),
            "daily_total_gap_gwh", "daily_solar_gap_gwh", "daily_non_solar_residual_gap_gwh"])
    sums = daily.groupby("month")[[*DAILY_FIELDS.values(),
        "daily_total_gap_gwh", "daily_solar_gap_gwh", "daily_non_solar_residual_gap_gwh"]].sum(min_count=1)
    sums["daily_reconciliation_days"] = daily.groupby("month").size()
    return sums.reset_index()


def compare(reference: pd.DataFrame, dispatch: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    monthly = reference.merge(dispatch, on="month", validate="one_to_one")
    monthly = monthly.merge(daily_monthly(daily), on="month", how="left", validate="one_to_one")
    monthly["ceb_gross_total_solar_gwh"] = (
        monthly["ipp_gross_grid_solar_gwh"] + monthly["ipp_gross_rooftop_solar_gwh"]
    )
    monthly["solar_reported_minus_ceb_grid_plus_rooftop_gwh"] = (
        monthly["daily_reported_solar_gwh"] - monthly["ceb_gross_total_solar_gwh"]
    )
    monthly["solar_interval_minus_ceb_grid_solar_gwh"] = (
        monthly["solar_integrated_all_gwh"] - monthly["ipp_gross_grid_solar_gwh"]
    )
    monthly["solar_gap_minus_ceb_rooftop_gwh"] = (
        monthly["daily_solar_gap_gwh"] - monthly["ipp_gross_rooftop_solar_gwh"]
    )
    monthly["reported_daily_total_minus_ceb_gross_gwh"] = (
        monthly["daily_reported_total_gwh"] - monthly["system_gross_generation_gwh"]
    )
    monthly["reported_daily_total_minus_ceb_net_gwh"] = (
        monthly["daily_reported_total_gwh"] - monthly["system_net_generation_gwh"]
    )
    monthly["interval_coal_minus_ceb_net_coal_gwh"] = (
        monthly["coal_integrated_all_gwh"] - monthly["ceb_net_coal_gwh"]
    )
    monthly["daily_reference_days_complete"] = (
        monthly["daily_reconciliation_days"] == monthly["expected_days"]
    )
    monthly["month_status"] = monthly.apply(
        lambda r: "complete_observation" if r["fully_observed"] and r["daily_reference_days_complete"]
        else "incomplete_or_missing_daily", axis=1)
    return monthly


def run(reference_path: Path, dispatch_path: Path, daily_dir: Path, outdir: Path) -> pd.DataFrame:
    reference = load_reference(reference_path)
    dispatch = dispatch_monthly(dispatch_path)
    daily = load_daily_reconciliations(daily_dir)
    monthly = compare(reference, dispatch, daily)
    outdir.mkdir(parents=True, exist_ok=True)
    monthly_out = outdir / "ceb_pucsl_2024_monthly_comparison.csv"
    daily_out = outdir / "ceb_pucsl_2024_daily_comparison.csv"
    json_out = outdir / "ceb_pucsl_2024_reconciliation_metadata.json"
    monthly.to_csv(monthly_out, index=False, float_format="%.6f")
    daily.to_csv(daily_out, index=False, float_format="%.6f")
    metadata = {
        "description": "DIAGNOSTIC accounting-boundary comparison (NOT a proof of equivalent definitions)",
        "reference": "CEB Sales and Generation Data Book 2024, printed pp. 73-74",
        "reference_csv": reference_path.as_posix(),
        "dispatch_csv": dispatch_path.as_posix(),
        "daily_reconciliation_dir": daily_dir.as_posix(),
        "conversion": "15-minute MW x 0.25 h / 1000 = GWh; daily MWh / 1000 = GWh",
        "intervals_observed": int(monthly["observed_intervals"].sum()),
        "intervals_expected": int(monthly["expected_intervals"].sum()),
        "intervals_flagged": int(monthly["review_required_intervals"].sum()),
        "daily_reference_rows": len(daily),
        "reference_annual_gross_gwh_published": 17460.066,
        "reference_annual_net_gwh_published": 16801.627,
        "boundary_warning": "PUCSL dispatch, daily workbook summaries, and CEB gross/net generation may not use equivalent boundaries; comparisons are exploratory",
        "missing_day_policy": "Never extrapolate missing June 30; do not treat incomplete-month totals as directly comparable to CEB full-month figures",
    }
    json_out.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print("Month  Observed  Flagged  Daily rows   PUCSL 15-min GWh   Daily reported GWh   CEB gross GWh   CEB net GWh")
    for _, r in monthly.iterrows():
        v = lambda col: f"{r[col]:.1f}" if pd.notna(r[col]) else "N/A"
        print(f"{int(r['month']):5} {int(r['observed_intervals']):9} {int(r['review_required_intervals']):8} "
              f"{int(r['daily_reconciliation_days']) if pd.notna(r['daily_reconciliation_days']) else 0:10} "
              f"{v('dispatch_integrated_all_gwh'):>17} {v('daily_reported_total_gwh'):>20} "
              f"{v('system_gross_generation_gwh'):>15} {v('system_net_generation_gwh'):>13}")
    print(f"\nSaved: {monthly_out}\n       {daily_out}\n       {json_out}")
    print("WARNING: these are NOT yet like-for-like quantities. Missing intervals and flagged observations remain visible.")
    return monthly


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reference", type=Path, default=PROJECT_ROOT / "docs/ceb_2024_monthly_reference.csv")
    parser.add_argument("--dispatch", type=Path, default=PROJECT_ROOT / "data/processed/grid/pucsl_2024_consolidated_dispatch_15min.csv")
    parser.add_argument("--daily-dir", type=Path, default=PROJECT_ROOT / "data/processed/grid")
    parser.add_argument("--outdir", type=Path, default=PROJECT_ROOT / "results/ceb_2024_reconciliation")
    args = parser.parse_args()
    run(args.reference, args.dispatch, args.daily_dir, args.outdir)


if __name__ == "__main__":
    main()
