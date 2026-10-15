"""Tests for scripts/build_hourly_net_demand.py.

Hand-built fixtures; quote nothing from here.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import analyse_curtailment_pressure as acp  # noqa: E402
import build_hourly_net_demand as bnd  # noqa: E402


def _day(date: str, total: float = 1500.0) -> pd.DataFrame:
    starts = pd.date_range(f"{date} 00:00", periods=96, freq="15min", tz="Asia/Colombo")
    return pd.DataFrame({
        "operating_date": date,
        "interval_number": range(1, 97),
        "interval_start_local": starts.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "coal_mw": 500.0, "oil_ceb_mw": 100.0, "oil_ipp_mw": 0.0,
        "major_hydro_mw": 400.0, "wind_mw": 50.0, "solar_mw": 70.0,
        "biomass_mw": 10.0, "mini_hydro_mw": 100.0,
        "total_dispatch_reported_mw": total,
        "interval_quality_status": "no_interval_flags",
    })


def _hourly(tmp_path, frames, year=2024):
    path = tmp_path / "d.csv"
    pd.concat(frames, ignore_index=True).to_csv(path, index=False)
    return bnd.hourly_series(acp.load(path), year)


def test_every_hour_of_leap_year_is_a_row(tmp_path):
    hourly = _hourly(tmp_path, [_day("2024-01-01")])
    assert len(hourly) == 8784
    assert hourly.index[0].strftime("%Y-%m-%d %H:%M %z") == "2024-01-01 00:00 +0530"


def test_hour_is_mean_of_its_four_quarter_hours(tmp_path):
    raw = _day("2024-01-01")
    raw.loc[4:7, "total_dispatch_reported_mw"] = [1000.0, 1100.0, 1200.0, 1300.0]
    hourly = _hourly(tmp_path, [raw])
    assert hourly.iloc[1]["total_dispatch_reported_mw"] == pytest.approx(1150.0)
    assert hourly.iloc[1]["hour_status"] == "complete"


def test_net_demand_subtracts_solar_and_wind_but_not_hydro(tmp_path):
    hourly = _hourly(tmp_path, [_day("2024-01-01")])
    row = hourly.iloc[0]
    assert row["net_demand_mw"] == pytest.approx(1500.0 - 70.0 - 50.0)


def test_missing_day_is_24_missing_rows_with_no_values(tmp_path):
    hourly = _hourly(tmp_path, [_day("2024-01-01"), _day("2024-01-03")])
    jan2 = hourly.loc["2024-01-02"]
    assert len(jan2) == 24
    assert (jan2["hour_status"] == "missing").all()
    assert jan2["net_demand_mw"].isna().all()


def test_flagged_interval_withholds_the_hour_rather_than_averaging_three(tmp_path):
    raw = _day("2024-01-01")
    raw.loc[9, "interval_quality_status"] = "review_required"
    raw.loc[9, "total_dispatch_reported_mw"] = 9999.0
    hourly = _hourly(tmp_path, [raw])
    assert hourly.iloc[2]["hour_status"] == "contains_flag"
    assert pd.isna(hourly.iloc[2]["net_demand_mw"])
    assert hourly.iloc[3]["hour_status"] == "complete"


def test_partial_hour_is_incomplete(tmp_path):
    raw = _day("2024-01-01").drop(index=[0])
    hourly = _hourly(tmp_path, [raw])
    assert hourly.iloc[0]["hour_status"] == "incomplete"
    assert pd.isna(hourly.iloc[0]["net_demand_mw"])


def test_negative_readings_are_preserved_not_clipped(tmp_path):
    raw = _day("2024-01-01")
    raw.loc[0:3, "wind_mw"] = -2.0
    hourly = _hourly(tmp_path, [raw])
    assert hourly.iloc[0]["wind_mw"] == pytest.approx(-2.0)
    assert hourly.iloc[0]["net_demand_mw"] == pytest.approx(1500.0 - 70.0 + 2.0)


def test_system_event_day_is_marked(tmp_path):
    hourly = _hourly(tmp_path, [_day("2024-01-01"), _day("2024-01-02", total=40.0)])
    assert not hourly.loc["2024-01-01", "is_system_event_day"].any()
    assert hourly.loc["2024-01-02", "is_system_event_day"].all()


def test_summary_counts_and_excludes_event_days(tmp_path):
    hourly = _hourly(tmp_path, [_day("2024-01-01"), _day("2024-01-02", total=40.0)])
    info = bnd.summary(hourly, 2024, "d.csv")
    assert info["hours_by_status"]["complete"] == 48
    assert info["hours_by_status"]["missing"] == 8784 - 48
    assert info["usable_hours"] == 24
    assert info["system_event_days"] == ["2024-01-02"]
