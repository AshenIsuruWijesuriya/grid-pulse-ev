"""Commitment-aware coal headroom in the lowest midday dispatch intervals.

analyse_curtailment_pressure.py compares mean coal in the stressed midday
intervals with ONE fleet-wide floor (the 1st percentile of all midday coal).
Coal output in the PUCSL record clusters at multiples of a single unit's net
output, and that fleet-wide floor is set mostly by periods when only one unit
was online. Comparing it with intervals in which two units were online
measures a decommitment decision, not turndown headroom.

This script asks the narrower question: GIVEN the units that were online,
how far above their lowest observed loading was coal during the stressed
intervals?

Method
  1. Unit step. Net full output of one unit, estimated as the 99th
     percentile of quality-filtered coal output divided by --coal-units.
     Printed so it can be checked; override with --unit-net-max-mw once a CEB
     figure is cited.
  2. Units online. For each interval, ceil(max coal in a centred rolling
     window / (step x (1 + tolerance))), capped at --coal-units. This is a
     HEURISTIC, not a bound: a unit held at deep turndown for the whole window
     is missed (undercount), while a unit that starts or stops inside the
     window is attributed to the whole window (overcount). The window is swept
     so the sensitivity to that choice is visible. Intervals where coal is off
     or quality-flagged are never assigned units.
  3. Empirical per-unit floor. The 1st percentile of coal / units online
     across valid, coal-producing intervals. This is the lowest loading the
     operator actually used, NOT the technical minimum stable generation;
     that comes from docs/nlmin_derivation.md.
  4. Headroom = coal - units online x per-unit floor.

Nothing here measures curtailment. It tests one presumption behind
C(t) = max(0, NLmin - NL(t)): that low net demand means the floor is reached.

Usage
    .venv\\Scripts\\python.exe scripts\\analyse_coal_commitment.py ^
        --input data\\processed\\grid\\pucsl_2024_consolidated_dispatch_15min.csv ^
        --input data\\processed\\grid\\pucsl_2025_consolidated_dispatch_15min.csv ^
        --outdir results

Writes to --outdir:
    coal_commitment_sensitivity.csv       one row per rolling window
    coal_commitment_stressed_intervals.csv  stressed intervals, reference window
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import pandas as pd

import analyse_curtailment_pressure as acp

# Coal at or below this is treated as no unit online; the record shows small
# negative values (station auxiliary load) when the plant is off.
UNIT_ONLINE_THRESHOLD_MW = 5.0

# Quantile of per-unit loading taken as the empirical turndown floor.
FLOOR_QUANTILE = 0.01

# A stressed interval within this fraction of the n-unit floor is counted as
# "at the floor".
AT_FLOOR_MARGIN = 0.10

WINDOWS_HOURS = (2, 4, 8, 24)
REFERENCE_WINDOW_HOURS = 4


def estimate_unit_step(coal: pd.Series, units: int) -> float:
    """Net full output of one unit, from the top of the coal distribution."""
    if units <= 0:
        raise ValueError("units must be positive")
    top = coal.quantile(0.99)
    if not top > 0:
        raise ValueError("coal output never rises above zero; cannot estimate step")
    return float(top) / units


def units_online(df: pd.DataFrame, step_mw: float, max_units: int,
                 window_hours: float, tolerance: float = 0.05) -> pd.Series:
    """Heuristic estimate of coal units online, per interval.

    df must be sorted by ts_local. The rolling maximum is time-based, so
    missing days do not shift the window.
    """
    if step_mw <= 0 or max_units <= 0 or window_hours <= 0:
        raise ValueError("step_mw, max_units and window_hours must be positive")
    # Unreliable observations must not propagate into nearby commitment
    # estimates through the rolling maximum.
    good = acp.quality_mask(df)
    coal = pd.to_numeric(df["coal_mw"], errors="coerce")
    coal_for_window = coal.where(good & (coal > UNIT_ONLINE_THRESHOLD_MW), 0.0)
    indexed = pd.Series(coal_for_window.to_numpy(), index=df["ts_local"])
    window_max = indexed.rolling(f"{int(window_hours * 60)}min",
                                 center=True, min_periods=1).max().to_numpy()
    capacity = step_mw * (1.0 + tolerance)
    n = [0 if (not good.iloc[i] or not pd.notna(coal.iloc[i])
                or coal.iloc[i] <= UNIT_ONLINE_THRESHOLD_MW)
         else min(max_units, max(1, math.ceil(m / capacity)))
         for i, m in enumerate(window_max)]
    return pd.Series(n, index=df.index, dtype="int64")


def per_unit_floor(coal: pd.Series, units: pd.Series,
                   eligible: pd.Series | None = None) -> float:
    """Empirical floor from valid, truly coal-producing intervals only."""
    online = (units > 0) & (coal > UNIT_ONLINE_THRESHOLD_MW) & coal.notna()
    if eligible is not None:
        online &= eligible
    if not online.any():
        raise ValueError("no eligible coal-producing intervals to estimate floor")
    return float((coal[online] / units[online]).quantile(FLOOR_QUANTILE))


def stressed_mask(df: pd.DataFrame, daily: pd.DataFrame,
                  quantile: float) -> pd.Series:
    """Same selection as acp.composition: lowest midday intervals, no outages."""
    lo, hi = acp.MIDDAY_HOURS
    outage_days = set(daily.loc[daily["is_outage_day"], "operating_date"])
    observed_days = set(daily["operating_date"])
    mid = (df["hour"].between(lo, hi)
           & df["operating_date"].isin(observed_days)
           & ~df["operating_date"].isin(outage_days)
           & acp.quality_mask(df))
    if not mid.any():
        raise ValueError("no eligible midday intervals for stress selection")
    cut = df.loc[mid, "total_dispatch_reported_mw"].quantile(quantile)
    return mid & (df["total_dispatch_reported_mw"] <= cut)


def headroom_table(df: pd.DataFrame, stressed: pd.Series, step_mw: float,
                   max_units: int, window_hours: float) -> tuple[dict, pd.DataFrame]:
    n = units_online(df, step_mw, max_units, window_hours)
    daily = acp.daily_table(df)
    excluded_days = set(daily.loc[daily["is_outage_day"], "operating_date"])
    observed_days = set(daily["operating_date"])
    good = (acp.quality_mask(df)
            & df["operating_date"].isin(observed_days)
            & ~df["operating_date"].isin(excluded_days))
    floor = per_unit_floor(df["coal_mw"], n, eligible=good)

    if (stressed & ~good).any():
        raise ValueError("stressed mask includes flagged or system-event intervals")
    s = df.loc[stressed, ["ts_local", "operating_date", "total_dispatch_reported_mw",
                          "coal_mw"]].copy()
    s["units_online"] = n[stressed]
    s["per_unit_loading_mw"] = s["coal_mw"] / s["units_online"].where(s["units_online"] > 0)
    s["commitment_floor_mw"] = s["units_online"] * floor
    s["headroom_mw"] = s["coal_mw"] - s["commitment_floor_mw"]

    units_share = s["units_online"].value_counts(normalize=True)
    summary = {
        "window_hours": window_hours,
        "unit_step_mw": step_mw,
        "per_unit_floor_mw": floor,
        "stressed_intervals": len(s),
        **{f"share_{k}_units": float(units_share.get(k, 0.0))
           for k in range(max_units + 1)},
        "mean_coal_mw": s["coal_mw"].mean(),
        "mean_headroom_mw": s["headroom_mw"].mean(),
        "median_headroom_mw": s["headroom_mw"].median(),
        "share_at_floor": float((s["headroom_mw"]
                                 <= AT_FLOOR_MARGIN * s["commitment_floor_mw"]).mean()),
    }
    return summary, s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", action="append", required=True,
                    help="consolidated dispatch CSV; repeat for multiple years")
    ap.add_argument("--outdir", default="results")
    ap.add_argument("--stressed-quantile", type=float, default=0.02)
    ap.add_argument("--coal-units", type=int, default=3,
                    help="number of coal units in the fleet")
    ap.add_argument("--unit-net-max-mw", type=float,
                    help="net full output of one unit; estimated from data if omitted")
    args = ap.parse_args()

    df = pd.concat([acp.load(Path(p)) for p in args.input], ignore_index=True)
    df = df.sort_values("ts_local").reset_index(drop=True)
    if (df["interval_quality_status"] == "legacy_unflagged").any():
        print("WARNING: Some inputs lack audited interval-quality flags."
              " Treat estimates as preliminary and re-import those months.")
    daily = acp.daily_table(df)
    stressed = stressed_mask(df, daily, args.stressed_quantile)

    eligible_days = set(daily["operating_date"])
    outage_days = set(daily.loc[daily["is_outage_day"], "operating_date"])
    base_valid = (acp.quality_mask(df)
                  & df["operating_date"].isin(eligible_days)
                  & ~df["operating_date"].isin(outage_days))
    if args.unit_net_max_mw:
        step, step_source = args.unit_net_max_mw, "supplied"
    else:
        step = estimate_unit_step(df.loc[base_valid, "coal_mw"].dropna(),
                                  args.coal_units)
        step_source = f"estimated: quality-filtered p99 coal / {args.coal_units} units"

    print(f"\n{'=' * 78}")
    print("  COAL HEADROOM CONDITIONAL ON UNITS ONLINE")
    print(f"{'=' * 78}")
    print(f"\n  {len(df):,} intervals; {int(stressed.sum())} stressed midday "
          f"intervals (lowest {args.stressed_quantile:.0%})")
    print(f"  unit step {step:.0f} MW ({step_source})")
    print("\n  DEPRECATED: comparing coal to one fleet-wide observed minimum "
          "is invalid when the number of running units varies; do not quote "
          "the old '160 MW headroom' estimate.")

    rows, reference = [], None
    for w in WINDOWS_HOURS:
        summary, detail = headroom_table(df, stressed, step, args.coal_units, w)
        rows.append(summary)
        if w == REFERENCE_WINDOW_HOURS:
            reference = detail
    table = pd.DataFrame(rows)

    unit_cols = [f"share_{k}_units" for k in range(args.coal_units + 1)]
    print(f"\n  {'window':>8}{'per-unit':>10}" +
          "".join(f"{k} units".rjust(9) for k in range(args.coal_units + 1)) +
          f"{'mean':>10}{'median':>10}{'at floor':>10}")
    print(f"  {'':>8}{'floor':>10}" + " " * 9 * len(unit_cols) +
          f"{'headroom':>10}{'headroom':>10}{'(<10%)':>10}")
    for r in table.itertuples():
        shares = "".join(f"{getattr(r, c):>9.0%}" for c in unit_cols)
        print(f"  {r.window_hours:>6.0f} h{r.per_unit_floor_mw:>7.0f} MW{shares}"
              f"{r.mean_headroom_mw:>7.0f} MW{r.median_headroom_mw:>7.0f} MW"
              f"{r.share_at_floor:>10.0%}")

    print("\n  CAUTION: unit commitment is inferred, not observed; centred rolling"
          "\n  windows can under- or overestimate units near switching events."
          "\n  The per-unit floor is an empirical 1st-percentile proxy, not a"
          "\n  technical minimum. Headroom is NOT a proven upper or lower bound."
          "\n  Do not infer measured curtailment from these estimates.")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    table.to_csv(outdir / "coal_commitment_sensitivity.csv", index=False)
    reference.to_csv(outdir / "coal_commitment_stressed_intervals.csv", index=False)
    print(f"\n  Wrote sensitivity and stressed-interval tables to {outdir}/\n")


if __name__ == "__main__":
    main()
