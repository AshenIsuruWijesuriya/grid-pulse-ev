"""Tests for comparison logic; synthetic data do not represent grid measurements."""
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import reconcile_ceb_2024 as rec


def test_reference_annual_check():
    path = Path(__file__).resolve().parents[1] / "docs/ceb_2024_monthly_reference.csv"
    ref = rec.load_reference(path)
    assert len(ref) == 12
    assert ref.loc[0, "ipp_gross_rooftop_solar_gwh"] == pytest.approx(53.191)
    assert ref["system_net_generation_gwh"].sum() == pytest.approx(16801.627)


def synthetic_intervals():
    return pd.DataFrame({
        "interval_start_local": ["2024-01-01T00:00:00+0530", "2024-01-01T00:15:00+0530",
                                 "2024-01-01T00:30:00+0530", "2024-01-01T00:45:00+0530"],
        "interval_quality_status": ["no_interval_flags", "no_interval_flags", "review_required", "no_interval_flags"],
        "total_dispatch_reported_mw": [1000.0]*4, "solar_mw": [10.0, 20.0, -2.0, 30.0],
        "wind_mw": [3.0]*4, "coal_mw": [200.0]*4,
        "major_hydro_mw": [100.0]*4, "oil_ceb_mw": [20.0]*4,
        "oil_ipp_mw": [0.0]*4, "mini_hydro_mw": [50.0]*4,
    })


def test_integrates_15min_and_excludes_flag_only_from_clean(tmp_path):
    path = tmp_path / "dispatch.csv"
    synthetic_intervals().to_csv(path, index=False)
    out = rec.dispatch_monthly(path).iloc[0]
    assert out["observed_intervals"] == 4
    assert out["review_required_intervals"] == 1
    assert out["dispatch_integrated_all_gwh"] == pytest.approx(1.0)
    assert out["dispatch_integrated_quality_clean_gwh"] == pytest.approx(.75)
    assert out["solar_integrated_all_gwh"] == pytest.approx(.0145)
    assert out["solar_integrated_quality_clean_gwh"] == pytest.approx(.015)
    assert not out["fully_observed"]
    assert out["expected_intervals"] == 31*96
    assert not rec.dispatch_monthly(path).iloc[5]["fully_observed"]


def test_duplicate_interval_rejected(tmp_path):
    path = tmp_path / "dispatch.csv"
    df = synthetic_intervals()
    df.loc[1, "interval_start_local"] = df.loc[0, "interval_start_local"]
    df.to_csv(path, index=False)
    with pytest.raises(ValueError, match="Duplicate"):
        rec.dispatch_monthly(path)


def test_unknown_quality_status_rejected(tmp_path):
    path = tmp_path / "dispatch.csv"
    df = synthetic_intervals()
    df.loc[0, "interval_quality_status"] = "legacy_unflagged"
    df.to_csv(path, index=False)
    with pytest.raises(ValueError, match="quality status"):
        rec.dispatch_monthly(path)


def test_daily_reported_energy_gap_is_not_silently_adjusted(tmp_path):
    df = pd.DataFrame([{
        "operating_date": "2024-01-01",
        "reported_daily_total_energy_mwh": 3000,
        "reported_daily_solar_energy_mwh": 200,
        "reported_daily_wind_energy_mwh": 50,
        "integrated_total_energy_mwh": 2500,
        "integrated_solar_energy_mwh": 50,
    }])
    df.to_csv(tmp_path/"pucsl_2024_01_reconciliation.csv", index=False)
    daily = rec.load_daily_reconciliations(tmp_path)
    assert daily.iloc[0]["daily_total_gap_gwh"] == pytest.approx(.5)
    assert daily.iloc[0]["daily_solar_gap_gwh"] == pytest.approx(.15)
    assert daily.iloc[0]["daily_non_solar_residual_gap_gwh"] == pytest.approx(.35)


def test_missing_daily_files_do_not_turn_into_zero(tmp_path):
    daily = rec.load_daily_reconciliations(tmp_path)
    assert daily.empty
    ref = rec.load_reference(Path(__file__).resolve().parents[1]/"docs/ceb_2024_monthly_reference.csv")
    p = tmp_path/"dispatch.csv"
    synthetic_intervals().to_csv(p, index=False)
    comp = rec.compare(ref, rec.dispatch_monthly(p), daily)
    assert pd.isna(comp.loc[0, "daily_reported_total_gwh"])
    assert comp.loc[0, "month_status"] == "incomplete_or_missing_daily"


def test_partial_month_reconciliation_included(tmp_path):
    # June's partial-month filename must be discovered, never silently skipped.
    df = pd.DataFrame([{
        "operating_date": "2024-06-01",
        "reported_daily_total_energy_mwh": 3000,
        "reported_daily_solar_energy_mwh": 200,
        "reported_daily_wind_energy_mwh": 50,
        "integrated_total_energy_mwh": 2500,
        "integrated_solar_energy_mwh": 50,
    }])
    df.to_csv(tmp_path / "pucsl_2024_06_partial_reconciliation.csv", index=False)
    daily = rec.load_daily_reconciliations(tmp_path)
    assert len(daily) == 1
    assert daily.iloc[0]["month"] == 6
    assert daily.iloc[0]["daily_total_gap_gwh"] == pytest.approx(0.5)

def test_standard_and_partial_duplicate_dates_rejected(tmp_path):
    # If a month has both versions, overlapping dates must not be silently counted twice.
    row = {
        "operating_date": "2024-06-01",
        "reported_daily_total_energy_mwh": 3000,
        "reported_daily_solar_energy_mwh": 200,
        "reported_daily_wind_energy_mwh": 50,
        "integrated_total_energy_mwh": 2500,
        "integrated_solar_energy_mwh": 50,
    }
    for filename in ("pucsl_2024_06_reconciliation.csv",
                     "pucsl_2024_06_partial_reconciliation.csv"):
        pd.DataFrame([row]).to_csv(tmp_path / filename, index=False)
    with pytest.raises(ValueError, match="Duplicate operating dates"):
        rec.load_daily_reconciliations(tmp_path)
