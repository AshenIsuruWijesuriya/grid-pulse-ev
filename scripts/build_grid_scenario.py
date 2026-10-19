"""Hourly grid state and inferred curtailment-risk signal C(t) (SO1/SO2).

Method (see docs/methods_and_literature.md for the literature basis)

1. System-boundary correction. PUCSL 15-minute dispatch D(t) excludes
   embedded generation: rooftop PV, part of grid solar, part of mini hydro
   and wind (CEB reconciliation, research log 2026-10-19). Each source is
   rescaled so that its monthly energy matches the CEB Sales and Generation
   Data Book 2024:
       solar      S(t)  = NASA irradiance shape x CEB monthly (grid + rooftop)
       wind       W(t)  = metered wind  x CEB/PUCSL monthly ratio
       must-take  MT(t) = metered (mini hydro + biomass) x monthly ratio
   Underlying demand adds back what D(t) left out:
       D_sys(t) = D(t) + (S - solar_mw) + (W - wind_mw) + (MT - mt_metered)

2. Available solar is MODELLED from irradiance, not taken from metered
   dispatch, because metered output is recorded after any curtailment
   (Denholm & Margolis 2007 simulate PV output for the same reason).

3. Scenario residual load for the dispatchable synchronous fleet
   (coal, oil, major hydro):
       NL_s(t) = k_D * D_sys - m_solar * S - m_wind * W - k_MT * MT
   With all multipliers at 1 this equals observed dispatchable generation
   hour by hour, so the 2024 baseline is observed, not modelled.
   Mini hydro and biomass are must-take (run-of-river / must-run), not
   dispatchable.

4. Curtailment risk in MW, from the two constraint families island
   operators use (minimum synchronous generation; EirGrid-style SNSP):
       floor(t) = max(0, NLmin - NL_s)
       snsp(t)  = max(0, m_solar*S + m_wind*W - snsp_max * D_sys)
       C(t)     = max(floor, snsp)
   C(t) is an inferred scenario proxy, NOT measured curtailment.

5. Scenario years. The multipliers for a year Y come from the CEB Long Term
   Generation Expansion Plan 2025-2044 base case (ltgep_scenario): installed
   capacity at the end of 2023, planned additions per year (Table E3) and
   the net-generation forecast (Table 3.3). Each source keeps its 2024
   hourly shape and is scaled by mid-year capacity (or energy) relative to
   2024, the usual way to project a reference-year profile.

A day is eligible for scheduling only when all 24 hours are complete and
unflagged and it is not a system-event day. Nothing is imputed.

Usage
    .venv\\Scripts\\python.exe scripts\\build_grid_scenario.py
Writes data/processed/grid/grid_base_<year>_hourly.csv and
results/grid_scenario/grid_base_<year>_diagnostics.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

TZ = "Asia/Colombo"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "scenarios.json"


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #

def nasa_local_hourly(csv_path: Path, year: int) -> pd.Series:
    """Mean irradiance (W/m^2) for each local clock hour of `year`.

    NASA hourly samples cover one UTC hour, which is HH:30-HH+1:30 in Sri
    Lanka. Local hour [h, h+1) is half of the sample starting at h-0:30 and
    half of the one starting at h+0:30, so it gets their mean.
    """
    df = pd.read_csv(csv_path)
    utc = pd.to_datetime(df["timestamp_utc"], utc=True)
    ghi = pd.Series(pd.to_numeric(df["ALLSKY_SFC_SW_DWN"], errors="raise").to_numpy(),
                    index=utc.dt.tz_convert(TZ))
    if (ghi < 0).any() or ghi.isna().any():
        raise ValueError(f"{csv_path}: negative or missing irradiance")
    hours = pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="h",
                          inclusive="left", tz=TZ)
    before = ghi.reindex(hours.shift(-30, freq="min")).to_numpy()
    after = ghi.reindex(hours.shift(30, freq="min")).to_numpy()
    out = pd.Series((before + after) / 2.0, index=hours, name=Path(csv_path).stem)
    if out.isna().any():
        raise ValueError(f"{csv_path}: NASA file does not cover local year {year}")
    night = out[out.index.hour.isin([0, 1, 2, 3, 22, 23])]
    if night.max() > 1.0:
        raise ValueError(f"{csv_path}: non-zero overnight irradiance; time zone is wrong")
    return out


def solar_shape(site_paths: dict, year: int) -> tuple[pd.Series, dict]:
    """Mean irradiance across sites plus per-site series for diagnostics."""
    sites = {name: nasa_local_hourly(PROJECT_ROOT / p, year) for name, p in site_paths.items()}
    shape = pd.concat(sites, axis=1).mean(axis=1).rename("ghi_mean_w_m2")
    return shape, sites


def monthly_reference(path: Path, year: int) -> pd.DataFrame:
    ref = pd.read_csv(path)
    ref = ref[ref["year"] == year].set_index("month")
    if len(ref) != 12:
        raise ValueError(f"CEB reference needs 12 months for {year}")
    return pd.DataFrame({
        "solar_gwh": ref["ipp_gross_grid_solar_gwh"] + ref["ipp_gross_rooftop_solar_gwh"],
        "wind_gwh": ref["ceb_gross_wind_gwh"] + ref["ipp_gross_wind_gwh"],
        "must_take_gwh": (ref["ipp_gross_mini_hydro_gwh"] + ref["ipp_gross_dendro_gwh"]
                          + ref["ipp_gross_biomass_gwh"] + ref["ipp_gross_municipal_waste_gwh"]),
        "net_generation_gwh": ref["system_net_generation_gwh"],
    })


def pucsl_monthly_observed(dispatch_15min: Path, must_take: list[str]) -> pd.DataFrame:
    """PUCSL energy over ALL observed intervals and the observed share of the month.

    Flagged intervals are included: the ratio compares energy totals, and
    excluding them would bias every ratio upward.
    """
    df = pd.read_csv(dispatch_15min)
    ts = pd.to_datetime(df["interval_start_local"], utc=True).dt.tz_convert(TZ)
    df["month"] = ts.dt.month
    df["must_take_mw"] = df[must_take].sum(axis=1)
    g = df.groupby("month")
    out = pd.DataFrame({
        "solar_gwh": g["solar_mw"].sum() * 0.25 / 1000,
        "wind_gwh": g["wind_mw"].sum() * 0.25 / 1000,
        "must_take_gwh": g["must_take_mw"].sum() * 0.25 / 1000,
        "observed_intervals": g.size(),
    })
    days = pd.Series({m: pd.Period(f"{ts.dt.year.iloc[0]}-{m:02d}").days_in_month
                      for m in out.index})
    out["observed_fraction"] = out["observed_intervals"] / (days * 96)
    return out


# --------------------------------------------------------------------------- #
# Base frame (multipliers = 1)
# --------------------------------------------------------------------------- #

def build_base(hourly: pd.DataFrame, shape: pd.Series, ceb: pd.DataFrame,
               pucsl: pd.DataFrame, must_take: list[str]) -> pd.DataFrame:
    """Boundary-corrected hourly frame. Observed columns stay empty where
    hour_status != complete; modelled solar exists for every hour."""
    b = pd.DataFrame(index=shape.index)
    b.index.name = "hour_start_local"
    h = hourly.reindex(b.index)
    month = b.index.month

    # Solar: distribute each CEB month's energy over that month's irradiance.
    month_shape_sum = shape.groupby(month).transform("sum")
    solar_mwh_month = ceb["solar_gwh"].reindex(month).to_numpy() * 1000
    b["solar_available_mw"] = shape.to_numpy() / month_shape_sum.to_numpy() * solar_mwh_month

    # Scale PUCSL month totals to the observed share before taking ratios,
    # so June (one day missing) is not penalised.
    frac = pucsl["observed_fraction"].reindex(month).to_numpy()
    wind_ratio = (ceb["wind_gwh"].reindex(month).to_numpy() * frac
                  / pucsl["wind_gwh"].reindex(month).to_numpy())
    mt_ratio = (ceb["must_take_gwh"].reindex(month).to_numpy() * frac
                / pucsl["must_take_gwh"].reindex(month).to_numpy())
    b["wind_ratio"] = wind_ratio
    b["must_take_ratio"] = mt_ratio

    b["hour_status"] = h["hour_status"].fillna("missing")
    b["is_system_event_day"] = h["is_system_event_day"].fillna(False).astype(bool)
    b["dispatch_reported_mw"] = h["total_dispatch_reported_mw"]
    b["solar_metered_mw"] = h["solar_mw"]
    b["wind_metered_mw"] = h["wind_mw"]
    mt_metered = h[must_take].sum(axis=1, min_count=len(must_take))
    b["must_take_metered_mw"] = mt_metered
    b["wind_mw"] = h["wind_mw"] * wind_ratio
    b["must_take_mw"] = mt_metered * mt_ratio
    b["dispatchable_observed_mw"] = (h["coal_mw"] + h["oil_ceb_mw"] + h["oil_ipp_mw"]
                                     + h["major_hydro_mw"])
    b["demand_system_mw"] = (b["dispatch_reported_mw"]
                             + (b["solar_available_mw"] - b["solar_metered_mw"])
                             + (b["wind_mw"] - b["wind_metered_mw"])
                             + (b["must_take_mw"] - b["must_take_metered_mw"]))
    return b


def eligible_days(base: pd.DataFrame) -> list[pd.Timestamp]:
    """Local dates whose 24 hours are complete and not a system event."""
    ok = (base["hour_status"] == "complete") & ~base["is_system_event_day"]
    by_day = ok.groupby(base.index.normalize()).agg(["sum", "size"])
    return [d for d, r in by_day.iterrows() if r["sum"] == 24 and r["size"] == 24]


# --------------------------------------------------------------------------- #
# Scenario signal
# --------------------------------------------------------------------------- #

def ltgep_scenario(sy: dict, year: int) -> dict:
    """Multipliers on the 2024 hourly profiles for LTGEP base-case year `year`.

    Capacity sources use mid-year installed capacity relative to mid-2024:
        C_mid(Y) = C_2023 + A_2024 + sum(A_2025..A_Y-1) + A_Y / 2
        m(Y)     = C_mid(Y) / (C_2023 + A_2024 / 2)
    Demand uses the net-generation forecast relative to CEB 2024 actual.
    """
    out = {"scenario_year": int(year)}
    if year == 2024:
        return {**out, "solar_multiplier": 1.0, "wind_multiplier": 1.0,
                "demand_multiplier": 1.0, "must_take_multiplier": 1.0}
    for src in ("solar", "wind", "must_take"):
        c0, a24 = sy["installed_end_2023_mw"][src], sy["additions_2024_mw"][src]
        adds = {int(k): v for k, v in sy["additions_mw"][src].items()}
        if year not in adds:
            raise ValueError(f"no LTGEP additions for {src} in {year}")
        mid = c0 + a24 + sum(v for y, v in adds.items() if y < year) + adds[year] / 2
        out[f"{src}_multiplier"] = round(mid / (c0 + a24 / 2), 4)
        out[f"{src}_mid_year_mw"] = round(mid, 1)
    out["demand_multiplier"] = round(sy["net_generation_forecast_gwh"][str(year)]
                                     / sy["net_generation_2024_gwh"], 4)
    return out


def scenario_kwargs(cfg: dict, year: int | None = None, **override) -> dict:
    """scenario_signal keyword arguments for the central case (optionally another year)."""
    c = cfg["central"]
    m = ltgep_scenario(cfg["scenario_years"], c["scenario_year"] if year is None else year)
    kw = {"nlmin_mw": c["nlmin_mw"], "snsp_max": c["snsp_max"],
          "peak_band_fraction": cfg["grid"]["peak_band_fraction"],
          **{k: m[k] for k in ("solar_multiplier", "wind_multiplier", "demand_multiplier",
                               "must_take_multiplier")}}
    kw.update(override)
    return kw


def scenario_signal(base: pd.DataFrame, nlmin_mw: float, solar_multiplier: float = 1.0,
                    wind_multiplier: float = 1.0, snsp_max: float | None = None,
                    peak_band_fraction: float = 0.03, demand_multiplier: float = 1.0,
                    must_take_multiplier: float = 1.0) -> pd.DataFrame:
    """NL_s(t), C(t) and peak-stress windows for one scenario (MW, hourly)."""
    s = pd.DataFrame(index=base.index)
    s["demand_system_mw"] = demand_multiplier * base["demand_system_mw"]
    s["solar_mw"] = solar_multiplier * base["solar_available_mw"]
    s["wind_mw"] = wind_multiplier * base["wind_mw"]
    s["vre_mw"] = s["solar_mw"] + s["wind_mw"]
    s["must_take_mw"] = must_take_multiplier * base["must_take_mw"]
    s["net_load_mw"] = s["demand_system_mw"] - s["vre_mw"] - s["must_take_mw"]
    s["nlmin_mw"] = float(nlmin_mw)
    s["snsp_max"] = np.nan if snsp_max is None else float(snsp_max)
    s["c_floor_mw"] = (nlmin_mw - s["net_load_mw"]).clip(lower=0)
    if snsp_max is None:
        s["c_snsp_mw"] = 0.0
    else:
        s["c_snsp_mw"] = (s["vre_mw"] - snsp_max * s["demand_system_mw"]).clip(lower=0)
    s["curtailment_risk_mw"] = s[["c_floor_mw", "c_snsp_mw"]].max(axis=1)
    # More curtailment than available VRE means the floor cannot be held
    # even with all VRE off: a security shortfall, reported separately.
    s["security_shortfall_mw"] = (s["curtailment_risk_mw"] - s["vre_mw"]).clip(lower=0)
    s.loc[s["net_load_mw"].isna(), ["c_floor_mw", "c_snsp_mw", "curtailment_risk_mw",
                                    "security_shortfall_mw"]] = np.nan
    day_max = s["net_load_mw"].groupby(s.index.normalize()).transform("max")
    s["is_peak_window"] = s["net_load_mw"] >= day_max * (1 - peak_band_fraction)
    return s


# --------------------------------------------------------------------------- #
# Diagnostics (proposal §3.5 input and signal validity)
# --------------------------------------------------------------------------- #

def diagnostics(base: pd.DataFrame, sites: dict, ceb: pd.DataFrame, cfg: dict) -> dict:
    complete = base["hour_status"] == "complete"
    c = base[complete]
    corr = {name: round(float(np.corrcoef(series[complete], c["solar_metered_mw"])[0, 1]), 3)
            for name, series in sites.items()}
    corr["mean_of_sites"] = round(float(np.corrcoef(
        pd.concat(sites, axis=1).mean(axis=1)[complete], c["solar_metered_mw"])[0, 1]), 3)

    central = cfg["central"]
    base_sig = scenario_signal(base, **scenario_kwargs(cfg, 2024))
    cen_sig = scenario_signal(base, **scenario_kwargs(cfg))
    days = eligible_days(base)

    def signal_profile(sig: pd.DataFrame) -> dict:
        x = sig[complete]
        risk = x["curtailment_risk_mw"]
        weekend = x.index.dayofweek >= 5
        by_hour = risk.groupby(x.index.hour).sum()
        by_month = risk.groupby(x.index.month).sum()
        return {
            "hours_with_risk": int((risk > 0).sum()),
            "risk_energy_mwh": round(float(risk.sum()), 1),
            "share_of_risk_energy_10_to_15h": round(
                float(by_hour.loc[10:14].sum() / by_hour.sum()), 3) if by_hour.sum() else None,
            "risk_mwh_per_weekend_day": round(float(risk[weekend].sum() / max(1, weekend.sum() / 24)), 1),
            "risk_mwh_per_weekday": round(float(risk[~weekend].sum() / max(1, (~weekend).sum() / 24)), 1),
            "risk_mwh_by_month": {int(k): round(float(v), 1) for k, v in by_month.items()},
            "security_shortfall_hours": int((x["security_shortfall_mw"] > 0).sum()),
        }

    return {
        "status": "INFERRED SCENARIO SIGNAL - not measured curtailment; parameters provisional",
        "year": int(base.index.year[0]),
        "hours": len(base),
        "complete_hours": int(complete.sum()),
        "eligible_days": len(days),
        "solar_shape_correlation_with_pucsl_metered_solar": corr,
        "modelled_solar_overnight_max_mw": round(float(base.loc[base.index.hour.isin([0, 1, 2, 3, 22, 23]),
                                                                 "solar_available_mw"].max()), 3),
        "annual_energy_gwh": {
            "solar_available_model": round(float(base["solar_available_mw"].sum() / 1000), 1),
            "ceb_solar_grid_plus_rooftop": round(float(ceb["solar_gwh"].sum()), 1),
            "demand_system_complete_hours": round(float(c["demand_system_mw"].sum() / 1000), 1),
            "demand_system_scaled_to_full_year": round(float(
                c["demand_system_mw"].sum() / 1000 / complete.mean()), 1),
            "dispatch_reported_complete_hours": round(float(c["dispatch_reported_mw"].sum() / 1000), 1),
            "ceb_net_generation_full_year": round(float(ceb["net_generation_gwh"].sum()), 1),
            "complete_hour_share": round(float(complete.mean()), 4),
        },
        "observed_dispatchable_mw": {
            "min": round(float(c["dispatchable_observed_mw"].min()), 1),
            "p01": round(float(c["dispatchable_observed_mw"].quantile(0.01)), 1),
        },
        "nlmin_cross_check": {
            "nlmin_mw": central["nlmin_mw"],
            "note": "hours where OBSERVED 2024 dispatchable generation ran below NLmin; "
                    "many such hours mean NLmin is set too high for 2024 operation",
            **signal_profile(base_sig),
        },
        "central_scenario_signal": {
            **ltgep_scenario(cfg["scenario_years"], central["scenario_year"]),
            **signal_profile(cen_sig),
        },
        "scenario_years": scenario_year_table(base, cfg),
    }


def scenario_year_table(base: pd.DataFrame, cfg: dict) -> list[dict]:
    """Risk under each LTGEP base-case year (2024 weather and shapes, central NLmin/SNSP)."""
    complete = base["hour_status"] == "complete"
    years = [2024] + sorted(int(y) for y in cfg["scenario_years"]["net_generation_forecast_gwh"])
    rows = []
    for y in years:
        s = scenario_signal(base, **scenario_kwargs(cfg, y))[complete]
        risk = s["curtailment_risk_mw"]
        rows.append({**ltgep_scenario(cfg["scenario_years"], y),
                     "risk_hours": int((risk > 0).sum()),
                     "risk_energy_gwh": round(float(risk.sum() / 1000), 1),
                     "days_with_risk": int((risk.groupby(s.index.normalize()).sum() > 0).sum()),
                     "snsp_binding_hours": int((s["c_snsp_mw"] > s["c_floor_mw"]).sum()),
                     "security_shortfall_hours": int((s["security_shortfall_mw"] > 0).sum()),
                     "risk_share_of_available_vre": round(float(risk.sum() / s["vre_mw"].sum()), 4)})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--outdir", type=Path, default=PROJECT_ROOT / "data/processed/grid")
    ap.add_argument("--diagdir", type=Path, default=PROJECT_ROOT / "results/grid_scenario")
    args = ap.parse_args()

    cfg = load_config(args.config)
    g = cfg["grid"]
    year = g["year"]
    hourly = pd.read_csv(PROJECT_ROOT / g["hourly_net_demand"])
    hourly.index = pd.to_datetime(hourly["hour_start_local"], utc=True).dt.tz_convert(TZ)
    shape, sites = solar_shape(g["nasa_sites"], year)
    ceb = monthly_reference(PROJECT_ROOT / g["ceb_monthly_reference"], year)
    pucsl = pucsl_monthly_observed(PROJECT_ROOT / g["dispatch_15min"], g["must_take_columns"])
    base = build_base(hourly, shape, ceb, pucsl, g["must_take_columns"])
    info = diagnostics(base, sites, ceb, cfg)

    args.outdir.mkdir(parents=True, exist_ok=True)
    args.diagdir.mkdir(parents=True, exist_ok=True)
    out = base.reset_index()
    out["hour_start_local"] = out["hour_start_local"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    out.to_csv(args.outdir / f"grid_base_{year}_hourly.csv", index=False)
    (args.diagdir / f"grid_base_{year}_diagnostics.json").write_text(
        json.dumps(info, indent=2), encoding="utf-8")
    print(json.dumps(info, indent=2))
    print(f"Wrote grid_base_{year}_hourly.csv to {args.outdir}")
    print("REMINDER: C(t) is an inferred scenario proxy. NLmin is derived (pending CEB "
          "review); scenario years follow the LTGEP base case, before any new storage.")


if __name__ == "__main__":
    main()
