"""Tests for scripts/schedule_ev_charging.py on hand-built toy days."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_grid_scenario as bgs  # noqa: E402
import schedule_ev_charging as sch  # noqa: E402

TZ = "Asia/Colombo"
DAY = pd.Timestamp("2024-04-14", tz=TZ)


def _signal(nlmin=800.0, snsp=None):
    """Toy day: solar peaks at 12:00 but demand is high then; demand dips at 14:00,
    so the only curtailment-risk hour is 14:00. Evening peak at 19:00."""
    idx = pd.date_range(DAY, periods=48, freq="h")
    h = idx.hour
    demand = np.full(len(idx), 1500.0)
    demand[h == 12] = 1700.0
    demand[h == 14] = 1100.0
    demand[h == 19] = 2200.0
    solar = np.where((h >= 7) & (h <= 17), 300.0, 0.0)
    solar[h == 12] = 450.0
    base = pd.DataFrame({"demand_system_mw": demand, "solar_available_mw": solar,
                         "wind_mw": 0.0, "must_take_mw": 100.0}, index=idx)
    return bgs.scenario_signal(base, nlmin, snsp_max=snsp)


def _sessions(rows):
    out = []
    for i, (arr, dep, kwh, kw, station, declared) in enumerate(rows):
        out.append({"session_id": f"s{i}", "day": "2024-04-14",
                    "arrival_local": DAY + pd.offsets.Minute(int(arr * 60)),
                    "departure_local": DAY + pd.offsets.Minute(int(dep * 60)),
                    "energy_request_kwh": kwh, "max_power_kw": kw,
                    "station_id": station, "declares_departure": declared})
    return pd.DataFrame(out)


def _run(strategy, sessions, cap=1e6, signal=None, weights=None):
    signal = _signal() if signal is None else signal
    prob = sch.day_problem(DAY, sessions, cap)
    grid = sch.plan_grid(signal, prob.hours) if strategy != "uncontrolled" else None
    r = sch.run_strategy(prob, strategy, grid, weights or {"peak": 0.0, "delay": 0.0})
    return prob, r


def test_toy_signal_has_risk_only_at_14h():
    s = _signal()
    risky = s.index[s["curtailment_risk_mw"] > 0]
    assert list(risky.hour) == [14, 14]


def test_uncontrolled_shares_station_capacity_proportionally():
    S = _sessions([(9, 12, 20, 4.0, 0, True), (9, 12, 20, 4.0, 0, True)])
    prob, r = _run("uncontrolled", S, cap=5.0)
    assert r["P"][:, 36] == pytest.approx([2.5, 2.5])   # 09:00 slot
    assert sch.audit_schedule(prob, r["P"], False)["total"] == 0


def test_curtailment_aware_absorbs_more_than_solar_following():
    # 25 sessions, 1 MWh in total, plugged in 10:00-16:00; risk only at 14:00.
    S = _sessions([(10, 16, 40.0, 40.0, i, True) for i in range(25)])
    out = {}
    for strat in ("uncontrolled", "solar_following", "curtailment_aware"):
        prob, r = _run(strat, S)
        assert r["status"] in ("optimal", "simulated")
        assert sch.audit_schedule(prob, r["P"], strat != "uncontrolled")["total"] == 0
        load = sch.hourly_load_mw(prob, r["P"])
        sig = _signal()
        c0 = sig["curtailment_risk_mw"].sum()
        out[strat] = c0 - sch.curtailment_with_load(sig, load).sum()
    assert out["curtailment_aware"] == pytest.approx(1.0)   # all 1 MWh at 14:00
    assert out["solar_following"] == pytest.approx(0.0)     # charges at 12:00
    assert out["curtailment_aware"] > out["uncontrolled"]


def test_energy_is_a_hard_constraint_and_peak_avoided():
    S = _sessions([(17, 22, 10.0, 7.2, 0, True)])
    prob, r = _run("curtailment_aware", S, weights={"peak": 10.0, "delay": 0.0})
    assert r["P"].sum() * 0.25 == pytest.approx(10.0)
    assert hourly_at(prob, r["P"], 19) == pytest.approx(0.0)


def hourly_at(prob, P, hour):
    load = sch.hourly_load_mw(prob, P)
    return load[load.index.hour == hour].iloc[0]


def test_undeclared_session_charges_on_arrival():
    S = _sessions([(10, 16, 7.2, 7.2, 0, False)])
    prob, r = _run("curtailment_aware", S)
    assert r["P"][0, 40] == pytest.approx(7.2)     # 10:00 slot


def test_infeasible_station_capacity_is_reported_not_relaxed():
    S = _sessions([(10, 11, 7.0, 7.2, 0, True), (10, 11, 7.0, 7.2, 0, True)])
    _, r = _run("curtailment_aware", S, cap=7.2)
    assert r["status"] == "infeasible"


def test_audit_catches_violations():
    S = _sessions([(10, 11, 1.0, 4.0, 0, True)])
    prob = sch.day_problem(DAY, S, 3.0)
    P = np.zeros((1, prob.n_slots))
    P[0, 40] = 5.0                    # above rating, above station cap
    P[0, 0] = 1.0                     # while unplugged
    v = sch.audit_schedule(prob, P, True)
    assert v["above_charger_rating"] == 1
    assert v["station_capacity_exceeded"] == 1
    assert v["charging_while_unplugged"] == 1
    assert v["over_delivery"] == 1


def test_snsp_absorption_scales_with_limit():
    sig = _signal(nlmin=0.0, snsp=0.2)
    load = pd.Series(10.0, index=sig.index[:24])
    absorbed = (sig["curtailment_risk_mw"] - sch.curtailment_with_load(sig, load))
    noon = sig.index[12]
    assert absorbed[noon] == pytest.approx(0.2 * 10.0)
