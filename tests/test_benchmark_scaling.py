"""Tests for the NFR1/NFR2 runtime benchmark in scripts/benchmark_scaling.py."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import benchmark_scaling as bs  # noqa: E402
import build_grid_scenario as bgs  # noqa: E402

TZ = "Asia/Colombo"


def test_run_one_reports_optimal_audited_solve():
    day = pd.Timestamp("2024-03-10", tz=TZ)
    idx = pd.date_range(day - pd.offsets.Day(1), periods=24 * 3, freq="h")
    solar = np.clip(np.sin((idx.hour - 6) / 12 * np.pi), 0, None) * 900.0
    base = pd.DataFrame({"demand_system_mw": 1500.0, "solar_available_mw": solar,
                         "wind_mw": 0.0, "must_take_mw": 0.0}, index=idx)
    signal = bgs.scenario_signal(base, nlmin_mw=800.0)
    cfg = bgs.load_config(bgs.DEFAULT_CONFIG)
    row = bs.run_one(day, signal, cfg, 40, seed=1)
    assert row["sessions"] == 40 and row["status"] == "optimal"
    assert row["constraint_violations"] == 0
    assert row["stations"] == 5                         # 40 sessions / 8 per station
    assert row["session_slot_variables"] > 0 and row["total_s"] >= row["solver_s"]
    assert cfg["central"]["sessions_per_day"] == 10000  # the config is not modified
