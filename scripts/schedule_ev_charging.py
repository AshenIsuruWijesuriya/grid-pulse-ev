"""Charging strategies, LP scheduler, constraint audit and metrics (SO4/SO5).

Strategies, all on the same sessions and grid hours
    uncontrolled       charge on arrival at max power until full or departed;
                       stations share their connection limit proportionally
    solar_following    LP: put energy in the hours with most available solar,
                       ignoring whether that solar is at risk of curtailment
    curtailment_aware  LP: minimise  sum_h c(h)                 [MWh]
                                    + w_peak  * peak           [MW]
                                    + w_delay * sum p*dt*delay [MWh*h]
                       where c(h) >= NLmin - NL(h) - L(h)
                             c(h) >= VRE(h) - snsp*(D(h) + L(h))   (if SNSP set)
                             peak >= NL(h) + L(h)
                       i.e. curtailment left after EV load L(h), in the
                       epigraph form of max(0, ...), so the LP is exact.

Hard constraints in both LPs (proposal FR10)
    0 <= p[s,t] <= max_power_kw[s]   only while plugged in
    sum_t p[s,t] * 0.25 h = energy_kwh[s]   (100 % completion)
    sum_{s at station k} p[s,t] <= station capacity   for every k, t
Sessions without a declared departure cannot be shifted: they charge on
arrival. Two station rules decide who gets the connection first:
    arrival_first   undeclared load is fixed before the LP runs and takes
                    its share of station capacity (the original rule)
    declared_first  declared sessions are planned with the full station
                    capacity and keep their 100 % guarantee; undeclared
                    sessions then charge on arrival in what is left, with no
                    guarantee. The LP uses their on-arrival load only as its
                    estimate of their effect on the grid.
An infeasible LP is REPORTED, never relaxed (README: report infeasibility,
do not hide it).

Solver: HiGHS through scipy.optimize.linprog. Every returned schedule is
re-checked by audit_schedule(), which does not trust the solver.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import linprog

STEP_HOURS = 0.25
SLOTS_PER_HOUR = 4
KW_SLOT_TO_MW_HOUR = 1 / (SLOTS_PER_HOUR * 1000)   # hourly mean MW per kW in one slot
TIE_BREAK = 1e-4                                     # prefer earlier charging on ties
TOL_KWH = 1e-3
TOL_KW = 1e-3
STATION_RULES = ("arrival_first", "declared_first")
STATUS = {0: "optimal", 1: "iteration_or_time_limit", 2: "infeasible",
          3: "unbounded", 4: "numerical_difficulty"}


@dataclass
class DayProblem:
    day: pd.Timestamp
    sessions: pd.DataFrame
    slots: pd.DatetimeIndex
    hours: pd.DatetimeIndex
    slot_hour: np.ndarray
    arr: np.ndarray
    dep: np.ndarray
    energy: np.ndarray
    pmax: np.ndarray
    station: np.ndarray
    declared: np.ndarray
    station_capacity_kw: float

    @property
    def n_slots(self) -> int:
        return len(self.slots)


def day_problem(day: pd.Timestamp, sessions: pd.DataFrame,
                station_capacity_kw: float) -> DayProblem:
    s = sessions.reset_index(drop=True)
    start = day
    end = max(s["departure_local"].max(), start + pd.offsets.Day(1))
    end = end.ceil("h")
    slots = pd.date_range(start, end, freq="15min", inclusive="left")
    hours = pd.date_range(start, end, freq="h", inclusive="left")
    to_slot = lambda col: ((s[col] - start).dt.total_seconds() // 900).astype(int).to_numpy()
    return DayProblem(
        day=day, sessions=s, slots=slots, hours=hours,
        slot_hour=np.arange(len(slots)) // SLOTS_PER_HOUR,
        arr=to_slot("arrival_local"), dep=to_slot("departure_local"),
        energy=s["energy_request_kwh"].to_numpy(dtype=float),
        pmax=s["max_power_kw"].to_numpy(dtype=float),
        station=s["station_id"].to_numpy(dtype=int),
        declared=s["declares_departure"].to_numpy(dtype=bool),
        station_capacity_kw=float(station_capacity_kw),
    )


# --------------------------------------------------------------------------- #
# Uncontrolled charging
# --------------------------------------------------------------------------- #

def asap_profile(prob: DayProblem, mask: np.ndarray,
                 fixed_station_kw: np.ndarray | None = None) -> np.ndarray:
    """Charge-on-arrival at max power, with proportional sharing of station capacity."""
    n_st = int(prob.station.max()) + 1
    P = np.zeros((len(prob.arr), prob.n_slots))
    rem = np.where(mask, prob.energy, 0.0)
    for t in range(prob.n_slots):
        idx = np.flatnonzero((prob.arr <= t) & (t < prob.dep) & (rem > 1e-9))
        if not len(idx):
            continue
        want = np.minimum(prob.pmax[idx], rem[idx] / STEP_HOURS)
        load = np.bincount(prob.station[idx], weights=want, minlength=n_st)
        avail = prob.station_capacity_kw - (0 if fixed_station_kw is None else fixed_station_kw[:, t])
        scale = np.ones(n_st)
        np.divide(np.clip(avail, 0, None), load, out=scale, where=(load > avail) & (load > 0))
        p = want * np.clip(scale, 0, 1)[prob.station[idx]]
        P[idx, t] = p
        rem[idx] -= p * STEP_HOURS
    return P


def station_load(prob: DayProblem, P: np.ndarray) -> np.ndarray:
    n_st = int(prob.station.max()) + 1
    out = np.zeros((n_st, prob.n_slots))
    np.add.at(out, prob.station, P)
    return out


# --------------------------------------------------------------------------- #
# LP strategies
# --------------------------------------------------------------------------- #

def plan_grid(signal, hours: pd.DatetimeIndex):
    """The planning grid over `hours`; a list of scenario signals gives a list of grids."""
    if isinstance(signal, list):
        return [plan_grid(s, hours) for s in signal]
    g = signal.reindex(hours)
    need = ["net_load_mw", "demand_system_mw", "vre_mw", "solar_mw", "nlmin_mw"]
    if g[need].isna().any().any():
        raise ValueError("grid signal missing for part of the scheduling horizon")
    return g


def solve_lp(prob: DayProblem, grid, mode: str, weights: dict,
             fixed_P: np.ndarray, fixed_holds_capacity: bool = True) -> tuple[np.ndarray, str, float]:
    """Optimise declared sessions; fixed_P holds the undeclared (on-arrival) load.

    `grid` is one planning grid, or a list of scenario grids for the
    curtailment-aware LP (see solve_lp_scenarios). With fixed_holds_capacity
    False, fixed_P counts on the grid but not against station capacity
    (declared_first rule).
    """
    if isinstance(grid, list):
        if mode != "curtailment_aware":
            raise ValueError("scenario planning is defined for curtailment_aware only")
        return solve_lp_scenarios(prob, grid, weights, fixed_P, fixed_holds_capacity)
    ids = np.flatnonzero(prob.declared)
    P = fixed_P.copy()
    if not len(ids):
        return P, "optimal", 0.0
    a, d = prob.arr[ids], prob.dep[ids]
    lens = d - a
    nx = int(lens.sum())
    var_sess = np.repeat(np.arange(len(ids)), lens)
    offsets = np.repeat(np.cumsum(lens) - lens, lens)
    var_slot = np.arange(nx) - offsets + np.repeat(a, lens)
    var_hour = prob.slot_hour[var_slot]
    H = len(prob.hours)
    n = nx + H + 1                                    # x, c(h), peak
    i_c, i_peak = nx, nx + H

    fixed_station = station_load(prob, fixed_P if fixed_holds_capacity else np.zeros_like(fixed_P))
    fixed_hour_mw = np.bincount(prob.slot_hour, weights=fixed_P.sum(axis=0),
                                minlength=H) * KW_SLOT_TO_MW_HOUR
    delay_h = (var_slot - a[var_sess]) * STEP_HOURS

    cost = np.zeros(n)
    rows, cols, vals, rhs = [], [], [], []
    r0 = 0

    # Station capacity, one row per (station, slot) that has a variable.
    key = prob.station[ids][var_sess] * prob.n_slots + var_slot
    ukey, row = np.unique(key, return_inverse=True)
    rows.append(row); cols.append(np.arange(nx)); vals.append(np.ones(nx))
    cap = prob.station_capacity_kw - fixed_station[ukey // prob.n_slots, ukey % prob.n_slots]
    rhs.append(np.clip(cap, 0, None))
    r0 += len(ukey)

    if mode == "curtailment_aware":
        nl = grid["net_load_mw"].to_numpy()
        dem = grid["demand_system_mw"].to_numpy()
        vre = grid["vre_mw"].to_numpy()
        nlmin = grid["nlmin_mw"].to_numpy()
        snsp = grid["snsp_max"].iloc[0]
        hrs = np.arange(H)
        # c(h) >= NLmin - NL - Lfix - L   ->  -c - k*x <= -(NLmin - NL - Lfix)
        rows += [r0 + var_hour, r0 + hrs]
        cols += [np.arange(nx), i_c + hrs]
        vals += [np.full(nx, -KW_SLOT_TO_MW_HOUR), -np.ones(H)]
        rhs.append(-(nlmin - nl - fixed_hour_mw)); r0 += H
        if pd.notna(snsp):
            # c(h) >= VRE - snsp*(D + Lfix + L)
            rows += [r0 + var_hour, r0 + hrs]
            cols += [np.arange(nx), i_c + hrs]
            vals += [np.full(nx, -snsp * KW_SLOT_TO_MW_HOUR), -np.ones(H)]
            rhs.append(-(vre - snsp * (dem + fixed_hour_mw))); r0 += H
        # peak >= NL + Lfix + L
        rows += [r0 + var_hour, r0 + hrs]
        cols += [np.arange(nx), np.full(H, i_peak)]
        vals += [np.full(nx, KW_SLOT_TO_MW_HOUR), -np.ones(H)]
        rhs.append(-(nl + fixed_hour_mw)); r0 += H
        cost[i_c:i_c + H] = 1.0
        cost[i_peak] = weights.get("peak", 0.0)
        cost[:nx] = (weights.get("delay", 0.0) + TIE_BREAK) * STEP_HOURS * delay_h / 1000
    elif mode == "solar_following":
        solar = grid["solar_mw"].to_numpy()
        cost[:nx] = (-solar[var_hour] / 1000 + TIE_BREAK * delay_h / 1000) * STEP_HOURS
    else:
        raise ValueError(f"unknown LP mode {mode}")

    A_ub = sparse.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                             shape=(r0, n))
    A_eq = sparse.csr_matrix((np.full(nx, STEP_HOURS), (var_sess, np.arange(nx))),
                             shape=(len(ids), n))
    lo = np.zeros(n); hi = np.empty(n)
    hi[:nx] = prob.pmax[ids][var_sess]
    hi[i_c:i_c + H] = np.inf if mode == "curtailment_aware" else 0.0
    lo[i_peak], hi[i_peak] = (-np.inf, np.inf) if mode == "curtailment_aware" else (0.0, 0.0)

    t0 = time.perf_counter()
    res = linprog(cost, A_ub=A_ub, b_ub=np.concatenate(rhs), A_eq=A_eq,
                  b_eq=prob.energy[ids], bounds=np.column_stack([lo, hi]),
                  method="highs", options={"time_limit": 300})
    runtime = time.perf_counter() - t0
    status = STATUS.get(res.status, f"status_{res.status}")
    if res.status != 0:
        return P, status, runtime
    x = np.clip(res.x[:nx], 0, None)
    P[ids[var_sess], var_slot] = x
    return P, status, runtime


def solve_lp_scenarios(prob: DayProblem, grids: list, weights: dict, fixed_P: np.ndarray,
                       fixed_holds_capacity: bool = True) -> tuple[np.ndarray, str, float]:
    """Curtailment-aware LP over K grid scenarios (sample average approximation).

        minimise  (1/K) sum_k [ sum_h c_k(h) + w_peak * peak_k ]  + w_delay * delay
        c_k(h)  >= NLmin - NL_k(h) - Lfix(h) - L(h)
        c_k(h)  >= VRE_k(h) - snsp*(D_k(h) + Lfix(h) + L(h))   (if SNSP set)
        peak_k  >= NL_k(h) + Lfix(h) + L(h)
        L(h)     = hourly MW of the declared sessions' schedule

    One schedule serves every scenario: it is fixed at 00:00 and cannot see
    which grid occurs. L(h) is a separate variable so each scenario adds rows
    with two entries instead of one entry per session slot. With one scenario
    the optimum equals that of solve_lp.
    """
    ids = np.flatnonzero(prob.declared)
    P = fixed_P.copy()
    if not len(ids):
        return P, "optimal", 0.0
    a, d = prob.arr[ids], prob.dep[ids]
    lens = d - a
    nx = int(lens.sum())
    var_sess = np.repeat(np.arange(len(ids)), lens)
    offsets = np.repeat(np.cumsum(lens) - lens, lens)
    var_slot = np.arange(nx) - offsets + np.repeat(a, lens)
    var_hour = prob.slot_hour[var_slot]
    H, K = len(prob.hours), len(grids)
    i_L, i_c, i_peak = nx, nx + H, nx + H + K * H
    n = i_peak + K
    hrs = np.arange(H)

    fixed_station = station_load(prob, fixed_P if fixed_holds_capacity else np.zeros_like(fixed_P))
    fixed_hour_mw = np.bincount(prob.slot_hour, weights=fixed_P.sum(axis=0),
                                minlength=H) * KW_SLOT_TO_MW_HOUR
    delay_h = (var_slot - a[var_sess]) * STEP_HOURS

    rows, cols, vals, rhs = [], [], [], []
    key = prob.station[ids][var_sess] * prob.n_slots + var_slot
    ukey, row = np.unique(key, return_inverse=True)
    rows.append(row); cols.append(np.arange(nx)); vals.append(np.ones(nx))
    rhs.append(np.clip(prob.station_capacity_kw
                       - fixed_station[ukey // prob.n_slots, ukey % prob.n_slots], 0, None))
    r0 = len(ukey)
    for k, g in enumerate(grids):
        nl = g["net_load_mw"].to_numpy()
        ck = i_c + k * H + hrs
        # c_k >= NLmin - NL_k - Lfix - L   ->  -c_k - L <= -(NLmin - NL_k - Lfix)
        rows += [r0 + hrs, r0 + hrs]; cols += [ck, i_L + hrs]; vals += [-np.ones(H), -np.ones(H)]
        rhs.append(-(g["nlmin_mw"].to_numpy() - nl - fixed_hour_mw)); r0 += H
        snsp = g["snsp_max"].iloc[0]
        if pd.notna(snsp):
            rows += [r0 + hrs, r0 + hrs]; cols += [ck, i_L + hrs]; vals += [-np.ones(H), np.full(H, -snsp)]
            rhs.append(-(g["vre_mw"].to_numpy() - snsp * (g["demand_system_mw"].to_numpy() + fixed_hour_mw)))
            r0 += H
        # peak_k >= NL_k + Lfix + L   ->  L - peak_k <= -(NL_k + Lfix)
        rows += [r0 + hrs, r0 + hrs]; cols += [i_L + hrs, np.full(H, i_peak + k)]
        vals += [np.ones(H), -np.ones(H)]
        rhs.append(-(nl + fixed_hour_mw)); r0 += H

    cost = np.zeros(n)
    cost[:nx] = (weights.get("delay", 0.0) + TIE_BREAK) * STEP_HOURS * delay_h / 1000
    cost[i_c:i_peak] = 1.0 / K
    cost[i_peak:] = weights.get("peak", 0.0) / K

    A_ub = sparse.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                             shape=(r0, n))
    # Session energy, then L(h) = sum of the hour's slots.
    A_eq = sparse.csr_matrix((np.concatenate([np.full(nx, STEP_HOURS), np.full(nx, KW_SLOT_TO_MW_HOUR),
                                              -np.ones(H)]),
                              (np.concatenate([var_sess, len(ids) + var_hour, len(ids) + hrs]),
                               np.concatenate([np.arange(nx), np.arange(nx), i_L + hrs]))),
                             shape=(len(ids) + H, n))
    b_eq = np.concatenate([prob.energy[ids], np.zeros(H)])
    lo, hi = np.zeros(n), np.full(n, np.inf)
    hi[:nx] = prob.pmax[ids][var_sess]
    lo[i_peak:] = -np.inf

    t0 = time.perf_counter()
    res = linprog(cost, A_ub=A_ub, b_ub=np.concatenate(rhs), A_eq=A_eq, b_eq=b_eq,
                  bounds=np.column_stack([lo, hi]), method="highs", options={"time_limit": 300})
    runtime = time.perf_counter() - t0
    status = STATUS.get(res.status, f"status_{res.status}")
    if res.status != 0:
        return P, status, runtime
    P[ids[var_sess], var_slot] = np.clip(res.x[:nx], 0, None)
    return P, status, runtime


def run_strategy(prob: DayProblem, strategy: str, grid: pd.DataFrame | None,
                 weights: dict, rule: str = "arrival_first") -> dict:
    if rule not in STATION_RULES:
        raise ValueError(f"unknown station rule {rule}")
    t0 = time.perf_counter()
    if strategy == "uncontrolled":
        P = asap_profile(prob, np.ones(len(prob.arr), dtype=bool))
        return {"P": P, "status": "simulated", "solver_s": 0.0,
                "wall_s": time.perf_counter() - t0}
    fixed = asap_profile(prob, ~prob.declared)
    P, status, solver_s = solve_lp(prob, grid, strategy, weights, fixed,
                                   fixed_holds_capacity=(rule == "arrival_first"))
    if rule == "declared_first" and status == "optimal":
        # Undeclared sessions charge on arrival in the capacity the plan leaves.
        P[~prob.declared] = 0.0
        P += asap_profile(prob, ~prob.declared, fixed_station_kw=station_load(prob, P))
    return {"P": P, "status": status, "solver_s": solver_s,
            "wall_s": time.perf_counter() - t0}


# --------------------------------------------------------------------------- #
# Audit and session outcomes (independent of the solver)
# --------------------------------------------------------------------------- #

def audit_schedule(prob: DayProblem, P: np.ndarray, require_full) -> dict:
    """require_full: bool or per-session mask of sessions that must be fully served."""
    t = np.arange(prob.n_slots)
    outside = (t[None, :] < prob.arr[:, None]) | (t[None, :] >= prob.dep[:, None])
    delivered = P.sum(axis=1) * STEP_HOURS
    over_station = station_load(prob, P) - prob.station_capacity_kw
    v = {
        "negative_power": int((P < -TOL_KW).sum()),
        "above_charger_rating": int((P > prob.pmax[:, None] + TOL_KW).sum()),
        "charging_while_unplugged": int((np.abs(P) * outside > TOL_KW).sum()),
        "over_delivery": int((delivered > prob.energy + TOL_KWH).sum()),
        "station_capacity_exceeded": int((over_station > TOL_KW).sum()),
        "energy_not_delivered": int(((delivered < prob.energy - TOL_KWH)
                                     & np.broadcast_to(require_full, delivered.shape)).sum()),
    }
    v["total"] = sum(v.values())
    return v


def session_outcomes(prob: DayProblem, P: np.ndarray) -> pd.DataFrame:
    delivered = P.sum(axis=1) * STEP_HOURS
    done = delivered >= prob.energy - TOL_KWH
    active = P > TOL_KW
    last = np.where(active.any(axis=1), prob.n_slots - np.argmax(active[:, ::-1], axis=1), prob.arr)
    need = np.ceil(prob.energy / (prob.pmax * STEP_HOURS) - 1e-9).astype(int)
    delay = np.where(done, (last - (prob.arr + need)) * 15.0, np.nan)
    return pd.DataFrame({
        "session_id": prob.sessions["session_id"],
        "requested_kwh": prob.energy,
        "delivered_kwh": delivered,
        "completed": done,
        "delay_min": np.clip(delay, 0, None),
        "shiftable": prob.declared & (prob.dep - prob.arr > need),
        "declared": prob.declared,
    })


def hourly_load_mw(prob: DayProblem, P: np.ndarray) -> pd.Series:
    mw = np.bincount(prob.slot_hour, weights=P.sum(axis=0),
                     minlength=len(prob.hours)) * KW_SLOT_TO_MW_HOUR
    return pd.Series(mw, index=prob.hours)


# --------------------------------------------------------------------------- #
# Metrics (proposal Table 3.3)
# --------------------------------------------------------------------------- #

def curtailment_with_load(signal: pd.DataFrame, load_mw: pd.Series) -> pd.Series:
    L = load_mw.reindex(signal.index, fill_value=0.0)
    floor = signal["nlmin_mw"] - signal["net_load_mw"] - L
    snsp = signal["snsp_max"]
    s_term = (signal["vre_mw"] - snsp * (signal["demand_system_mw"] + L)).fillna(0.0)
    return pd.concat([floor, s_term], axis=1).max(axis=1).clip(lower=0)


def evaluate(days: list, signal: pd.DataFrame, load_mw: pd.Series,
             outcomes: pd.DataFrame, audits: list, statuses: list,
             runtimes: list, evening_hours: tuple) -> dict:
    """Grid and service metrics, always reported together."""
    day_hours = pd.DatetimeIndex(np.concatenate(
        [pd.date_range(d, periods=24, freq="h") for d in days]))
    hours = day_hours.union(load_mw.index[load_mw > 0])
    sig = signal.reindex(hours)
    L = load_mw.reindex(hours, fill_value=0.0)
    c0 = sig["curtailment_risk_mw"]
    c1 = curtailment_with_load(sig, L)
    nl = sig["net_load_mw"]
    peaks, eve = [], []
    for d in days:
        h = pd.date_range(d, periods=24, freq="h")
        e = h[(h.hour >= evening_hours[0]) & (h.hour <= evening_hours[1])]
        peaks.append(((nl + L)[h].max(), nl[h].max()))
        eve.append(((nl + L)[e].max(), nl[e].max()))
    peaks, eve = np.array(peaks), np.array(eve)
    done = outcomes["completed"]
    return {
        "days": len(days),
        "sessions": len(outcomes),
        "ev_energy_mwh": round(float(L.sum()), 2),
        "curtailment_risk_mwh": round(float(c0.sum()), 2),
        "absorbed_mwh": round(float((c0 - c1).sum()), 2),
        "unabsorbed_mwh": round(float(c1.sum()), 2),
        "absorbed_share_of_ev_energy": round(float((c0 - c1).sum() / L.sum()), 4) if L.sum() else None,
        "mean_daily_peak_mw": round(float(peaks[:, 0].mean()), 2),
        "mean_daily_peak_increase_mw": round(float((peaks[:, 0] - peaks[:, 1]).mean()), 2),
        "mean_evening_peak_mw": round(float(eve[:, 0].mean()), 2),
        "mean_evening_peak_increase_mw": round(float((eve[:, 0] - eve[:, 1]).mean()), 2),
        "completion_rate": round(float(done.mean()), 4),
        "undeclared_completion_rate": (round(float(done[~outcomes["declared"]].mean()), 4)
                                       if (~outcomes["declared"]).any() else None),
        "unmet_kwh": round(float((outcomes["requested_kwh"] - outcomes["delivered_kwh"]).clip(lower=0).sum()), 2),
        "mean_delay_min": round(float(outcomes.loc[done, "delay_min"].mean()), 1),
        "p95_delay_min": round(float(outcomes.loc[done, "delay_min"].quantile(0.95)), 1),
        "shiftable_fraction": round(float(outcomes["shiftable"].mean()), 4),
        "constraint_violations": int(sum(a["total"] for a in audits)),
        "solver_status": {k: int(v) for k, v in pd.Series(statuses).value_counts().items()},
        "solver_s_total": round(float(sum(runtimes)), 2),
        "solver_s_max_day": round(float(max(runtimes)), 2) if runtimes else 0.0,
    }
