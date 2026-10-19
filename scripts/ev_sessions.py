"""Public EV charging-session sets for the scheduling experiments (SO3).

Two generators share one output schema:

parametric  Uniform draws within configured ranges. An uncalibrated FALLBACK,
            used only until an empirical dataset is available.
gmm         Gaussian Mixture Model over (arrival hour, dwell hours, energy kWh)
            fitted to ACN-Data sessions, the approach used by ACN-Sim
            (Lee et al. 2021). Component count is chosen by BIC.

Rules applied to every generated session
- Times sit on the 15-minute grid; dwell is at least one interval.
- Energy is capped at what the charger can deliver while plugged in
  (max_power_kw x dwell). Without this a 100%-completion constraint is
  infeasible by construction; capped sessions are flagged, not hidden.
- Each session is assigned to a station; stations share a connection limit.
- declares_departure marks sessions whose driver states a departure time.
  Sessions without one cannot be shifted and charge on arrival (SO6).

ACN-Data requires a free account and API token (https://ev.caltech.edu).
Download sessions as JSON and pass the file to load_acn_sessions().
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

TZ = "Asia/Colombo"
STEP_MINUTES = 15
STEP_HOURS = STEP_MINUTES / 60
SLOTS_PER_DAY = 24 * 60 // STEP_MINUTES


# --------------------------------------------------------------------------- #
# Empirical source: ACN-Data
# --------------------------------------------------------------------------- #

def load_acn_sessions(path: Path) -> pd.DataFrame:
    """ACN-Data JSON export -> arrival_hour, duration_h, energy_kwh (site local time).

    kWhDelivered is MEASURED delivered energy, used here as the energy each
    session needed; it is not relabelled as the driver's requested energy.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw["_items"] if isinstance(raw, dict) else raw
    rows = []
    for it in items:
        tz = it.get("timezone") or "America/Los_Angeles"
        start = pd.to_datetime(it["connectionTime"], utc=True).tz_convert(tz)
        end = pd.to_datetime(it["disconnectTime"], utc=True).tz_convert(tz)
        rows.append({
            "session_id": it.get("sessionID") or it.get("_id"),
            "site_id": it.get("siteID"),
            "arrival_hour": start.hour + start.minute / 60,
            "duration_h": (end - start).total_seconds() / 3600,
            "energy_kwh": float(it["kWhDelivered"]),
            "weekday": start.dayofweek < 5,
        })
    df = pd.DataFrame(rows)
    keep = (df["duration_h"] > 0) & (df["duration_h"] <= 24) & (df["energy_kwh"] > 0)
    return df[keep].reset_index(drop=True)


def fit_gmm(features: pd.DataFrame, max_components: int = 8, seed: int = 42):
    """GaussianMixture on (arrival_hour, duration_h, energy_kwh), n chosen by BIC."""
    from sklearn.mixture import GaussianMixture

    x = features[["arrival_hour", "duration_h", "energy_kwh"]].to_numpy(dtype=float)
    if len(x) < 10:
        raise ValueError("Need at least 10 sessions to fit a GMM")
    best, best_bic = None, np.inf
    for k in range(1, min(max_components, len(x) // 5) + 1):
        model = GaussianMixture(n_components=k, covariance_type="full",
                                random_state=seed).fit(x)
        bic = model.bic(x)
        if bic < best_bic:
            best, best_bic = model, bic
    return best


def _sample_gmm(model, n: int, rng: np.random.Generator) -> np.ndarray:
    """Rejection-sample n physically valid rows from a fitted GMM."""
    out = np.empty((0, 3))
    while len(out) < n:
        draw, _ = model.sample(max(2 * n, 100))
        draw = draw[rng.permutation(len(draw))]
        ok = ((draw[:, 0] >= 0) & (draw[:, 0] < 24) & (draw[:, 1] >= STEP_HOURS)
              & (draw[:, 1] <= 24) & (draw[:, 2] > 0.5))
        out = np.vstack([out, draw[ok]])
    return out[:n]


# --------------------------------------------------------------------------- #
# Session sets
# --------------------------------------------------------------------------- #

def local_midnights(days: list) -> pd.DatetimeIndex:
    """Dates, strings or timestamps -> local midnights in Asia/Colombo."""
    out = []
    for d in days:
        ts = pd.Timestamp(d)
        ts = ts.tz_convert(TZ) if ts.tzinfo else ts.tz_localize(TZ)
        out.append(ts.normalize())
    return pd.DatetimeIndex(out)


def _frame(days: list, arrival_hour: np.ndarray, dwell_h: np.ndarray,
           energy: np.ndarray, power: np.ndarray, per_day: int, origin: str) -> pd.DataFrame:
    arr_slot = np.clip(np.round(arrival_hour * 4).astype(int), 0, SLOTS_PER_DAY - 1)
    dwell_slots = np.maximum(1, np.round(dwell_h * 4).astype(int))
    deliverable = power * dwell_slots * STEP_HOURS
    capped = energy > deliverable
    energy = np.round(np.minimum(energy, deliverable), 4)
    day_start = local_midnights(days).repeat(per_day)
    arrival = day_start + pd.to_timedelta(arr_slot * STEP_MINUTES, unit="min")
    departure = arrival + pd.to_timedelta(dwell_slots * STEP_MINUTES, unit="min")
    return pd.DataFrame({
        "session_id": [f"{d:%Y%m%d}-{i % per_day + 1:05d}" for i, d in enumerate(day_start)],
        "day": day_start.strftime("%Y-%m-%d"),
        "arrival_local": arrival,
        "departure_local": departure,
        "energy_request_kwh": energy,
        "max_power_kw": power,
        "energy_capped": capped,
        "data_origin": origin,
    })


def parametric_sessions(days: list, per_day: int, params: dict,
                        rng: np.random.Generator) -> pd.DataFrame:
    n = len(days) * per_day
    lo, hi = params["arrival_hour_range"]
    arrival = rng.integers(int(lo * 4), int(hi * 4), n) / 4
    dlo, dhi = params["dwell_hours_range"]
    dwell = rng.integers(int(dlo * 4), int(dhi * 4) + 1, n) / 4
    energy = rng.uniform(*params["energy_kwh_range"], n)
    power = rng.choice(params["charger_kw"], n, p=params["charger_probability"])
    return _frame(days, arrival, dwell, energy, power, per_day,
                  "synthetic_parametric_uncalibrated")


def gmm_sessions(model, days: list, per_day: int, charger_kw: list,
                 charger_probability: list, rng: np.random.Generator) -> pd.DataFrame:
    x = _sample_gmm(model, len(days) * per_day, rng)
    power = rng.choice(charger_kw, len(x), p=charger_probability)
    return _frame(days, x[:, 0], x[:, 1], x[:, 2], power, per_day,
                  "synthetic_gmm_fitted_acn_data")


def assign_stations(sessions: pd.DataFrame, per_day: int, sessions_per_station: int,
                    rng: np.random.Generator) -> pd.DataFrame:
    n_stations = max(1, int(np.ceil(per_day / sessions_per_station)))
    out = sessions.copy()
    out["station_id"] = rng.integers(0, n_stations, len(out))
    return out


def declare_departures(sessions: pd.DataFrame, fraction: float,
                       rng: np.random.Generator) -> pd.DataFrame:
    if not 0 <= fraction <= 1:
        raise ValueError("declared_departure_fraction must be within [0, 1]")
    out = sessions.copy()
    out["declares_departure"] = rng.random(len(out)) < fraction
    return out


def build_session_set(days: list, cfg: dict, seed: int, gmm_model=None,
                      declared_fraction: float | None = None) -> pd.DataFrame:
    """Complete session set for `days` from the central configuration."""
    central, sess = cfg["central"], cfg["sessions"]
    rng = np.random.default_rng(seed)
    per_day = int(central["sessions_per_day"])
    p = sess["parametric"]
    if gmm_model is not None:
        df = gmm_sessions(gmm_model, days, per_day, p["charger_kw"],
                          p["charger_probability"], rng)
    else:
        df = parametric_sessions(days, per_day, p, rng)
    df = assign_stations(df, per_day, int(central["sessions_per_station_per_day"]), rng)
    # A separate stream keeps the declared flags identical for every other
    # setting of the sweep, so only the swept fraction changes.
    frac = central["declared_departure_fraction"] if declared_fraction is None else declared_fraction
    return declare_departures(df, frac, np.random.default_rng(seed + 1))


def plausibility(sessions: pd.DataFrame, kwh_per_km: float) -> dict:
    """Implied driving distance per session (proposal §3.5 input validity)."""
    km = sessions["energy_request_kwh"] / kwh_per_km
    dwell_h = (sessions["departure_local"] - sessions["arrival_local"]).dt.total_seconds() / 3600
    return {
        "sessions": len(sessions),
        "kwh_per_km_assumed": kwh_per_km,
        "implied_km_median": round(float(km.median()), 1),
        "implied_km_p95": round(float(km.quantile(0.95)), 1),
        "energy_kwh_mean": round(float(sessions["energy_request_kwh"].mean()), 2),
        "dwell_h_mean": round(float(dwell_h.mean()), 2),
        "energy_capped_share": round(float(sessions["energy_capped"].mean()), 4),
        "declared_departure_share": round(float(sessions["declares_departure"].mean()), 4),
        "data_origin": sorted(sessions["data_origin"].unique().tolist()),
    }
