"""Scheduler runtime across session volumes (proposal NFR1, NFR2).

NFR1: a single-day run at national session volume solves to optimality in
under 60 s. NFR2: runtime growth is characterised across session counts
spanning at least one order of magnitude.

One representative curtailment-risk day of the central scenario is solved by
the curtailment-aware LP at each session count, single process, so timings
are comparable. The number of stations grows with the session count
(sessions_per_station_per_day is fixed), as it would for a growing fleet.

Usage
    .venv\\Scripts\\python.exe scripts\\benchmark_scaling.py
Writes results/experiments/scaling_benchmark.csv
"""

from __future__ import annotations

import argparse
import copy
import json
import platform
import time
from pathlib import Path

import numpy as np
import pandas as pd

import build_grid_scenario as bgs
import ev_sessions as evs
import run_experiments as rx
import schedule_ev_charging as sch

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_one(day: pd.Timestamp, signal: pd.DataFrame, cfg: dict, n: int, seed: int) -> dict:
    c = copy.deepcopy(cfg)
    c["central"]["sessions_per_day"] = n
    sessions = evs.build_session_set([day], c, seed)
    t0 = time.perf_counter()
    prob = sch.day_problem(day, sessions, c["central"]["station_capacity_kw"])
    grid = sch.plan_grid(signal, prob.hours)
    r = sch.run_strategy(prob, "curtailment_aware", grid, c["central"]["weights"])
    wall = time.perf_counter() - t0
    audit = sch.audit_schedule(prob, r["P"], prob.declared) if r["status"] == "optimal" else {}
    active = int(sum(min(d, prob.n_slots) - a for a, d in zip(prob.arr, prob.dep)))
    return {"sessions": n, "stations": int(prob.station.max()) + 1, "slots": prob.n_slots,
            "session_slot_variables": active, "status": r["status"],
            "solver_s": round(r["solver_s"], 2), "total_s": round(wall, 2),
            "constraint_violations": audit.get("total")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=bgs.DEFAULT_CONFIG)
    ap.add_argument("--sessions", type=int, nargs="+", default=[1000, 3000, 10000, 30000])
    ap.add_argument("--outdir", type=Path, default=PROJECT_ROOT / "results/experiments")
    args = ap.parse_args()

    cfg = bgs.load_config(args.config)
    base = rx.load_base(cfg)
    signal = bgs.scenario_signal(base, **bgs.scenario_kwargs(cfg))
    risk = signal["curtailment_risk_mw"].groupby(signal.index.normalize()).sum()
    days = [d for d in rx.schedulable_days(base) if risk.get(d, 0) > 0]
    day = max(days, key=lambda d: risk[d])          # hardest day: largest risk window
    rows = [run_one(day, signal, cfg, n, int(cfg["sessions"]["seed"])) for n in args.sessions]
    df = pd.DataFrame(rows)
    df.insert(0, "day", str(day.date()))
    if len(df) > 1:
        # Empirical growth exponent: total_s ~ sessions^k
        k = np.polyfit(np.log(df["sessions"]), np.log(df["total_s"]), 1)[0]
        print(f"runtime grows as sessions^{k:.2f}")
    args.outdir.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.outdir / "scaling_benchmark.csv", index=False)
    (args.outdir / "scaling_benchmark_machine.json").write_text(json.dumps(
        {"processor": platform.processor(), "machine": platform.machine(),
         "python": platform.python_version(), "single_process": True}, indent=2), encoding="utf-8")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
