"""Curtailment-pressure analysis on consolidated PUCSL 15-minute dispatch data.

Answers three questions from the dispatch record, with no modelling and no
assumed parameters:

  1. SEASONAL PROFILE   On how many days does midday net demand fall below the
                        overnight trough? Which months, and how low?

  2. TREND              Comparing the same months across two years, is midday
                        net demand falling while dispatched solar stays flat?
                        (That gap is the signature of embedded rooftop growth,
                        which the PUCSL report cannot see directly.)

  3. DISPATCHABLE       During the lowest-net-demand intervals, is the
     HEADROOM           dispatchable fleet actually at its floor -- oil off,
                        coal at technical minimum, hydro backed down -- or does
                        it still have room to come down?

Question 3 is the one that matters for the C(t) formulation. C(t) =
max(0, NLmin - NL(t)) presumes that low net demand implies the floor has been
reached. This script tests that presumption against the record instead of
assuming it.

IMPORTANT -- interpreting the output
    * 'solar_mw' is CENTRALLY METERED solar only (historically under ~120 MW).
      Behind-the-meter rooftop is already netted out of total_dispatch_reported_mw
      and is not visible as a generation row. Do not read solar_mw as the
      national solar fleet.
    * major_hydro and mini_hydro are DISPATCHABLE / must-take respectively in
      the Sri Lankan system. Major hydro counts toward the conventional floor;
      it is NOT a variable renewable to be subtracted from demand.
    * Days containing an island-wide outage are excluded. 2025-02-09 is the
      known case (total dispatch to zero 11:30-13:00, then staged restoration).
      Detection is by collapse of total dispatch, not by date, so future events
      are caught as well.

Usage
    .venv\\Scripts\\python.exe scripts\\analyse_curtailment_pressure.py ^
        --input data\\processed\\grid\\pucsl_2024_consolidated_dispatch_15min.csv ^
        --input data\\processed\\grid\\pucsl_2025_consolidated_dispatch_15min.csv ^
        --outdir results

Writes to --outdir:
    curtailment_pressure_daily.csv     one row per operating day, auditable
    curtailment_pressure_monthly.csv   the monthly table printed to stdout
    curtailment_pressure_composition.csv  generation mix, stressed vs typical
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Hours (local) defining the solar window and the overnight trough. The midday
# window is deliberately narrow so that it captures the solar bulge rather than
# the shoulder periods.
MIDDAY_HOURS = (10, 14)
NIGHT_HOURS_BEFORE = 5

# Total dispatch below this is not a low-demand hour, it is a system event.
# Heuristic threshold for possible system events, NOT an outage diagnosis.
OUTAGE_THRESHOLD_MW = 300.0

# Oil below this is treated as "off" -- auxiliary load and rounding leave small
# non-zero values on units that are not generating.
OIL_OFF_THRESHOLD_MW = 5.0

SOURCE_COLUMNS = ["coal_mw", "oil_ceb_mw", "oil_ipp_mw", "major_hydro_mw",
                  "wind_mw", "solar_mw", "biomass_mw", "mini_hydro_mw"]

# Simplified dispatch role assumptions; confirm per-plant operating rules with CEB.
DISPATCHABLE = ["coal_mw", "oil_ceb_mw", "oil_ipp_mw", "major_hydro_mw"]
MUST_TAKE = ["wind_mw", "solar_mw", "biomass_mw", "mini_hydro_mw"]


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def load(path: Path) -> pd.DataFrame:
    """Read a consolidated dispatch CSV and add local-time helper columns."""
    df = pd.read_csv(path, parse_dates=["operating_date"])

    missing = [c for c in ["operating_date", "interval_start_local",
                           "total_dispatch_reported_mw"] if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name} is missing required columns: {missing}")

    ts = pd.to_datetime(df["interval_start_local"], utc=True)
    df["ts_local"] = ts.dt.tz_convert("Asia/Colombo")
    df["hour"] = df["ts_local"].dt.hour
    df["month"] = df["operating_date"].dt.month
    df["year"] = df["operating_date"].dt.year
    df["day_of_week"] = df["operating_date"].dt.dayofweek

    absent_sources = [col for col in SOURCE_COLUMNS if col not in df.columns]
    if absent_sources:
        raise ValueError(f"{path.name} is missing generation columns: {absent_sources}")

    # Old monthly exports may predate interval-level quality flags. Keep those
    # observations distinguishable; they need to be reprocessed and audited.
    if "interval_quality_status" not in df.columns:
        df["interval_quality_status"] = "legacy_unflagged"
    if "interval_quality_flags" not in df.columns:
        df["interval_quality_flags"] = ""

    df["dispatchable_mw"] = df[DISPATCHABLE].sum(axis=1, min_count=1)
    df["must_take_mw"] = df[MUST_TAKE].sum(axis=1, min_count=1)
    # A descriptive central-VRE-subtracted dispatch proxy, not validated demand.
    df["net_dispatch_proxy_mw"] = (df["total_dispatch_reported_mw"]
                                   - df["solar_mw"] - df["wind_mw"])

    return df.sort_values("ts_local").reset_index(drop=True)


def quality_mask(df: pd.DataFrame) -> pd.Series:
    """Keep unflagged intervals; legacy files remain explicitly distinguishable.

    'legacy_unflagged' does NOT mean validated. Re-import those files with the
    audited preprocessing pipeline before interpreting physical conclusions.
    """
    if "interval_quality_status" not in df.columns:
        return pd.Series(True, index=df.index, dtype=bool)
    return df["interval_quality_status"].isin(
        ["no_interval_flags", "legacy_unflagged"])


# --------------------------------------------------------------------------- #
# 1. Seasonal profile
# --------------------------------------------------------------------------- #

def daily_table(df: pd.DataFrame) -> pd.DataFrame:
    """One row per operating day: midday minimum, overnight minimum, flags."""
    lo, hi = MIDDAY_HOURS
    rows = []

    for day, g in df.groupby("operating_date", sort=True):
        # Require an actual complete set of 96 distinct intervals. A single
        # reading in each window would otherwise be misclassified as a full day.
        if len(g) != 96 or g["ts_local"].nunique() != 96:
            continue
        clean = g.loc[quality_mask(g)]
        total = clean["total_dispatch_reported_mw"]
        midday = clean.loc[clean["hour"].between(lo, hi), "total_dispatch_reported_mw"]
        night = clean.loc[clean["hour"] < NIGHT_HOURS_BEFORE, "total_dispatch_reported_mw"]
        if midday.empty or night.empty or total.empty:
            continue

        rows.append({
            "operating_date": day,
            "year": day.year,
            "month": day.month,
            "day_of_week": day.dayofweek,
            "intervals": len(g),
            "quality_excluded_intervals": len(g) - len(clean),
            "midday_min_mw": midday.min(),
            "night_min_mw": night.min(),
            "day_min_mw": total.min(),
            "day_max_mw": total.max(),
            "metered_solar_max_mw": clean["solar_mw"].max(),
            # Potential event day, not independently verified grid outage.
            "is_outage_day": bool(total.min() < OUTAGE_THRESHOLD_MW),
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # Descriptive inversion in reported dispatch; its cause is not identified.
    out["midday_below_night"] = out["midday_min_mw"] < out["night_min_mw"]
    return out


def monthly_table(daily: pd.DataFrame) -> pd.DataFrame:
    """Aggregate the daily table to the monthly summary."""
    clean = daily[~daily["is_outage_day"]]
    rows = []
    for (year, month), g in clean.groupby(["year", "month"], sort=True):
        rows.append({
            "year": year,
            "month": month,
            "month_name": MONTHS[month - 1],
            "days": len(g),
            "days_midday_below_night": int(g["midday_below_night"].sum()),
            "share_midday_below_night": g["midday_below_night"].mean(),
            "lowest_dispatch_mw": g["day_min_mw"].min(),
            "mean_midday_min_mw": g["midday_min_mw"].mean(),
            "peak_metered_solar_mw": g["metered_solar_max_mw"].max(),
        })
    return pd.DataFrame(rows)


def print_monthly(daily: pd.DataFrame) -> None:
    clean = daily[~daily["is_outage_day"]]
    excluded = daily[daily["is_outage_day"]]

    for year, yg in clean.groupby("year", sort=True):
        print(f"\n{'=' * 78}")
        print(f"  {year}   {len(yg)} operating days analysed")
        print(f"{'=' * 78}")

        ex = excluded[excluded["year"] == year]
        if len(ex):
            dates = ", ".join(str(d.date()) for d in ex["operating_date"])
            print(f"  excluded as system-event days: {dates}")

        print(f"\n  {'':5}{'days':>6}{'midday < night':>17}{'lowest':>11}"
              f"{'mean midday':>14}{'peak solar':>13}")
        for month in range(1, 13):
            s = yg[yg["month"] == month]
            if s.empty:
                continue
            share = s["midday_below_night"].mean()
            bar = "#" * int(round(share * 28))
            label = f"{int(s['midday_below_night'].sum())}/{len(s)}  {share:4.0%}"
            print(f"  {MONTHS[month - 1]:5}{len(s):>6}{label:>17}"
                  f"{s['day_min_mw'].min():>8.0f} MW"
                  f"{s['midday_min_mw'].mean():>11.0f} MW"
                  f"{s['metered_solar_max_mw'].max():>10.0f} MW   {bar}")

        share = yg["midday_below_night"].mean()
        label = f"{int(yg['midday_below_night'].sum())}/{len(yg)}  {share:4.0%}"
        print(f"  {'ALL':5}{len(yg):>6}{label:>17}"
              f"{yg['day_min_mw'].min():>8.0f} MW"
              f"{yg['midday_min_mw'].mean():>11.0f} MW"
              f"{yg['metered_solar_max_mw'].max():>10.0f} MW")

        # Weekday and weekend profiles may differ for multiple reasons.
        sunday = yg[yg["day_of_week"] == 6]["midday_min_mw"].mean()
        weekday = yg[yg["day_of_week"] < 5]["midday_min_mw"].mean()
        if pd.notna(sunday) and pd.notna(weekday):
            print(f"\n  Day type: Sunday midday minimum {sunday:.0f} MW vs "
                  f"weekday {weekday:.0f} MW  (difference {weekday - sunday:+.0f} MW)")


# --------------------------------------------------------------------------- #
# 2. Year-over-year trend
# --------------------------------------------------------------------------- #

def print_year_over_year(daily: pd.DataFrame) -> None:
    """Compare the same calendar months across years.

    A change in midday reported dispatch could reflect many factors (demand,
    behind-the-meter solar, seasonality, weather, generation mix, data coverage).
    These comparisons do not isolate rooftop generation growth.
    """
    clean = daily[~daily["is_outage_day"]]
    years = sorted(clean["year"].unique())
    if len(years) < 2:
        return

    first, last = years[0], years[-1]
    a = clean[clean["year"] == first]
    b = clean[clean["year"] == last]
    shared = sorted(set(a["month"]) & set(b["month"]))
    if not shared:
        return

    print(f"\n{'=' * 78}")
    print(f"  YEAR OVER YEAR  {first} vs {last}, matched months")
    print(f"{'=' * 78}")
    print(f"\n  {'':5}{'share midday < night':>24}{'mean midday minimum':>28}"
          f"{'peak metered solar':>24}")
    print(f"  {'':5}{first:>10}{last:>8}{'':6}{first:>10} MW{last:>9} MW"
          f"{'  change':>10}{first:>8} MW{last:>9} MW")

    for month in shared:
        sa, sb = a[a["month"] == month], b[b["month"] == month]
        ma, mb = sa["midday_min_mw"].mean(), sb["midday_min_mw"].mean()
        print(f"  {MONTHS[month - 1]:5}"
              f"{sa['midday_below_night'].mean():>10.0%}"
              f"{sb['midday_below_night'].mean():>8.0%}{'':6}"
              f"{ma:>10.0f} MW{mb:>9.0f} MW{mb - ma:>+9.0f}"
              f"{sa['metered_solar_max_mw'].max():>9.0f} MW"
              f"{sb['metered_solar_max_mw'].max():>9.0f} MW")

    print("\n  INTERPRETATION: changes in midday dispatch cannot be attributed"
          "\n  uniquely to rooftop PV without independent generation/demand data.")


# --------------------------------------------------------------------------- #
# 3. Dispatchable headroom
# --------------------------------------------------------------------------- #

def composition(df: pd.DataFrame, daily: pd.DataFrame,
                stressed_quantile: float = 0.02) -> pd.DataFrame:
    """Exploratory source-mix comparison at lowest midday *reported dispatch*.

    This does not identify technical minimum generation or measured curtailment.
    Excludes interval-quality exceptions and candidate system-event days.
    """
    lo, hi = MIDDAY_HOURS
    outage_days = set(daily.loc[daily["is_outage_day"], "operating_date"])

    complete_days = set(daily["operating_date"])
    mid = df[df["hour"].between(lo, hi)
             & df["operating_date"].isin(complete_days)
             & ~df["operating_date"].isin(outage_days)
             & quality_mask(df)].copy()
    if mid.empty:
        return pd.DataFrame()

    cut = mid["total_dispatch_reported_mw"].quantile(stressed_quantile)
    stressed = mid[mid["total_dispatch_reported_mw"] <= cut]
    typical = mid[mid["total_dispatch_reported_mw"] > cut]

    rows = []
    for col in (["total_dispatch_reported_mw", "dispatchable_mw", "must_take_mw"]
                + SOURCE_COLUMNS):
        s_mean, t_mean = stressed[col].mean(), typical[col].mean()
        if pd.isna(s_mean) and pd.isna(t_mean):
            continue
        rows.append({
            "series": col,
            "stressed_mean_mw": s_mean,
            "typical_mean_mw": t_mean,
            "change_mw": s_mean - t_mean,
            "observed_min_mw": mid[col].min(),
            "observed_p05_mw": mid[col].quantile(0.05),
        })

    table = pd.DataFrame(rows)

    print(f"\n{'=' * 78}")
    print(f"  GENERATION MIX AT THE LOWEST {stressed_quantile:.0%} OF MIDDAY "
          f"INTERVALS")
    print(f"{'=' * 78}")
    print(f"\n  threshold: total dispatch <= {cut:.0f} MW"
          f"   (n = {len(stressed)} stressed, {len(typical)} typical)\n")
    print(f"  {'series':<28}{'stressed':>11}{'typical':>11}{'change':>11}"
          f"{'year min':>11}{'year p05':>11}")
    for r in table.itertuples():
        print(f"  {r.series:<28}{r.stressed_mean_mw:>8.0f} MW"
              f"{r.typical_mean_mw:>8.0f} MW{r.change_mw:>+8.0f} MW"
              f"{r.observed_min_mw:>8.0f} MW{r.observed_p05_mw:>8.0f} MW")

    # Descriptive only. A fleet-wide observed coal minimum is NOT a comparable
    # per-unit technical floor when commitment varies, so do not infer headroom.
    oil_total = stressed["oil_ceb_mw"].fillna(0) + stressed["oil_ipp_mw"].fillna(0)
    oil_off = (oil_total < OIL_OFF_THRESHOLD_MW).mean()
    coal_mean = stressed["coal_mw"].mean()
    print(f"\n  Oil effectively off (< {OIL_OFF_THRESHOLD_MW:.0f} MW):"
          f" {oil_off:.0%} of stressed intervals")
    print(f"  Mean observed coal dispatch: {coal_mean:.0f} MW")
    print("\n  NO CURTAILMENT VERDICT: the source mix cannot establish technical"
          "\n  minima, actual curtailment, rooftop solar output, or secure NLmin."
          "\n  For conditional coal estimates, see analyse_coal_commitment.py."
          "\n  Treat all results as exploratory until independent validation.")

    return table


# --------------------------------------------------------------------------- #

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", action="append", required=True,
                    help="consolidated dispatch CSV; repeat for multiple years")
    ap.add_argument("--outdir", default="results")
    ap.add_argument("--stressed-quantile", type=float, default=0.02,
                    help="quantile of midday intervals treated as stressed")
    args = ap.parse_args()

    frames = []
    for raw in args.input:
        path = Path(raw)
        print(f"Loading {path.name}")
        frames.append(load(path))
    df = pd.concat(frames, ignore_index=True)
    print(f"  {len(df):,} intervals, "
          f"{df['operating_date'].nunique()} operating days")

    if (df["interval_quality_status"] == "legacy_unflagged").any():
        print("WARNING: Some rows lack audited quality flags (legacy_unflagged);"
              " reprocess them before scientific interpretation.")
    daily = daily_table(df)
    if daily.empty:
        raise SystemExit("No complete operating days found; check the input.")

    print_monthly(daily)
    print_year_over_year(daily)
    comp = composition(df, daily, args.stressed_quantile)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    daily.to_csv(outdir / "curtailment_pressure_daily.csv", index=False)
    monthly_table(daily).to_csv(outdir / "curtailment_pressure_monthly.csv",
                                index=False)
    if not comp.empty:
        comp.to_csv(outdir / "curtailment_pressure_composition.csv", index=False)

    print(f"\n  Wrote daily, monthly and composition tables to {outdir}/")
    print("\n  REMINDER: centrally reported solar is not total available solar."
          "\n  Reported dispatch is not yet validated as national demand.\n")


if __name__ == "__main__":
    main()
