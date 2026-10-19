"""SO5/SO6 experiments: strategy comparison, weight trade-off, sensitivity.

Controlled numerical experiment (proposal §3.1): the session set and grid
hours are identical for every strategy, so strategy is the only variable.

Strategies
    uncontrolled, solar_following, curtailment_aware      (perfect foresight)
    curtailment_aware_persistence   plans on yesterday's grid signal (persistence
                                    forecast, proposal Table 3.2) and is scored
                                    on today's actual signal
    curtailment_aware_ml            plans on the day-ahead forecast from
                                    forecast_grid.py (run it first), scored on
                                    today's actual signal

Days
    A day is schedulable when it, the previous day (persistence forecast) and
    the early hours of the next day (overnight departures) are complete.
    central : one day per ISO week, rotating the weekday so weekends count
    sweeps  : every fourth central day (~one per month)
    Days where any LP is infeasible are dropped for ALL strategies and counted.

Usage
    .venv\\Scripts\\python.exe scripts\\run_experiments.py            (full, ~30 min)
    .venv\\Scripts\\python.exe scripts\\run_experiments.py --quick    (4 days, smoke test)
Outputs go to results/experiments/. Every figure is a provisional-scenario
result until NLmin, SNSP, fleet volume and session data are replaced.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import build_grid_scenario as bgs
import ev_sessions as evs
import schedule_ev_charging as sch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TZ = "Asia/Colombo"
STRATEGIES = ["uncontrolled", "solar_following", "curtailment_aware",
              "curtailment_aware_persistence"]
PLAN_COLUMNS = ["net_load_mw", "demand_system_mw", "vre_mw", "solar_mw"]
# Reference categorical palette, slots 1-4 in fixed order (dataviz skill).
COLORS = {"uncontrolled": "#2a78d6", "solar_following": "#eb6834",
          "curtailment_aware": "#1baf7a", "curtailment_aware_persistence": "#eda100",
          "curtailment_aware_ml": "#e87ba4"}
INK, INK_2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
LABELS = {"uncontrolled": "Uncontrolled", "solar_following": "Solar-following",
          "curtailment_aware": "Curtailment-aware",
          "curtailment_aware_persistence": "Curtailment-aware (persistence forecast)",
          "curtailment_aware_ml": "Curtailment-aware (ML day-ahead forecast)"}
FORECAST_SUFFIXES = ("_persistence", "_ml")


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #

def load_base(cfg: dict) -> pd.DataFrame:
    path = PROJECT_ROOT / "data/processed/grid" / f"grid_base_{cfg['grid']['year']}_hourly.csv"
    if not path.is_file():
        raise SystemExit(f"{path} not found. Run scripts/build_grid_scenario.py first.")
    base = pd.read_csv(path)
    base.index = pd.to_datetime(base["hour_start_local"], utc=True).dt.tz_convert(TZ)
    return base


def schedulable_days(base: pd.DataFrame) -> list[pd.Timestamp]:
    ok = set(bgs.eligible_days(base))
    complete = (base["hour_status"] == "complete") & ~base["is_system_event_day"]
    out = []
    for d in sorted(ok):
        nxt = pd.date_range(d + pd.offsets.Day(1), periods=9, freq="h")
        if d - pd.offsets.Day(1) in ok and complete.reindex(nxt, fill_value=False).all():
            out.append(d)
    return out


def sample_days(days: list[pd.Timestamp]) -> list[pd.Timestamp]:
    """One day per ISO week; week k prefers weekday k mod 7 (unbiased by weekday)."""
    by_week: dict = {}
    for d in days:
        by_week.setdefault(d.isocalendar()[:2], []).append(d)
    out = []
    for k, (_, ds) in enumerate(sorted(by_week.items())):
        want = k % 7
        out.append(min(ds, key=lambda d: (d.dayofweek - want) % 7))
    return out


def persistence_plan(signal: pd.DataFrame) -> pd.DataFrame:
    plan = signal.copy()
    plan[PLAN_COLUMNS] = signal[PLAN_COLUMNS].shift(24)
    return plan


# --------------------------------------------------------------------------- #
# One experimental case
# --------------------------------------------------------------------------- #

def run_case(days: list, sessions: pd.DataFrame, signal: pd.DataFrame, cfg: dict,
             strategies: list[str], weights: dict, station_kw: float,
             plans: dict | None = None) -> dict:
    """Run strategies on identical inputs; returns metrics and hourly loads.

    plans maps a strategy suffix to the signal that strategy PLANS on; every
    strategy is SCORED on `signal`.
    """
    plans = dict(plans or {})
    plans.setdefault("_persistence", persistence_plan(signal))
    per = {s: {"loads": [], "outcomes": [], "audits": [], "status": [], "solver": []}
           for s in strategies}
    kept, dropped = [], []
    for d in days:
        sd = sessions[sessions["day"] == d.strftime("%Y-%m-%d")]
        prob = sch.day_problem(d, sd, station_kw)
        day_runs = {}
        for s in strategies:
            suffix = next((k for k in FORECAST_SUFFIXES if s.endswith(k)), "")
            plan = plans[suffix] if suffix else signal
            mode = s[: len(s) - len(suffix)] if suffix else s
            grid = sch.plan_grid(plan, prob.hours) if mode != "uncontrolled" else None
            day_runs[s] = sch.run_strategy(prob, mode, grid, weights)
        bad = [s for s, r in day_runs.items() if r["status"] not in ("optimal", "simulated")]
        if bad:
            dropped.append({"day": str(d.date()), "strategies": bad,
                            "status": [day_runs[s]["status"] for s in bad]})
            continue
        kept.append(d)
        for s, r in day_runs.items():
            full = prob.declared if s != "uncontrolled" else False
            per[s]["loads"].append(sch.hourly_load_mw(prob, r["P"]))
            per[s]["outcomes"].append(sch.session_outcomes(prob, r["P"]))
            per[s]["audits"].append(sch.audit_schedule(prob, r["P"], full))
            per[s]["status"].append(r["status"])
            per[s]["solver"].append(r["solver_s"])
    evening = tuple(cfg["grid"]["evening_hours"])
    metrics, loads = {}, {}
    for s in strategies:
        if not kept:
            break
        load = pd.concat(per[s]["loads"]).groupby(level=0).sum()
        loads[s] = load
        metrics[s] = sch.evaluate(kept, signal, load, pd.concat(per[s]["outcomes"]),
                                  per[s]["audits"], per[s]["status"], per[s]["solver"], evening)
    unc = metrics.get("uncontrolled")
    for s, m in metrics.items():
        if unc:
            m["evening_peak_reduction_vs_uncontrolled_mw"] = round(
                unc["mean_evening_peak_mw"] - m["mean_evening_peak_mw"], 2)
            m["evening_peak_reduction_pct_of_ev_increase"] = round(
                100 * (unc["mean_evening_peak_increase_mw"] - m["mean_evening_peak_increase_mw"])
                / unc["mean_evening_peak_increase_mw"], 1) if unc["mean_evening_peak_increase_mw"] else None
            m["absorbed_gain_vs_uncontrolled_mwh"] = round(m["absorbed_mwh"] - unc["absorbed_mwh"], 2)
    return {"metrics": metrics, "loads": loads, "days": kept, "dropped_days": dropped}


def ranking(metrics: dict) -> dict:
    """Strategy order by absorption (high first) and evening peak (low first)."""
    s = [k for k in metrics if not k.endswith(FORECAST_SUFFIXES)]
    return {
        "by_absorption": sorted(s, key=lambda k: -metrics[k]["absorbed_mwh"]),
        "by_evening_peak": sorted(s, key=lambda k: metrics[k]["mean_evening_peak_mw"]),
    }


# --------------------------------------------------------------------------- #
# Figures
# --------------------------------------------------------------------------- #

def _style(ax):
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(INK_2)
    ax.tick_params(colors=INK_2, labelsize=9)


def plot_day_profile(day, signal, loads, path: Path) -> None:
    import matplotlib.pyplot as plt
    hours = pd.date_range(day, periods=30, freq="h")
    sig = signal.reindex(hours)
    x = np.arange(len(hours))
    fig, ax = plt.subplots(figsize=(10, 5.2))
    _style(ax)
    ax.fill_between(x, sig["net_load_mw"], np.maximum(sig["net_load_mw"], sig["nlmin_mw"]),
                    color="#e34948", alpha=0.18, linewidth=0, step=None,
                    label="Curtailment risk C(t) (gap to NLmin)")
    ax.plot(x, sig["nlmin_mw"], color=INK_2, linestyle="--", linewidth=1.2, label="NLmin (provisional)")
    ax.plot(x, sig["net_load_mw"], color=INK, linewidth=2, label="Residual load, no EVs")
    for s in ("uncontrolled", "solar_following", "curtailment_aware"):
        L = loads[s].reindex(hours, fill_value=0.0)
        ax.plot(x, sig["net_load_mw"] + L, color=COLORS[s], linewidth=2, label=f"+ EVs: {LABELS[s]}")
    ax.set_xticks(x[::3], [h.strftime("%H:%M") for h in hours[::3]])
    ax.set_ylabel("MW", color=INK_2)
    ax.set_title(f"Residual load for the dispatchable fleet, {day:%a %d %b %Y}",
                 loc="left", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=8.5, loc="upper left", ncol=2)
    fig.text(0.01, 0.01, "Inferred scenario signal, not measured curtailment. Provisional parameters.",
             fontsize=7.5, color=INK_2)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_tradeoff(tradeoff: pd.DataFrame, path: Path) -> None:
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 5.2))
    _style(ax)
    for dw, g in tradeoff.groupby("delay_weight"):
        g = g.sort_values("peak_weight")
        ax.plot(g["absorbed_mwh"], g["evening_peak_reduction_vs_uncontrolled_mw"], marker="o",
                markersize=7, linewidth=2, label=f"delay weight {dw:g}",
                color=["#1baf7a", "#2a78d6", "#6250d6"][list(sorted(tradeoff["delay_weight"].unique())).index(dw)])
        last = g.iloc[-1]
        ax.annotate(f"mean delay {last['mean_delay_min']:.0f} min", (last["absorbed_mwh"],
                    last["evening_peak_reduction_vs_uncontrolled_mw"]), textcoords="offset points",
                    xytext=(6, -12), fontsize=8, color=INK_2)
    ax.set_xlabel("Curtailment-risk energy absorbed (MWh)", color=INK_2)
    ax.set_ylabel("Evening peak reduction vs uncontrolled (MW)", color=INK_2)
    ax.set_title("Trade-off from sweeping the peak weight (0 to 16)", loc="left", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=8.5)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_sensitivity(sens: pd.DataFrame, path: Path) -> None:
    import matplotlib.pyplot as plt
    dims = list(dict.fromkeys(sens["dimension"]))
    fig, axes = plt.subplots(1, len(dims), figsize=(3.4 * len(dims), 4.2), sharey=False)
    strategies = ["uncontrolled", "solar_following", "curtailment_aware"]
    for ax, dim in zip(np.atleast_1d(axes), dims):
        _style(ax)
        g = sens[sens["dimension"] == dim]
        values = list(dict.fromkeys(g["value"]))
        width = 0.26
        for j, s in enumerate(strategies):
            y = [g[(g["value"] == v) & (g["strategy"] == s)]["absorbed_mwh"].iloc[0] for v in values]
            ax.bar(np.arange(len(values)) + (j - 1) * width, y, width * 0.92,
                   color=COLORS[s], label=LABELS[s])
        ax.set_xticks(np.arange(len(values)), [str(v) for v in values], fontsize=8)
        ax.set_title(dim, loc="left", fontsize=9.5, color=INK)
    np.atleast_1d(axes)[0].set_ylabel("Absorbed MWh", color=INK_2)
    np.atleast_1d(axes)[0].legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=bgs.DEFAULT_CONFIG)
    ap.add_argument("--acn-json", type=Path, help="ACN-Data JSON; switches sessions to a fitted GMM")
    ap.add_argument("--quick", action="store_true", help="4 central days, 2 sweep days")
    ap.add_argument("--skip-sweeps", action="store_true")
    ap.add_argument("--outdir", type=Path, default=PROJECT_ROOT / "results/experiments")
    args = ap.parse_args()

    t_start = time.perf_counter()
    cfg = bgs.load_config(args.config)
    c = cfg["central"]
    seed = int(cfg["sessions"]["seed"])
    base = load_base(cfg)
    days_all = schedulable_days(base)
    central_days = sample_days(days_all)
    sweep_days = central_days[::4]
    if args.quick:
        central_days, sweep_days = central_days[13:17], central_days[13:15]
    out = args.outdir
    (out / "figures").mkdir(parents=True, exist_ok=True)

    gmm = None
    if args.acn_json:
        acn = evs.load_acn_sessions(args.acn_json)
        gmm = evs.fit_gmm(acn, cfg["sessions"]["gmm_max_components"], seed)
        print(f"GMM fitted on {len(acn)} ACN-Data sessions, {gmm.n_components} components")

    def signal_for(nlmin=None, solar=None, snsp="central"):
        return bgs.scenario_signal(
            base, c["nlmin_mw"] if nlmin is None else nlmin,
            c["solar_multiplier"] if solar is None else solar, c["wind_multiplier"],
            c["snsp_max"] if snsp == "central" else snsp, cfg["grid"]["peak_band_fraction"])

    sessions = evs.build_session_set(sorted(set(central_days) | set(sweep_days)), cfg, seed, gmm)
    plaus = evs.plausibility(sessions, c["kwh_per_km"])
    print(f"{len(days_all)} schedulable days; central sample {len(central_days)}, sweeps {len(sweep_days)}")
    print("Sessions:", json.dumps(plaus))

    # SO5 central comparison
    central_signal = signal_for()
    strategies, plans = list(STRATEGIES), {}
    fpath = PROJECT_ROOT / "data/processed/grid" / f"grid_forecast_{cfg['grid']['year']}_hourly.csv"
    if fpath.is_file():
        fc = pd.read_csv(fpath)
        fc.index = pd.to_datetime(fc["hour_start_local"], utc=True).dt.tz_convert(TZ)
        fbase = base.copy()
        for col in ("demand_system_mw", "solar_available_mw", "wind_mw", "must_take_mw"):
            fbase[col] = fc[col].reindex(base.index)
        plans["_ml"] = bgs.scenario_signal(
            fbase, c["nlmin_mw"], c["solar_multiplier"], c["wind_multiplier"],
            c["snsp_max"], cfg["grid"]["peak_band_fraction"])
        strategies.append("curtailment_aware_ml")
    else:
        print("No forecast file; run forecast_grid.py to add the ML-forecast strategy.")
    res = run_case(central_days, sessions, central_signal, cfg, strategies, c["weights"],
                   c["station_capacity_kw"], plans)
    comp = pd.DataFrame(res["metrics"]).T
    comp.index.name = "strategy"
    comp.to_csv(out / "so5_comparison.csv")
    print(comp[["absorbed_mwh", "unabsorbed_mwh", "mean_evening_peak_increase_mw",
                "evening_peak_reduction_vs_uncontrolled_mw", "completion_rate",
                "mean_delay_min", "p95_delay_min", "constraint_violations"]].to_string())
    if res["days"]:
        risk_by_day = central_signal["curtailment_risk_mw"].groupby(
            central_signal.index.normalize()).sum()
        show = max(res["days"], key=lambda d: risk_by_day.get(d, 0))
        plot_day_profile(show, central_signal, res["loads"], out / "figures/so5_day_profile.png")

    summary = {
        "status": "PROVISIONAL scenario results; see config/scenarios.json _status",
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "config": cfg,
        "session_plausibility": plaus,
        "central_days": [str(d.date()) for d in res["days"]],
        "dropped_days": res["dropped_days"],
        "so5_ranking": ranking(res["metrics"]),
    }

    if not args.skip_sweeps:
        sw = cfg["sweeps"]
        three = ["uncontrolled", "solar_following", "curtailment_aware"]
        rows = []
        for pw in sw["peak_weight"]:
            for dw in sw["delay_weight"]:
                r = run_case(sweep_days, sessions, central_signal, cfg, ["uncontrolled", "curtailment_aware"],
                             {"curtailment": 1.0, "peak": pw, "delay": dw}, c["station_capacity_kw"])
                m = r["metrics"].get("curtailment_aware")
                if m:
                    rows.append({"peak_weight": pw, "delay_weight": dw, **{k: m[k] for k in (
                        "absorbed_mwh", "evening_peak_reduction_vs_uncontrolled_mw",
                        "mean_evening_peak_increase_mw", "mean_delay_min", "p95_delay_min",
                        "completion_rate", "constraint_violations")}, "days": m["days"]})
                print(f"trade-off peak={pw} delay={dw} done")
        tradeoff = pd.DataFrame(rows)
        tradeoff.to_csv(out / "so5_tradeoff.csv", index=False)
        if len(tradeoff):
            plot_tradeoff(tradeoff, out / "figures/so5_tradeoff.png")

        base_rank = None
        sens_rows = []
        cases = ([("NLmin (MW)", v, signal_for(nlmin=v), sessions) for v in sw["nlmin_mw"]]
                 + [("SNSP limit", "off" if v is None else v, signal_for(snsp=v), sessions)
                    for v in sw["snsp_max"]]
                 + [("Solar multiplier", v, signal_for(solar=v), sessions) for v in sw["solar_multiplier"]]
                 + [("Declared departure share", v, central_signal,
                     evs.build_session_set(sorted(set(central_days) | set(sweep_days)), cfg, seed, gmm, v))
                    for v in sw["declared_departure_fraction"]])
        for dim, val, sig, sess in cases:
            r = run_case(sweep_days, sess, sig, cfg, three, c["weights"], c["station_capacity_kw"])
            rk = ranking(r["metrics"]) if r["metrics"] else None
            if base_rank is None and rk:
                central_r = run_case(sweep_days, sessions, central_signal, cfg, three, c["weights"],
                                     c["station_capacity_kw"])
                base_rank = ranking(central_r["metrics"])
            for s, m in r["metrics"].items():
                sens_rows.append({"dimension": dim, "value": val, "strategy": s,
                                  "absorbed_mwh": m["absorbed_mwh"],
                                  "curtailment_risk_mwh": m["curtailment_risk_mwh"],
                                  "mean_evening_peak_increase_mw": m["mean_evening_peak_increase_mw"],
                                  "mean_delay_min": m["mean_delay_min"],
                                  "completion_rate": m["completion_rate"],
                                  "constraint_violations": m["constraint_violations"],
                                  "days": m["days"], "dropped_days": len(r["dropped_days"]),
                                  "ranking_preserved": rk == base_rank if rk else None})
            print(f"sensitivity {dim}={val} done")
        sens = pd.DataFrame(sens_rows)
        sens.to_csv(out / "so6_sensitivity.csv", index=False)
        if len(sens):
            plot_sensitivity(sens, out / "figures/so6_sensitivity.png")
            preserved = sens.drop_duplicates(["dimension", "value"])["ranking_preserved"]
            summary["so6_ranking_preserved_share"] = round(float(preserved.mean()), 3)
        summary["so6_reference_ranking"] = base_rank

    summary["runtime_s"] = round(time.perf_counter() - t_start, 1)
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"Done in {summary['runtime_s']} s. Outputs in {out}")
    print("REMINDER: provisional NLmin/SNSP/fleet/session parameters; inferred, not measured, curtailment.")


if __name__ == "__main__":
    main()
