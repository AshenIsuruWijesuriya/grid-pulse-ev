"""Tests for the NLmin derivation in scripts/derive_nlmin.py."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import derive_nlmin as dn  # noqa: E402

PARAMS = {"coal_unit_net_mw": 270, "coal_unit_min_mw": 150, "coal_units_installed": 3,
          "spinning_reserve_share_of_demand": 0.05, "max_single_unit_share_of_demand": 0.25,
          "snsp_planning_limit": 0.75, "stress_quantile": 0.1}


def _hours(n=100, stressed=10):
    """`stressed` low-output hours with 2 coal units and 200-290 MW hydro; the rest high."""
    idx = pd.date_range("2024-03-01", periods=n, freq="h", tz="Asia/Colombo")
    h = pd.DataFrame({"coal_mw": 700.0, "oil_ceb_mw": 50.0, "oil_ipp_mw": 0.0,
                      "major_hydro_mw": 800.0, "demand_system_mw": 2000.0, "vre_mw": 300.0},
                     index=idx)
    h.iloc[:stressed, h.columns.get_loc("coal_mw")] = 300.0       # ceil(300/270) = 2 units
    h.iloc[:stressed, h.columns.get_loc("oil_ceb_mw")] = 0.0
    h.iloc[:stressed, h.columns.get_loc("major_hydro_mw")] = np.arange(200.0, 300.0, 10.0)
    h.iloc[:stressed, h.columns.get_loc("demand_system_mw")] = 1200.0
    h["dispatchable_mw"] = h["coal_mw"] + h["oil_ceb_mw"] + h["oil_ipp_mw"] + h["major_hydro_mw"]
    return h


def test_scenarios_follow_formula():
    info = dn.derive(_hours(), PARAMS)
    sc = info["scenarios"]
    hydro = pd.Series(np.arange(200.0, 300.0, 10.0))
    assert info["stressed_hours"]["count"] == 10
    assert info["stressed_hours"]["coal_units_online_distribution"] == {2: 10}
    assert sc["low"]["nlmin_mw"] == pytest.approx(1 * 150 + 200.0)
    assert sc["central"]["nlmin_mw"] == pytest.approx(2 * 150 + hydro.quantile(0.10), abs=0.05)
    assert sc["high"]["nlmin_mw"] == pytest.approx(3 * 150 + hydro.median())
    assert info["stressed_hours"]["oil_mw"]["share_off_below_5mw"] == 1.0


def test_sweep_is_sorted_and_spans_scenarios():
    info = dn.derive(_hours(), PARAMS)
    sweep = info["sweep_mw"]
    assert sweep == sorted(sweep) and len(sweep) <= 5
    assert sweep[0] == round(info["scenarios"]["low"]["nlmin_mw"], -1)
    assert sweep[-1] == round(info["scenarios"]["high"]["nlmin_mw"], -1)


def test_security_checks():
    info = dn.derive(_hours(), PARAMS)
    ch = info["checks_central"]
    assert ch["coal_headroom_mw"] == 2 * (270 - 150)
    assert ch["spinning_reserve_required_mw_max"] == pytest.approx(0.05 * 1200)
    assert ch["reserve_met_by_coal_headroom_share"] == 1.0
    assert ch["single_unit_le_25pct_demand_share"] == 1.0      # 270 <= 0.25 x 1200
    # Lowest observed hour is 300 + 200 = 500 MW, below the 509 MW central floor.
    assert ch["hours_observed_below_central_floor"] == 1
    assert info["snsp"]["observed_2024_max_share_vre_of_demand"] == pytest.approx(0.25)
