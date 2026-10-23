"""Tests for scripts/download_nwp_forecast.py. Synthetic responses only, no network."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import download_nwp_forecast as dn  # noqa: E402


def _raw(**over):
    raw = {"utc_offset_seconds": 0,
           "hourly_units": {c: "W/m²" for c in dn.COLUMNS},
           "hourly": {"time": ["2024-10-01T07:00", "2024-10-01T08:00"],
                      "shortwave_radiation_previous_day1": [500.0, None],
                      "shortwave_radiation_previous_day2": [450.0, 400.0]}}
    raw.update(over)
    return raw


def test_relabels_to_interval_start_and_keeps_end_label():
    f = dn.to_frame(_raw())
    assert str(f["timestamp_utc"].iloc[0]) == "2024-10-01 06:00:00+00:00"
    assert str(f["label_end_utc"].iloc[0]) == "2024-10-01 07:00:00+00:00"
    assert str(f["timestamp_sri_lanka"].iloc[0]) == "2024-10-01 11:30:00+05:30"
    assert f["shortwave_radiation_previous_day1"].isna().tolist() == [False, True]


@pytest.mark.parametrize("bad", [{"utc_offset_seconds": 19800},
                                 {"hourly_units": {c: "Wh/m²" for c in dn.COLUMNS}},
                                 {"hourly": {"time": ["2024-10-01T07:00"]}}])
def test_rejects_wrong_time_zone_units_or_structure(bad):
    with pytest.raises(ValueError):
        dn.to_frame(_raw(**bad))


def test_rejects_negative_irradiance():
    raw = _raw()
    raw["hourly"]["shortwave_radiation_previous_day2"] = [-1.0, 400.0]
    with pytest.raises(ValueError):
        dn.to_frame(raw)
