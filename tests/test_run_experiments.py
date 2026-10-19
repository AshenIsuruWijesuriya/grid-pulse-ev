"""Tests for the experiment harness helpers in scripts/run_experiments.py."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_experiments as rx  # noqa: E402

TZ = "Asia/Colombo"


def test_sample_days_one_per_week_rotating_weekday():
    days = list(pd.date_range("2024-01-01", "2024-03-31", freq="D", tz=TZ))
    picked = rx.sample_days(days)
    weeks = [d.isocalendar()[:2] for d in picked]
    assert len(weeks) == len(set(weeks))
    assert {d.dayofweek for d in picked} == set(range(7))


def test_persistence_plan_uses_previous_day():
    idx = pd.date_range("2024-01-01", periods=72, freq="h", tz=TZ)
    sig = pd.DataFrame({c: np.arange(72.0) for c in rx.PLAN_COLUMNS}, index=idx)
    sig["nlmin_mw"] = 700.0
    plan = rx.persistence_plan(sig)
    assert plan["net_load_mw"].iloc[30] == sig["net_load_mw"].iloc[6]
    assert plan["nlmin_mw"].iloc[30] == 700.0
    assert plan["net_load_mw"].iloc[:24].isna().all()


def test_schedulable_days_need_neighbours_complete():
    idx = pd.date_range("2024-01-01", periods=24 * 5, freq="h", tz=TZ)
    base = pd.DataFrame({"hour_status": "complete", "is_system_event_day": False}, index=idx)
    base.loc[idx[24 * 3 + 2], "hour_status"] = "missing"      # 4 Jan 02:00
    got = [d.day for d in rx.schedulable_days(base)]
    # 1 Jan has no previous day; 3 Jan's next morning is incomplete; 4 Jan is not eligible;
    # 5 Jan has no previous eligible day and no next morning.
    assert got == [2]


def test_ranking_orders_absorption_and_peak():
    m = {"uncontrolled": {"absorbed_mwh": 1, "mean_evening_peak_mw": 30},
         "solar_following": {"absorbed_mwh": 2, "mean_evening_peak_mw": 20},
         "curtailment_aware": {"absorbed_mwh": 3, "mean_evening_peak_mw": 10},
         "curtailment_aware_persistence": {"absorbed_mwh": 9, "mean_evening_peak_mw": 1}}
    r = rx.ranking(m)
    assert r["by_absorption"] == ["curtailment_aware", "solar_following", "uncontrolled"]
    assert r["by_evening_peak"] == ["curtailment_aware", "solar_following", "uncontrolled"]
