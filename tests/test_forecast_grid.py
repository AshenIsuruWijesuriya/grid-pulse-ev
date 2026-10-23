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
