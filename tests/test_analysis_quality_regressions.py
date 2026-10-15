"""Regression tests for data-quality exclusions and scientific overclaims.

These tests use synthetic 15-minute records; no empirical system limit is
estimated or asserted by these tests.
"""
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import analyse_coal_commitment as acc
import analyse_curtailment_pressure as acp


def day(date, coal=320.0, midday=1100.0, night=1400.0):
    ts = pd.date_range(f"{date} 00:00", periods=96, freq="15min", tz="Asia/Colombo")
    reported = [night if t.hour < 5 else midday if 10 <= t.hour <= 14 else 1600.0
                for t in ts]
    return pd.DataFrame({
        "operating_date": [date] * 96,
        "interval_number": range(1, 97),
        "interval_start_local": ts.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "coal_mw": [coal] * 96,
        "oil_ceb_mw": [0.0] * 96,
        "oil_ipp_mw": [0.0] * 96,
        "major_hydro_mw": [400.0] * 96,
        "wind_mw": [40.0] * 96,
        "solar_mw": [50.0] * 96,
        "biomass_mw": [10.0] * 96,
        "mini_hydro_mw": [100.0] * 96,
        "total_dispatch_reported_mw": reported,
        "interval_quality_status": ["no_interval_flags"] * 96,
    })


def load(tmp_path, *frames):
    p = tmp_path / "sample.csv"
    pd.concat(frames, ignore_index=True).to_csv(p, index=False)
    return acp.load(p)


def test_off_plant_stays_off_with_high_neighbouring_output(tmp_path):
    raw = day("2024-01-01", coal=540)
    raw.loc[48, "coal_mw"] = 0.0
    df = load(tmp_path, raw)
    units = acc.units_online(df, step_mw=275, max_units=3, window_hours=4)
    assert units.iloc[48] == 0
    assert units.iloc[49] == 2


def test_off_plant_cannot_set_zero_per_unit_floor():
    coal = pd.Series([0.0, 320.0, 540.0])
    units = pd.Series([2, 2, 2])  # even if inference wrongly says online at coal=0
    assert acc.per_unit_floor(coal, units) >= 160.0


def test_all_off_floor_is_rejected():
    with pytest.raises(ValueError, match="no eligible coal-producing"):
        acc.per_unit_floor(pd.Series([0.0, -2.0]), pd.Series([2, 1]))


def test_flagged_coal_spike_does_not_raise_rolling_commitment(tmp_path):
    raw = day("2024-01-01", coal=160)
    raw.loc[50, "coal_mw"] = 820
    raw.loc[50, "interval_quality_status"] = "review_required"
    df = load(tmp_path, raw)
    units = acc.units_online(df, step_mw=275, max_units=3, window_hours=4)
    assert units.iloc[50] == 0
    assert units.iloc[49] == 1


def test_flagged_dispatch_outlier_not_used_in_daily_or_stress(tmp_path):
    typical = day("2024-01-01", coal=540, midday=1400)
    stressed = day("2024-01-02", coal=320, midday=900)
    stressed.loc[51, "total_dispatch_reported_mw"] = 15
    stressed.loc[51, "interval_quality_status"] = "review_required"
    df = load(tmp_path, typical, stressed)
    daily = acp.daily_table(df)
    assert not daily["is_outage_day"].any()  # flagged glitch is not a verified outage
    row = daily.loc[daily["operating_date"] == pd.Timestamp("2024-01-02")].iloc[0]
    assert row["day_min_mw"] == 900
    assert row["quality_excluded_intervals"] == 1
    mask = acc.stressed_mask(df, daily, quantile=0.03)
    assert not bool(mask.iloc[96 + 51])
    assert mask.any()


def test_95_interval_day_is_not_a_complete_day(tmp_path):
    raw = day("2024-01-01").drop(index=50)
    df = load(tmp_path, raw)
    assert acp.daily_table(df).empty


def test_headroom_rejects_review_required_stressed_points(tmp_path):
    raw = day("2024-01-01")
    raw.loc[50, "interval_quality_status"] = "review_required"
    df = load(tmp_path, raw)
    selection = pd.Series(False, index=df.index)
    selection.iloc[50] = True
    with pytest.raises(ValueError, match="flagged or system-event"):
        acc.headroom_table(df, selection, step_mw=275, max_units=3, window_hours=4)


def test_composition_does_not_claim_measured_curtailment(tmp_path, capsys):
    df = load(tmp_path, day("2024-01-01", coal=400, midday=1200),
              day("2024-01-02", coal=330, midday=1000))
    acp.composition(df, acp.daily_table(df), stressed_quantile=0.03)
    printed = capsys.readouterr().out
    assert "NO CURTAILMENT VERDICT" in printed
    assert "the fleet was NOT at its floor" not in printed
    assert "the fleet was at or near its floor" not in printed


def test_legacy_file_is_explicitly_marked(tmp_path):
    raw = day("2024-01-01").drop(columns="interval_quality_status")
    df = load(tmp_path, raw)
    assert df["interval_quality_status"].eq("legacy_unflagged").all()
