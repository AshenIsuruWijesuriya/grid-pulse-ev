"""Reproducible SYNTHETIC EV sessions and plug-and-charge baseline (15-min).

This is an engineering prototype, NOT calibrated Sri Lankan EV behaviour.
Only per-session charging power and physical presence constraints are applied.
No EVSE-count/site-transformer constraint or real-world scheduling optimization.

From project root:
    .\\.venv\\Scripts\\python.exe scripts\\ev_charging_baseline.py --start-date 2024-04-01 --days 7 --sessions-per-day 20 --seed 42
"""
from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

TZ = "Asia/Colombo"
STEP_MINUTES = 15
STEP_HOURS = STEP_MINUTES / 60
NEEDED = {"session_id", "arrival_local", "departure_local", "energy_request_kwh", "max_power_kw"}


def generate_synthetic_sessions(
    start_date: str = "2024-04-01", days: int = 7, sessions_per_day: int = 20, seed: int = 42
) -> pd.DataFrame:
    """Illustrative draws ONLY; no measured Sri Lankan behaviour implied."""
    if days < 1 or sessions_per_day < 1:
        raise ValueError("days and sessions_per_day must be positive")
    rng = np.random.default_rng(seed)
    first = pd.Timestamp(start_date).tz_localize(TZ)
    records = []
    for day in range(days):
        date = first + timedelta(days=day)
        for j in range(sessions_per_day):
            # Starts on the 15-minute grid, between 07:00 and 20:45.
            arrival = date + timedelta(minutes=int(rng.integers(28, 84)) * 15)
            # Dwell: 2 to 8 hours, multiples of 15 minutes.
            dwell_slots = int(rng.integers(8, 33))
            departure = arrival + timedelta(minutes=dwell_slots * STEP_MINUTES)
            # Uncalibrated, illustrative ranges; deliberately permits some unmet energy.
            requested = round(float(rng.uniform(6.0, 32.0)), 2)
            max_kw = float(rng.choice([3.6, 7.2, 11.0], p=[0.10, 0.75, 0.15]))
            records.append({
                "session_id": f"syn-{day + 1:02d}-{j + 1:03d}",
                "arrival_local": arrival.isoformat(),
                "departure_local": departure.isoformat(),
                "energy_request_kwh": requested,
                "max_power_kw": max_kw,
                "data_origin": "synthetic_illustrative_not_calibrated",
            })
    return pd.DataFrame(records)


def validated_sessions(sessions: pd.DataFrame) -> pd.DataFrame:
    missing = NEEDED - set(sessions.columns)
    if missing:
        raise ValueError(f"Missing required session columns: {sorted(missing)}")
    if sessions.empty:
        raise ValueError("No sessions supplied")
    d = sessions.copy()
    if d["session_id"].isna().any() or d["session_id"].duplicated().any():
        raise ValueError("session_id must be non-null and unique")
    for col in ("arrival_local", "departure_local"):
        d[col] = pd.to_datetime(d[col], utc=True, errors="raise").dt.tz_convert(TZ)
        if d[col].isna().any():
            raise ValueError(f"Invalid {col}")
        if not (d[col].dt.minute.mod(STEP_MINUTES).eq(0) & d[col].dt.second.eq(0)).all():
            raise ValueError(f"{col} must align to 15-minute intervals")
    if not (d["departure_local"] > d["arrival_local"]).all():
        raise ValueError("All departure times must be after arrival")
    for col in ("energy_request_kwh", "max_power_kw"):
        d[col] = pd.to_numeric(d[col], errors="raise")
        if not np.isfinite(d[col].to_numpy(dtype=float)).all() or not d[col].gt(0).all():
            raise ValueError(f"{col} must be finite and > 0")
    return d.sort_values(["arrival_local", "session_id"]).reset_index(drop=True)


def simulate_uncontrolled(sessions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Charge every plugged-in session immediately at its maximum power until fulfilled.

    Each session is assumed to have an available EVSE, with NO site-capacity limit.
    Unmet demand caused by departure before full charge is preserved, not erased.
    """
    d = validated_sessions(sessions)
    start = d["arrival_local"].min()
    end = d["departure_local"].max()
    times = pd.date_range(start, end, freq=f"{STEP_MINUTES}min", inclusive="left", tz=TZ)
    site_kw = np.zeros(len(times), dtype=float)
    connected = np.zeros(len(times), dtype=int)
    charging = np.zeros(len(times), dtype=int)
    records = []
    for s in d.itertuples(index=False):
        first = int((s.arrival_local - start).total_seconds() // (STEP_MINUTES * 60))
        last = int((s.departure_local - start).total_seconds() // (STEP_MINUTES * 60))
        connected[first:last] += 1
        remaining = float(s.energy_request_kwh)
        delivered = 0.0
        completion = pd.NaT
        for idx in range(first, last):
            amount = min(remaining, float(s.max_power_kw) * STEP_HOURS)
            if amount <= 1e-10:
                break
            site_kw[idx] += amount / STEP_HOURS
            charging[idx] += 1
            delivered += amount
            remaining = max(0.0, remaining - amount)
            if remaining <= 1e-8:
                completion = times[idx] + timedelta(minutes=STEP_MINUTES)
                break
        records.append({
            "session_id": s.session_id,
            "arrival_local": s.arrival_local.isoformat(),
            "departure_local": s.departure_local.isoformat(),
            "requested_kwh": float(s.energy_request_kwh),
            "delivered_kwh": round(delivered, 8),
            "unmet_kwh": round(max(remaining, 0.0), 8),
            "fully_served": remaining < 1e-8,
            "completion_local": completion.isoformat() if pd.notna(completion) else None,
        })
    profile = pd.DataFrame({
        "interval_start_local": times.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "site_charging_kw": site_kw,
        "connected_sessions": connected,
        "actively_charging_sessions": charging,
    })
    outcomes = pd.DataFrame(records)
    requested = float(outcomes["requested_kwh"].sum())
    delivered = float(outcomes["delivered_kwh"].sum())
    measured = float(profile["site_charging_kw"].sum() * STEP_HOURS)
    if abs(delivered - measured) > 1e-5:
        raise AssertionError("Energy conservation failed")
    metrics = {
        "data_status": "SYNTHETIC_UNCALIBRATED_SCENARIO",
        "charging_policy": "immediate_max_power_per_session_no_site_capacity_constraint",
        "interval_minutes": STEP_MINUTES,
        "number_of_sessions": len(outcomes),
        "total_requested_kwh": round(requested, 4),
        "total_delivered_kwh": round(delivered, 4),
        "total_unmet_kwh": round(float(outcomes["unmet_kwh"].sum()), 4),
        "fully_served_sessions": int(outcomes["fully_served"].sum()),
        "fully_served_fraction": round(float(outcomes["fully_served"].mean()), 4),
        "peak_incremental_site_kw": round(float(profile["site_charging_kw"].max()), 4),
        "maximum_concurrent_connected": int(profile["connected_sessions"].max()),
    }
    return profile, outcomes, metrics


def run_experiment(outdir: Path, start_date: str = "2024-04-01", days: int = 7,
                   sessions_per_day: int = 20, seed: int = 42) -> dict:
    sessions = generate_synthetic_sessions(start_date, days, sessions_per_day, seed)
    profile, outcomes, metrics = simulate_uncontrolled(sessions)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    sessions.to_csv(outdir / "synthetic_ev_sessions.csv", index=False)
    profile.to_csv(outdir / "uncontrolled_site_load_15min.csv", index=False)
    outcomes.to_csv(outdir / "uncontrolled_session_outcomes.csv", index=False)
    metrics.update({"seed": seed, "start_date": start_date, "days": days,
                    "sessions_per_day": sessions_per_day})
    (outdir / "uncontrolled_baseline_metrics.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    return metrics


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start-date", default="2024-04-01")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--sessions-per-day", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--outdir", type=Path, default=Path("results/ev_baseline"))
    args = ap.parse_args()
    metrics = run_experiment(args.outdir, args.start_date, args.days,
                             args.sessions_per_day, args.seed)
    print(json.dumps(metrics, indent=2))
    print(f"Saved 4 diagnostic outputs to {args.outdir}")
    print("IMPORTANT: this is a synthetic, uncalibrated EV scenario; NOT a measured Sri Lankan fleet.")


if __name__ == "__main__":
    main()
