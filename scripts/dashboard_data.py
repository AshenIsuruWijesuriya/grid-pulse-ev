"""Data layer for the dashboard (WBS 5.11, FR13).

Everything the dashboard shows is computed here with the same functions the
experiments use (scenario_signal, run_case, evaluate), so a number on screen
can never disagree with results/experiments. app/dashboard.py only draws.

Settings a user can change: scenario year, NLmin, SNSP limit, sessions per
day, declared-departure share and objective weights. Anything not changed
comes from config/scenarios.json.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

import build_grid_scenario as bgs
import ev_sessions as evs
import run_experiments as rx

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


def solve_one_day(day: pd.Timestamp, base: pd.DataFrame, cfg: dict, settings: Settings,
                  strategies: list[str] | None = None) -> dict:
    """Run every strategy on one day under `settings`.

    Returns the hourly grid window, hourly EV load per strategy (MW), the
    metrics table, and the day's status (dropped strategies are reported).
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
    sessions = evs.build_session_set([day], run_cfg, int(run_cfg["sessions"]["seed"]))
    res = rx.run_case([day], sessions, sig, run_cfg, strategies, c["weights"],
                      c["station_capacity_kw"], plans)
    hours = pd.date_range(day, periods=30, freq="h")
    window = sig.reindex(hours)[["demand_system_mw", "solar_mw", "wind_mw", "vre_mw", "net_load_mw",
                                 "nlmin_mw", "curtailment_risk_mw"]]
    loads = pd.DataFrame({s: res["loads"][s].reindex(hours, fill_value=0.0) for s in res["loads"]},
                         index=hours)
    metrics = pd.DataFrame(res["metrics"]).T.reindex(columns=list(METRIC_COLUMNS))
    return {"window": window, "loads": loads, "metrics": metrics,
            "dropped": res["dropped_days"], "sessions": len(sessions)}


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
    return pd.DataFrame(rows, columns=["Parameter", "Value", "Source"])
