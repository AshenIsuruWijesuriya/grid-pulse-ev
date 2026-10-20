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


def driver_examples(day: pd.Timestamp, base: pd.DataFrame, cfg: dict, settings: Settings,
                    pool: pd.DataFrame | None = None) -> list[dict]:
    """A typical driver from three groups, with their charging under each strategy.

    For each group the driver shown is the median by how far smart charging
    moves their charging time, among drivers it moves at all: typical, not the
    most extreme. Returns [] when the day is infeasible.
    """
    run_cfg = settings.apply(cfg)
    c = run_cfg["central"]
    sig = signal(base, run_cfg)
    sessions, _ = _day_sessions(day, run_cfg, pool)
    prob = sch.day_problem(day, sessions, c["station_capacity_kw"])
    shown = ["uncontrolled", "solar_following", "curtailment_aware"]
    P = {}
    for s in shown:
        grid = sch.plan_grid(sig, prob.hours) if s != "uncontrolled" else None
        r = sch.run_strategy(prob, s, grid, c["weights"])
        if r["status"] not in ("optimal", "simulated"):
            return []
        P[s] = r["P"]
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
        finish = {s: prob.slots[np.flatnonzero(P[s][i] > 1e-3).max()] + pd.offsets.Minute(15)
                  for s in shown}
        out.append({"group": name, "blurb": blurb, "session_id": prob.sessions["session_id"].iloc[i],
                    "arrival": prob.slots[prob.arr[i]],
                    "departure": prob.slots[0] + pd.offsets.Minute(15 * int(prob.dep[i])),
                    "energy_kwh": float(prob.energy[i]), "charger_kw": float(prob.pmax[i]),
                    "power_kw": power, "finish": finish,
                    "delivered_kwh": {s: float(P[s][i].sum() * sch.STEP_HOURS) for s in shown},
                    "group_size": int(mask.sum()), "group_moved": int(len(idx))})
    return out


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
    return out


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
