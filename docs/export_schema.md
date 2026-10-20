# Component 3 export format (proposal for FR12)

**Status:** proposed by Component 3, 2026-10-21. It needs agreement by the
group before other components rely on it. The prototype (`app/`, *Plan a day →
Download the results*) already writes these files through
`scripts/dashboard_data.py` (`export_*` functions).

## Conventions (all files)

| Rule | Value |
|---|---|
| Format | UTF-8 CSV with a header row; JSON for the summary |
| Time zone | Sri Lanka, written with its offset: `2024-11-03T09:00:00+05:30` (ISO 8601) |
| Time labels | **Start** of the interval. Hourly rows cover `[t, t+1h)`; schedule rows cover `[t, t+15min)` |
| Power | kW for one car or station, MW for the grid |
| Energy | kWh for cars, MWh for the grid |
| Strategy names | `uncontrolled`, `solar_following`, `curtailment_aware` (shown in the app as *Charge on arrival*, *Charge when sunny*, *Smart charging*) |
| File names | `<kind>_<day>_<scenario year>.<ext>`, e.g. `hourly_2024-11-03_2028.csv` |
| Traceability | Every download has a matching `day_summary_*.json` with the settings that produced it |

The weather and demand shape are always from **2024**. The scenario year only
scales them using the CEB expansion plan (see `docs/methods_and_literature.md`).

## 1. `day_summary_<day>_<year>.json`

| Field | Meaning |
|---|---|
| `schema` | `grid-pulse-ev/component3/day-result/v1`; changes when a field changes |
| `day`, `weather_year` | the 2024 day used, and 2024 |
| `settings` | `scenario_year`, `nlmin_mw`, `snsp_max` (null = off), `sessions_per_day`, `declared_departure_fraction`, `weights`, `station_capacity_kw` |
| `session_source` | `parametric` (placeholder) or `gmm` (fitted to ACN-Data) |
| `experiment_sessions` | true when the sessions are exactly those of the saved full-year study |
| `verdict` | the plain-language sentences shown in the app |
| `metrics` | per strategy: `absorbed_mwh`, `mean_evening_peak_increase_mw`, `evening_peak_reduction_pct_of_ev_increase`, `completion_rate`, `mean_delay_min`, `p95_delay_min`, `constraint_violations`, `solver_s_total` |
| `note` | the curtailment risk is inferred, not measured |

## 2. `hourly_<day>_<year>.csv` (30 rows: the day plus 6 hours of the next morning)

| Column | Unit | Meaning |
|---|---|---|
| `hour_start_local` | time | start of the hour |
| `demand_system_mw` | MW | system demand including embedded generation |
| `solar_available_mw`, `wind_mw` | MW | renewable output available before any curtailment |
| `net_load_mw` | MW | load left for coal, oil and major hydro |
| `nlmin_mw` | MW | minimum secure level of that fleet |
| `curtailment_risk_mw` | MW | inferred renewable power that would be switched off, C(t) |
| `ev_load_mw_<strategy>` | MW | public EV charging load under each strategy |

## 3. `sessions_<day>_<year>.csv` (one row per charging session)

| Column | Unit | Meaning |
|---|---|---|
| `session_id`, `station_id` | | identifiers (synthetic) |
| `arrival_local`, `departure_local` | time | plug-in and leaving times |
| `energy_request_kwh`, `max_power_kw` | kWh, kW | request (capped at what the charger can deliver) and charger rating |
| `declares_departure` | bool | false = always charges on arrival |
| `delivered_kwh_<strategy>` | kWh | energy delivered |
| `finish_local_<strategy>` | time | end of the last 15-minute interval with charging |
| `delay_min_<strategy>` | min | finish time minus the earliest possible finish at full power |

## 4. `schedule_smart_<day>_<year>.csv` (the charging instructions)

One row per session and 15-minute interval with non-zero charging, smart charging only.

| Column | Unit | Meaning |
|---|---|---|
| `session_id`, `station_id` | | as above |
| `interval_start_local` | time | start of the 15-minute interval |
| `power_kw` | kW | constant charging power during the interval |
| `energy_kwh` | kWh | `power_kw × 0.25` |

## Proposed input from Component 1 (fleet scenario)

Every absolute MW and MWh in Component 3 scales with the number of public
charging sessions per day, which is currently a placeholder (10,000). A
minimal file Component 1 could provide:

```json
{
  "schema": "grid-pulse-ev/component1/fleet-scenario/v1",
  "source": "Component 1 adoption model, run <date>",
  "public_sessions_per_day": {"2026": 0, "2027": 0, "2028": 0, "2030": 0}
}
```

Component 3 would then read the value for the chosen scenario year in place of
`central.sessions_per_day` in `config/scenarios.json`. The zeros are
placeholders for Component 1's numbers. **Not yet implemented; this needs
agreement on the file location and units.**

## Questions for the group

1. Does the unified dashboard read these files, or does it call Component 3 directly?
2. Is interval-start labelling acceptable everywhere? PUCSL data uses interval-end labels; Component 3 converts on input.
3. Who owns the fleet scenario, and when can a first version of the file above exist?
