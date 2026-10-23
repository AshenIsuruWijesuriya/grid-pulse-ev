"""Day-ahead forecasting layer for the scheduler (WBS 5.8, proposal Table 3.2).

Forecast issue time: 00:00 local on day D, for every hour of day D. Only
information available at issue time is used:
    calendar   hour, weekday, weekend, Sri Lankan public/Poya holiday flag
               (known in advance), seasonal position (day-of-year sin/cos)
    lags       same hour yesterday (t-24 h), same hour last week (t-168 h),
               yesterday's daily mean of the target
No same-day weather is used. This is a forecast WITHOUT numerical weather
prediction, so solar skill is limited; adding NWP is a documented extension.

Targets (hourly MW, from grid_base_<year>_hourly.csv)
    demand_system_mw, solar_available_mw, wind_mw, must_take_mw

Models
    persistence      y(t-24)  - the benchmark every model must beat
    seasonal_naive   y(t-168)
    ridge            linear regression with L2 penalty (scikit-learn)
    gbm              HistGradientBoostingRegressor (scikit-learn)

Validation
    1. Chronological hold-out: train Jan-Sep, test Oct-Dec. Accuracy (MAE,
       RMSE, MAPE) and skill = 1 - MAE/MAE_persistence. The hold-out
       predictions are saved for plotting (forecast_holdout_predictions.csv).
    2. Decision quality on the hold-out: C(t) error and risk-hour
       precision/recall under the central scenario.
    3. Leave-one-month-out predictions for every hour of the year, so the
       scheduler can be evaluated on the same days as the other strategies.
       Lags are real prior observations (no target leakage), but training
       months after the predicted month add mild seasonal look-ahead; the
       chronological hold-out is the headline accuracy result.

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
MODELS = ["persistence", "seasonal_naive", "ridge", "gbm"]
TRAIN_MONTHS = range(1, 10)
TEST_MONTHS = range(10, 13)


def holiday_dates(years) -> set:
    import holidays
    out = set()
    for y in years:
        out |= set(holidays.country_holidays("LK", years=y).keys())
    return out


def features(base: pd.DataFrame, target: str, holidays_: set) -> pd.DataFrame:
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
    return f


def make_model(name: str):
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    if name == "ridge":
        cat = ["hour", "weekday"]
        num = ["weekend", "holiday", "day_after_holiday", "doy_sin", "doy_cos",
               "lag_24", "lag_168", "prev_day_mean"]
        pre = ColumnTransformer([
            ("cat", OneHotEncoder(handle_unknown="ignore"), cat),
            ("num", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), num),
        ])
        return make_pipeline(pre, Ridge(alpha=1.0))
    if name == "gbm":
        return HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05,
                                             max_leaf_nodes=31, l2_regularization=1.0,
                                             categorical_features=[0, 1], random_state=0)
    raise ValueError(name)


def predict(name: str, X: pd.DataFrame, y: pd.Series, train: pd.Series,
            test: pd.Series) -> pd.Series:
    """Fit on rows where `train` and the target are valid; predict `test` rows."""
    if name == "persistence":
        return X.loc[test, "lag_24"]
    if name == "seasonal_naive":
        return X.loc[test, "lag_168"]
    fit_rows = train & y.notna()
    model = make_model(name).fit(X[fit_rows], y[fit_rows])
    return pd.Series(model.predict(X[test]), index=X.index[test])


def scores(y: pd.Series, p: pd.Series, mape: bool) -> dict:
    ok = y.notna() & p.notna()
    e = (p[ok] - y[ok])
    out = {"n": int(ok.sum()), "mae": round(float(e.abs().mean()), 2),
           "rmse": round(float(np.sqrt((e ** 2).mean())), 2)}
    if mape:
        out["mape_pct"] = round(float((e.abs() / y[ok]).mean() * 100), 2)
    return out


def forecast_base(base: pd.DataFrame, preds: dict) -> pd.DataFrame:
    """A base-like frame with forecast targets, for scenario_signal()."""
    fb = base.copy()
    for t, p in preds.items():
        fb[t] = p.reindex(base.index)
    # Solar cannot be negative, and is zero in hours of the day that are dark
    # all year (sun geometry, known in advance).
    hour = pd.Series(base.index.hour, index=base.index)
    dark = base["solar_available_mw"].groupby(hour).transform("max") == 0
    fb["solar_available_mw"] = fb["solar_available_mw"].clip(lower=0)
    fb.loc[dark, "solar_available_mw"] = 0.0
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

    holdout, lomo, best = {}, {}, {}
    hold_preds = {m: {} for m in MODELS}
    for t in TARGETS:
        X, y = features(base, t, hol), base[t]
        train, test = month.isin(TRAIN_MONTHS), month.isin(TEST_MONTHS)
        holdout[t] = {}
        for m in MODELS:
            p = predict(m, X, y, train, test)
            hold_preds[m][t] = p
            holdout[t][m] = scores(y[test], p, mape=(t == "demand_system_mw"))
        ref = holdout[t]["persistence"]["mae"]
        for m in MODELS:
            holdout[t][m]["skill_vs_persistence"] = round(1 - holdout[t][m]["mae"] / ref, 3) if ref else None
        # A learned model is used only if it beats persistence (Table 3.2).
        best[t] = min(MODELS, key=lambda m: holdout[t][m]["mae"])
        parts = []
        for mo in range(1, 13):
            parts.append(predict(best[t], X, y, month != mo, month == mo))
        lomo[t] = pd.concat(parts).sort_index()
        print(f"{t:<20} " + "  ".join(f"{m} MAE {holdout[t][m]['mae']:.1f}" for m in MODELS)
              + f"  -> {best[t]}")

    # Decision quality: does the forecast find the risk hours?
    sig_args = bgs.scenario_kwargs(cfg)
    actual_sig = bgs.scenario_signal(base, **sig_args)
    test_mask = month.isin(TEST_MONTHS) & usable
    decision = {}
    for m in MODELS:
        fb = forecast_base(base, hold_preds[m])
        decision[m] = risk_quality(actual_sig, bgs.scenario_signal(fb, **sig_args), test_mask)
    selected = {t: hold_preds[best[t]][t] for t in TARGETS}
    decision["selected_per_target"] = risk_quality(
        actual_sig, bgs.scenario_signal(forecast_base(base, selected), **sig_args), test_mask)

    args.outdir.mkdir(parents=True, exist_ok=True)
    out = forecast_base(base, lomo)[TARGETS].reset_index()
    out["hour_start_local"] = out["hour_start_local"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    out.to_csv(PROJECT_ROOT / "data/processed/grid" / f"grid_forecast_{year}_hourly.csv", index=False)
    rows = [{"target": t, "model": m, **v} for t, d in holdout.items() for m, v in d.items()]
    pd.DataFrame(rows).to_csv(args.outdir / "forecast_holdout_accuracy.csv", index=False)
    hold = pd.concat([holdout_table(base[t], hold_preds, t, best[t]) for t in TARGETS])
    hold.index = hold.index.strftime("%Y-%m-%dT%H:%M:%S%z")
    hold.rename_axis("hour_start_local").to_csv(args.outdir / "forecast_holdout_predictions.csv")
    info = {
        "status": "Day-ahead forecast without NWP; central scenario parameters are provisional",
        "issue_time": "00:00 local, day-ahead",
        "train_months": list(TRAIN_MONTHS), "test_months": list(TEST_MONTHS),
        "holdout_accuracy": holdout,
        "selected_model_per_target": best,
        "holdout_curtailment_risk_detection": decision,
        "lomo_note": "leave-one-month-out predictions saved for scheduling; mild seasonal look-ahead",
    }
    (args.outdir / "forecast_summary.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(json.dumps(decision, indent=2))
    print(f"Wrote grid_forecast_{year}_hourly.csv and {args.outdir}/forecast_summary.json")


if __name__ == "__main__":
    main()
