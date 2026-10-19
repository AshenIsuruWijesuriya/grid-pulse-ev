"""Small deterministic tests for the synthetic EV baseline prototype."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ev_charging_baseline import generate_synthetic_sessions, simulate_uncontrolled, validated_sessions, run_experiment


def test_seed_reproducible():
    a = generate_synthetic_sessions(days=2, sessions_per_day=3, seed=123)
    b = generate_synthetic_sessions(days=2, sessions_per_day=3, seed=123)
    pd.testing.assert_frame_equal(a, b)
    assert len(a) == 6
    assert (a.data_origin == "synthetic_illustrative_not_calibrated").all()


def fixture_session(energy=3.0, power=4.0, arrival="2024-04-01T09:00:00+05:30",
                    departure="2024-04-01T10:00:00+05:30"):
    return pd.DataFrame([{"session_id": "s1", "arrival_local": arrival,
                          "departure_local": departure, "energy_request_kwh": energy,
                          "max_power_kw": power}])


def test_full_charge_and_15_minute_energy():
    profile, o, m = simulate_uncontrolled(fixture_session())
    assert m["number_of_sessions"] == 1
    assert m["total_delivered_kwh"] == pytest.approx(3.0)
    assert m["total_unmet_kwh"] == pytest.approx(0.0)
    assert profile["site_charging_kw"].tolist() == pytest.approx([4, 4, 4, 0])
    assert o.loc[0, "completion_local"].startswith("2024-04-01T09:45:00")


def test_insufficient_dwell_records_unmet():
    _, o, m = simulate_uncontrolled(fixture_session(energy=9.0))
    assert m["total_delivered_kwh"] == pytest.approx(4.0)
    assert m["total_unmet_kwh"] == pytest.approx(5.0)
    assert not bool(o.loc[0, "fully_served"])


def test_two_concurrent_sessions_add_load():
    x = fixture_session(energy=1.0, power=4.0)
    y = x.copy()
    y.loc[0, "session_id"] = "s2"
    profile, _, metrics = simulate_uncontrolled(pd.concat([x, y], ignore_index=True))
    assert profile.loc[0, "site_charging_kw"] == pytest.approx(8.0)
    assert metrics["maximum_concurrent_connected"] == 2


def test_reject_duplicate_and_nonpositive():
    x = fixture_session()
    with pytest.raises(ValueError, match="unique"):
        validated_sessions(pd.concat([x, x], ignore_index=True))
    x.loc[0, "energy_request_kwh"] = 0
    with pytest.raises(ValueError, match="finite"):
        validated_sessions(x)


def test_reject_misaligned_arrival_and_deadline():
    x = fixture_session(arrival="2024-04-01T09:01:00+05:30")
    with pytest.raises(ValueError, match="align"):
        validated_sessions(x)
    x = fixture_session(departure="2024-04-01T08:45:00+05:30")
    with pytest.raises(ValueError, match="after arrival"):
        validated_sessions(x)


def test_output_files(tmp_path):
    metrics = run_experiment(tmp_path, days=1, sessions_per_day=3, seed=2)
    assert metrics["number_of_sessions"] == 3
    for fn in ["synthetic_ev_sessions.csv", "uncontrolled_site_load_15min.csv",
               "uncontrolled_session_outcomes.csv", "uncontrolled_baseline_metrics.json"]:
        assert (tmp_path / fn).is_file()
