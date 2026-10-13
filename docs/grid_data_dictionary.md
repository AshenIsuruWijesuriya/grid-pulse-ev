# Target grid-data schema (not a claim that these data are publicly available)

| Column | Unit | Meaning | Required? |
|---|---|---|---|
| `timestamp` | ISO 8601 with offset | Start of the observation interval; include `+05:30` or `Z` | Yes |
| `demand_mw` | MW | CEB national power demand at matching system boundary | Yes |
| `solar_actual_mw` | MW | Measured or documented actual (dispatched) PV generation | Yes |
| `wind_actual_mw` | MW | Measured or documented actual wind generation | Yes |
| `solar_available_mw` | MW | Estimated pre-curtailment PV availability, if separately modeled | Optional; not equivalent to actual |
| `wind_available_mw` | MW | Estimated pre-curtailment wind availability, if separately modeled | Optional; not equivalent to actual |

Do not invent unavailable measured columns. If the CEB source does not have hourly source-wise MW, document the missing variable and discuss an approved modeling alternative with the supervisor.

`observed_net_demand_mw = demand_mw - solar_actual_mw - wind_actual_mw` is merely an accounting definition; alone it **cannot estimate already-curtailed energy** when actual generation has been restricted. Hydro is not subtracted as variable solar/wind here because the proposal treats it as dispatchable. Grid boundary, distributed PV, time-zone, transmission losses and inclusion conventions must be checked.

At hourly resolution, each observation represents **one hour**; at 30-minute resolution it represents **0.5 hours**. For example, 10 MW held for 30 minutes = 5 MWh.
