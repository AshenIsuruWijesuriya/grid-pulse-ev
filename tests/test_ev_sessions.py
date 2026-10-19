"""Tests for scripts/ev_sessions.py. Synthetic fixtures only."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import ev_sessions as evs  # noqa: E402

PARAMS = {"arrival_hour_range": [7.0, 21.0], "dwell_hours_range": [0.25, 2.0],
          "energy_kwh_range": [6.0, 32.0], "charger_kw": [3.6, 7.2, 11.0],
          "charger_probability": [0.1, 0.75, 0.15]}
CFG = {"central": {"sessions_per_day": 50, "sessions_per_station_per_day": 5,
                   "declared_departure_fraction": 0.8},
       "sessions": {"parametric": PARAMS}}


def test_parametric_on_grid_and_feasible_by_construction():
    s = evs.parametric_sessions(["2024-04-01", "2024-04-02"], 200, PARAMS,
                                np.random.default_rng(1))
    assert len(s) == 400 and s["session_id"].is_unique
    for col in ("arrival_local", "departure_local"):
        assert (s[col].dt.minute % 15 == 0).all()
    dwell_h = (s["departure_local"] - s["arrival_local"]).dt.total_seconds() / 3600
    assert (s["energy_request_kwh"] <= s["max_power_kw"] * dwell_h + 1e-9).all()
    # Short dwell forces some caps; they are flagged, not hidden.
    assert s["energy_capped"].any()


def test_session_set_reproducible_and_declared_share():
    a = evs.build_session_set(["2024-04-01"], CFG, seed=7)
    b = evs.build_session_set(["2024-04-01"], CFG, seed=7)
    pd.testing.assert_frame_equal(a, b)
    assert a["station_id"].max() < 10
    full = evs.build_session_set(["2024-04-01"], CFG, seed=7, declared_fraction=1.0)
    assert full["declares_departure"].all()
    # Changing only the declared share leaves the sessions themselves unchanged.
    pd.testing.assert_series_equal(a["energy_request_kwh"], full["energy_request_kwh"])


def test_declared_fraction_is_validated():
    s = evs.parametric_sessions(["2024-04-01"], 5, PARAMS, np.random.default_rng(0))
    with pytest.raises(ValueError):
        evs.declare_departures(s, 1.5, np.random.default_rng(0))


def test_gmm_fit_and_sample_valid_rows():
    rng = np.random.default_rng(3)
    feats = pd.DataFrame({"arrival_hour": rng.normal(9, 1, 300).clip(0, 23.9),
                          "duration_h": rng.normal(6, 1.5, 300).clip(0.5, 12),
                          "energy_kwh": rng.normal(12, 4, 300).clip(1, 40)})
    model = evs.fit_gmm(feats, max_components=3, seed=0)
    s = evs.gmm_sessions(model, ["2024-04-01"], 100, PARAMS["charger_kw"],
                         PARAMS["charger_probability"], np.random.default_rng(4))
    assert len(s) == 100
    assert (s["data_origin"] == "synthetic_gmm_fitted_acn_data").all()
    assert (s["departure_local"] > s["arrival_local"]).all()


def test_load_acn_sessions_converts_site_time_and_drops_invalid(tmp_path):
    items = [
        {"sessionID": "a", "siteID": "0002", "timezone": "America/Los_Angeles",
         "connectionTime": "Wed, 25 Apr 2018 15:00:00 GMT",
         "disconnectTime": "Wed, 25 Apr 2018 23:00:00 GMT", "kWhDelivered": 10.5},
        {"sessionID": "b", "siteID": "0002", "timezone": "America/Los_Angeles",
         "connectionTime": "Wed, 25 Apr 2018 15:00:00 GMT",
         "disconnectTime": "Wed, 25 Apr 2018 14:00:00 GMT", "kWhDelivered": 3.0},
    ]
    p = tmp_path / "acn.json"
    p.write_text(json.dumps({"_items": items}))
    df = evs.load_acn_sessions(p)
    assert df["session_id"].tolist() == ["a"]
    assert df.loc[0, "arrival_hour"] == pytest.approx(8.0)     # 15:00 UTC = 08:00 PDT
    assert df.loc[0, "duration_h"] == pytest.approx(8.0)


def test_plausibility_report():
    s = evs.build_session_set(["2024-04-01"], CFG, seed=1)
    r = evs.plausibility(s, kwh_per_km=0.16)
    assert r["sessions"] == 50
    assert r["implied_km_median"] > 0
