"""Validate and combine PUCSL 15-minute dispatch CSVs without filling missing dates.

Inputs are the CSV/JSON outputs of scripts/preprocess_pucsl_dispatch.py.

Example from project root (include March 1-18 as explicitly partial):
    python scripts/consolidate_pucsl_dispatch.py --year 2025 --months 1 2 3 --include-partial --plot

Without --include-partial, incomplete months appear in the coverage report but
are excluded from the combined interval and daily data.

CAUTION: PUCSL dispatch has not been independently established as national
system demand. Reported daily energy differs from integrated quarter-hour
readings. Neither gap nor negative observation is silently repaired.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

LOCAL_TZ = timezone(timedelta(hours=5, minutes=30))
SOURCE_COLS = (
    "coal_mw", "oil_ceb_mw", "oil_ipp_mw", "major_hydro_mw",
    "wind_mw", "solar_mw", "biomass_mw", "mini_hydro_mw",
)
POWER_COLS = (*SOURCE_COLS, "total_dispatch_reported_mw",
              "sum_generation_by_source_mw", "dispatch_balance_difference_mw")
REQUIRED_COLS = (
    "source_filename", "source_sheet", "operating_date", "interval_number",
    "interval_start_local", "interval_end_local", "source_time_label", *POWER_COLS,
)
INTERVAL_MINUTES = 15


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"Required file is missing: {path}")
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {path}")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError(f"Repeated CSV columns: {path}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"CSV is empty: {path}")
    return rows


def _month_dates(year: int, month: int) -> set[str]:
    return {date(year, month, day).isoformat()
            for day in range(1, calendar.monthrange(year, month)[1] + 1)}


def _check_coverage(info: dict, year: int, month: int) -> tuple[list[str], list[str]]:
    if info.get("year") != year or info.get("month") != month:
        raise ValueError(f"Coverage year/month mismatch for {year}-{month:02d}")
    if info.get("resolution_minutes") != INTERVAL_MINUTES:
        raise ValueError("Expected a 15-minute coverage report")
    expected = _month_dates(year, month)
    available = info.get("available_dates", [])
    missing = info.get("missing_dates", [])
    if len(available) != len(set(available)) or len(missing) != len(set(missing)):
        raise ValueError("Duplicate dates in coverage report")
    if set(available) & set(missing) or set(available) | set(missing) != expected:
        raise ValueError("Coverage dates do not partition the entire calendar month")
    if not available:
        raise ValueError("Coverage report contains no days")
    if info.get("available_days") != len(available) or info.get("expected_days") != len(expected):
        raise ValueError("Coverage day counts do not match dates")
    if info.get("intervals") != len(available) * 96:
        raise ValueError("Coverage interval count does not equal 96 per available day")
    if info.get("status") != ("complete" if not missing else "incomplete"):
        raise ValueError("Coverage status conflicts with missing dates")
    return sorted(available), sorted(missing)


def _validate_intervals(rows: list[dict[str, str]], info: dict, available: list[str]) -> tuple[int, float]:
    if set(REQUIRED_COLS) - rows[0].keys():
        raise ValueError(f"Missing dispatch columns: {sorted(set(REQUIRED_COLS) - rows[0].keys())}")
    dates = defaultdict(list)
    negatives = 0
    max_difference = 0.0
    seen = set()
    for row in rows:
        day = row["operating_date"]
        if day not in available:
            raise ValueError(f"Dispatch row date not in coverage report: {day}")
        start = datetime.fromisoformat(row["interval_start_local"])
        end = datetime.fromisoformat(row["interval_end_local"])
        if start.utcoffset() != LOCAL_TZ.utcoffset(None) or end.utcoffset() != LOCAL_TZ.utcoffset(None):
            raise ValueError("Invalid Sri Lanka local timezone offset")
        if (end - start) != timedelta(minutes=INTERVAL_MINUTES):
            raise ValueError("Wrong interval duration")
        if start.date().isoformat() != day:
            raise ValueError("Interval start does not belong to operating date")
        if (start.hour * 60 + start.minute) % 15:
            raise ValueError("Interval start is not 15-minute aligned")
        if start.isoformat() in seen:
            raise ValueError(f"Duplicate interval: {start.isoformat()}")
        seen.add(start.isoformat())
        number = int(row["interval_number"])
        if not 1 <= number <= 96:
            raise ValueError("Invalid interval_number")
        dates[day].append((number, start, end))
        values = []
        for col in POWER_COLS:
            value = float(row[col])
            if not math.isfinite(value):
                raise ValueError(f"Non-finite power value in {col}")
            values.append(value)
        negatives += sum(float(row[col]) < 0 for col in SOURCE_COLS)
        if abs(sum(float(row[col]) for col in SOURCE_COLS)
               - float(row["sum_generation_by_source_mw"])) > 0.1:
            raise ValueError("Source sum column differs from source observations")
        actual_difference = float(row["total_dispatch_reported_mw"]) - float(row["sum_generation_by_source_mw"])
        if abs(actual_difference - float(row["dispatch_balance_difference_mw"])) > 0.1:
            raise ValueError("Dispatch balance difference column is inconsistent")
        max_difference = max(max_difference, abs(actual_difference))

    if set(dates) != set(available):
        raise ValueError("Available dates do not match dispatch dates")
    if len(rows) != info["intervals"]:
        raise ValueError("Interval row count does not match coverage JSON")
    for day, intervals in dates.items():
        intervals.sort(key=lambda x: x[1])
        if len(intervals) != 96 or [t[0] for t in intervals] != list(range(1, 97)):
            raise ValueError(f"Expected 96 correctly indexed intervals on {day}")
        midnight = datetime.combine(date.fromisoformat(day), datetime.min.time(), LOCAL_TZ)
        for index, (number, start, end) in enumerate(intervals):
            if start != midnight + timedelta(minutes=15 * index):
                raise ValueError(f"Wrong or missing timestamp on {day} at interval {number}")
    return negatives, max_difference


def _input_paths(root: Path, year: int, month: int, partial: bool) -> tuple[Path, Path, Path]:
    prefix = f"pucsl_{year}_{month:02d}_"
    suffix = "partial_" if partial else ""
    return (root / f"{prefix}{suffix}dispatch_15min.csv",
            root / f"{prefix}{suffix}reconciliation.csv",
            root / f"{prefix}{suffix}negative_generation_flags.csv")


def consolidate(root: Path, year: int, months: list[int], include_partial: bool) -> tuple[list[dict], list[dict], dict]:
    """Return interval records, observed-day summaries, and explicit coverage metadata."""
    if len(months) != len(set(months)) or any(m < 1 or m > 12 for m in months):
        raise ValueError("Months must be unique integers from 1 through 12")
    combined = []
    daily = []
    month_reports = []
    for month in sorted(months):
        coverage_path = root / f"pucsl_{year}_{month:02d}_coverage.json"
        if not coverage_path.is_file():
            raise FileNotFoundError(f"Coverage report is required: {coverage_path}")
        info = json.loads(coverage_path.read_text(encoding="utf-8"))
        available, missing = _check_coverage(info, year, month)
        partial = bool(missing)
        dispatch_path, recon_path, flags_path = _input_paths(root, year, month, partial)
        rows = _read_csv(dispatch_path)
        negatives, max_difference = _validate_intervals(rows, info, available)
        recon = _read_csv(recon_path)
        if len(recon) != len(available) or set(r["operating_date"] for r in recon) != set(available):
            raise ValueError("Daily reconciliation dates/count do not match coverage")
        if not flags_path.is_file():
            raise FileNotFoundError(f"Negative flags file is required: {flags_path}")
        with flags_path.open(newline="", encoding="utf-8-sig") as f:
            flag_rows = list(csv.DictReader(f))
        if len(flag_rows) != negatives:
            raise ValueError(f"Negative flag count disagrees with source values: {flags_path}")
        summary = {
            "year": year,
            "month": month,
            "coverage_status": "partial" if partial else "complete",
            "eligible_for_full_month_statistics": not partial,
            "included_in_interval_export": not partial or include_partial,
            "available_days": len(available),
            "expected_days": calendar.monthrange(year, month)[1],
            "observed_intervals": len(rows),
            "missing_dates": missing,
            "negative_generation_observations": negatives,
            "max_source_balance_difference_mw": round(max_difference, 3),
            "mean_reported_minus_integrated_mwh_per_observed_day": round(
                sum(float(r["daily_total_energy_difference_mwh"]) for r in recon) / len(recon), 2),
            "dispatch_file": dispatch_path.name,
            "reconciliation_file": recon_path.name,
            "coverage_file": coverage_path.name,
        }
        month_reports.append(summary)
        if partial and not include_partial:
            continue
        for row in rows:
            combined.append({**row, "month_coverage_status": summary["coverage_status"],
                             "eligible_for_full_month_statistics": str(not partial).lower()})
        by_day = defaultdict(list)
        for row in rows:
            by_day[row["operating_date"]].append(row)
        recon_by_day = {row["operating_date"]: row for row in recon}
        for operating_date, observations in sorted(by_day.items()):
            rec = recon_by_day[operating_date]
            total = sum(float(r["total_dispatch_reported_mw"]) * .25 for r in observations)
            solar = sum(float(r["solar_mw"]) * .25 for r in observations)
            wind = sum(float(r["wind_mw"]) * .25 for r in observations)
            if abs(total - float(rec["integrated_total_energy_mwh"])) > .2:
                raise ValueError(f"Integrated daily dispatch disagrees with reconciliation on {operating_date}")
            daily.append({
                "operating_date": operating_date,
                "month": month,
                "coverage_status": summary["coverage_status"],
                "eligible_for_full_month_statistics": str(not partial).lower(),
                "observed_intervals": len(observations),
                "integrated_dispatch_mwh": round(total, 4),
                "integrated_solar_mwh": round(solar, 4),
                "integrated_wind_mwh": round(wind, 4),
                "reported_daily_total_energy_mwh": rec["reported_daily_total_energy_mwh"],
                "reported_minus_integrated_mwh": round(float(rec["daily_total_energy_difference_mwh"]), 4),
            })
    combined.sort(key=lambda r: r["interval_start_local"])
    daily.sort(key=lambda r: r["operating_date"])
    if len(set(r["interval_start_local"] for r in combined)) != len(combined):
        raise ValueError("Duplicate intervals across workbooks")
    report = {
        "dataset": "PUCSL reported generation dispatch, not verified national demand",
        "year": year, "months_requested": sorted(months),
        "include_partial_requested": include_partial,
        "interval_duration_minutes": 15,
        "timestamp_convention": "Source time labels provisionally interpreted as interval ends",
        "total_exported_intervals": len(combined),
        "total_exported_observed_days": len(daily),
        "months": month_reports,
        "limitations": [
            "Reported dispatch not independently validated as national demand",
            "Reported daily MWh and quarter-hour integrated MW disagree; values are not corrected",
            "Some renewable sources, notably solar PV, may be underrepresented in quarter-hour dispatch",
            "Negative generation values are preserved and flagged",
            "Partial-month data are observational only, never full-month statistics",
            "Missing dates are not fabricated, filled with zero, or interpolated",
        ],
    }
    return combined, daily, report


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def plot_daily(daily_rows: list[dict], path: Path) -> None:
    """Daily integrated reported dispatch, segmented by month; partial months labeled."""
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 5))
    for month in sorted({r["month"] for r in daily_rows}):
        subset = [r for r in daily_rows if r["month"] == month]
        x = [date.fromisoformat(r["operating_date"]) for r in subset]
        y = [r["integrated_dispatch_mwh"] for r in subset]
        tag = " (partial; observed days only)" if subset[0]["coverage_status"] == "partial" else ""
        ax.plot(x, y, marker=".", linewidth=1.1, label=f"{calendar.month_abbr[month]}{tag}")
    ax.set(title="PUCSL reported dispatch integrated from 15-min MW readings",
           ylabel="Integrated reported dispatch (MWh / observed day)", xlabel="Operating date")
    ax.grid(alpha=.3)
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--year", type=int, default=2025)
    p.add_argument("--months", type=int, nargs="+", default=[1, 2, 3])
    p.add_argument("--input-dir", type=Path, default=Path("data/processed/grid"))
    p.add_argument("--output-dir", type=Path, default=Path("data/processed/grid"))
    p.add_argument("--include-partial", action="store_true")
    p.add_argument("--plot", action="store_true")
    args = p.parse_args()
    intervals, days, report = consolidate(args.input_dir, args.year, args.months, args.include_partial)
    stem = f"pucsl_{args.year}_consolidated"
    dispatch_path = args.output_dir / f"{stem}_dispatch_15min.csv"
    daily_path = args.output_dir / f"{stem}_observed_daily_summary.csv"
    report_path = args.output_dir / f"{stem}_coverage_quality.json"
    if intervals:
        write_csv(dispatch_path, intervals, [*REQUIRED_COLS, "month_coverage_status", "eligible_for_full_month_statistics"])
    else:
        raise ValueError("No eligible observations available for export")
    write_csv(daily_path, days, list(days[0].keys()))
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Consolidated 15-min intervals: {len(intervals)} | observed days: {len(days)}")
    for m in report["months"]:
        state = "included" if m["included_in_interval_export"] else "excluded"
        print(f"{args.year}-{m['month']:02d}: {m['coverage_status']}, {m['observed_intervals']} observed intervals; {state}; missing {len(m['missing_dates'])} days")
    print(f"Dispatch CSV: {dispatch_path}")
    print(f"Daily summary: {daily_path}")
    print(f"Coverage / quality report: {report_path}")
    print("NOTE: this is reported dispatch, NOT verified national demand; MWh reconciliation unresolved.")
    if args.plot:
        plot_path = Path("results/figures") / f"{stem}_daily_observed.png"
        plot_daily(days, plot_path)
        print(f"Observed-day figure: {plot_path}")


if __name__ == "__main__":
    main()
