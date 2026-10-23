"""Day-ahead forecasting layer for the scheduler (WBS 5.8, proposal Table 3.2).

Forecast issue time: 00:00 local on day D, for every hour of day D. Only
information available at issue time is used:
    calendar   hour, weekday, weekend, Sri Lankan public/Poya holiday flag
               (known in advance), seasonal position (day-of-year sin/cos)
    lags       same hour yesterday (t-24 h), same hour last week (t-168 h),
               yesterday's daily mean of the target
    weather    solar only: archived GFS irradiance FORECAST for the hour,
    forecast   mean of the three NASA sites (download_nwp_forecast.py). It is
               the forecast made >= 24 h before the hour (`nwp_lead_days` = 1).
               For every hour with daylight that run starts no later than
               12 UTC on D-1 and is published by about 22:00 local on D-1,
               before the 00:00 issue time. Hours with no archived forecast
               (1-18 Jan 2024) have no NWP prediction.
No observed same-day weather is used.

Targets (hourly MW, from grid_base_<year>_hourly.csv)
    demand_system_mw, solar_available_mw, wind_mw, must_take_mw

Models
    persistence      y(t-24)  - the benchmark every model must beat
    seasonal_naive   y(t-168)
    ridge            linear regression with L2 penalty (scikit-learn)
    gbm              HistGradientBoostingRegressor (scikit-learn)
    ridge_nwp, gbm_nwp   the same with the weather forecast (solar only):
                     Model Output Statistics, i.e. a statistical correction
                     of NWP output to the local target (Glahn & Lowry 1972)

Validation
    1. Model choice uses the training period only: fit Jan-Jun, choose the
       lowest MAE on Jul-Sep. The Oct-Dec test months play no part in it.
    2. Chronological hold-out: refit Jan-Sep, test Oct-Dec. Accuracy (MAE,
       RMSE, MAPE) and skill = 1 - MAE/MAE_persistence. Every model of a
       target is scored on the same hours (those where all of them, and the
       actual value, exist). Solar forecasts are scored after the same
       post-processing the scheduler uses (no negatives, zero when dark).
       The hold-out predictions are saved for plotting.
    3. Robustness of the weather forecast: the chosen NWP model is re-run on
       the forecast made >= 48 h ahead, which was certainly published before
       the issue time.
    4. Decision quality on the hold-out: C(t) error and risk-hour
       precision/recall under the central scenario, with and without the
       weather forecast, on the same hours.
    5. Leave-one-month-out predictions for every hour of the year, so the
       scheduler can be evaluated on the same days as the other strategies.
       Lags are real prior observations (no target leakage), but training
       months after the predicted month add mild seasonal look-ahead; the
       chronological hold-out is the headline accuracy result. Hours with no
       archived weather forecast fall back to persistence.

Usage
    .venv\\Scripts\\python.exe scripts\\forecast_grid.py
"""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import build_grid_scenario as bgs

TZ = "Asia/Colombo"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGETS = ["demand_system_mw", "solar_available_mw", "wind_mw", "must_take_mw"]
SOLAR = "solar_available_mw"
MODELS = ["persistence", "seasonal_naive", "ridge", "gbm"]
NWP_MODELS = ["ridge_nwp", "gbm_nwp"]
SELECT_FIT_MONTHS = range(1, 7)
SELECT_MONTHS = range(7, 10)
TRAIN_MONTHS = range(1, 10)
TEST_MONTHS = range(10, 13)
BASE_FEATURES = ["hour", "weekday", "weekend", "holiday", "day_after_holiday", "doy_sin", "doy_cos",
                 "lag_24", "lag_168", "prev_day_mean"]
NWP_FEATURES = ["nwp_ghi"]


def holiday_dates(years) -> set:
    import holidays
    out = set()
    for y in years:
        out |= set(holidays.country_holidays("LK", years=y).keys())
    return out


def nwp_local_hourly(csv_path: Path, year: int, lead_days: int) -> pd.Series:
    """Forecast irradiance (W/m^2) for each local clock hour of `year`; NaN where not archived.

    Same alignment as bgs.nasa_local_hourly: the files are labelled by the UTC
    hour they start, which is HH:30 in Sri Lanka, so local hour [h, h+1) is
    the mean of the samples starting at h-0:30 and h+0:30.
    """
    df = pd.read_csv(csv_path)
    utc = pd.to_datetime(df["timestamp_utc"], utc=True)
    ghi = pd.Series(pd.to_numeric(df[f"shortwave_radiation_previous_day{lead_days}"]).to_numpy(),
                    index=utc.dt.tz_convert(TZ))
    if (ghi < 0).any():
        raise ValueError(f"{csv_path}: negative forecast irradiance")
    hours = pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="h", inclusive="left", tz=TZ)
    before = ghi.reindex(hours.shift(-30, freq="min")).to_numpy()
    after = ghi.reindex(hours.shift(30, freq="min")).to_numpy()
    out = pd.Series((before + after) / 2.0, index=hours, name=Path(csv_path).stem)
    if out.notna().sum() < 0.9 * len(out):
        raise ValueError(f"{csv_path}: forecast covers under 90% of local year {year}")
    if out[out.index.hour.isin([0, 1, 2, 3, 22, 23])].max() > 1.0:
        raise ValueError(f"{csv_path}: non-zero overnight irradiance; time zone is wrong")
    return out


def nwp_irradiance(site_paths: dict, year: int, lead_days: int) -> pd.Series:
    """Mean forecast irradiance across sites (NaN unless every site has a forecast)."""
    sites = {name: nwp_local_hourly(PROJECT_ROOT / p, year, lead_days) for name, p in site_paths.items()}
    return pd.concat(sites, axis=1).mean(axis=1, skipna=False).rename("nwp_ghi")


def features(base: pd.DataFrame, target: str, holidays_: set,
             nwp: pd.Series | None = None) -> pd.DataFrame:
    """Feature matrix for `target`; every column is known at 00:00 of the day."""
    idx = base.index
    y = base[target]
    doy = idx.dayofyear.to_numpy()
    dates = idx.date
    daily_mean = y.groupby(idx.normalize()).transform("mean")
    f = pd.DataFrame(index=idx)
    f["hour"] = idx.hour
    f["weekday"] = idx.dayofweek
    f["weekend"] = (idx.dayofweek >= 5).astype(int)
    f["holiday"] = [int(d in holidays_) for d in dates]
    f["day_after_holiday"] = [int((d - timedelta(days=1)) in holidays_) for d in dates]
    f["doy_sin"] = np.sin(2 * np.pi * doy / 366)
    f["doy_cos"] = np.cos(2 * np.pi * doy / 366)
    f["lag_24"] = y.shift(24)
    f["lag_168"] = y.shift(168)
    f["prev_day_mean"] = daily_mean.shift(24)
    if nwp is not None:
        f["nwp_ghi"] = nwp.reindex(idx)
    return f


def model_columns(name: str) -> list[str]:
    return BASE_FEATURES + (NWP_FEATURES if name.endswith("_nwp") else [])


def make_model(name: str):
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    kind = name.removesuffix("_nwp")
    if kind == "ridge":
        cat = ["hour", "weekday"]
        num = [c for c in model_columns(name) if c not in cat]
        pre = ColumnTransformer([
            ("cat", OneHotEncoder(handle_unknown="ignore"), cat),
            ("num", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), num),
        ])
        return make_pipeline(pre, Ridge(alpha=1.0))
    if kind == "gbm":
        return HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05,
                                             max_leaf_nodes=31, l2_regularization=1.0,
                                             categorical_features=[0, 1], random_state=0)
    raise ValueError(name)


def predict(name: str, X: pd.DataFrame, y: pd.Series, train: pd.Series,
            test: pd.Series) -> pd.Series:
    """Fit on rows where `train` and the target are valid; predict `test` rows.

    NWP models are fitted and used only on hours that have a weather
    forecast; other test hours get NaN rather than an imputed forecast.
    """
    if name == "persistence":
        return X.loc[test, "lag_24"]
    if name == "seasonal_naive":
        return X.loc[test, "lag_168"]
    cols = model_columns(name)
    has_nwp = X[[c for c in cols if c in NWP_FEATURES]].notna().all(axis=1)
    fit_rows = train & y.notna() & has_nwp
    model = make_model(name).fit(X.loc[fit_rows, cols], y[fit_rows])
    out = pd.Series(np.nan, index=X.index[test])
    rows = test & has_nwp
    out[X.index[rows]] = model.predict(X.loc[rows, cols])
    return out


def dark_hours(base: pd.DataFrame) -> pd.Series:
    """Hours of the day that are dark all year (sun geometry, known in advance)."""
    hour = pd.Series(base.index.hour, index=base.index)
    return base[SOLAR].groupby(hour).transform("max") == 0


def postprocess(target: str, p: pd.Series, dark: pd.Series) -> pd.Series:
    """The forecast as the scheduler uses it: solar never negative and zero when dark."""
    if target != SOLAR:
        return p
    return p.clip(lower=0).mask(dark.reindex(p.index).to_numpy(), 0.0)


def scores(y: pd.Series, p: pd.Series, mape: bool) -> dict:
    ok = y.notna() & p.notna()
    e = (p[ok] - y[ok])
    out = {"n": int(ok.sum()), "mae": round(float(e.abs().mean()), 2),
           "rmse": round(float(np.sqrt((e ** 2).mean())), 2)}
    if mape:
        out["mape_pct"] = round(float((e.abs() / y[ok]).mean() * 100), 2)
    return out


def common_scores(y: pd.Series, preds: dict, mape: bool) -> dict:
    """Scores of every model on the same hours, with skill against persistence."""
    ok = y.notna()
    for p in preds.values():
        ok &= p.reindex(y.index).notna()
    out = {m: scores(y[ok], p.reindex(y.index)[ok], mape) for m, p in preds.items()}
    ref = out["persistence"]["mae"]
    for m in out:
        out[m]["skill_vs_persistence"] = round(1 - out[m]["mae"] / ref, 3) if ref else None
    return out


def select_model(X: pd.DataFrame, y: pd.Series, month: pd.Series, candidates: list[str],
                 dark: pd.Series, target: str) -> tuple[str, dict]:
    """Lowest Jul-Sep MAE after fitting on Jan-Jun; the test months are not touched.

    Ties go to the earlier candidate, so persistence wins unless beaten.
    """
    fit, val = month.isin(SELECT_FIT_MONTHS), month.isin(SELECT_MONTHS)
    preds = {m: postprocess(target, predict(m, X, y, fit, val), dark) for m in candidates}
    val_scores = common_scores(y[val], preds, mape=False)
    return min(candidates, key=lambda m: val_scores[m]["mae"]), val_scores


def forecast_base(base: pd.DataFrame, preds: dict) -> pd.DataFrame:
    """A base-like frame with forecast targets, for scenario_signal()."""
    fb = base.copy()
    for t, p in preds.items():
        fb[t] = p.reindex(base.index)
    fb[SOLAR] = postprocess(SOLAR, fb[SOLAR], dark_hours(base))
    return fb


def holdout_table(actual: pd.Series, preds: dict, target: str, chosen: str) -> pd.DataFrame:
    """Hold-out hours of one target: actual, the chosen model's forecast and persistence (MW)."""
    p = preds[chosen][target]
    return pd.DataFrame({"target": target, "model": chosen, "actual_mw": actual.reindex(p.index),
                         "forecast_mw": p, "persistence_mw": preds["persistence"][target].reindex(p.index)})


def risk_quality(actual: pd.DataFrame, fcst: pd.DataFrame, mask: pd.Series) -> dict:
    a = actual.loc[mask, "curtailment_risk_mw"]
    f = fcst.loc[mask, "curtailment_risk_mw"]
    ok = a.notna() & f.notna()
    a, f = a[ok], f[ok]
    tp = int(((a > 0) & (f > 0)).sum())
    return {
        "hours": int(ok.sum()),
        "actual_risk_hours": int((a > 0).sum()),
        "forecast_risk_hours": int((f > 0).sum()),
        "precision": round(tp / max(1, int((f > 0).sum())), 3),
        "recall": round(tp / max(1, int((a > 0).sum())), 3),
        "risk_mae_mw": round(float((f - a).abs().mean()), 2),
        "actual_risk_mwh": round(float(a.sum()), 1),
        "forecast_risk_mwh": round(float(f.sum()), 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=bgs.DEFAULT_CONFIG)
    ap.add_argument("--outdir", type=Path, default=PROJECT_ROOT / "results/forecast")
    args = ap.parse_args()

    cfg = bgs.load_config(args.config)
    year = cfg["grid"]["year"]
    base = pd.read_csv(PROJECT_ROOT / "data/processed/grid" / f"grid_base_{year}_hourly.csv")
    base.index = pd.to_datetime(base["hour_start_local"], utc=True).dt.tz_convert(TZ)
    usable = (base["hour_status"] == "complete") & ~base["is_system_event_day"]
    for t in ("demand_system_mw", "wind_mw", "must_take_mw"):
        base[t] = base[t].where(usable)
    hol = holiday_dates({year - 1, year, year + 1})
    month = pd.Series(base.index.month, index=base.index)
    dark = dark_hours(base)
    train, test = month.isin(TRAIN_MONTHS), month.isin(TEST_MONTHS)

    sites = cfg["grid"].get("nwp_sites", {})
    lead = cfg["grid"].get("nwp_lead_days", 1)
    nwp = None
    if sites and all((PROJECT_ROOT / p).is_file() for p in sites.values()):
        nwp = nwp_irradiance(sites, year, lead)
    else:
        print("No weather-forecast files; run download_nwp_forecast.py to add the NWP solar models.")

    validation, holdout, lomo, best, best_no_nwp = {}, {}, {}, {}, {}
    hold_preds = {m: {} for m in MODELS + NWP_MODELS}
    nwp_fallback_hours = 0
    for t in TARGETS:
        cands = MODELS + (NWP_MODELS if t == SOLAR and nwp is not None else [])
        X, y = features(base, t, hol, nwp if t == SOLAR else None), base[t]
        best[t], validation[t] = select_model(X, y, month, cands, dark, t)
        best_no_nwp[t] = min(MODELS, key=lambda m: validation[t][m]["mae"])
        preds = {m: postprocess(t, predict(m, X, y, train, test), dark) for m in cands}
        holdout[t] = common_scores(y[test], preds, mape=(t == "demand_system_mw"))
        for m, p in preds.items():
            hold_preds[m][t] = p
        parts = [predict(best[t], X, y, month != mo, month == mo) for mo in range(1, 13)]
        lomo[t] = pd.concat(parts).sort_index()
        if best[t] in NWP_MODELS:
            missing = lomo[t].isna() & ~dark
            nwp_fallback_hours = int(missing.sum())
            lomo[t] = lomo[t].fillna(X["lag_24"])
        print(f"{t:<20} hold-out " + "  ".join(f"{m} {holdout[t][m]['mae']:.1f}" for m in cands)
              + f"  -> chosen on Jul-Sep: {best[t]}")

    nwp_info = None
    if nwp is not None:
        nwp_info = {
            "source": "Open-Meteo Previous Runs API, NCEP GFS (gfs_seamless), archived forecasts",
            "variable": f"shortwave_radiation_previous_day{lead} (W/m^2), mean of {', '.join(sites)}",
            "lead_days": lead,
            "daylight_hours_without_forecast_filled_with_persistence": nwp_fallback_hours,
        }
        if best[SOLAR] in NWP_MODELS and lead < 2:
            # Robustness: a forecast certainly published before 00:00 on D.
            X2 = features(base, SOLAR, hol, nwp_irradiance(sites, year, 2))
            p2 = postprocess(SOLAR, predict(best[SOLAR], X2, base[SOLAR], train, test), dark)
            check = common_scores(base.loc[test, SOLAR], {"persistence": hold_preds["persistence"][SOLAR],
                                                          f"lead_{lead}_day": hold_preds[best[SOLAR]][SOLAR],
                                                          "lead_2_days": p2}, mape=False)
            nwp_info["lead_sensitivity_holdout"] = check

    # Decision quality: does the forecast find the risk hours? Same hours for every plan.
    sig_args = bgs.scenario_kwargs(cfg)
    actual_sig = bgs.scenario_signal(base, **sig_args)
    plans = {m: {t: hold_preds[m][t] for t in TARGETS} for m in MODELS}
    plans["selected_per_target"] = {t: hold_preds[best[t]][t] for t in TARGETS}
    if nwp is not None:
        plans["selected_without_nwp"] = {t: hold_preds[best_no_nwp[t]][t] for t in TARGETS}
    sigs = {k: bgs.scenario_signal(forecast_base(base, p), **sig_args) for k, p in plans.items()}
    common = test & usable & actual_sig["curtailment_risk_mw"].notna()
    for s in sigs.values():
        common &= s["curtailment_risk_mw"].notna()
    decision = {k: risk_quality(actual_sig, s, common) for k, s in sigs.items()}

    args.outdir.mkdir(parents=True, exist_ok=True)
    out = forecast_base(base, lomo)[TARGETS].reset_index()
    out["hour_start_local"] = out["hour_start_local"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    out.to_csv(PROJECT_ROOT / "data/processed/grid" / f"grid_forecast_{year}_hourly.csv", index=False)
    rows = [{"target": t, "model": m, **v, "selection_mae": validation[t][m]["mae"]}
            for t, d in holdout.items() for m, v in d.items()]
    pd.DataFrame(rows).to_csv(args.outdir / "forecast_holdout_accuracy.csv", index=False)
    hold = pd.concat([holdout_table(base[t], hold_preds, t, best[t]) for t in TARGETS])
    hold.index = hold.index.strftime("%Y-%m-%dT%H:%M:%S%z")
    hold.rename_axis("hour_start_local").to_csv(args.outdir / "forecast_holdout_predictions.csv")
    info = {
        "status": ("Day-ahead forecast; solar uses archived GFS irradiance forecasts"
                   if nwp is not None else "Day-ahead forecast without NWP")
                  + "; central scenario parameters are provisional",
        "issue_time": "00:00 local, day-ahead",
        "selection": {"fit_months": list(SELECT_FIT_MONTHS), "choose_on_months": list(SELECT_MONTHS),
                      "rule": "lowest MAE on common hours; persistence unless beaten"},
        "train_months": list(TRAIN_MONTHS), "test_months": list(TEST_MONTHS),
        "selection_accuracy": validation,
        "holdout_accuracy": holdout,
        "selected_model_per_target": best,
        "selected_model_per_target_without_nwp": best_no_nwp,
        "nwp": nwp_info,
        "holdout_curtailment_risk_detection": decision,
        "lomo_note": "leave-one-month-out predictions saved for scheduling; mild seasonal look-ahead",
    }
    (args.outdir / "forecast_summary.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(json.dumps({"nwp": nwp_info, "decision": decision}, indent=2))
    print(f"Wrote grid_forecast_{year}_hourly.csv and {args.outdir}/forecast_summary.json")


if __name__ == "__main__":
    main()
