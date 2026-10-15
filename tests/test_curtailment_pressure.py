"""Tests for scripts/analyse_curtailment_pressure.py.

The fixtures are built by hand so that each expected value can be checked by
eye. They are not representative of the real system and no number here should
be quoted anywhere.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import analyse_curtailment_pressure as acp  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixture construction
# --------------------------------------------------------------------------- #

def _day(date: str, midday_mw: float, night_mw: float,
         coal: float = 400.0, oil_ceb: float = 100.0, oil_ipp: float = 50.0,
         hydro: float = 300.0, solar: float = 60.0) -> pd.DataFrame:
    """One full operating day at 15-minute resolution.

    Total dispatch is set to `night_mw` before 05:00, `midday_mw` during
    10:00-14:59, and a mid value elsewhere, so the two comparison windows are
    unambiguous.
    """
    starts = pd.date_range(f"{date} 00:00", periods=96, freq="15min",
                           tz="Asia/Colombo")
    hours = starts.hour

    total = []
    for h in hours:
        if h < 5:
            total.append(night_mw)
        elif 10 <= h <= 14:
            total.append(midday_mw)
        else:
            total.append(max(night_mw, midday_mw) + 400.0)

    return pd.DataFrame({
        "operating_date": pd.Timestamp(date),
        "interval_number": range(1, 97),
        "interval_start_local": starts.tz_convert("UTC").strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"),
        "coal_mw": coal,
        "oil_ceb_mw": oil_ceb,
        "oil_ipp_mw": oil_ipp,
        "major_hydro_mw": hydro,
        "wind_mw": 40.0,
        "solar_mw": solar,
        "biomass_mw": 10.0,
        "mini_hydro_mw": 90.0,
        "total_dispatch_reported_mw": total,
    })


def _write(tmp_path: Path, frames) -> Path:
    path = tmp_path / "dispatch.csv"
    pd.concat(frames, ignore_index=True).to_csv(path, index=False)
    return path


# --------------------------------------------------------------------------- #
# load
# --------------------------------------------------------------------------- #

def test_load_adds_local_time_columns(tmp_path):
    df = acp.load(_write(tmp_path, [_day("2024-03-01", 1500, 1200)]))
    assert len(df) == 96
    # Converting the UTC stamps back to Asia/Colombo must recover local midnight.
    assert df["ts_local"].iloc[0].hour == 0
    assert df["hour"].tolist()[:4] == [0, 0, 0, 0]
    assert df["month"].iloc[0] == 3
    assert df["year"].iloc[0] == 2024


def test_load_rejects_missing_required_column(tmp_path):
    frame = _day("2024-03-01", 1500, 1200).drop(columns=["total_dispatch_reported_mw"])
    path = tmp_path / "bad.csv"
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="missing required columns"):
        acp.load(path)


def test_load_splits_dispatchable_from_must_take(tmp_path):
    df = acp.load(_write(tmp_path, [_day("2024-03-01", 1500, 1200)]))
    # coal 400 + oil 100 + oil 50 + hydro 300
    assert df["dispatchable_mw"].iloc[0] == pytest.approx(850.0)
    # wind 40 + solar 60 + biomass 10 + mini hydro 90
    assert df["must_take_mw"].iloc[0] == pytest.approx(200.0)


def test_major_hydro_counts_as_dispatchable_not_renewable():
    """Guards the central domain assumption of the study.

    Sri Lankan major hydro is dispatchable and counts toward the conventional
    floor. Moving it into MUST_TAKE would materially overstate inferred
    curtailment, so the split is asserted rather than left to a comment.
    """
    assert "major_hydro_mw" in acp.DISPATCHABLE
    assert "major_hydro_mw" not in acp.MUST_TAKE
    assert "mini_hydro_mw" in acp.MUST_TAKE
    assert "solar_mw" in acp.MUST_TAKE


# --------------------------------------------------------------------------- #
# daily_table
# --------------------------------------------------------------------------- #

def test_daily_table_picks_window_minima(tmp_path):
    df = acp.load(_write(tmp_path, [_day("2024-03-01", 1500, 1200)]))
    daily = acp.daily_table(df)
    assert len(daily) == 1
    row = daily.iloc[0]
    assert row["midday_min_mw"] == pytest.approx(1500.0)
    assert row["night_min_mw"] == pytest.approx(1200.0)
    assert row["intervals"] == 96


def test_midday_below_night_flag(tmp_path):
    frames = [
        _day("2024-03-01", 1500, 1200),   # normal: midday above night
        _day("2024-03-02", 1100, 1200),   # inverted: solar pushes midday under
    ]
    daily = acp.daily_table(acp.load(_write(tmp_path, frames)))
    assert daily["midday_below_night"].tolist() == [False, True]


def test_outage_day_detected_and_excluded_from_monthly(tmp_path):
    frames = [
        _day("2024-03-01", 1500, 1200),
        _day("2024-03-02", 1100, 1200),
        _day("2024-03-03", 50, 1200),     # collapse: a system event, not demand
    ]
    daily = acp.daily_table(acp.load(_write(tmp_path, frames)))
    assert daily["is_outage_day"].tolist() == [False, False, True]

    monthly = acp.monthly_table(daily)
    assert monthly["days"].iloc[0] == 2          # outage day dropped
    assert monthly["days_midday_below_night"].iloc[0] == 1
    assert monthly["share_midday_below_night"].iloc[0] == pytest.approx(0.5)


def test_outage_detection_is_by_collapse_not_by_date(tmp_path):
    """2025-02-09 is the known blackout, but detection must generalise."""
    daily = acp.daily_table(acp.load(_write(tmp_path, [
        _day("2026-07-04", 10, 1200),
    ])))
    assert bool(daily["is_outage_day"].iloc[0]) is True


def test_incomplete_day_is_skipped(tmp_path):
    """A day with no overnight readings cannot be compared and must not
    contribute a half-formed row."""
    partial = _day("2024-03-01", 1500, 1200)
    partial = partial[partial["interval_number"] > 40]   # drops all hours < 5
    daily = acp.daily_table(acp.load(_write(tmp_path, [partial])))
    assert daily.empty


# --------------------------------------------------------------------------- #
# composition
# --------------------------------------------------------------------------- #

def test_composition_reports_backdown_of_dispatchable_fleet(tmp_path):
    """Thirty ordinary days and one deeply suppressed day.

    The stressed column must show the suppressed day's mix, confirming the
    quantile split selects on total dispatch rather than on date order.
    """
    frames = [_day(f"2024-03-{d:02d}", 1600, 1200) for d in range(1, 31)]
    frames.append(_day("2024-03-31", 700, 1200,
                       coal=250.0, oil_ceb=0.0, oil_ipp=0.0, hydro=150.0))

    df = acp.load(_write(tmp_path, frames))
    daily = acp.daily_table(df)
    table = acp.composition(df, daily, stressed_quantile=0.03)

    coal = table.set_index("series").loc["coal_mw"]
    assert coal["stressed_mean_mw"] == pytest.approx(250.0)
    assert coal["typical_mean_mw"] == pytest.approx(400.0)
    assert coal["change_mw"] < 0

    oil = table.set_index("series").loc["oil_ceb_mw"]
    assert oil["stressed_mean_mw"] == pytest.approx(0.0)


def test_composition_excludes_outage_days(tmp_path):
    """An outage must not be mistaken for the lowest-demand intervals, or it
    would dominate the stressed group and make the fleet look cornered."""
    frames = [_day(f"2024-03-{d:02d}", 1600, 1200) for d in range(1, 31)]
    frames.append(_day("2024-03-31", 20, 20, coal=0.0, oil_ceb=0.0,
                       oil_ipp=0.0, hydro=0.0))

    df = acp.load(_write(tmp_path, frames))
    daily = acp.daily_table(df)
    table = acp.composition(df, daily, stressed_quantile=0.03)

    # Every remaining day is identical, so coal is flat at 400 MW.
    coal = table.set_index("series").loc["coal_mw"]
    assert coal["stressed_mean_mw"] == pytest.approx(400.0)


def test_composition_returns_empty_when_no_midday_data(tmp_path):
    df = acp.load(_write(tmp_path, [_day("2024-03-01", 1500, 1200)]))
    df = df[df["hour"] < 5]          # strip the midday window
    daily = acp.daily_table(acp.load(_write(tmp_path,
                                            [_day("2024-03-01", 1500, 1200)])))
    assert acp.composition(df, daily).empty


# --------------------------------------------------------------------------- #
# monthly / trend
# --------------------------------------------------------------------------- #

def test_monthly_table_separates_years(tmp_path):
    frames = [_day("2024-03-01", 1600, 1200), _day("2025-03-01", 1100, 1200)]
    monthly = acp.monthly_table(acp.daily_table(acp.load(_write(tmp_path, frames))))
    assert monthly["year"].tolist() == [2024, 2025]
    assert monthly["month_name"].tolist() == ["Mar", "Mar"]
    assert monthly["share_midday_below_night"].tolist() == [0.0, 1.0]


def test_peak_metered_solar_is_carried_through(tmp_path):
    frames = [_day("2024-03-01", 1600, 1200, solar=95.0)]
    monthly = acp.monthly_table(acp.daily_table(acp.load(_write(tmp_path, frames))))
    assert monthly["peak_metered_solar_mw"].iloc[0] == pytest.approx(95.0)
