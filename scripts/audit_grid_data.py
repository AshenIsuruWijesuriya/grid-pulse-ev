"""Audit a documented, time-aligned grid CSV without fabricating missing records."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

REQUIRED = ["timestamp", "demand_mw", "solar_actual_mw", "wind_actual_mw"]
TZ_PATTERN = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$", flags=re.I)


def audit(path: Path) -> dict:
    data = pd.read_csv(path)
    missing_columns = sorted(set(REQUIRED) - set(data.columns))
    if missing_columns:
        raise ValueError(f"Missing required columns: {missing_columns}. Check docs/grid_data_dictionary.md")
    if data.empty:
        raise ValueError("Input is empty")
    text_times = data["timestamp"].astype("string")
    no_offset = text_times.notna() & ~text_times.str.contains(TZ_PATTERN, na=False)
    if no_offset.any():
        raise ValueError("Timestamps must explicitly specify their offset, e.g. 2025-01-01T00:00:00+05:30")
    stamps = pd.to_datetime(data["timestamp"], utc=True, errors="coerce")
    if stamps.isna().any():
        raise ValueError("Unparseable or missing timestamp(s) in input")
    missing = {}
    negatives = {}
    for column in REQUIRED[1:]:
        numeric = pd.to_numeric(data[column], errors="coerce")
        missing[column] = int(numeric.isna().sum())
        negatives[column] = int((numeric < 0).sum())
    if any(negatives.values()):
        raise ValueError(f"Negative MW measurements: {negatives}")

    series = stamps.sort_values()
    duplicates = int(series.duplicated().sum())
    intervals = series.diff().dropna().dt.total_seconds() / 60
    dominant = int(intervals.mode().iloc[0]) if len(intervals) else None
    known_interval = dominant in (30, 60)
    expected_count = (int((series.max() - series.min()).total_seconds() / (dominant*60)) + 1) if known_interval else None
    expected_annual = {30: 365*48, 60: 365*24}.get(dominant)
    observation_count = len(series)
    coverage = (1 - max(0, expected_count-observation_count)/expected_count) if expected_count else None
    twelve_month_target = bool(expected_annual and observation_count >= expected_annual and coverage is not None and coverage >= 0.99 and duplicates == 0 and not any(missing.values()))

    return {
        "file": str(path),
        "records": observation_count,
        "first_timestamp_utc": series.iloc[0].isoformat(),
        "last_timestamp_utc": series.iloc[-1].isoformat(),
        "dominant_interval_minutes": dominant,
        "supported_interval_30_or_60_minutes": known_interval,
        "duplicate_timestamps": duplicates,
        "missing_numeric_values": missing,
        "negative_numeric_values": negatives,
        "expected_records_within_observed_range": expected_count,
        "coverage_within_observed_range": coverage,
        "12_month_data_target_pass_preliminary": twelve_month_target,
        "caution": "A passing shape check does NOT verify data provenance, source definitions, or that actual renewable generation equals available renewable generation.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Path to grid CSV using documented schema")
    args = parser.parse_args()
    path = Path(args.input)
    if not path.is_file():
        raise SystemExit(f"No such CSV: {path}")
    try:
        results = audit(path)
    except ValueError as error:
        raise SystemExit(f"Audit failed: {error}")
    print(json.dumps(results, indent=2))
    print("Status:", "PASS (preliminary shape check only)" if results["12_month_data_target_pass_preliminary"] else "NOT READY for SO1")


if __name__ == "__main__":
    main()
