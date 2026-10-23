"""Data layer for the prototype app (WBS 5.11, FR13).

Everything the app shows is computed here with the same functions the
experiments use (scenario_signal, run_case, evaluate), so a number on screen
can never disagree with results/experiments. The pages in app/ only draw.
It also holds the plain-language layer: friendly strategy names, ready-made
example days, the written verdict for a day, and example drivers.

Settings a user can change: scenario year, NLmin, SNSP limit, sessions per
day, declared-departure share and objective weights. Anything not changed
comes from config/scenarios.json.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

import build_grid_scenario as bgs
import ev_sessions as evs
import run_experiments as rx
import schedule_ev_charging as sch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TZ = "Asia/Colombo"
DAY_STRATEGIES = ["uncontrolled", "solar_following", "curtailment_aware", "curtailment_aware_ml"]
METRIC_COLUMNS = {
    "absorbed_mwh": "Absorbed (MWh)",
    "absorbed_gain_vs_uncontrolled_mwh": "Gain vs uncontrolled (MWh)",
    "mean_evening_peak_increase_mw": "Evening peak increase (MW)",
    "evening_peak_reduction_pct_of_ev_increase": "Evening peak cut (%)",
    "completion_rate": "Completion",
    "mean_delay_min": "Mean delay (min)",
    "p95_delay_min": "p95 delay (min)",
    "constraint_violations": "Violations",
    "solver_s_total": "Solve time (s)",
}
# Plain names for people who have not read the method.
FRIENDLY = {
    "uncontrolled": "Charge on arrival",
    "solar_following": "Charge when sunny",
    "curtailment_aware": "Smart charging",
    "curtailment_aware_persistence": "Smart charging, yesterday as forecast",
    "curtailment_aware_ml": "Smart charging, tomorrow's forecast",
}
EXPLAIN = {
    "uncontrolled": "What happens today: every car starts charging as soon as it plugs in.",
    "solar_following": "Today's advice: charge in the sunniest hours.",
    "curtailment_aware": "This research: charge when clean power would otherwise be switched off, "
                         "and stay away from the evening peak, while every car still leaves full.",
    "curtailment_aware_ml": "Smart charging that only knows a machine-learning forecast of tomorrow, "
                            "as it would in real life.",
}
# Ready-made days (2024 record). Sunday 3 Nov carries the most risk in the central
# year; 9 June is the monsoon day where wind moves the risk past the solar peak;
# Tuesday 5 Nov is an ordinary weekday in the same week with no risk.
PRESETS = {
    "Sunny Sunday": ("2024-11-03", "Low demand and strong sun: the grid has more clean power than it can use."),
    "Monsoon wind day": ("2024-06-09", "Strong wind all day moves the surplus into the early afternoon, "
                                       "after the sunniest hour."),
    "Ordinary weekday": ("2024-11-05", "Normal demand: all clean power is used, so the question is only "
                                       "the evening peak."),
}


@dataclass(frozen=True)
class Settings:
    scenario_year: int
    nlmin_mw: float
    snsp_max: float | None
    sessions_per_day: int
    declared_departure_fraction: float
    weights: dict = field(default_factory=dict)

    @classmethod
    def central(cls, cfg: dict) -> "Settings":
        c = cfg["central"]
        return cls(c["scenario_year"], c["nlmin_mw"], c["snsp_max"], c["sessions_per_day"],
                   c["declared_departure_fraction"], dict(c["weights"]))

    def apply(self, cfg: dict) -> dict:
        """A copy of `cfg` with these settings as the central case."""
        out = copy.deepcopy(cfg)
        c = out["central"]
        c.update(scenario_year=self.scenario_year, nlmin_mw=self.nlmin_mw, snsp_max=self.snsp_max,
                 sessions_per_day=self.sessions_per_day,
                 declared_departure_fraction=self.declared_departure_fraction)
        c["weights"] = {**c["weights"], **self.weights}
        return out


def load_inputs(cfg: dict) -> tuple[pd.DataFrame, list[pd.Timestamp]]:
    """Boundary-corrected 2024 hourly grid and the schedulable days."""
    base = rx.load_base(cfg)
    return base, rx.schedulable_days(base)


def signal(base: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    return bgs.scenario_signal(base, **bgs.scenario_kwargs(cfg))


def day_table(sig: pd.DataFrame, days: list[pd.Timestamp]) -> pd.DataFrame:
    """One row per schedulable day: risk energy and hours, for the day picker."""
    risk = sig["curtailment_risk_mw"]
    by_day = risk.groupby(sig.index.normalize())
    energy, hours = by_day.sum(), (risk > 0).groupby(sig.index.normalize()).sum()
    rows = [{"day": d, "weekday": d.day_name()[:3], "risk_mwh": round(float(energy.get(d, 0.0)), 1),
             "risk_hours": int(hours.get(d, 0))} for d in days]
    return pd.DataFrame(rows)


def session_pool(base: pd.DataFrame, cfg: dict, settings: Settings) -> pd.DataFrame:
    """Sessions drawn over the experiment's day set, as run_experiments.py draws them.

    With the central session settings, a day from that set gets exactly the
    sessions it had in the saved experiment. The day set itself always comes
    from the configured central scenario, not from the dashboard's grid settings.
    """
    days = rx.experiment_days(base, cfg)["all"]
    return evs.build_session_set(days, settings.apply(cfg), int(cfg["sessions"]["seed"]))


def solve_one_day(day: pd.Timestamp, base: pd.DataFrame, cfg: dict, settings: Settings,
                  strategies: list[str] | None = None, pool: pd.DataFrame | None = None) -> dict:
    """Run every strategy on one day under `settings`.

    `pool` is a session_pool(); days outside it get their own draw. Returns the
    hourly grid window, hourly EV load per strategy (MW), the metrics table,
    and the day's status (dropped strategies are reported).
    """
    run_cfg = settings.apply(cfg)
    c = run_cfg["central"]
    sig = signal(base, run_cfg)
    plans = {}
    strategies = list(strategies or DAY_STRATEGIES)
    if "curtailment_aware_ml" in strategies:
        ml = rx.ml_forecast_plan(base, run_cfg)
        if ml is None:
            strategies.remove("curtailment_aware_ml")
        else:
            plans["_ml"] = ml
    sessions, in_pool = _day_sessions(day, run_cfg, pool)
    res = rx.run_case([day], sessions, sig, run_cfg, strategies, c["weights"],
                      c["station_capacity_kw"], plans)
    hours = pd.date_range(day, periods=30, freq="h")
    window = sig.reindex(hours)[["demand_system_mw", "solar_mw", "wind_mw", "vre_mw", "net_load_mw",
                                 "nlmin_mw", "curtailment_risk_mw"]]
    loads = pd.DataFrame({s: res["loads"][s].reindex(hours, fill_value=0.0) for s in res["loads"]},
                         index=hours)
    metrics = pd.DataFrame(res["metrics"]).T.reindex(columns=list(METRIC_COLUMNS))
    return {"window": window, "loads": loads, "metrics": metrics,
            "dropped": res["dropped_days"], "sessions": len(sessions), "experiment_sessions": in_pool}


def _day_sessions(day: pd.Timestamp, run_cfg: dict, pool: pd.DataFrame | None) -> tuple[pd.DataFrame, bool]:
    """The day's sessions from `pool` when it holds them, else a draw for that day only."""
    key = day.strftime("%Y-%m-%d")
    if pool is not None and (pool["day"] == key).any():
        return pool[pool["day"] == key], True
    return evs.build_session_set([day], run_cfg, int(run_cfg["sessions"]["seed"])), False


def verdict(day: pd.Timestamp, out: dict) -> dict:
    """The day's result in plain words, from a solve_one_day() result."""
    w = out["window"].iloc[:24]
    risk = w["curtailment_risk_mw"]
    m = out["metrics"]

    def get(s, col):
        return float(m.loc[s, col]) if s in m.index else float("nan")

    def pct(s):                     # whole percent, never "-0"
        return int(round(get(s, "evening_peak_reduction_pct_of_ev_increase"))) + 0

    when = f"{day:%A} {day.day} {day:%B}"

    risk_mwh = float(risk.sum())
    hours = risk.index[risk > 0]
    unc, smart, sunny = "uncontrolled", "curtailment_aware", "solar_following"
    if len(hours):
        grid = (f"On {when}, about {risk_mwh:,.0f} MWh of clean power would have to be "
                f"switched off between {hours[0]:%H:%M} and {hours[-1] + pd.offsets.Hour(1):%H:%M}, "
                "because the big power stations cannot turn down any further.")
        clean = (f"Smart charging puts {get(smart, 'absorbed_mwh') - get(unc, 'absorbed_mwh'):,.1f} MWh "
                 "more of that clean power into cars than charging on arrival")
        if sunny in m.index:
            more = get(smart, "absorbed_mwh") - get(sunny, "absorbed_mwh")
            clean += (f", and {more:,.1f} MWh more than charging when sunny, because here the surplus does "
                      "not sit on the sunniest hours." if more >= 0.5 else
                      ". Charging when sunny does about as well here, because the surplus sits on the "
                      "sunniest hours.")
        else:
            clean += "."
    else:
        grid = (f"On {when}, the grid can use all of its solar and wind power, so no clean "
                "power is wasted. Smart charging still matters for the evening peak.")
        clean = ""
    peak = (f"If every car charges on arrival, EVs add {get(unc, 'mean_evening_peak_increase_mw'):,.1f} MW "
            f"to the evening peak. Smart charging removes "
            f"{pct(smart)}% of that")
    peak += (f"; charging when sunny removes {pct(sunny)}%."
             if sunny in m.index else ".")
    drivers = (f"Drivers wait {get(smart, 'mean_delay_min'):.0f} minutes longer on average to finish "
               f"charging, and {get(smart, 'completion_rate') * 100:.0f}% of cars leave fully charged.")
    return {"has_risk": bool(len(hours)), "risk_mwh": risk_mwh, "grid": grid, "clean": clean,
            "peak": peak, "drivers": drivers}


DETAIL_STRATEGIES = ["uncontrolled", "solar_following", "curtailment_aware"]
VISITOR_ID = "YOU"


def day_detail(day: pd.Timestamp, base: pd.DataFrame, cfg: dict, settings: Settings,
               pool: pd.DataFrame | None = None, extra: pd.DataFrame | None = None) -> dict:
    """Solve one day keeping every session's 15-minute schedule (sessions x slots, kW).

    `extra` adds sessions (a visitor's own trip) before solving. `feasible` is
    False when any LP is infeasible; then `P` is empty, never a relaxed plan.
    """
    run_cfg = settings.apply(cfg)
    c = run_cfg["central"]
    sig = signal(base, run_cfg)
    sessions, in_pool = _day_sessions(day, run_cfg, pool)
    if extra is not None:
        sessions = pd.concat([sessions, extra], ignore_index=True)
    prob = sch.day_problem(day, sessions, c["station_capacity_kw"])
    grid_hours = sig.reindex(prob.hours)
    risk_slot = (grid_hours["curtailment_risk_mw"].fillna(0.0).to_numpy() > 0)[prob.slot_hour]
    # The day's own peak window (net load within peak_band_fraction of its maximum),
    # not "the evening": charging at 22:00 does not raise the peak.
    peak_hour = grid_hours["is_peak_window"].fillna(False).astype(bool).to_numpy()
    peak_slot = peak_hour[prob.slot_hour] & np.asarray(prob.slots.normalize() == day)
    P, status = {}, {}
    for s in DETAIL_STRATEGIES:
        grid = sch.plan_grid(sig, prob.hours) if s != "uncontrolled" else None
        r = sch.run_strategy(prob, s, grid, c["weights"])
        status[s] = r["status"]
        if r["status"] not in ("optimal", "simulated"):
            return {"prob": prob, "P": {}, "status": status, "feasible": False, "settings": settings,
                    "risk_slot": risk_slot, "peak_slot": peak_slot, "grid": grid_hours,
                    "experiment_sessions": in_pool}
        P[s] = r["P"]
    return {"prob": prob, "P": P, "status": status, "feasible": True, "settings": settings,
            "risk_slot": risk_slot, "peak_slot": peak_slot, "grid": grid_hours,
            "experiment_sessions": in_pool}


def _finish(prob, row: np.ndarray) -> pd.Timestamp:
    on = np.flatnonzero(row > 1e-3)
    return prob.slots[on.max()] + pd.offsets.Minute(15) if len(on) else prob.slots[0]


def driver_examples(day: pd.Timestamp, base: pd.DataFrame, cfg: dict, settings: Settings,
                    pool: pd.DataFrame | None = None, detail: dict | None = None) -> list[dict]:
    """A typical driver from three groups, with their charging under each strategy.

    For each group the driver shown is the median by how far smart charging
    moves their charging time, among drivers it moves at all: typical, not the
    most extreme. Returns [] when the day is infeasible.
    """
    detail = detail or day_detail(day, base, cfg, settings, pool)
    if not detail["feasible"]:
        return []
    prob, P, shown = detail["prob"], detail["P"], DETAIL_STRATEGIES
    t = np.arange(prob.n_slots)
    centre = {s: (P[s] * t).sum(axis=1) / np.maximum(P[s].sum(axis=1), 1e-9) for s in shown}
    shift = np.abs(centre["curtailment_aware"] - centre["uncontrolled"]) / 4      # hours
    arr_h, dwell_h = prob.arr / 4, (prob.dep - prob.arr) / 4
    groups = [("Morning commuter", "Arrives in the morning and stays most of the day",
               (arr_h >= 7) & (arr_h < 9.5) & (dwell_h >= 6)),
              ("Midday shopper", "Stops for a few hours around midday",
               (arr_h >= 10.5) & (arr_h < 13) & (dwell_h <= 3.5)),
              ("Evening visitor", "Arrives in the late afternoon or evening",
               (arr_h >= 16.5) & (dwell_h >= 3))]
    out = []
    for name, blurb, mask in groups:
        idx = np.flatnonzero(mask & prob.declared & (shift > 0.25))
        if not len(idx):
            continue
        i = idx[np.argsort(shift[idx])[len(idx) // 2]]
        power = pd.DataFrame({s: P[s][i] for s in shown}, index=prob.slots)
        finish = {s: _finish(prob, P[s][i]) for s in shown}
        out.append({"group": name, "blurb": blurb, "session_id": prob.sessions["session_id"].iloc[i],
                    "arrival": prob.slots[prob.arr[i]],
                    "departure": prob.slots[0] + pd.offsets.Minute(15 * int(prob.dep[i])),
                    "energy_kwh": float(prob.energy[i]), "charger_kw": float(prob.pmax[i]),
                    "power_kw": power, "finish": finish,
                    "delivered_kwh": {s: float(P[s][i].sum() * sch.STEP_HOURS) for s in shown},
                    "group_size": int(mask.sum()), "group_moved": int(len(idx))})
    return out


def visitor_session(day: pd.Timestamp, arrival: str, departure: str, energy_kwh: float,
                    charger_kw: float, sessions: pd.DataFrame, sessions_per_station: int) -> dict:
    """A visitor's own trip as one session row ("HH:MM" times; leaving earlier means next day).

    The car joins the least busy station that day, declares its departure, and
    its request is capped at what the charger can deliver in the time plugged in.
    """
    def at(hhmm: str) -> pd.Timestamp:
        h, m = (int(x) for x in hhmm.split(":"))
        if m % 15:
            raise ValueError("times must be on the quarter hour")
        return day + pd.offsets.Hour(h) + pd.offsets.Minute(m)

    arr, dep = at(arrival), at(departure)
    if dep <= arr:
        dep += pd.offsets.Day(1)
    latest = day + pd.offsets.Day(1) + pd.offsets.Hour(9)
    if dep > latest:
        raise ValueError("the car must leave by 09:00 the next morning")
    slots = int((dep.value - arr.value) // (15 * 60 * 10**9))
    deliverable = charger_kw * slots * sch.STEP_HOURS
    n_stations = max(1, int(np.ceil(len(sessions) / sessions_per_station)))
    busy = sessions["station_id"].value_counts().reindex(range(n_stations), fill_value=0)
    row = pd.DataFrame({
        "session_id": [VISITOR_ID], "day": [day.strftime("%Y-%m-%d")], "arrival_local": [arr],
        "departure_local": [dep], "energy_request_kwh": [round(min(energy_kwh, deliverable), 4)],
        "max_power_kw": [float(charger_kw)], "energy_capped": [energy_kwh > deliverable],
        "data_origin": ["visitor"], "station_id": [int(busy.idxmin())], "declares_departure": [True]})
    return {"row": row, "capped": bool(energy_kwh > deliverable), "deliverable_kwh": deliverable}


def my_charge(day: pd.Timestamp, base: pd.DataFrame, cfg: dict, settings: Settings,
              pool: pd.DataFrame | None, arrival: str, departure: str, energy_kwh: float,
              charger_kw: float) -> dict:
    """Plan the day with the visitor's car added, and describe that car's charging."""
    run_cfg = settings.apply(cfg)
    sessions, _ = _day_sessions(day, run_cfg, pool)
    v = visitor_session(day, arrival, departure, energy_kwh, charger_kw, sessions,
                        int(run_cfg["central"]["sessions_per_station_per_day"]))
    d = day_detail(day, base, cfg, settings, pool, extra=v["row"])
    out = {"feasible": d["feasible"], "capped": v["capped"], "station_id": int(v["row"]["station_id"][0]),
           "deliverable_kwh": v["deliverable_kwh"], "grid": d["grid"]}
    if not d["feasible"]:
        return out
    prob = d["prob"]
    i = int(np.flatnonzero(prob.sessions["session_id"].to_numpy() == VISITOR_ID)[0])
    need = int(np.ceil(prob.energy[i] / (prob.pmax[i] * sch.STEP_HOURS) - 1e-9))
    earliest = prob.slots[prob.arr[i]] + pd.offsets.Minute(15 * need)
    per = {}
    for s in DETAIL_STRATEGIES:
        row = d["P"][s][i]
        kwh = row * sch.STEP_HOURS
        total = float(kwh.sum())
        fin = _finish(prob, row)
        per[s] = {"finish": fin, "delivered_kwh": total,
                  "clean_kwh": float(kwh[d["risk_slot"]].sum()),
                  "clean_share": float(kwh[d["risk_slot"]].sum() / total) if total else 0.0,
                  "peak_kwh": float(kwh[d["peak_slot"]].sum()),
                  "extra_wait_min": max(0.0, (fin.value - earliest.value) / 60e9)}
    out.update(arrival=prob.slots[prob.arr[i]],
               departure=prob.slots[0] + pd.offsets.Minute(15 * int(prob.dep[i])),
               energy_kwh=float(prob.energy[i]), charger_kw=float(prob.pmax[i]),
               power_kw=pd.DataFrame({s: d["P"][s][i] for s in DETAIL_STRATEGIES}, index=prob.slots),
               per_strategy=per, risk_in_window=bool(d["risk_slot"][prob.arr[i]:prob.dep[i]].any()))
    return out


def station_ids(detail: dict) -> list[int]:
    """Stations that day, busiest first (most sessions, then lowest id)."""
    counts = detail["prob"].sessions["station_id"].value_counts()
    return sorted(counts.index.astype(int), key=lambda k: (-counts[k], k))


def station_view(detail: dict, station_id: int) -> dict:
    """One station's cars and its total power under each strategy (15-minute kW)."""
    prob, P = detail["prob"], detail["P"]
    idx = np.flatnonzero(prob.station == station_id)
    order = idx[np.argsort(prob.arr[idx], kind="stable")]
    cars = pd.DataFrame({
        "Car": [f"Car {k + 1}" for k in range(len(order))],
        "Plugs in": [prob.slots[prob.arr[i]] for i in order],
        "Leaves": [prob.slots[0] + pd.offsets.Minute(15 * int(prob.dep[i])) for i in order],
        "Energy (kWh)": prob.energy[order],
        "Charger (kW)": prob.pmax[order],
        "Said when leaving": prob.declared[order],
        **{f"Full by: {FRIENDLY[s]}": [_finish(prob, P[s][i]) for i in order] for s in DETAIL_STRATEGIES},
    })
    totals = pd.DataFrame({s: P[s][order].sum(axis=0) for s in DETAIL_STRATEGIES}, index=prob.slots)
    smart = pd.DataFrame({cars["Car"].iloc[k]: P["curtailment_aware"][i] for k, i in enumerate(order)},
                         index=prob.slots)
    return {"cars": cars, "totals_kw": totals, "smart_kw": smart, "capacity_kw": prob.station_capacity_kw,
            "peak_kw": {s: float(totals[s].max()) for s in DETAIL_STRATEGIES}}


# --------------------------------------------------------------------------- #
# Exports (docs/export_schema.md): interval-start times, Asia/Colombo, ISO 8601
# --------------------------------------------------------------------------- #

def _iso(ts) -> pd.Series:
    return pd.Series(pd.DatetimeIndex(ts).strftime("%Y-%m-%dT%H:%M:%S%z")).str.replace(
        r"(\+\d{2})(\d{2})$", r"\1:\2", regex=True)


def export_hourly(out: dict) -> str:
    """Grid signal and EV load per strategy for each hour of the day window (CSV)."""
    w, loads = out["window"], out["loads"]
    df = pd.DataFrame({"hour_start_local": _iso(w.index).to_numpy(),
                       "demand_system_mw": w["demand_system_mw"].to_numpy(),
                       "solar_available_mw": w["solar_mw"].to_numpy(), "wind_mw": w["wind_mw"].to_numpy(),
                       "net_load_mw": w["net_load_mw"].to_numpy(), "nlmin_mw": w["nlmin_mw"].to_numpy(),
                       "curtailment_risk_mw": w["curtailment_risk_mw"].to_numpy()})
    for s in loads.columns:
        df[f"ev_load_mw_{s}"] = loads[s].reindex(w.index, fill_value=0.0).to_numpy()
    return df.round(4).to_csv(index=False)


def export_sessions(detail: dict) -> str:
    """One row per charging session with its outcome under each strategy (CSV)."""
    prob, P = detail["prob"], detail["P"]
    s0 = prob.sessions
    df = pd.DataFrame({"session_id": s0["session_id"].to_numpy(), "station_id": prob.station,
                       "arrival_local": _iso(s0["arrival_local"]).to_numpy(),
                       "departure_local": _iso(s0["departure_local"]).to_numpy(),
                       "energy_request_kwh": prob.energy, "max_power_kw": prob.pmax,
                       "declares_departure": prob.declared})
    for s in DETAIL_STRATEGIES:
        o = sch.session_outcomes(prob, P[s])
        df[f"delivered_kwh_{s}"] = o["delivered_kwh"].to_numpy()
        df[f"finish_local_{s}"] = _iso([_finish(prob, P[s][i]) for i in range(len(df))]).to_numpy()
        df[f"delay_min_{s}"] = o["delay_min"].to_numpy()
    return df.round(4).to_csv(index=False)


def export_schedule(detail: dict, strategy: str = "curtailment_aware") -> str:
    """Every non-zero 15-minute charging instruction of one strategy (long CSV)."""
    prob, P = detail["prob"], detail["P"][strategy]
    i, t = np.nonzero(P > 1e-6)
    df = pd.DataFrame({"session_id": prob.sessions["session_id"].to_numpy()[i], "station_id": prob.station[i],
                       "interval_start_local": _iso(prob.slots[t]).to_numpy(),
                       "power_kw": P[i, t], "energy_kwh": P[i, t] * sch.STEP_HOURS})
    return df.round(4).to_csv(index=False)


def export_summary(day: pd.Timestamp, settings: Settings, out: dict, verdict_: dict, cfg: dict) -> str:
    """The day's settings, verdict and metrics (JSON), so every file can be traced."""
    m = out["metrics"].astype(float).round(4)
    payload = {
        "schema": "grid-pulse-ev/component3/day-result/v1",
        "day": day.strftime("%Y-%m-%d"), "weather_year": 2024,
        "settings": {"scenario_year": settings.scenario_year, "nlmin_mw": settings.nlmin_mw,
                     "snsp_max": settings.snsp_max, "sessions_per_day": settings.sessions_per_day,
                     "declared_departure_fraction": settings.declared_departure_fraction,
                     "weights": settings.weights,
                     "station_capacity_kw": cfg["central"]["station_capacity_kw"]},
        "session_source": cfg["sessions"]["source"],
        "experiment_sessions": bool(out.get("experiment_sessions")),
        "verdict": {k: verdict_[k] for k in ("grid", "clean", "peak", "drivers")},
        "metrics": {s: {k: (None if pd.isna(v) else v) for k, v in row.items()} for s, row in m.iterrows()},
        "note": "Curtailment risk is inferred from published grid limits, not measured.",
    }
    return json.dumps(payload, indent=2, default=str)


def metrics_for_display(metrics: pd.DataFrame) -> pd.DataFrame:
    out = metrics.rename(columns=METRIC_COLUMNS).copy()
    if "Completion" in out:
        out["Completion"] = (out["Completion"].astype(float) * 100).round(2).astype(str) + "%"
    out.index = [rx.LABELS.get(i, i) for i in out.index]
    return out


def long_loads(loads: pd.DataFrame) -> pd.DataFrame:
    """Tidy (hour, strategy, MW) rows for plotting."""
    df = loads.rename_axis("hour").reset_index().melt("hour", var_name="strategy", value_name="ev_mw")
    df["label"] = df["strategy"].map(rx.LABELS)
    return df


def experiment_results() -> dict:
    """Saved full-experiment outputs, or empty entries when they have not been run."""
    out_dir = PROJECT_ROOT / "results/experiments"
    out = {}
    for key, name in (("risk_days", "so5_risk_days_comparison.csv"), ("weekly", "so5_comparison.csv"),
                      ("by_day", "so5_risk_days_by_day.csv"), ("tradeoff", "so5_tradeoff.csv"),
                      ("sensitivity", "so6_sensitivity.csv"), ("scaling", "scaling_benchmark.csv")):
        p = out_dir / name
        out[key] = pd.read_csv(p) if p.is_file() else pd.DataFrame()
    p = out_dir / "run_summary.json"
    out["summary"] = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    p = PROJECT_ROOT / "results/grid_scenario/grid_base_2024_diagnostics.json"
    out["scenario_years"] = (pd.DataFrame(json.loads(p.read_text(encoding="utf-8"))["scenario_years"])
                             if p.is_file() else pd.DataFrame())
    f_dir = PROJECT_ROOT / "results/forecast"
    p = f_dir / "forecast_holdout_accuracy.csv"
    out["forecast_accuracy"] = pd.read_csv(p) if p.is_file() else pd.DataFrame()
    p = f_dir / "forecast_summary.json"
    out["forecast_summary"] = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    p = f_dir / "forecast_holdout_predictions.csv"
    if p.is_file():
        h = pd.read_csv(p)
        h.index = pd.to_datetime(h.pop("hour_start_local"), utc=True).dt.tz_convert(TZ)
        out["forecast_holdout"] = h
    else:
        out["forecast_holdout"] = pd.DataFrame()
    return out


# Plain names for the forecast targets and models (Evidence page).
FORECAST_TARGETS = {"demand_system_mw": "Demand", "solar_available_mw": "Solar",
                    "wind_mw": "Wind", "must_take_mw": "Small hydro and biomass"}
FORECAST_MODELS = {"persistence": "Tomorrow = today", "seasonal_naive": "Same day last week",
                   "ridge": "Ridge regression", "gbm": "Gradient boosting"}


def forecast_table(accuracy: pd.DataFrame, selected: dict) -> pd.DataFrame:
    """Hold-out MAE (MW) of every model per target, the chosen model and its skill."""
    mae = accuracy.pivot(index="target", columns="model", values="mae")
    rows = []
    for t, name in FORECAST_TARGETS.items():
        if t not in mae.index:
            continue
        chosen = selected.get(t, "persistence")
        row = {"Forecast of": name}
        row.update({FORECAST_MODELS[m]: mae.loc[t, m] for m in FORECAST_MODELS if m in mae.columns})
        row["Chosen"] = FORECAST_MODELS[chosen]
        row["Better than tomorrow = today"] = 1 - mae.loc[t, chosen] / mae.loc[t, "persistence"]
        rows.append(row)
    return pd.DataFrame(rows)


def forecast_skill_same_hours(holdout: pd.DataFrame, target: str) -> dict:
    """MAE of the chosen model and of persistence on the hours where both exist."""
    h = holdout[holdout["target"] == target].dropna(subset=["actual_mw", "forecast_mw", "persistence_mw"])
    mae = float((h["forecast_mw"] - h["actual_mw"]).abs().mean())
    ref = float((h["persistence_mw"] - h["actual_mw"]).abs().mean())
    mape = float(((h["forecast_mw"] - h["actual_mw"]).abs() / h["actual_mw"]).mean() * 100)
    return {"hours": len(h), "mae_mw": mae, "persistence_mae_mw": ref, "skill": 1 - mae / ref,
            "mape_pct": mape}


def assumptions(cfg: dict) -> pd.DataFrame:
    """Every central parameter with its stated source (NFR6)."""
    c = cfg["central"]
    rows = [("Scenario year", c["scenario_year"], c.get("scenario_year_source", "")),
            ("NLmin (MW)", c["nlmin_mw"], c.get("nlmin_source", "")),
            ("SNSP limit", c["snsp_max"], c.get("snsp_source", "")),
            ("Sessions per day", c["sessions_per_day"], c.get("sessions_per_day_source", "")),
            ("Station capacity (kW)", c["station_capacity_kw"], c.get("station_source", "")),
            ("Sessions per station per day", c["sessions_per_station_per_day"], c.get("station_source", "")),
            ("Declared-departure share", c["declared_departure_fraction"], "provisional"),
            ("Objective weights", json.dumps(c["weights"]), "swept in SO5 (no single right weighting)"),
            ("Session source", cfg["sessions"]["source"], cfg["sessions"].get("source_note", ""))]
    out = pd.DataFrame(rows, columns=["Parameter", "Value", "Source"])
    out["Value"] = out["Value"].map(lambda v: "off" if v is None else str(v))   # mixed types
    return out
