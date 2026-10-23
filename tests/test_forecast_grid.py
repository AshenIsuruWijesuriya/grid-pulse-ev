"""Tests for scripts/forecast_grid.py. Synthetic fixtures only."""

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import forecast_grid as fg  # noqa: E402

TZ = "Asia/Colombo"


def _base(days=21):
    idx = pd.date_range("2024-01-01", periods=24 * days, freq="h", tz=TZ)
    rng = np.random.default_rng(0)
    demand = 1500 + 300 * np.sin((idx.hour - 13) / 24 * 2 * np.pi) + rng.normal(0, 10, len(idx))
    demand = np.where(idx.dayofweek >= 5, demand - 200, demand)
    solar = np.clip(np.sin((idx.hour - 6) / 12 * np.pi), 0, None) * 500
    return pd.DataFrame({"demand_system_mw": demand, "solar_available_mw": solar,
                         "wind_mw": 50.0, "must_take_mw": 100.0}, index=idx)


def test_features_use_only_past_information():
    b = _base()
    f = fg.features(b, "demand_system_mw", set())
    t = b.index[24 * 9 + 13]
    assert f.loc[t, "lag_24"] == b["demand_system_mw"].iloc[24 * 8 + 13]
    assert f.loc[t, "lag_168"] == b["demand_system_mw"].iloc[24 * 2 + 13]
    yesterday = b["demand_system_mw"].iloc[24 * 8: 24 * 9].mean()
    assert f.loc[t, "prev_day_mean"] == pytest.approx(yesterday)


def test_holiday_and_day_after_flags():
    b = _base(days=3)
    f = fg.features(b, "demand_system_mw", {date(2024, 1, 2)})
    assert f["holiday"].groupby(f.index.day).max().tolist() == [0, 1, 0]
    assert f["day_after_holiday"].groupby(f.index.day).max().tolist() == [0, 0, 1]


def test_persistence_is_yesterday_same_hour():
    b = _base()
    X, y = fg.features(b, "demand_system_mw", set()), b["demand_system_mw"]
    test = pd.Series(b.index >= b.index[24 * 14], index=b.index)
    p = fg.predict("persistence", X, y, ~test, test)
    assert p.iloc[0] == y.iloc[24 * 13]


def test_gbm_beats_persistence_on_weekly_pattern():
    b = _base(days=70)
    X, y = fg.features(b, "demand_system_mw", set()), b["demand_system_mw"]
    test = pd.Series(b.index >= b.index[24 * 56], index=b.index)
    mae = {m: fg.scores(y[test], fg.predict(m, X, y, ~test, test), False)["mae"]
           for m in ("persistence", "gbm")}
    assert mae["gbm"] < mae["persistence"]


def test_risk_quality_precision_recall():
    idx = pd.date_range("2024-01-01", periods=4, freq="h", tz=TZ)
    a = pd.DataFrame({"curtailment_risk_mw": [0, 10, 20, 0]}, index=idx)
    f = pd.DataFrame({"curtailment_risk_mw": [0, 5, 0, 7]}, index=idx)
    q = fg.risk_quality(a, f, pd.Series(True, index=idx))
    assert q["precision"] == 0.5 and q["recall"] == 0.5
    assert q["risk_mae_mw"] == pytest.approx((0 + 5 + 20 + 7) / 4)


def test_forecast_solar_zero_in_dark_hours():
    b = _base()
    p = pd.Series(30.0, index=b.index)
    fb = fg.forecast_base(b, {"solar_available_mw": p})
    assert (fb.loc[fb.index.hour == 2, "solar_available_mw"] == 0).all()
    assert (fb.loc[fb.index.hour == 12, "solar_available_mw"] == 30).all()


def test_holdout_table_lines_up_actual_forecast_and_persistence():
    idx = pd.date_range("2024-10-01", periods=3, freq="h", tz=TZ)
    actual = pd.Series([100.0, 110.0, 120.0], index=idx)
    preds = {"gbm": {"demand_system_mw": pd.Series([101.0, 109.0, 125.0], index=idx)},
             "persistence": {"demand_system_mw": pd.Series([90.0, np.nan, 130.0], index=idx)}}
    h = fg.holdout_table(actual, preds, "demand_system_mw", "gbm")
    assert list(h.columns) == ["target", "model", "actual_mw", "forecast_mw", "persistence_mw"]
    assert h["actual_mw"].tolist() == [100.0, 110.0, 120.0] and h["forecast_mw"].iloc[2] == 125.0
    assert np.isnan(h["persistence_mw"].iloc[1]) and (h["model"] == "gbm").all()


def _nwp_csv(tmp_path, day1=100.0, day2=50.0):
    """Open-Meteo-style analysis CSV: sun 06:00-18:00 local, constant day-1 and day-2 values."""
    start = pd.date_range("2023-12-31 18:00", "2025-01-01 00:00", freq="h", tz="UTC")
    lit = start.tz_convert(TZ).hour.isin(range(6, 18))
    df = pd.DataFrame({"timestamp_utc": start,
                       "shortwave_radiation_previous_day1": np.where(lit, day1, 0.0),
                       "shortwave_radiation_previous_day2": np.where(lit, day2, 0.0)})
    p = tmp_path / "nwp.csv"
    df.to_csv(p, index=False)
    return p


def test_run_is_public_only_if_published_before_midnight():
    issue = pd.DatetimeIndex([pd.Timestamp("2024-10-10", tz=TZ)] * 2)
    # 11:30 local = 06:00 UTC: day-1 run 06 UTC on the 9th, public 14:00 UTC < 18:30 UTC.
    # 17:30 local = 12:00 UTC: day-1 run 12 UTC on the 9th, public 20:00 UTC > 18:30 UTC.
    samples = pd.DatetimeIndex([pd.Timestamp("2024-10-10 11:30", tz=TZ), pd.Timestamp("2024-10-10 17:30", tz=TZ)])
    assert fg.public_by_issue(samples, 1, 8, issue).tolist() == [True, False]
    assert fg.public_by_issue(samples, 1, 5, issue).tolist() == [True, True]      # GFS-like delay
    assert fg.public_by_issue(samples, 2, 8, issue).tolist() == [True, True]


def test_nwp_local_hourly_uses_day2_when_day1_was_not_public(tmp_path):
    s = fg.nwp_local_hourly(_nwp_csv(tmp_path), 2024, delay_hours=8)
    day = s[s.index.normalize() == pd.Timestamp("2024-10-10", tz=TZ)]
    assert day.iloc[12] == 100.0                     # both halves from day-1 runs
    assert day.iloc[16] == 75.0                      # 16:30-17:30 half falls back to day 2
    assert day.iloc[17] == 50.0 and day.iloc[2] == 0.0
    forced = fg.nwp_local_hourly(_nwp_csv(tmp_path), 2024, delay_hours=8, lead_days=2)
    assert forced[forced.index.hour == 12].eq(50.0).all()


def test_nwp_local_hourly_falls_back_to_day2_when_day1_missing(tmp_path):
    p = _nwp_csv(tmp_path)
    df = pd.read_csv(p)
    df["shortwave_radiation_previous_day1"] = np.nan
    df.to_csv(p, index=False)
    s = fg.nwp_local_hourly(p, 2024, delay_hours=8)
    assert s[s.index.hour == 12].eq(50.0).all()


def test_nwp_feature_scales_yesterday_by_forecast_day_ratio():
    b = _base(days=4)
    nwp = pd.Series(np.where(b.index.hour.isin(range(6, 18)), 100.0, 0.0), index=b.index)
    nwp[b.index.normalize() == b.index[24 * 2].normalize()] *= 0.5      # day 3 forecast half as sunny
    f = fg.features(b, "solar_available_mw", set(), nwp)
    t = b.index[24 * 2 + 12]
    assert f.loc[t, "nwp_scaled_lag_24"] == pytest.approx(0.5 * b["solar_available_mw"].iloc[24 + 12])
    assert np.isnan(f["nwp_scaled_lag_24"].iloc[12])                      # no yesterday
    nwp.iloc[24 * 3 + 8] = np.nan                                         # a daylight hour unknown
    assert fg.features(b, "solar_available_mw", set(), nwp)["nwp_scaled_lag_24"].iloc[24 * 3:].isna().all()


def test_nwp_model_predicts_nan_without_weather_forecast():
    b = _base(days=30)
    nwp = pd.Series(np.where(b.index.hour.isin(range(6, 18)), 100.0, 0.0), index=b.index)
    nwp[b.index < b.index[24 * 20]] = np.nan
    X, y = fg.features(b, "solar_available_mw", set(), nwp), b["solar_available_mw"]
    test = pd.Series(b.index >= b.index[24 * 25], index=b.index)
    p = fg.predict("ridge_nwp", X, y, ~test, test)
    assert p.notna().all()
    early = pd.Series(b.index < b.index[24 * 10], index=b.index)          # no forecast at all
    assert fg.predict("ridge_nwp", X, y, ~early, early).isna().all()


def test_model_choice_ignores_the_test_months():
    b = _base(days=366)
    X, y = fg.features(b, "demand_system_mw", set()), b["demand_system_mw"]
    month = pd.Series(b.index.month, index=b.index)
    dark = fg.dark_hours(b)
    chosen, val = fg.select_model(X, y, month, ["persistence", "ridge"], dark, "demand_system_mw")
    y2 = y.where(month < 10, y * 5)                                       # wreck Oct-Dec
    chosen2, val2 = fg.select_model(fg.features(b.assign(demand_system_mw=y2), "demand_system_mw", set()),
                                    y2, month, ["persistence", "ridge"], dark, "demand_system_mw")
    assert chosen == chosen2 and val == val2


def test_common_scores_use_the_same_hours():
    idx = pd.date_range("2024-10-01", periods=3, freq="h", tz=TZ)
    y = pd.Series([100.0, 200.0, 100.0], index=idx)
    s = fg.common_scores(y, {"persistence": pd.Series([80.0, 240.0, np.nan], index=idx),
                             "gbm": pd.Series([110.0, 190.0, 100.0], index=idx)}, mape=False)
    assert s["gbm"]["n"] == s["persistence"]["n"] == 2
    assert s["gbm"]["mae"] == 10.0 and s["persistence"]["mae"] == 30.0
    assert s["gbm"]["skill_vs_persistence"] == pytest.approx(0.667, abs=1e-3)


def test_solar_postprocess_clips_and_darkens():
    b = _base(days=2)
    p = pd.Series(-5.0, index=b.index)
    out = fg.postprocess("solar_available_mw", p, fg.dark_hours(b))
    assert (out == 0).all()
    assert fg.postprocess("demand_system_mw", p, fg.dark_hours(b)).eq(-5.0).all()
