"""Download an untouched NASA POWER hourly JSON file + a convenient aligned CSV.

NASA POWER solar irradiance is NOT grid-connected electricity generation.
Requires internet access; deliberately not run in the offline starter bundle.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

API = "https://power.larc.nasa.gov/api/temporal/hourly/point"
PARAMETERS = ["ALLSKY_SFC_SW_DWN", "T2M", "WS10M"]


def get_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="20250101", help="YYYYMMDD")
    parser.add_argument("--end", default="20251231", help="YYYYMMDD")
    parser.add_argument("--lat", type=float, default=6.9271, help="Latitude (pilot: Colombo)")
    parser.add_argument("--lon", type=float, default=79.8612, help="Longitude (pilot: Colombo)")
    parser.add_argument("--name", default="colombo", help="Simple output-location label")
    return parser.parse_args()


def main():
    args = get_args()
    for day in (args.start, args.end):
        if not re.fullmatch(r"\d{8}", day):
            raise SystemExit("--start and --end must be YYYYMMDD")
        pd.to_datetime(day, format="%Y%m%d", errors="raise")
    if args.end < args.start:
        raise SystemExit("End date must be on/after start date")
    if not (-90 <= args.lat <= 90 and -180 <= args.lon <= 180):
        raise SystemExit("Invalid lat/lon")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.name):
        raise SystemExit("--name must use only letters, numbers, underscore or hyphen")

    params = {
        "parameters": ",".join(PARAMETERS),
        "community": "RE",
        "longitude": args.lon,
        "latitude": args.lat,
        "start": args.start,
        "end": args.end,
        "format": "JSON",
        "time-standard": "UTC",
    }
    print(f"Requesting NASA POWER hourly data: {args.start}–{args.end} ({args.lat}, {args.lon})")
    try:
        response = requests.get(API, params=params, timeout=120)
        response.raise_for_status()
        raw = response.json()
    except (requests.RequestException, ValueError) as error:
        raise SystemExit(f"NASA POWER request failed: {error}")

    series = raw.get("properties", {}).get("parameter", {})
    if not series or not all(field in series for field in PARAMETERS):
        raise SystemExit(f"Unexpected NASA POWER structure: {list(series)}. No files saved.")

    frame = pd.DataFrame({name: pd.Series(series[name], dtype="float64") for name in PARAMETERS})
    try:
        frame.index = pd.to_datetime(frame.index, format="%Y%m%d%H", utc=True)
    except ValueError as error:
        raise SystemExit(f"Unexpected hourly timestamp keys: {error}")
    frame = frame.sort_index()
    frame = frame.replace(-999, float("nan"))
    frame.index.name = "timestamp_utc"
    frame = frame.reset_index()
    frame.insert(1, "timestamp_sri_lanka", frame["timestamp_utc"].dt.tz_convert(ZoneInfo("Asia/Colombo")))

    stem = f"nasa_power_{args.name}_{args.start}_{args.end}_utc"
    raw_dir = Path("data/raw/solar")
    processed_dir = Path("data/processed/solar")
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{stem}.json"
    csv_path = processed_dir / f"{stem}.csv"
    if raw_path.exists() or csv_path.exists():
        raise SystemExit("Output already exists. Move/rename it first to avoid accidental overwriting.")

    raw_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    frame.to_csv(csv_path, index=False)
    print(f"Saved unmodified API response: {raw_path}")
    print(f"Saved analysis copy:          {csv_path}")
    print(f"Rows: {len(frame)} | missing: {frame[PARAMETERS].isna().sum().to_dict()}")
    print("IMPORTANT: NASA irradiance is NOT observed Sri Lankan grid solar generation (MW).")
    print("Refer to raw API metadata for variable units.")


if __name__ == "__main__":
    main()
