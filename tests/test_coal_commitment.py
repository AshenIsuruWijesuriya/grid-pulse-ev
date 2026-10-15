"""Tests for scripts/analyse_coal_commitment.py.

Hand-built fixtures, chosen so every expected value can be checked by eye.
They are not representative of the real system; quote nothing from here.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import analyse_coal_commitment as acc  # noqa: E402
import analyse_curtailment_pressure as acp  # noqa: E402

STEP = 275.0


def _day(date: str, coal: float, midday_mw: float = 1600.0) -> pd.DataFrame:
    """One day with constant coal; total dispatch dips only at 10:00-14:59."""
    starts = pd.date_range(f"{date} 00:00", periods=96, freq="15min",
                           tz="Asia/Colombo")
    total = [midday_mw if 10 <= h <= 14 else 1500.0 for h in starts.hour]
    return pd.DataFrame({
        "operating_date": pd.Timestamp(date),
        "interval_number": range(1, 97),
        "interval_start_local": starts.tz_convert("UTC").strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"),
        "coal_mw": coal, "oil_ceb_mw": 0.0, "oil_ipp_mw": 0.0,
        "major_hydro_mw": 300.0, "wind_mw": 40.0, "solar_mw": 60.0,
        "biomass_mw": 10.0, "mini_hydro_mw": 90.0,
        "total_dispatch_reported_mw": total,
    })


def _load(tmp_path: Path, frames) -> pd.DataFrame:
    path = tmp_path / "dispatch.csv"
    pd.concat(frames, ignore_index=True).to_csv(path, index=False)
    return acp.load(path)


def test_unit_step_is_top_of_distribution_over_units():
    coal = pd.Series([825.0] * 100)
    assert acc.estimate_unit_step(coal, 3) == pytest.approx(275.0)


def test_unit_step_rejects_plant_that_never_ran():
    with pytest.raises(ValueError):
        acc.estimate_unit_step(pd.Series([0.0, -3.0]), 3)


@pytest.mark.parametrize("coal, expected", [
    (-3.0, 0),     # auxiliary load with the plant off
    (160.0, 1),    # one unit, turned down
    (285.0, 1),    # one unit slightly above nominal, within 5% tolerance
    (320.0, 2),    # two units at deep turndown, NOT one unit
    (540.0, 2),    # two units near full
    (820.0, 3),
    (990.0, 3),    # capped at the fleet size
])
def test_units_online_from_output(tmp_path, coal, expected):
    df = _load(tmp_path, [_day("2024-03-01", coal)])
    n = acc.units_online(df, STEP, max_units=3, window_hours=4)
    assert (n == expected).all()


def test_units_online_window_sees_neighbouring_output(tmp_path):
    """A unit that is running two units' worth an hour later was online now."""
    df = _load(tmp_path, [_day("2024-03-01", 160.0)])
    df.loc[df["hour"] == 13, "coal_mw"] = 540.0
    n = acc.units_online(df, STEP, max_units=3, window_hours=4)
    assert n[df["hour"] == 12].eq(2).all()
    assert n[df["hour"] == 2].eq(1).all()


def test_headroom_is_measured_against_units_online_not_fleet_floor(tmp_path):
    """The defect this script exists to correct.

    One day with a single unit at 160 MW sets a fleet-wide floor of 160 MW.
    The stressed day runs two units at 160 MW each (320 MW). Against the
    fleet floor that looks like 160 MW of headroom; against two committed
    units it is zero.
    """
    frames = [_day(f"2024-03-{d:02d}", 540.0) for d in range(1, 31)]
    frames.append(_day("2024-03-31", 160.0, midday_mw=1500.0))
    frames.append(_day("2024-04-01", 320.0, midday_mw=800.0))
    df = _load(tmp_path, frames).sort_values("ts_local").reset_index(drop=True)
    daily = acp.daily_table(df)

    stressed = acc.stressed_mask(df, daily, 0.02)
    assert df.loc[stressed, "operating_date"].eq(pd.Timestamp("2024-04-01")).all()

    summary, detail = acc.headroom_table(df, stressed, STEP, 3, window_hours=4)
    assert summary["per_unit_floor_mw"] == pytest.approx(160.0)
    assert detail["units_online"].eq(2).all()
    assert summary["mean_headroom_mw"] == pytest.approx(0.0)
    assert summary["share_at_floor"] == pytest.approx(1.0)


def test_stressed_selection_excludes_outage_days(tmp_path):
    frames = [_day(f"2024-03-{d:02d}", 540.0) for d in range(1, 31)]
    frames.append(_day("2024-03-31", 0.0, midday_mw=20.0))   # system collapse
    df = _load(tmp_path, frames).sort_values("ts_local").reset_index(drop=True)
    stressed = acc.stressed_mask(df, acp.daily_table(df), 0.02)
    assert not df.loc[stressed, "operating_date"].eq(pd.Timestamp("2024-03-31")).any()
