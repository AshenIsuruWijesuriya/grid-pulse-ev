"""Build the hourly observed net-demand series NL(t) for one year (SO1).

Input is a consolidated PUCSL 15-minute dispatch CSV from
consolidate_pucsl_dispatch.py. Output is one row for EVERY local hour of the
year, so missing source days remain visible as rows rather than vanishing.

Definitions (see docs/nlmin_derivation.md §2)
    D(t)   total_dispatch_reported_mw: transmission-level dispatch, net of
           behind-the-meter rooftop PV. Not verified as national demand.
    R(t)   solar_mw + wind_mw: centrally metered variable renewables only.
    NL(t)  D(t) - R(t). Major hydro is NOT subtracted (dispatchable).

Aggregation
    Hourly values are the mean of the four quarter-hour MW readings whose
    interval STARTS in that hour (Asia/Colombo). Mean MW over one hour is
    numerically equal to MWh in that hour. A value is published only when all
    four intervals are present and unflagged; otherwise it is left empty and
    hour_status says why. Nothing is interpolated or imputed.

    hour_status
        complete       4 observed, 4 unflagged -> values published
        contains_flag  4 observed, at least one review_required -> empty
        incomplete     1-3 observed -> empty
        missing        no source observation -> empty

    is_system_event_day marks days whose unflagged total dispatch collapses
    below acp.OUTAGE_THRESHOLD_MW. Their hours keep their values but must be
    excluded from demand statistics.

Usage
    .venv\\Scripts\\python.exe scripts\\build_hourly_net_demand.py ^
        --input data\\processed\\grid\\pucsl_2024_consolidated_dispatch_15min.csv ^
        --year 2024 --outdir data\\processed\\grid

Writes to --outdir:
    pucsl_<year>_hourly_net_demand.csv
    pucsl_<year>_hourly_net_demand_summary.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import analyse_curtailment_pressure as acp

INTERVALS_PER_HOUR = 4
TZ = "Asia/Colombo"

VALUE_COLUMNS = ["total_dispatch_reported_mw", *acp.SOURCE_COLUMNS]


def year_hours(year: int) -> pd.DatetimeIndex:
    return pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="h",
                         inclusive="left", tz=TZ, name="hour_start_local")


def hourly_series(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """One row per local hour of `year`; df is the output of acp.load."""
    df = df.copy()
    df["hour_start_local"] = df["ts_local"].dt.floor("h")
    df = df[df["hour_start_local"].dt.year == year]
    if df["ts_local"].duplicated().any():
        raise ValueError("duplicate 15-minute timestamps in input")

    good = acp.quality_mask(df)
    by_hour = df.groupby("hour_start_local")
    observed = by_hour.size()
    valid = good.groupby(df["hour_start_local"]).sum()
    means = df[good].groupby("hour_start_local")[VALUE_COLUMNS].mean()

    out = pd.DataFrame(index=year_hours(year))
    out["observed_intervals"] = observed.reindex(out.index, fill_value=0).astype(int)
    out["valid_intervals"] = valid.reindex(out.index, fill_value=0).astype(int)
    if (out["observed_intervals"] > INTERVALS_PER_HOUR).any():
        raise ValueError("more than four quarter-hour intervals in one hour")

    out["hour_status"] = "incomplete"
    out.loc[out["observed_intervals"] == 0, "hour_status"] = "missing"
    full = out["observed_intervals"] == INTERVALS_PER_HOUR
    out.loc[full & (out["valid_intervals"] < INTERVALS_PER_HOUR),
            "hour_status"] = "contains_flag"
    out.loc[full & (out["valid_intervals"] == INTERVALS_PER_HOUR),
            "hour_status"] = "complete"

    complete = out["hour_status"] == "complete"
    values = means.reindex(out.index).where(complete, axis=0)
    out = out.join(values)
    out["vre_metered_mw"] = out["solar_mw"] + out["wind_mw"]
    out["net_demand_mw"] = out["total_dispatch_reported_mw"] - out["vre_metered_mw"]

    day_min = df[good].groupby(df["operating_date"].dt.date)[
        "total_dispatch_reported_mw"].min()
    event_days = set(day_min[day_min < acp.OUTAGE_THRESHOLD_MW].index)
    out["is_system_event_day"] = [ts.date() in event_days for ts in out.index]
    return out


def summary(hourly: pd.DataFrame, year: int, source: str) -> dict:
    status = hourly["hour_status"].value_counts().to_dict()
    gaps = hourly[hourly["hour_status"] != "complete"]
    usable = hourly[(hourly["hour_status"] == "complete")
                    & ~hourly["is_system_event_day"]]
    nl = usable["net_demand_mw"]
    return {
        "dataset": "Hourly NL(t) proxy from PUCSL reported dispatch; "
                   "not verified national demand",
        "source_file": source,
        "year": year,
        "time_zone": TZ,
        "hour_convention": "hour_start_local; mean of the four quarter-hours "
                           "starting in that hour",
        "definition": "net_demand_mw = total_dispatch_reported_mw - solar_mw - wind_mw",
        "expected_hours": len(hourly),
        "hours_by_status": {k: int(status.get(k, 0)) for k in
                            ("complete", "contains_flag", "incomplete", "missing")},
        "system_event_days": sorted({str(ts.date()) for ts in
                                     hourly.index[hourly["is_system_event_day"]]}),
        "dates_with_unusable_hours": {
            str(d): int(n) for d, n in
            gaps.groupby(gaps.index.date).size().items()},
        "usable_hours": len(usable),
        "net_demand_mw_usable": {
            "min": float(nl.min()), "p01": float(nl.quantile(0.01)),
            "median": float(nl.median()), "p99": float(nl.quantile(0.99)),
            "max": float(nl.max()),
        } if len(nl) else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True,
                    help="consolidated PUCSL 15-minute dispatch CSV")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--outdir", default="data/processed/grid")
    args = ap.parse_args()

    path = Path(args.input)
    df = acp.load(path)
    if (df["interval_quality_status"] == "legacy_unflagged").any():
        raise SystemExit("Input lacks interval-quality flags (legacy_unflagged). "
                         "Re-import it with the current preprocessor first.")

    hourly = hourly_series(df, args.year)
    info = summary(hourly, args.year, path.name)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"pucsl_{args.year}_hourly_net_demand"
    hourly.reset_index().assign(
        hour_start_local=lambda d: d["hour_start_local"].dt.strftime(
            "%Y-%m-%dT%H:%M:%S%z")
    ).to_csv(outdir / f"{stem}.csv", index=False)
    (outdir / f"{stem}_summary.json").write_text(json.dumps(info, indent=2))

    counts = info["hours_by_status"]
    print(f"{args.year}: {info['expected_hours']} hours expected")
    for k, v in counts.items():
        print(f"  {k:<14}{v:>6}")
    print(f"  usable (complete, no system event): {info['usable_hours']}")
    if info["net_demand_mw_usable"]:
        s = info["net_demand_mw_usable"]
        print(f"  NL(t) hourly MW: min {s['min']:.0f}  p01 {s['p01']:.0f}  "
              f"median {s['median']:.0f}  p99 {s['p99']:.0f}  max {s['max']:.0f}")
    print(f"Wrote {stem}.csv and {stem}_summary.json to {outdir}/")
    print("REMINDER: NL(t) here is a dispatch proxy; rooftop PV is already netted"
          " out of D(t), and reconciliation against CEB totals is still pending.")


if __name__ == "__main__":
    main()
