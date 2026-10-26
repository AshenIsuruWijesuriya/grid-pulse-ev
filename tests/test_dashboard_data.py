"""Tests for the dashboard data layer in scripts/dashboard_data.py."""

import io
import json
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


SETTINGS = dd.Settings(2024, 520.0, 0.75, 200, 0.8, {"curtailment": 1.0, "peak": 1.0, "delay": 0.01})
DAY = pd.Timestamp("2024-03-04", tz=TZ)


def test_visitor_session_rules():
    sessions = pd.DataFrame({"station_id": [0, 0, 1, 2, 2, 2]})
    v = dd.visitor_session(DAY, "08:00", "17:00", 25.0, 7.2, sessions, 2)
    row = v["row"].iloc[0]
    assert row["session_id"] == dd.VISITOR_ID and row["declares_departure"]
    assert row["station_id"] == 1                                   # least busy station
    assert row["departure_local"].value - row["arrival_local"].value == 9 * 3600 * 10**9
    over = dd.visitor_session(DAY, "08:00", "09:00", 25.0, 7.2, sessions, 2)
    assert over["capped"] and over["row"]["energy_request_kwh"].iloc[0] == pytest.approx(7.2)
    night = dd.visitor_session(DAY, "20:00", "07:00", 10.0, 3.6, sessions, 2)
    assert night["row"]["departure_local"].iloc[0] == DAY + pd.offsets.Day(1) + pd.offsets.Hour(7)
    with pytest.raises(ValueError, match="quarter hour"):
        dd.visitor_session(DAY, "08:10", "17:00", 10.0, 7.2, sessions, 2)
    with pytest.raises(ValueError, match="09:00"):
        dd.visitor_session(DAY, "20:00", "11:00", 10.0, 7.2, sessions, 2)


def test_my_charge_delivers_in_full_and_prefers_wasted_power():
    mc = dd.my_charge(DAY, _base(), _cfg(200), SETTINGS, None, "08:00", "17:00", 20.0, 7.2)
    assert mc["feasible"] and mc["risk_in_window"]
    for r in mc["per_strategy"].values():
        assert r["delivered_kwh"] == pytest.approx(20.0, abs=1e-3)
        assert mc["arrival"] < r["finish"] <= mc["departure"]
    smart, unc = mc["per_strategy"]["curtailment_aware"], mc["per_strategy"]["uncontrolled"]
    assert smart["clean_share"] > unc["clean_share"]          # moved into the wasted-power hours
    p = mc["power_kw"]
    outside = (p.index < mc["arrival"]) | (p.index >= mc["departure"])
    assert (p[outside].abs() < 1e-6).all().all()


def test_station_view_respects_the_connection_limit():
    d = dd.day_detail(DAY, _base(), _cfg(200), SETTINGS)
    assert d["feasible"]
    sid = dd.station_ids(d)[0]
    view = dd.station_view(d, sid)
    assert len(view["cars"]) == int((d["prob"].station == sid).sum())
    assert max(view["peak_kw"].values()) <= view["capacity_kw"] + 1e-6
    assert list(view["smart_kw"].columns) == list(view["cars"]["Car"])


def test_exports_have_the_documented_columns():
    base, cfg = _base(), _cfg(200)
    d = dd.day_detail(DAY, base, cfg, SETTINGS)
    out = dd.solve_one_day(DAY, base, cfg, SETTINGS, strategies=dd.DETAIL_STRATEGIES)
    hourly = pd.read_csv(io.StringIO(dd.export_hourly(out)))
    assert hourly.columns[0] == "hour_start_local" and len(hourly) == 30
    assert hourly["hour_start_local"].iloc[0] == "2024-03-04T00:00:00+05:30"
    sessions = pd.read_csv(io.StringIO(dd.export_sessions(d)))
    assert len(sessions) == 200 and {"delivered_kwh_curtailment_aware", "finish_local_uncontrolled"} <= set(sessions)
    sched = pd.read_csv(io.StringIO(dd.export_schedule(d)))
    assert list(sched.columns) == ["session_id", "station_id", "interval_start_local", "power_kw", "energy_kwh"]
    total = sched.groupby("session_id")["energy_kwh"].sum()
    req = sessions.set_index("session_id")["energy_request_kwh"]
    assert np.allclose(total.reindex(req.index, fill_value=0).to_numpy(), req.to_numpy(), atol=1e-2)
    summary = json.loads(dd.export_summary(DAY, SETTINGS, out, dd.verdict(DAY, out), cfg))
    assert summary["schema"].endswith("/v1") and summary["settings"]["nlmin_mw"] == 520.0


def test_long_loads_and_assumptions_are_tidy():
    idx = pd.date_range("2024-03-03", periods=3, freq="h")
    df = dd.long_loads(pd.DataFrame({"uncontrolled": [1.0, 2.0, 3.0]}, index=idx))
    assert list(df.columns) == ["hour", "strategy", "ev_mw", "label"] and len(df) == 3
    a = dd.assumptions(_cfg())
    assert a["Value"].map(type).eq(str).all() and a["Source"].str.len().gt(0).all()


def test_forecast_table():
    acc = pd.DataFrame({"target": ["demand_system_mw"] * 2 + ["solar_available_mw"] * 3,
                        "model": ["persistence", "gbm", "persistence", "gbm", "ridge_nwp"],
                        "mae": [100.0, 60.0, 20.0, 25.0, 16.0]})
    t = dd.forecast_table(acc, {"demand_system_mw": "gbm", "solar_available_mw": "ridge_nwp"})
    assert t["Forecast of"].tolist() == ["Demand", "Solar"]
    assert t["Chosen"].tolist() == ["Gradient boosting", "Ridge + weather forecast"]
    assert t["Better than tomorrow = today"].tolist() == pytest.approx([0.4, 0.2])
    assert np.isnan(t["Ridge + weather forecast"].iloc[0])          # demand has no weather model


def test_settings_carry_the_station_rule():
    cfg = _cfg()
    s = dd.Settings.central(cfg)
    assert s.station_rule == cfg["central"].get("station_rule", "arrival_first")
    t = dd.Settings(s.scenario_year, s.nlmin_mw, s.snsp_max, s.sessions_per_day,
                    s.declared_departure_fraction, s.weights, "declared_first")
    assert t.apply(cfg)["central"]["station_rule"] == "declared_first"


def test_evidence_facts_come_from_the_saved_tables():
    sens = pd.DataFrame({
        "dimension": ["NLmin (MW)"] * 2 + ["Declared departure share"] * 2 + ["Declared share, declared first rule"] * 2,
        "value": [240, 240, 0.5, 0.5, 0.5, 0.5],
        "strategy": ["uncontrolled", "curtailment_aware"] * 3,
        "absorbed_mwh": [1.0, 2.0, 1.0, 2.0, 1.0, 3.0], "mean_evening_peak_increase_mw": [5, 3, 5, 3, 5, 2],
        "days": [42, 42, 22, 22, 42, 42], "dropped_days": [0, 0, 20, 20, 0, 0],
        "undeclared_completion_rate": [1.0, 1.0, 1.0, 1.0, 0.9, 0.9],
        "curtailment_aware_not_outranked": [True, True, True, True, False, False],
        "constraint_violations": [0, 0, 0, 1, 0, 0]})
    assert dd.sensitivity_cases(sens) == (3, 2)
    t = dd.station_rule_table(sens)
    assert t["Rule"].tolist() == [dd.RULES["arrival_first"], dd.RULES["declared_first"]]
    assert t["Days not possible"].tolist() == [20, 0] and t["Others leave full"].tolist() == [1.0, 0.9]
    total, checked = dd.audit_violations({"sensitivity": sens, "weekly": pd.DataFrame()})
    assert (total, checked) == (1, 6)
    assert dd.station_rule_table(sens[sens["dimension"] != "Declared share, declared first rule"]).empty
