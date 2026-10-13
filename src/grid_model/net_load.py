"""Basic unit-tested power-balance definitions — NOT a validated curtailment model."""

from __future__ import annotations


def observed_net_demand_mw(demand_mw: float, solar_actual_mw: float, wind_actual_mw: float) -> float:
    """Demand minus ACTUALLY generated variable renewables, all in MW.

    Requires matching timestamps/system boundaries. This quantity cannot
    reveal energy that was curtailed before actual generation was reported.
    """
    vals = (demand_mw, solar_actual_mw, wind_actual_mw)
    if any(v < 0 for v in vals):
        raise ValueError("Demand and actual generation must be non-negative MW")
    return demand_mw - solar_actual_mw - wind_actual_mw


def energy_mwh(power_mw: float, interval_minutes: int) -> float:
    """Integrate constant average power within one interval."""
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be > 0")
    return power_mw * interval_minutes / 60.0


def illustrative_potential_surplus_mw(
    demand_mw: float, minimum_dispatchable_mw: float, variable_renewable_available_mw: float
) -> float:
    """Simple illustrative surplus proxy, NOT operational CEB curtailment.

    Must not be used for the dissertation without physically credible
    derivation/validation of must-run, reserve, inertia and network limits.
    """
    if min(demand_mw, minimum_dispatchable_mw, variable_renewable_available_mw) < 0:
        raise ValueError("Values must be non-negative")
    return max(0.0, minimum_dispatchable_mw + variable_renewable_available_mw - demand_mw)
