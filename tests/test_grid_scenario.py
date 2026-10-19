"""Tests for scripts/build_grid_scenario.py. Hand-built fixtures; quote nothing."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_grid_scenario as bgs  # noqa: E402

TZ = "Asia/Colombo"


def _nasa_csv(path: Path, year: int = 2024, shift_hours: int = 0) -> Path:
    utc = pd.date_range(f"{year - 1}-12-31", f"{year + 1}-01-02", freq="h", tz="UTC")
    local_mid = utc.shift(30, freq="min").tz_convert(TZ)   # sample centre
    hour = local_mid.hour + local_mid.minute / 60 - shift_hours
    ghi = np.clip(np.sin((hour - 6) / 12 * np.pi), 0, None) * 900
    pd.DataFrame({"timestamp_utc": utc.strftime("%Y-%m-%dT%H:%M:%S%z"),
                  "ALLSKY_SFC_SW_DWN": np.round(ghi, 3)}).to_csv(path, index=False)
    return path


def test_nasa_local_hour_is_mean_of_two_half_overlapping_samples(tmp_path):
    p = _nasa_csv(tmp_path / "n.csv")
    out = bgs.nasa_local_hourly(p, 2024)
    raw = pd.read_csv(p)
    raw.index = pd.to_datetime(raw["timestamp_utc"], utc=True).dt.tz_convert(TZ)
    h = pd.Timestamp("2024-03-01 12:00", tz=TZ)
    expected = (raw.loc[h - pd.offsets.Minute(30), "ALLSKY_SFC_SW_DWN"]
                + raw.loc[h + pd.offsets.Minute(30), "ALLSKY_SFC_SW_DWN"]) / 2
    assert out[h] == pytest.approx(expected)
    assert len(out) == 8784
    assert out[out.index.hour.isin([0, 1, 2, 23])].max() == 0


def test_nasa_time_zone_error_is_caught(tmp_path):
    p = _nasa_csv(tmp_path / "n.csv", shift_hours=8)
    with pytest.raises(ValueError, match="overnight"):
        bgs.nasa_local_hourly(p, 2024)


def _base(hours=24, demand=1500.0, solar_peak=600.0):
    idx = pd.date_range("2024-01-06", periods=hours, freq="h", tz=TZ)
    solar = np.clip(np.sin((idx.hour - 6) / 12 * np.pi), 0, None) * solar_peak
    return pd.DataFrame({"demand_system_mw": demand, "solar_available_mw": solar,
                         "wind_mw": 50.0, "must_take_mw": 100.0}, index=idx)


def test_scenario_net_load_and_floor_risk():
    b = _base()
    s = bgs.scenario_signal(b, nlmin_mw=900, solar_multiplier=1.0)
    noon = s.index[12]
    assert s.loc[noon, "net_load_mw"] == pytest.approx(1500 - 600 - 50 - 100)
    assert s.loc[noon, "curtailment_risk_mw"] == pytest.approx(900 - 750)
    assert s.loc[s.index[0], "curtailment_risk_mw"] == 0
    s2 = bgs.scenario_signal(b, nlmin_mw=900, solar_multiplier=2.0)
    assert s2.loc[noon, "curtailment_risk_mw"] == pytest.approx(900 - 150)


def test_snsp_term_and_max_of_constraints():
    b = _base()
    s = bgs.scenario_signal(b, nlmin_mw=0, snsp_max=0.3)
    noon = s.index[12]
    assert s.loc[noon, "c_floor_mw"] == 0
    assert s.loc[noon, "c_snsp_mw"] == pytest.approx(650 - 0.3 * 1500)
    assert s.loc[noon, "curtailment_risk_mw"] == pytest.approx(200)


def test_security_shortfall_when_floor_exceeds_available_vre():
    b = _base(solar_peak=0.0)
    s = bgs.scenario_signal(b, nlmin_mw=1500)
    # NL = 1350, risk 150, but only 50 MW of VRE can be curtailed.
    assert s["security_shortfall_mw"].iloc[0] == pytest.approx(100)


def test_missing_observation_propagates_as_nan():
    b = _base()
    b.iloc[3, b.columns.get_loc("demand_system_mw")] = np.nan
    s = bgs.scenario_signal(b, nlmin_mw=900)
    assert np.isnan(s["curtailment_risk_mw"].iloc[3])


def _hourly_day(date="2024-01-06"):
    idx = pd.date_range(date, periods=24, freq="h", tz=TZ)
    src = {"coal_mw": 500.0, "oil_ceb_mw": 50.0, "oil_ipp_mw": 0.0, "major_hydro_mw": 300.0,
           "wind_mw": 40.0, "solar_mw": 60.0, "biomass_mw": 5.0, "mini_hydro_mw": 95.0}
    df = pd.DataFrame(src, index=idx)
    df["total_dispatch_reported_mw"] = df.sum(axis=1)
    df["hour_status"] = "complete"
    df["is_system_event_day"] = False
    return df


def test_base_identity_observed_dispatchable_at_multiplier_one():
    hourly = _hourly_day()
    shape = pd.Series(np.clip(np.sin((hourly.index.hour - 6) / 12 * np.pi), 0, None) * 800,
                      index=hourly.index)
    ceb = pd.DataFrame({"solar_gwh": [3.0], "wind_gwh": [1.2], "must_take_gwh": [2.6]}, index=[1])
    pucsl = pd.DataFrame({"wind_gwh": [0.96], "must_take_gwh": [2.4],
                          "observed_fraction": [1.0]}, index=[1])
    base = bgs.build_base(hourly, shape, ceb, pucsl, ["mini_hydro_mw", "biomass_mw"])
    assert base["solar_available_mw"].sum() == pytest.approx(3000.0)
    s = bgs.scenario_signal(base, nlmin_mw=0)
    assert np.allclose(s["net_load_mw"], base["dispatchable_observed_mw"])
    assert bgs.eligible_days(base) == [hourly.index[0].normalize()]


def test_eligible_days_excludes_flagged_hour():
    hourly = _hourly_day()
    hourly.iloc[5, hourly.columns.get_loc("hour_status")] = "contains_flag"
    base = pd.DataFrame({"hour_status": hourly["hour_status"],
                         "is_system_event_day": hourly["is_system_event_day"]})
    assert bgs.eligible_days(base) == []
