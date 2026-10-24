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
    curtailment_aware_robust        plans one schedule against K grid scenarios:
                                    the same forecast plus the forecast errors
                                    of K analog days (forecast_scenarios), by
                                    minimising the scenario-average curtailment
                                    and peak; scored on today's actual signal

Days
    A day is schedulable when it, the previous day (persistence forecast) and
    the early hours of the next day (overnight departures) are complete.
    central   : one day per ISO week, rotating the weekday so weekends count;
                year-representative peak, delay and completion
    risk days : every schedulable day with curtailment risk in the central
                scenario year; the absorption comparison and the weight
                trade-off (absorption only differs where there is risk)
    sweeps    : the central days (a thinner subset can miss every risk day
                and make strategies tie trivially)
    Days where any LP is infeasible are dropped for ALL strategies and counted.

Usage
    .venv\\Scripts\\python.exe scripts\\run_experiments.py --workers 4   (full)
    .venv\\Scripts\\python.exe scripts\\run_experiments.py --quick    (4 days, smoke test;
                                                                     writes results/experiments_quick)
Outputs go to results/experiments/. Every figure is a provisional-scenario
result until NLmin, SNSP, fleet volume and session data are replaced.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from concurrent.futures import Executor, ProcessPoolExecutor
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
          "curtailment_aware_ml": "#e87ba4", "curtailment_aware_robust": "#6250d6"}
INK, INK_2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
LABELS = {"uncontrolled": "Uncontrolled", "solar_following": "Solar-following",
          "curtailment_aware": "Curtailment-aware",
          "curtailment_aware_persistence": "Curtailment-aware (persistence forecast)",
          "curtailment_aware_ml": "Curtailment-aware (ML day-ahead forecast)",
          "curtailment_aware_robust": "Curtailment-aware (forecast scenarios)"}
FORECAST_SUFFIXES = ("_persistence", "_ml", "_robust")
FORECAST_TARGETS = ["demand_system_mw", "solar_available_mw", "wind_mw", "must_take_mw"]


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


def ml_forecast_plan(base: pd.DataFrame, cfg: dict, **override) -> pd.DataFrame | None:
    """Signal built from the day-ahead ML forecast (forecast_grid.py), or None if absent.

    `override` takes the same keywords as bgs.scenario_kwargs, so the plan uses
    the same scenario as the signal it is scored on.
    """
    fc = load_forecast(base, cfg)
    if fc is None:
        return None
    fbase = base.copy()
    fbase[FORECAST_TARGETS] = fc
    return bgs.scenario_signal(fbase, **bgs.scenario_kwargs(cfg, **override))


def load_forecast(base: pd.DataFrame, cfg: dict) -> pd.DataFrame | None:
    """The day-ahead forecast of the four targets on base's hours, or None if absent."""
    fpath = PROJECT_ROOT / "data/processed/grid" / f"grid_forecast_{cfg['grid']['year']}_hourly.csv"
    if not fpath.is_file():
        return None
    fc = pd.read_csv(fpath)
    fc.index = pd.to_datetime(fc["hour_start_local"], utc=True).dt.tz_convert(TZ)
    return fc[FORECAST_TARGETS].reindex(base.index)


def analog_days(days: pd.DatetimeIndex, candidates: pd.DatetimeIndex, k: int,
                past_only: bool = False) -> np.ndarray:
    """For each day, the k candidate days of OTHER months nearest in day of year.

    Excluding the day's own month follows the leave-one-month-out forecast: no
    day's own month informs its forecast or its error scenarios. `past_only`
    keeps only months before the day's month (strictly causal); a day with
    fewer than k such days gets -1 (no scenarios).
    """
    out = np.empty((len(days), k), dtype=int)
    cdoy, cmonth = candidates.dayofyear.to_numpy(), candidates.month.to_numpy()
    for i, d in enumerate(days):
        gap = np.abs(cdoy - d.dayofyear)
        gap = np.minimum(gap, 366 - gap).astype(float)
        gap[cmonth == d.month] = np.inf
        if past_only:
            gap[candidates >= d.replace(day=1)] = np.inf
        order = np.argsort(gap, kind="stable")[:k]
        if not np.isfinite(gap[order]).all():
            if not past_only:
                raise ValueError(f"fewer than {k} analog days for {d.date()}")
            order = np.full(k, -1)
        out[i] = order
    return out


def forecast_scenarios(base: pd.DataFrame, cfg: dict, k: int | None = None,
                       past_only: bool = False, **override) -> list[pd.DataFrame] | None:
    """K grid scenarios around the day-ahead forecast, for curtailment_aware_robust.

    Scenario j for day d = forecast(d) + the forecast errors (actual - forecast)
    of d's j-th analog day, hour by hour. All four targets take their errors
    from the same analog day, so their correlation is kept (e.g. a cloudy
    afternoon with lower demand). Solar, wind and must-take stay >= 0.
    Days without analogs (past_only in January) get NaN, so planning them fails
    loudly rather than silently using the plain forecast.
    """
    fc = load_forecast(base, cfg)
    if fc is None:
        return None
    k = int(k or cfg["central"]["forecast_scenarios"])
    days = base.index.normalize().unique()
    if len(base) != 24 * len(days):
        raise ValueError("forecast scenarios need 24 hours on every day")
    usable = (base["hour_status"] == "complete") & ~base["is_system_event_day"]
    err = (base[FORECAST_TARGETS] - fc).where(usable, np.nan)
    complete = err.notna().all(axis=1).groupby(base.index.normalize()).all()
    candidates = complete.index[complete.to_numpy()]
    pick = analog_days(days, candidates, k, past_only)
    pos = {d: i for i, d in enumerate(days)}
    err_by_day = err.to_numpy().reshape(len(days), 24, len(FORECAST_TARGETS))
    none = np.full((24, len(FORECAST_TARGETS)), np.nan)
    out = []
    for j in range(k):
        e = np.stack([err_by_day[pos[candidates[c]]] if c >= 0 else none for c in pick[:, j]])
        sb = base.copy()
        sb[FORECAST_TARGETS] = fc.to_numpy() + e.reshape(len(base), len(FORECAST_TARGETS))
        for col in ("solar_available_mw", "wind_mw", "must_take_mw"):
            sb[col] = sb[col].clip(lower=0)
        out.append(bgs.scenario_signal(sb, **bgs.scenario_kwargs(cfg, **override)))
    return out


def experiment_days(base: pd.DataFrame, cfg: dict, quick: bool = False) -> dict:
    """The day sets of the experiment, derived from the central scenario.

    Sessions are drawn for `all` in one pass, so any tool that wants the
    experiment's exact sessions for a day must draw them over this same set.
    """
    days_all = schedulable_days(base)
    central = sample_days(days_all)
    sig = bgs.scenario_signal(base, **bgs.scenario_kwargs(cfg))
    risk_by_day = sig["curtailment_risk_mw"].groupby(sig.index.normalize()).sum()
    risk = [d for d in days_all if risk_by_day.get(d, 0) > 0]
    if quick:
        central, risk = central[13:17], risk[:2]
    return {"schedulable": days_all, "central": central, "sweep": central, "risk": risk,
            "all": sorted(set(central) | set(risk))}


def persistence_plan(signal: pd.DataFrame) -> pd.DataFrame:
    plan = signal.copy()
    plan[PLAN_COLUMNS] = signal[PLAN_COLUMNS].shift(24)
    return plan


# --------------------------------------------------------------------------- #
# One experimental case
# --------------------------------------------------------------------------- #

def solve_day(task: tuple) -> dict:
    """Solve every strategy for one day (module level so worker processes can run it).

    task = (day, sessions of that day, station kW, {strategy: (mode, planning grid)}, weights,
            station rule)
    """
    d, sd, station_kw, runs, weights, rule = task
    prob = sch.day_problem(d, sd, station_kw)
    out = {}
    for s, (mode, plan) in runs.items():
        grid = sch.plan_grid(plan, prob.hours) if plan is not None else None
        r = sch.run_strategy(prob, mode, grid, weights, rule)
        res = {"status": r["status"], "solver_s": r["solver_s"]}
        if r["status"] in ("optimal", "simulated"):
            full = prob.declared if s != "uncontrolled" else False
            res.update(load=sch.hourly_load_mw(prob, r["P"]),
                       outcomes=sch.session_outcomes(prob, r["P"]),
                       audit=sch.audit_schedule(prob, r["P"], full))
        out[s] = res
    return out


def run_case(days: list, sessions: pd.DataFrame, signal: pd.DataFrame, cfg: dict,
             strategies: list[str], weights: dict, station_kw: float,
             plans: dict | None = None, pool: Executor | None = None) -> dict:
    """Run strategies on identical inputs; returns metrics and hourly loads.

    plans maps a strategy suffix to the signal that strategy PLANS on; every
    strategy is SCORED on `signal`. Days run in `pool` when one is given; the
    result does not depend on it.
    """
    plans = dict(plans or {})
    plans.setdefault("_persistence", persistence_plan(signal))
    per = {s: {"loads": [], "outcomes": [], "audits": [], "status": [], "solver": []}
           for s in strategies}
    tasks = []
    for d in days:
        # Only the day's window of each signal travels to the worker.
        window = slice(d - pd.offsets.Day(1), d + pd.offsets.Day(3))
        runs = {}
        for s in strategies:
            suffix = next((k for k in FORECAST_SUFFIXES if s.endswith(k)), "")
            plan = plans[suffix] if suffix else signal
            mode = s[: len(s) - len(suffix)] if suffix else s
            if mode == "uncontrolled":
                runs[s] = (mode, None)
            else:
                runs[s] = (mode, [p.loc[window] for p in plan] if isinstance(plan, list) else plan.loc[window])
        tasks.append((d, sessions[sessions["day"] == d.strftime("%Y-%m-%d")], station_kw, runs, weights,
                      cfg["central"].get("station_rule", "arrival_first")))
    results = pool.map(solve_day, tasks) if pool else map(solve_day, tasks)
    kept, dropped = [], []
    for d, day_runs in zip(days, results):
        bad = [s for s, r in day_runs.items() if r["status"] not in ("optimal", "simulated")]
        if bad:
            dropped.append({"day": str(d.date()), "strategies": bad,
                            "status": [day_runs[s]["status"] for s in bad]})
            continue
        kept.append(d)
        for s, r in day_runs.items():
            per[s]["loads"].append(r["load"])
            per[s]["outcomes"].append(r["outcomes"])
            per[s]["audits"].append(r["audit"])
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


def leads(rank: dict, strategy: str = "curtailment_aware") -> bool:
    """True when `strategy` is first or tied first in every ranking (never outranked)."""
    return all(strategy in groups[0] for groups in rank.values())


def absorbed_by_day(result: dict, signal: pd.DataFrame) -> pd.DataFrame:
    """Risk energy absorbed (MWh) per day and strategy, plus the day's risk energy."""
    rows = {}
    for d in result["days"]:
        hours = pd.date_range(d, periods=24, freq="h")
        risk = signal["curtailment_risk_mw"].reindex(hours).fillna(0.0)
        row = {"risk_mwh": round(float(risk.sum()), 2)}
        for s, load in result["loads"].items():
            left = sch.curtailment_with_load(signal, load).reindex(hours).fillna(0.0)
            row[s] = round(float((risk - left).sum()), 2)
        rows[str(d.date())] = row
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("day")


def ranking(metrics: dict, tol_mwh: float = 0.5, tol_mw: float = 0.05) -> dict:
    """Strategy order by absorption (high first) and evening peak (low first).

    Each rank is a group: strategies within the tolerance of the group's best
    are tied, so solver-level differences (e.g. 0.02 MWh) do not count as a
    ranking change in SO6.
    """
    s = [k for k in metrics if not k.endswith(FORECAST_SUFFIXES)]

    def groups(key, tol):
        out = []
        for k in sorted(s, key=key):
            if out and key(k) - key(out[-1][0]) <= tol:
                out[-1].append(k)
            else:
                out.append([k])
        return [sorted(g) for g in out]

    return {
        "by_absorption": groups(lambda k: -metrics[k]["absorbed_mwh"], tol_mwh),
        "by_evening_peak": groups(lambda k: metrics[k]["mean_evening_peak_mw"], tol_mw),
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
    """Top: residual load and NLmin. Bottom: EV load per strategy (on the top
    panel's scale the EV lines would overlap and hide each other)."""
    import matplotlib.pyplot as plt
    hours = pd.date_range(day, periods=30, freq="h")
    sig = signal.reindex(hours)
    x = np.arange(len(hours))
    risk = (sig["curtailment_risk_mw"] > 0).to_numpy()
    fig, (top, bot) = plt.subplots(2, 1, figsize=(10, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [1.15, 1]})
    for ax in (top, bot):
        _style(ax)
        for i in np.flatnonzero(risk):
            ax.axvspan(i - 0.5, i + 0.5, color="#e34948", alpha=0.10, linewidth=0)
    below = (sig["net_load_mw"] < sig["nlmin_mw"]).to_numpy()
    top.fill_between(x, sig["net_load_mw"], sig["nlmin_mw"], where=below, interpolate=True,
                     color="#e34948", alpha=0.25, linewidth=0,
                     label="Curtailment risk C(t) (gap to NLmin)")
    top.plot(x, sig["nlmin_mw"], color=INK_2, linestyle="--", linewidth=1.2, label="NLmin")
    top.plot(x, sig["net_load_mw"], color=INK, linewidth=2, label="Residual load, no EVs")
    top.set_ylabel("MW", color=INK_2)
    top.set_title(f"Residual load for the dispatchable fleet, {day:%a %d %b %Y}",
                  loc="left", color=INK, fontsize=11)
    top.legend(frameon=False, fontsize=8.5, loc="upper left")
    for s in ("uncontrolled", "solar_following", "curtailment_aware", "curtailment_aware_ml"):
        if s in loads:
            # The forecast line often coincides with perfect foresight; dash it so both show.
            bot.plot(x, loads[s].reindex(hours, fill_value=0.0), color=COLORS[s], linewidth=2,
                     linestyle="--" if s.endswith(FORECAST_SUFFIXES) else "-", label=LABELS[s])
    bot.set_ylabel("EV charging load (MW)", color=INK_2)
    bot.set_title("EV load by strategy (shaded: hours with curtailment risk)", loc="left",
                  color=INK, fontsize=10)
    bot.legend(frameon=False, fontsize=8.5, loc="upper left")
    bot.set_xticks(x[::3], [h.strftime("%H:%M") for h in hours[::3]])
    fig.text(0.01, 0.01, "Inferred scenario signal, not measured curtailment. Provisional fleet and "
             "session parameters.", fontsize=7.5, color=INK_2)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_tradeoff(tradeoff: pd.DataFrame, path: Path) -> None:
    import matplotlib.pyplot as plt
    # Absorption is shown in the subtitle, not on an axis: when it does not move with
    # the weights, an absorption axis collapses every point onto one vertical line.
    fig, ax = plt.subplots(figsize=(8, 5.2))
    _style(ax)
    dws = sorted(tradeoff["delay_weight"].unique())
    for dw, g in tradeoff.groupby("delay_weight"):
        g = g.sort_values("peak_weight")
        ax.plot(g["mean_delay_min"], g["evening_peak_reduction_vs_uncontrolled_mw"], marker="o",
                markersize=8, linewidth=2, label=f"delay weight {dw:g}",
                color=["#1baf7a", "#2a78d6", "#6250d6"][dws.index(dw)])
    first = tradeoff[tradeoff["delay_weight"] == dws[0]].sort_values("peak_weight")
    for _, r in first.drop_duplicates(["mean_delay_min", "evening_peak_reduction_vs_uncontrolled_mw"]).iterrows():
        ax.annotate(f"peak weight {r['peak_weight']:g}", (r["mean_delay_min"],
                    r["evening_peak_reduction_vs_uncontrolled_mw"]), textcoords="offset points",
                    xytext=(8, -14), fontsize=8, color=INK_2)
    lo, hi = tradeoff["absorbed_mwh"].min(), tradeoff["absorbed_mwh"].max()
    ax.set_xlabel("Mean driver delay (min)", color=INK_2)
    ax.set_ylabel("Evening peak reduction vs uncontrolled (MW)", color=INK_2)
    ax.set_title("Peak reduction against driver delay, curtailment-risk days", loc="left",
                 color=INK, fontsize=11, pad=20)
    absorbed = f"{lo:.1f} MWh" if hi - lo < 0.05 else f"{lo:.1f}-{hi:.1f} MWh"
    ax.text(0, 1.01, f"Absorbed risk energy at every weight setting: {absorbed}",
            transform=ax.transAxes, fontsize=8.5, color=INK_2, va="bottom")
    ax.legend(frameon=False, fontsize=8.5, loc="lower right")
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
    ap.add_argument("--quick", action="store_true", help="4 days, smoke test")
    ap.add_argument("--skip-sweeps", action="store_true")
    ap.add_argument("--workers", type=int, default=1,
                    help="worker processes that solve days in parallel")
    ap.add_argument("--outdir", type=Path, default=None,
                    help="default results/experiments, or results/experiments_quick with --quick")
    args = ap.parse_args()

    t_start = time.perf_counter()
    cfg = bgs.load_config(args.config)
    c = cfg["central"]
    seed = int(cfg["sessions"]["seed"])
    base = load_base(cfg)
    ed = experiment_days(base, cfg, args.quick)
    days_all, central_days, risk_days = ed["schedulable"], ed["central"], ed["risk"]
    sweep_days, all_days = ed["sweep"], ed["all"]
    # A smoke test must never overwrite the full results the app and documents read.
    out = args.outdir or PROJECT_ROOT / ("results/experiments_quick" if args.quick else "results/experiments")
    (out / "figures").mkdir(parents=True, exist_ok=True)

    gmm = None
    if args.acn_json:
        acn = evs.load_acn_sessions(args.acn_json)
        gmm = evs.fit_gmm(acn, cfg["sessions"]["gmm_max_components"], seed)
        print(f"GMM fitted on {len(acn)} ACN-Data sessions, {gmm.n_components} components")

    def signal_for(year=None, **override):
        return bgs.scenario_signal(base, **bgs.scenario_kwargs(cfg, year, **override))

    central_signal = signal_for()
    risk_by_day = central_signal["curtailment_risk_mw"].groupby(central_signal.index.normalize()).sum()
    sessions = evs.build_session_set(all_days, cfg, seed, gmm)
    plaus = evs.plausibility(sessions, c["kwh_per_km"])
    print(f"{len(days_all)} schedulable days; central sample {len(central_days)}, "
          f"risk days {len(risk_days)}, sweeps {len(sweep_days)}")
    print("Sessions:", json.dumps(plaus))

    # SO5 central comparison
    strategies, plans = list(STRATEGIES), {}
    ml = ml_forecast_plan(base, cfg)
    if ml is not None:
        plans["_ml"] = ml
        strategies.append("curtailment_aware_ml")
        plans["_robust"] = forecast_scenarios(base, cfg)
        strategies.append("curtailment_aware_robust")
    else:
        print("No forecast file; run forecast_grid.py to add the ML-forecast strategy.")
    pool = ProcessPoolExecutor(args.workers) if args.workers > 1 else None
    res = run_case(central_days, sessions, central_signal, cfg, strategies, c["weights"],
                   c["station_capacity_kw"], plans, pool)
    comp = pd.DataFrame(res["metrics"]).T
    comp.index.name = "strategy"
    comp.to_csv(out / "so5_comparison.csv")
    if res["loads"]:
        pd.DataFrame(res["loads"]).to_csv(out / "so5_hourly_ev_load_mw.csv",
                                          index_label="hour_start_local")
    print(comp[["absorbed_mwh", "unabsorbed_mwh", "mean_evening_peak_increase_mw",
                "evening_peak_reduction_vs_uncontrolled_mw", "completion_rate",
                "mean_delay_min", "p95_delay_min", "constraint_violations"]].to_string())

    # SO5 on every risk day: where absorption can differ at all
    rres = run_case(risk_days, sessions, central_signal, cfg, strategies, c["weights"],
                    c["station_capacity_kw"], plans, pool)
    rcomp = pd.DataFrame(rres["metrics"]).T
    rcomp.index.name = "strategy"
    rcomp.to_csv(out / "so5_risk_days_comparison.csv")
    per_day = pd.DataFrame()
    if len(rcomp):
        print("Risk days:")
        print(rcomp[["absorbed_mwh", "curtailment_risk_mwh", "mean_evening_peak_increase_mw",
                     "mean_delay_min", "completion_rate", "constraint_violations"]].to_string())
        pd.DataFrame(rres["loads"]).to_csv(out / "so5_risk_days_hourly_ev_load_mw.csv",
                                           index_label="hour_start_local")
        per_day = absorbed_by_day(rres, central_signal)
        per_day.to_csv(out / "so5_risk_days_by_day.csv")
    shown = rres if rres["days"] else res
    if shown["days"]:
        show = max(shown["days"], key=lambda d: risk_by_day.get(d, 0))
        plot_day_profile(show, central_signal, shown["loads"], out / "figures/so5_day_profile.png")
    if len(per_day) and {"curtailment_aware", "solar_following"} <= set(per_day.columns):
        # The day where following the risk beats following the sun by the most, if any.
        diff = per_day["curtailment_aware"] - per_day["solar_following"]
        if diff.max() > 0.5:
            d = pd.Timestamp(diff.idxmax(), tz=TZ)
            plot_day_profile(d, central_signal, rres["loads"],
                             out / "figures/so5_day_profile_largest_difference.png")

    summary = {
        "status": "PROVISIONAL scenario results; see config/scenarios.json _status",
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "config": cfg,
        "scenario": bgs.ltgep_scenario(cfg["scenario_years"], c["scenario_year"]),
        "session_plausibility": plaus,
        "central_days": [str(d.date()) for d in res["days"]],
        "dropped_days": res["dropped_days"],
        "so5_ranking": ranking(res["metrics"]),
        "risk_days": [str(d.date()) for d in rres["days"]],
        "risk_days_dropped": rres["dropped_days"],
        "so5_risk_days_ranking": ranking(rres["metrics"]),
    }

    if not args.skip_sweeps:
        sw = cfg["sweeps"]
        three = ["uncontrolled", "solar_following", "curtailment_aware"]
        rows = []
        for pw in sw["peak_weight"]:
            for dw in sw["delay_weight"]:
                r = run_case(risk_days, sessions, central_signal, cfg, ["uncontrolled", "curtailment_aware"],
                             {"curtailment": 1.0, "peak": pw, "delay": dw}, c["station_capacity_kw"],
                             pool=pool)
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

        if "_robust" in plans:
            # Scenario planning: number of scenarios, and errors from past months only.
            rows = []
            for k in sw["forecast_scenarios"]:
                for past in (False, True):
                    r = run_case(risk_days, sessions, central_signal, cfg,
                                 ["uncontrolled", "curtailment_aware_robust"], c["weights"],
                                 c["station_capacity_kw"],
                                 {"_robust": forecast_scenarios(base, cfg, k, past_only=past)}, pool)
                    m = r["metrics"].get("curtailment_aware_robust")
                    if m:
                        rows.append({"scenarios": k, "past_months_only": past, **{f: m[f] for f in (
                            "absorbed_mwh", "mean_evening_peak_increase_mw",
                            "evening_peak_reduction_pct_of_ev_increase", "mean_delay_min",
                            "p95_delay_min", "completion_rate", "constraint_violations",
                            "solver_s_total")}, "days": m["days"],
                            "dropped_days": len(r["dropped_days"])})
                    print(f"scenarios k={k} past_only={past} done")
            pd.DataFrame(rows).to_csv(out / "robust_scenario_count.csv", index=False)

        base_rank = None
        sens_rows = []
        cases = ([("NLmin (MW)", v, signal_for(nlmin_mw=v), sessions) for v in sw["nlmin_mw"]]
                 + [("SNSP limit", "off" if v is None else v, signal_for(snsp_max=v), sessions)
                    for v in sw["snsp_max"]]
                 + [("Scenario year", v, signal_for(year=v), sessions) for v in sw["scenario_year"]]
                 + [("Declared departure share", v, central_signal,
                     evs.build_session_set(all_days, cfg, seed, gmm, v))
                    for v in sw["declared_departure_fraction"]])
        cases = [(*case, cfg) for case in cases]
        # The other station rule, where it matters: when not everyone declares.
        other = {**cfg, "central": {**c, "station_rule": "declared_first"
                                    if c.get("station_rule", "arrival_first") == "arrival_first"
                                    else "arrival_first"}}
        cases += [(f"Declared share, {other['central']['station_rule'].replace('_', ' ')} rule", v,
                   central_signal, evs.build_session_set(all_days, cfg, seed, gmm, v), other)
                  for v in sw["declared_departure_fraction"]]
        for dim, val, sig, sess, case_cfg in cases:
            r = run_case(sweep_days, sess, sig, case_cfg, three, c["weights"], c["station_capacity_kw"],
                         pool=pool)
            rk = ranking(r["metrics"]) if r["metrics"] else None
            if base_rank is None and rk:
                # sweep_days are the central days, so the SO5 run is the reference.
                base_rank = ranking({s: res["metrics"][s] for s in three})
            for s, m in r["metrics"].items():
                sens_rows.append({"dimension": dim, "value": val, "strategy": s,
                                  "absorbed_mwh": m["absorbed_mwh"],
                                  "curtailment_risk_mwh": m["curtailment_risk_mwh"],
                                  "mean_evening_peak_increase_mw": m["mean_evening_peak_increase_mw"],
                                  "mean_delay_min": m["mean_delay_min"],
                                  "completion_rate": m["completion_rate"],
                                  "undeclared_completion_rate": m["undeclared_completion_rate"],
                                  "constraint_violations": m["constraint_violations"],
                                  "days": m["days"], "dropped_days": len(r["dropped_days"]),
                                  "ranking_preserved": rk == base_rank if rk else None,
                                  "curtailment_aware_not_outranked": leads(rk) if rk else None})
            print(f"sensitivity {dim}={val} done")
        sens = pd.DataFrame(sens_rows)
        sens.to_csv(out / "so6_sensitivity.csv", index=False)
        if len(sens):
            plot_sensitivity(sens, out / "figures/so6_sensitivity.png")
            cases_df = sens.drop_duplicates(["dimension", "value"])
            summary["so6_ranking_preserved_share"] = round(float(cases_df["ranking_preserved"].mean()), 3)
            # A change from "tied" to "ahead" also breaks exact preservation, so report
            # separately whether the proposed strategy is ever outranked.
            summary["so6_curtailment_aware_not_outranked_share"] = round(
                float(cases_df["curtailment_aware_not_outranked"].mean()), 3)
        summary["so6_reference_ranking"] = base_rank

    if pool:
        pool.shutdown()
    summary["runtime_s"] = round(time.perf_counter() - t_start, 1)
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"Done in {summary['runtime_s']} s. Outputs in {out}")
    print("REMINDER: provisional NLmin/SNSP/fleet/session parameters; inferred, not measured, curtailment.")


if __name__ == "__main__":
    main()
