"""Validate, trim and visualize NASA POWER hourly data for a Sri Lankan calendar year.

Keep the downloader's untouched JSON and wider processed CSV as provenance.
This script uses local timestamp dates to select 24 UTC-derived hourly
observations per local day. NASA UTC hour-start timestamps appear at HH:30
in Sri Lanka; this is NOT exact midnight-to-midnight energy aggregation.

Example (run from project root):
    python scripts/prepare_nasa_local_year.py --year 2025 --name colombo
"""
from __future__ import annotations

import argparse
import calendar
import json
from pathlib import Path

import pandas as pd

PARAMETERS = ("ALLSKY_SFC_SW_DWN", "T2M", "WS10M")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--year", type=int, default=2025)
    p.add_argument("--name", default="colombo")
    p.add_argument("--input", type=Path, help="Wider UTC CSV produced by NASA downloader")
    p.add_argument("--no-plot", action="store_true")
    return p.parse_args()


def prepare_local_year(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """Select all NASA hourly sample starts falling in the local calendar year."""
    needed = {"timestamp_utc", "timestamp_sri_lanka", *PARAMETERS}
    if not needed.issubset(df.columns):
        raise ValueError(f"Missing CSV columns: {sorted(needed.difference(df.columns))}")

    utc = pd.to_datetime(df["timestamp_utc"], utc=True, errors="raise")
    local = utc.dt.tz_convert("Asia/Colombo")
    given_local = pd.to_datetime(df["timestamp_sri_lanka"], utc=True, errors="raise")
    if not given_local.equals(utc):
        raise ValueError("Local timestamp column disagrees with UTC timestamp column")

    year_start = pd.Timestamp(year=year, month=1, day=1, tz="Asia/Colombo")
    year_end = pd.Timestamp(year=year + 1, month=1, day=1, tz="Asia/Colombo")
    mask = (local >= year_start) & (local < year_end)
    result = df.loc[mask].copy()
    result["timestamp_utc"] = utc.loc[mask]
    result["timestamp_sri_lanka"] = local.loc[mask]
    result = result.sort_values("timestamp_utc").reset_index(drop=True)

    expected = (366 if calendar.isleap(year) else 365) * 24
    local_times = result["timestamp_sri_lanka"]
    if len(result) != expected:
        raise ValueError(f"Expected {expected} local-year hourly samples; got {len(result)}")
    if result["timestamp_utc"].duplicated().any():
        raise ValueError("Duplicate UTC timestamps")
    deltas = result["timestamp_utc"].diff().dropna()
    if (deltas.dt.total_seconds() != 3600).any():
        raise ValueError("Missing or irregular hourly timestamps")
    per_day = local_times.dt.date.value_counts()
    if len(per_day) != expected // 24 or (per_day != 24).any():
        raise ValueError("At least one local day does not have 24 sample starts")
    if (local_times.dt.minute != 30).any():
        raise ValueError("Unexpected minute alignment; expected HH:30 for UTC hourly samples")
    for col in PARAMETERS:
        result[col] = pd.to_numeric(result[col], errors="raise")
        if result[col].isna().any() or (result[col] == -999).any():
            raise ValueError(f"Missing or -999 values in {col}")
    if (result["ALLSKY_SFC_SW_DWN"] < 0).any():
        raise ValueError("Negative solar irradiance detected")

    # Physical check. Everything above is internal consistency; this is the
    # only test against reality. If the source data or the downloader applied
    # the UTC offset incorrectly, the structural checks still pass but every
    # curtailment window derived downstream would be shifted by hours.
    night = result.loc[local_times.dt.hour.isin([0, 1, 2, 3, 22, 23]),
                       "ALLSKY_SFC_SW_DWN"]
    if night.max() > 1.0:
        raise ValueError(
            f"Non-zero overnight irradiance ({night.max():.1f}); "
            "timezone handling is wrong"
        )

    # Derived column: average power over each hourly interval.
    #
    # The API reports ALLSKY_SFC_SW_DWN in Wh/m^2 - the energy received during
    # the hour, not an instantaneous flux. For a 1-hour interval the average
    # power in W/m^2 is numerically the same, which is exactly why the two are
    # easy to confuse. The distinction matters on resampling: energy must be
    # split across sub-intervals, power carries across them.
    #
    # Carrying an explicit power column means all later alignment with CEB
    # data happens in power units, which is what NL(t) = D(t) - R(t) needs.
    # The original Wh/m^2 column is left untouched as provenance.
    result["ghi_w_m2"] = result["ALLSKY_SFC_SW_DWN"] / 1.0   # 1-hour interval

    return result


def plot_monthly_diurnal(df: pd.DataFrame, plot_path: Path) -> None:
    """Exploratory chart; not a validation of actual grid curtailment."""
    import matplotlib.pyplot as plt

    local = df["timestamp_sri_lanka"]
    plot_data = pd.DataFrame({
        "month": local.dt.month,
        "hour": local.dt.hour + local.dt.minute / 60,
        "irradiance": df["ALLSKY_SFC_SW_DWN"],
    })
    monthly = plot_data.groupby(["month", "hour"], as_index=False)["irradiance"].mean()
    fig, ax = plt.subplots(figsize=(11, 6))
    for month, group in monthly.groupby("month"):
        ax.plot(group["hour"], group["irradiance"], label=calendar.month_abbr[int(month)])
    ax.set(xlabel="Sri Lanka local time (sample starts at HH:30)",
           ylabel="Mean hourly solar irradiation (Wh/m²)",
           title="Colombo: mean hourly solar irradiation by month")
    ax.set_xlim(0, 24)
    ax.set_xticks(range(0, 25, 3))
    ax.grid(alpha=0.3)
    ax.legend(title="Month", bbox_to_anchor=(1.02, 1), loc="upper left", ncol=1)
    fig.tight_layout()
    plot_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(plot_path, dpi=160)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    if args.year < 2001 or args.year > 2100:
        raise SystemExit("Choose a supported calendar year")
    input_path = args.input or Path(
        f"data/processed/solar/nasa_power_{args.name}_{args.year - 1}1231_{args.year + 1}0101_utc.csv"
    )
    if not input_path.is_file():
        raise SystemExit(f"Input CSV not found: {input_path}. Download the wider period first.")

    raw = Path("data/raw/solar") / f"{input_path.stem}.json"
    if raw.is_file():
        metadata = json.loads(raw.read_text(encoding="utf-8"))
        units = {k: v.get("units", "not supplied") for k, v in metadata.get("parameters", {}).items()}
        print("NASA raw API parameter units:", units)
    else:
        print("NOTE: raw API metadata JSON not found; verify units separately.")

    result = prepare_local_year(pd.read_csv(input_path), args.year)
    output = Path(f"data/processed/solar/nasa_power_{args.name}_{args.year}_local.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False)
    print(f"Saved: {output}")
    print(f"Samples: {len(result)}; days: {result['timestamp_sri_lanka'].dt.date.nunique()}")
    print("First local sample:", result["timestamp_sri_lanka"].iloc[0])
    print("Last local sample: ", result["timestamp_sri_lanka"].iloc[-1])
    print("Duplicate UTC hours:", result["timestamp_utc"].duplicated().sum())
    print("IMPORTANT: time bins start at HH:30 local; exact midnight-to-midnight"
          " aggregation and CEB timestamp alignment still require explicit treatment.")

    if not args.no_plot:
        path = Path(f"results/figures/nasa_{args.name}_monthly_diurnal_{args.year}.png")
        plot_monthly_diurnal(result, path)
        print(f"Saved exploratory chart: {path}")


if __name__ == "__main__":
    main()
