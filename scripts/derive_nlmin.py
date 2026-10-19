"""Derive the minimum secure net-demand level NLmin (SO2, Appendix C §3-§8).

NLmin is the lowest output the dispatchable synchronous fleet (coal, oil,
major hydro) can run at while the system stays secure. It is built from
cited CEB planning parameters plus observed 2024 operating practice:

    NLmin = n_coal x P_min,coal + H_min

    P_min,coal  150 MW per Lakvijaya unit (CEB LTGEP 2025-2044, Table 2.7)
    n_coal      coal units kept online in the most stressed hours (observed)
    H_min       major-hydro output kept online in those hours (observed;
                operating practice for frequency regulation, not a limit)
    oil         0 MW: oil was fully off in stressed hours (observed)

The inertia requirement is NOT added as MW (that would double-count the
same running units). It is a separate SNSP limit in C(t); the LTGEP plans
for SNSP <= 75 % (pp. 7-7, 7-13, 10-14).

Checks at the stressed hours (LTGEP assumptions, p. 7-13):
    spinning reserve 5 % of demand <= upward headroom of the committed coal
    no single unit above 25 % of demand
    observed dispatchable output never ran below the derived floor

Stressed hours: the lowest `stress_quantile` of observed hourly dispatchable
output among complete 2024 hours.

Usage
    .venv\\Scripts\\python.exe scripts\\derive_nlmin.py
Writes results/grid_scenario/nlmin_derivation.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import build_grid_scenario as bgs

TZ = "Asia/Colombo"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_hours(year: int) -> pd.DataFrame:
    """Complete, non-event hours with observed sources and boundary-corrected demand."""
    g = PROJECT_ROOT / "data/processed/grid"
    base = pd.read_csv(g / f"grid_base_{year}_hourly.csv")
    hourly = pd.read_csv(g / f"pucsl_{year}_hourly_net_demand.csv")
    for df in (base, hourly):
        df.index = pd.to_datetime(df["hour_start_local"], utc=True).dt.tz_convert(TZ)
    ok = (base["hour_status"] == "complete") & ~base["is_system_event_day"]
    x = hourly.loc[ok, ["coal_mw", "oil_ceb_mw", "oil_ipp_mw", "major_hydro_mw"]].copy()
    x["dispatchable_mw"] = base.loc[ok, "dispatchable_observed_mw"]
    x["demand_system_mw"] = base.loc[ok, "demand_system_mw"]
    x["vre_mw"] = base.loc[ok, "solar_available_mw"] + base.loc[ok, "wind_mw"]
    return x


def derive(hours: pd.DataFrame, p: dict) -> dict:
    stress = hours[hours["dispatchable_mw"] <= hours["dispatchable_mw"].quantile(p["stress_quantile"])]
    # Fewest coal units consistent with the observed output.
    units = np.ceil(stress["coal_mw"] / p["coal_unit_net_mw"]).astype(int)
    n_mode = int(units.mode().iloc[0])
    hydro = stress["major_hydro_mw"]
    oil = stress["oil_ceb_mw"] + stress["oil_ipp_mw"]
    pmin = p["coal_unit_min_mw"]

    scen = {
        "low": {"coal_units": 1, "hydro_basis": "minimum", "hydro_mw": float(hydro.min())},
        "central": {"coal_units": n_mode, "hydro_basis": "10th percentile", "hydro_mw": float(hydro.quantile(0.10))},
        "high": {"coal_units": p["coal_units_installed"], "hydro_basis": "median", "hydro_mw": float(hydro.median())},
    }
    for s in scen.values():
        s["coal_floor_mw"] = s["coal_units"] * pmin
        s["nlmin_mw"] = round(s["coal_floor_mw"] + s["hydro_mw"], 1)

    c = scen["central"]
    headroom = c["coal_units"] * (p["coal_unit_net_mw"] - pmin)
    reserve = p["spinning_reserve_share_of_demand"] * stress["demand_system_mw"]
    lo, mid, hi = (scen[k]["nlmin_mw"] for k in ("low", "central", "high"))
    sweep = sorted({int(round(v / 10) * 10) for v in (lo, (lo + mid) / 2, mid, (mid + hi) / 2, hi)})
    snsp_obs = (hours["vre_mw"] / hours["demand_system_mw"])
    return {
        "status": "Derived from cited CEB planning parameters and observed 2024 operating practice; "
                  "awaiting CEB System Control review (proposal §3.5)",
        "formula": "NLmin = coal units online x coal minimum operating level + major hydro kept online",
        "parameters": p,
        "stressed_hours": {
            "definition": f"lowest {p['stress_quantile']:.0%} of observed hourly dispatchable output, complete 2024 hours",
            "count": int(len(stress)),
            "dispatchable_threshold_mw": round(float(hours["dispatchable_mw"].quantile(p["stress_quantile"])), 1),
            "coal_units_online_distribution": {int(k): int(v) for k, v in units.value_counts().sort_index().items()},
            "major_hydro_mw": {"min": round(float(hydro.min()), 1), "p10": round(float(hydro.quantile(0.10)), 1),
                               "median": round(float(hydro.median()), 1)},
            "oil_mw": {"median": round(float(oil.median()), 1), "share_off_below_5mw": round(float((oil < 5).mean()), 3)},
        },
        "scenarios": scen,
        "sweep_mw": sweep,
        "checks_central": {
            "spinning_reserve_required_mw_max": round(float(reserve.max()), 1),
            "coal_headroom_mw": headroom,
            "reserve_met_by_coal_headroom_share": round(float((reserve <= headroom).mean()), 3),
            "single_unit_le_25pct_demand_share": round(float(
                (p["coal_unit_net_mw"] <= p["max_single_unit_share_of_demand"] * stress["demand_system_mw"]).mean()), 3),
            "hours_observed_below_central_floor": int((hours["dispatchable_mw"] < c["nlmin_mw"]).sum()),
            "observed_min_dispatchable_mw": round(float(hours["dispatchable_mw"].min()), 1),
        },
        "snsp": {
            "planning_limit": p["snsp_planning_limit"],
            "observed_2024_max_share_vre_of_demand": round(float(snsp_obs.max()), 3),
            "note": "2024 operation never exceeded this share; the 9 Feb 2025 blackout occurred above ~50%",
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=bgs.DEFAULT_CONFIG)
    ap.add_argument("--outdir", type=Path, default=PROJECT_ROOT / "results/grid_scenario")
    args = ap.parse_args()
    cfg = bgs.load_config(args.config)
    info = derive(load_hours(cfg["grid"]["year"]), cfg["nlmin_derivation"])
    args.outdir.mkdir(parents=True, exist_ok=True)
    (args.outdir / "nlmin_derivation.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(json.dumps({k: info[k] for k in ("stressed_hours", "scenarios", "sweep_mw", "checks_central", "snsp")},
                     indent=2))
    print("Set config central.nlmin_mw and sweeps.nlmin_mw from these values; cite the sources in "
          "docs/nlmin_derivation.md.")


if __name__ == "__main__":
    main()
