"""Tests for the dashboard data layer in scripts/dashboard_data.py."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_grid_scenario as bgs  # noqa: E402
import dashboard_data as dd  # noqa: E402

TZ = "Asia/Colombo"


def _cfg(sessions=40):
    cfg = bgs.load_config(bgs.DEFAULT_CONFIG)
    cfg["central"]["sessions_per_day"] = sessions
    return cfg


def _base(days=6):
    """Complete synthetic 2024 hours with midday risk at NLmin 520 MW."""
    idx = pd.date_range("2024-03-03", periods=24 * days, freq="h", tz=TZ)
    solar = np.clip(np.sin((idx.hour - 6) / 12 * np.pi), 0, None) * 900.0
    return pd.DataFrame({"demand_system_mw": 1400.0, "solar_available_mw": solar, "wind_mw": 0.0,
                         "must_take_mw": 0.0, "hour_status": "complete",
                         "is_system_event_day": False}, index=idx)


def test_settings_apply_overrides_without_mutating():
    cfg = _cfg()
    s = dd.Settings(2030, 650.0, None, 3000, 1.0, {"peak": 4.0})
    out = s.apply(cfg)
    assert out["central"]["scenario_year"] == 2030 and out["central"]["snsp_max"] is None
    assert out["central"]["weights"]["peak"] == 4.0 and out["central"]["weights"]["curtailment"] == 1.0
    assert cfg["central"]["sessions_per_day"] == 40 and cfg["central"]["weights"]["peak"] == 1.0
    assert dd.Settings.central(cfg).nlmin_mw == cfg["central"]["nlmin_mw"]


def test_day_table_counts_risk():
    base = _base()
    cfg = dd.Settings(2024, 520.0, None, 40, 0.8).apply(_cfg())
    sig = dd.signal(base, cfg)
    days = sorted(set(base.index.normalize()))
    t = dd.day_table(sig, days)
    assert len(t) == len(days) and (t["risk_mwh"] > 0).all()
    assert t["risk_hours"].iloc[0] == int((sig["curtailment_risk_mw"].iloc[:24] > 0).sum())


def test_solve_one_day_matches_pool_and_is_audited():
    base, cfg = _base(), _cfg()
    s = dd.Settings(2024, 520.0, 0.75, 40, 0.8, {"curtailment": 1.0, "peak": 1.0, "delay": 0.01})
    pool = dd.session_pool(base, cfg, s)
    day = pd.Timestamp(sorted(pool["day"].unique())[0], tz=TZ)
    out = dd.solve_one_day(day, base, cfg, s, strategies=["uncontrolled", "curtailment_aware"], pool=pool)
    assert out["experiment_sessions"] and out["sessions"] == 40 and not out["dropped"]
    assert list(out["loads"].columns) == ["uncontrolled", "curtailment_aware"]
    assert len(out["window"]) == 30 and (out["metrics"]["constraint_violations"] == 0).all()
    m = out["metrics"]
    assert m.loc["curtailment_aware", "absorbed_mwh"] >= m.loc["uncontrolled", "absorbed_mwh"]
    shown = dd.metrics_for_display(m)
    assert "Absorbed (MWh)" in shown.columns and shown["Completion"].str.endswith("%").all()


def test_verdict_is_plain_and_matches_metrics():
    base, cfg = _base(), _cfg()
    s = dd.Settings(2024, 520.0, 0.75, 40, 0.8, {"curtailment": 1.0, "peak": 1.0, "delay": 0.01})
    day = pd.Timestamp("2024-03-04", tz=TZ)
    out = dd.solve_one_day(day, base, cfg, s, strategies=["uncontrolled", "solar_following",
                                                          "curtailment_aware"])
    v = dd.verdict(day, out)
    assert v["has_risk"] and v["risk_mwh"] > 0
    assert v["grid"].startswith("On Monday 4 March")
    pct = int(round(out["metrics"].loc["curtailment_aware", "evening_peak_reduction_pct_of_ev_increase"]))
    assert f"removes {pct}% of that" in v["peak"] and "-0%" not in v["peak"]
    assert "100% of cars leave fully charged" in v["drivers"]


def test_verdict_without_risk_says_nothing_is_wasted():
    base, cfg = _base(), _cfg()
    s = dd.Settings(2024, 0.0, None, 40, 0.8, {"curtailment": 1.0, "peak": 1.0, "delay": 0.01})
    day = pd.Timestamp("2024-03-04", tz=TZ)
    v = dd.verdict(day, dd.solve_one_day(day, base, cfg, s, strategies=["uncontrolled",
                                                                       "curtailment_aware"]))
    assert not v["has_risk"] and "no clean power is wasted" in v["grid"] and v["clean"] == ""


def test_driver_examples_are_complete_and_inside_plug_in_window():
    base, cfg = _base(), _cfg(sessions=400)
    s = dd.Settings(2024, 520.0, 0.75, 400, 1.0, {"curtailment": 1.0, "peak": 1.0, "delay": 0.01})
    ex = dd.driver_examples(pd.Timestamp("2024-03-04", tz=TZ), base, cfg, s)
    assert ex, "expected at least one example driver"
    for e in ex:
        assert e["group"] in {"Morning commuter", "Midday shopper", "Evening visitor"}
        assert 0 < e["group_moved"] <= e["group_size"]
        for strat, kwh in e["delivered_kwh"].items():
            assert kwh == pytest.approx(e["energy_kwh"], abs=1e-3)        # every car leaves full
            assert e["arrival"] < e["finish"][strat] <= e["departure"]
        p = e["power_kw"]
        outside = (p.index < e["arrival"]) | (p.index >= e["departure"])
        assert (p[outside].abs() < 1e-6).all().all()                     # never charges unplugged


def test_long_loads_and_assumptions_are_tidy():
    idx = pd.date_range("2024-03-03", periods=3, freq="h")
    df = dd.long_loads(pd.DataFrame({"uncontrolled": [1.0, 2.0, 3.0]}, index=idx))
    assert list(df.columns) == ["hour", "strategy", "ev_mw", "label"] and len(df) == 3
    a = dd.assumptions(_cfg())
    assert a["Value"].map(type).eq(str).all() and a["Source"].str.len().gt(0).all()
