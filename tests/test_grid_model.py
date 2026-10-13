import pytest

from src.grid_model.net_load import (
    observed_net_demand_mw, energy_mwh, illustrative_potential_surplus_mw
)


def test_observed_net_demand_uses_dispatched_generation():
    assert observed_net_demand_mw(1500, 500, 0) == 1000


def test_mw_mwh_units_for_30_minutes():
    assert energy_mwh(10, 30) == 5


def test_illustrative_surplus_is_not_actual_dispatch():
    assert illustrative_potential_surplus_mw(1500, 1000, 700) == 200


def test_invalid_values_raise():
    with pytest.raises(ValueError):
        observed_net_demand_mw(1500, -10, 0)
    with pytest.raises(ValueError):
        energy_mwh(10, 0)
