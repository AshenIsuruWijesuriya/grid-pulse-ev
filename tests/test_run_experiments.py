"""Tests for the experiment harness helpers in scripts/run_experiments.py."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_experiments as rx  # noqa: E402

TZ = "Asia/Colombo"


def test_sample_days_one_per_week_rotating_weekday():
    days = list(pd.date_range("2024-01-01", "2024-03-31", freq="D", tz=TZ))
    picked = rx.sample_days(days)
    weeks = [d.isocalendar()[:2] for d in picked]
    assert len(weeks) == len(set(weeks))
    assert {d.dayofweek for d in picked} == set(range(7))


def test_persistence_plan_uses_previous_day():
    idx = pd.date_range("2024-01-01", periods=72, freq="h", tz=TZ)
    sig = pd.DataFrame({c: np.arange(72.0) for c in rx.PLAN_COLUMNS}, index=idx)
    sig["nlmin_mw"] = 700.0
    plan = rx.persistence_plan(sig)
    assert plan["net_load_mw"].iloc[30] == sig["net_load_mw"].iloc[6]
    assert plan["nlmin_mw"].iloc[30] == 700.0
    assert plan["net_load_mw"].iloc[:24].isna().all()


def test_schedulable_days_need_neighbours_complete():
    idx = pd.date_range("2024-01-01", periods=24 * 5, freq="h", tz=TZ)
    base = pd.DataFrame({"hour_status": "complete", "is_system_event_day": False}, index=idx)
    base.loc[idx[24 * 3 + 2], "hour_status"] = "missing"      # 4 Jan 02:00
    got = [d.day for d in rx.schedulable_days(base)]
    # 1 Jan has no previous day; 3 Jan's next morning is incomplete; 4 Jan is not eligible;
    # 5 Jan has no previous eligible day and no next morning.
    assert got == [2]


def test_ranking_orders_absorption_and_peak():
    m = {"uncontrolled": {"absorbed_mwh": 1, "mean_evening_peak_mw": 30},
         "solar_following": {"absorbed_mwh": 2, "mean_evening_peak_mw": 20},
         "curtailment_aware": {"absorbed_mwh": 3, "mean_evening_peak_mw": 10},
         "curtailment_aware_persistence": {"absorbed_mwh": 9, "mean_evening_peak_mw": 1}}
    r = rx.ranking(m)
    assert r["by_absorption"] == [["curtailment_aware"], ["solar_following"], ["uncontrolled"]]
    assert r["by_evening_peak"] == [["curtailment_aware"], ["solar_following"], ["uncontrolled"]]


def test_ranking_groups_ties():
    m = {"uncontrolled": {"absorbed_mwh": 265.3, "mean_evening_peak_mw": 30.0},
         "solar_following": {"absorbed_mwh": 292.85, "mean_evening_peak_mw": 20.0},
         "curtailment_aware": {"absorbed_mwh": 292.87, "mean_evening_peak_mw": 19.98}}
    r = rx.ranking(m)
    assert r["by_absorption"] == [["curtailment_aware", "solar_following"], ["uncontrolled"]]
    assert r["by_evening_peak"] == [["curtailment_aware", "solar_following"], ["uncontrolled"]]
    assert rx.ranking(m, tol_mw=0.01)["by_evening_peak"][0] == ["curtailment_aware"]


def test_run_case_same_result_in_worker_pool():
    from concurrent.futures import ThreadPoolExecutor
    import json
    import build_grid_scenario as bgs
    import ev_sessions as evs
    day = pd.Timestamp("2024-03-10", tz=TZ)
    idx = pd.date_range(day - pd.offsets.Day(2), periods=24 * 5, freq="h")
    solar = np.clip(np.sin((idx.hour - 6) / 12 * np.pi), 0, None) * 900.0
    base = pd.DataFrame({"demand_system_mw": 1500.0, "solar_available_mw": solar,
                         "wind_mw": 0.0, "must_take_mw": 0.0}, index=idx)
    sig = bgs.scenario_signal(base, nlmin_mw=800.0)
    cfg = bgs.load_config(bgs.DEFAULT_CONFIG)
    cfg["central"]["sessions_per_day"] = 40
    sess = evs.build_session_set([day], cfg, 1)
    args = ([day], sess, sig, cfg, ["uncontrolled", "curtailment_aware"],
            cfg["central"]["weights"], 50.0)
    serial = rx.run_case(*args)
    with ThreadPoolExecutor(2) as pool:
        pooled = rx.run_case(*args, pool=pool)
    def results(r):                     # solve times vary run to run; compare the results only
        return {s: {k: v for k, v in m.items() if not k.startswith("solver_s")} for s, m in r["metrics"].items()}
    assert json.dumps(results(serial), default=str) == json.dumps(results(pooled), default=str)
    assert serial["metrics"]["curtailment_aware"]["constraint_violations"] == 0


def test_leads_means_never_outranked():
    assert rx.leads({"by_absorption": [["curtailment_aware", "solar_following"], ["uncontrolled"]],
                     "by_evening_peak": [["curtailment_aware"], ["solar_following"]]})
    assert not rx.leads({"by_absorption": [["solar_following"], ["curtailment_aware"]],
                         "by_evening_peak": [["curtailment_aware"]]})


def test_absorbed_by_day_matches_load_in_risk_hours():
    day = pd.Timestamp("2024-03-10", tz=TZ)
    idx = pd.date_range(day, periods=24, freq="h")
    sig = pd.DataFrame({"nlmin_mw": 500.0, "net_load_mw": 600.0, "vre_mw": 100.0,
                        "demand_system_mw": 1000.0, "snsp_max": np.nan}, index=idx)
    sig.loc[idx[12], "net_load_mw"] = 450.0                     # 50 MW of risk at 12:00
    sig["curtailment_risk_mw"] = (sig["nlmin_mw"] - sig["net_load_mw"]).clip(lower=0)
    load = pd.Series(0.0, index=idx)
    load[idx[12]] = 20.0
    out = rx.absorbed_by_day({"days": [day], "loads": {"a": load}}, sig)
    assert out.loc["2024-03-10", "risk_mwh"] == 50.0
    assert out.loc["2024-03-10", "a"] == 20.0


def test_analog_days_are_nearest_dates_in_other_months():
    days = pd.date_range("2024-01-01", "2024-03-31", freq="D", tz=TZ)
    pick = rx.analog_days(days, days, 3)
    feb1 = days.get_loc(pd.Timestamp("2024-02-01", tz=TZ))
    chosen = days[pick[feb1]]
    assert (chosen.month != 2).all()
    assert list(chosen.strftime("%m-%d")) == ["01-31", "01-30", "01-29"]
    jan1 = days[pick[0]]                                   # Jan 1: nearest other-month days are in Feb
    assert (jan1.month == 2).all() and jan1[0].day == 1


def test_forecast_scenarios_add_analog_errors_and_skip_unusable_days(monkeypatch):
    idx = pd.date_range("2024-01-01", "2024-03-31 23:00", freq="h", tz=TZ)
    rng = np.random.default_rng(1)
    base = pd.DataFrame({t: 1000 + 100 * rng.random(len(idx)) for t in rx.FORECAST_TARGETS}, index=idx)
    base["hour_status"] = "complete"
    base["is_system_event_day"] = False
    day_no = (idx.normalize() - idx[0]).days.to_numpy()
    fc = base[rx.FORECAST_TARGETS].sub(day_no.astype(float), axis=0)          # error of day i is +i MW
    base.loc[idx.normalize() == pd.Timestamp("2024-01-31", tz=TZ), "hour_status"] = "partial"
    monkeypatch.setattr(rx, "load_forecast", lambda b, cfg: fc)
    monkeypatch.setattr(rx.bgs, "scenario_kwargs", lambda cfg, **o: {"nlmin_mw": 0.0})
    sc = rx.forecast_scenarios(base, {"central": {"forecast_scenarios": 2}})
    assert len(sc) == 2
    t = pd.Timestamp("2024-02-01 12:00", tz=TZ)
    # Feb 1's nearest other-month days are Jan 31 (unusable, skipped), Jan 30, Jan 29.
    assert sc[0].loc[t, "demand_system_mw"] == pytest.approx(fc.loc[t, "demand_system_mw"] + 29)
    assert sc[1].loc[t, "demand_system_mw"] == pytest.approx(fc.loc[t, "demand_system_mw"] + 28)
    assert sc[0].loc[t, "wind_mw"] == pytest.approx(fc.loc[t, "wind_mw"] + 29)  # same analog for every target


def test_past_only_analogs_never_look_ahead():
    days = pd.date_range("2024-01-01", "2024-03-31", freq="D", tz=TZ)
    pick = rx.analog_days(days, days, 3, past_only=True)
    mar15 = days.get_loc(pd.Timestamp("2024-03-15", tz=TZ))
    assert (days[pick[mar15]] < pd.Timestamp("2024-03-01", tz=TZ)).all()
    assert (pick[days.month == 1] == -1).all()                # January has no earlier month
