"""Download archived day-ahead irradiance FORECASTS (Open-Meteo Previous Runs API).

The values are what a weather model predicted ahead of time, not what
happened: `shortwave_radiation_previous_day1` is the forecast made at least
24 h before the valid hour, `_previous_day2` at least 48 h before. Forecasts
issued later (`previous_day0`, the live run) are deliberately NOT requested, so
nothing in these files can leak same-day weather into a midnight forecast.

Open-Meteo labels each radiation value by the END of the hour it averages.
The analysis CSV relabels it to the START of that hour (`timestamp_utc`), the
same convention as the NASA POWER files, and keeps the original label
(`label_end_utc`). Verified for Colombo 2024: the relabelled series correlates
best with NASA POWER at zero shift.

Forecast irradiance is NOT measured irradiance and NOT solar generation (MW).
Requires internet access.

Usage
    .venv\\Scripts\\python.exe scripts\\download_nwp_forecast.py --lat 6.9271 --lon 79.8612 --name colombo
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

API = "https://previous-runs-api.open-meteo.com/v1/forecast"
LEAD_DAYS = (1, 2)
COLUMNS = [f"shortwave_radiation_previous_day{d}" for d in LEAD_DAYS]


def to_frame(raw: dict) -> pd.DataFrame:
    """Analysis frame: interval-start UTC label, local label, forecast GHI (W/m^2)."""
    hourly = raw.get("hourly", {})
    units = raw.get("hourly_units", {})
    if "time" not in hourly or not all(c in hourly for c in COLUMNS):
        raise ValueError(f"Unexpected Open-Meteo structure: {list(hourly)}")
    if any(units.get(c) != "W/m²" for c in COLUMNS):
        raise ValueError(f"Unexpected units: {units}")
    if raw.get("utc_offset_seconds") != 0:
        raise ValueError("Request the data in GMT so the labels are UTC")
    end = pd.to_datetime(pd.Series(hourly["time"]), format="%Y-%m-%dT%H:%M", utc=True)
    frame = pd.DataFrame({c: pd.to_numeric(pd.Series(hourly[c], dtype="float64")) for c in COLUMNS})
    if (frame < 0).any().any():
        raise ValueError("Negative forecast irradiance")
    start = end - pd.offsets.Hour(1)
    frame.insert(0, "label_end_utc", end)
    frame.insert(0, "timestamp_sri_lanka", start.dt.tz_convert(ZoneInfo("Asia/Colombo")))
    frame.insert(0, "timestamp_utc", start)
    return frame


def get_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2023-12-31", help="YYYY-MM-DD (UTC)")
    parser.add_argument("--end", default="2025-01-01", help="YYYY-MM-DD (UTC)")
    parser.add_argument("--lat", type=float, default=6.9271, help="Latitude (pilot: Colombo)")
    parser.add_argument("--lon", type=float, default=79.8612, help="Longitude (pilot: Colombo)")
    parser.add_argument("--name", default="colombo", help="Simple output-location label")
    parser.add_argument("--model", default="gfs_seamless",
                        help="Open-Meteo model; GFS is archived from 19 Jan 2024, ECMWF IFS only from March 2024")
    return parser.parse_args()


def main():
    args = get_args()
    for day in (args.start, args.end):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise SystemExit("--start and --end must be YYYY-MM-DD")
        pd.to_datetime(day, format="%Y-%m-%d", errors="raise")
    if args.end < args.start:
        raise SystemExit("End date must be on/after start date")
    if not (-90 <= args.lat <= 90 and -180 <= args.lon <= 180):
        raise SystemExit("Invalid lat/lon")
    for value in (args.name, args.model):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise SystemExit("--name and --model must use only letters, numbers, underscore or hyphen")

    params = {
        "latitude": args.lat,
        "longitude": args.lon,
        "hourly": ",".join(COLUMNS),
        "models": args.model,
        "start_date": args.start,
        "end_date": args.end,
        "timezone": "GMT",
    }
    print(f"Requesting archived {args.model} forecasts: {args.start}–{args.end} ({args.lat}, {args.lon})")
    try:
        response = requests.get(API, params=params, timeout=180)
        response.raise_for_status()
        raw = response.json()
        frame = to_frame(raw)
    except (requests.RequestException, ValueError) as error:
        raise SystemExit(f"Open-Meteo request failed: {error}. No files saved.")

    stem = f"openmeteo_{args.model}_{args.name}_{args.start.replace('-', '')}_{args.end.replace('-', '')}_utc"
    raw_dir = Path("data/raw/weather_forecast")
    processed_dir = Path("data/processed/weather_forecast")
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{stem}.json"
    csv_path = processed_dir / f"{stem}.csv"
    if raw_path.exists() or csv_path.exists():
        raise SystemExit("Output already exists. Move/rename it first to avoid accidental overwriting.")

    raw["request"] = {"url": API, "params": params,
                      "retrieved_utc": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")}
    raw_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    frame.to_csv(csv_path, index=False)
    print(f"Saved API response (+ request record): {raw_path}")
    print(f"Saved analysis copy:                   {csv_path}")
    print(f"Model grid point: {raw.get('latitude')}, {raw.get('longitude')}")
    print(f"Rows: {len(frame)} | missing: {frame[COLUMNS].isna().sum().to_dict()}")
    print("IMPORTANT: forecast irradiance is NOT measured irradiance or Sri Lankan solar generation (MW).")


if __name__ == "__main__":
    main()
