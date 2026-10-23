# Methods, precedents and deviations from the proposal

**Project:** J26-DS-331 · Component 3 · Wijesuriya W A A I (IT23274648)
**Status (2026-10-19):** implemented pipeline; every change from the approved
proposal below is **pending supervisor approval**. NLmin, the SNSP limit and
the scenario years are now cited to the CEB LTGEP 2025–2044. Fleet volume,
station capacity and session behaviour are still tagged `provisional` in
`config/scenarios.json`.

## 1. Method → precedent

| Step | Implementation | Precedent / standard practice | Code |
|---|---|---|---|
| Data cleaning | Flag, never impute; publish an hour only when all four quarter-hours are clean | Standard open-data practice | `preprocess_pucsl_dispatch.py`, `build_hourly_net_demand.py` |
| System boundary | Rescale each PUCSL source to CEB monthly energy; add embedded generation back to demand | Reconciliation against published aggregates (proposal SO1, §3.5) | `build_grid_scenario.py` |
| Available solar | NASA POWER irradiance (3 sites) shaped to CEB monthly grid + rooftop energy | Denholm & Margolis (2007) simulate hourly PV rather than use dispatched output | `build_grid_scenario.py` |
| Net load | NL = demand − solar − wind − must-take (mini hydro, biomass) | Net/residual load as used by NREL and CAISO | `scenario_signal()` |
| Curtailment risk | C(t) = max(NLmin − NL, VRE − SNSP·D, 0) | Minimum-generation curtailment (Denholm & Margolis 2007); SNSP and minimum-unit limits used by EirGrid for island inertia | `scenario_signal()` |
| NLmin | Committed coal units × cited 150 MW minimum + major hydro kept online, both read from the 2024 stressed hours; checked against the 5% reserve and 25% single-unit rules | Unit-commitment view of minimum generation; CEB LTGEP Table 2.7 and p. 7-13 | `derive_nlmin.py` |
| Scenario years | 2024 hourly shapes scaled by LTGEP base-case mid-year capacity (solar, wind, must-take) and net-generation forecast (demand) | Reference-year profile scaling, as in the LTGEP's own studies and Denholm & Margolis (2007) | `ltgep_scenario()` |
| Sessions | GMM over arrival, dwell, energy fitted to ACN-Data; parametric fallback until data is obtained | ACN-Sim (Lee et al. 2021) provides GMM utilities; Monte Carlo sampling as in Ranathunga & Samaliarachchi (2017) | `ev_sessions.py` |
| Uncontrolled baseline | Charge on arrival at max power | "Dumb" charging in Dixon et al. (2020) and ACN-Sim | `asap_profile()` |
| Scheduler | LP, session + station constraints, 100% energy as a hard constraint, HiGHS | Time-coupled LP scheduling (Dixon et al. 2020; ACN-Sim offline optimal) | `solve_lp()` |
| Curtailment term | Epigraph of max(0, ·) so absorbed MWh is exact in the LP | Standard LP reformulation | `solve_lp()` |
| Forecast benchmark | Persistence: plan on yesterday's grid signal, score on today's | Standard naive benchmark (proposal Table 3.2) | `run_experiments.py` |
| Day-ahead forecast | Ridge regression and gradient-boosted trees on calendar, Sri Lankan holiday/Poya and lag features. Each model is chosen on Jul–Sep (fit Jan–Jun) and used only if it beats persistence; every model is scored on the same hours | Standard short-term load-forecasting practice: calendar and lag regressors, naive benchmarks, chronological hold-out with a separate validation period (e.g. Hong & Fan 2016) | `forecast_grid.py` |
| Solar weather input | Archived ECMWF IFS irradiance forecasts at the three NASA sites; per hour, the freshest run published before 00:00 local (day-1 bucket if run + 8 h ≤ issue, else day 2). Feature: yesterday's solar × forecast daylight irradiance ratio (D / D−1), which cancels NWP bias and the monthly level | Model Output Statistics: statistical post-processing of NWP output to a local target (Glahn & Lowry 1972) | `download_nwp_forecast.py`, `forecast_grid.py` |
| Evaluation days | (a) one day per ISO week, rotating weekday: year-representative peak, delay, completion; (b) every schedulable day with risk: absorption and the weight trade-off | Stratified evaluation: a rare-event outcome needs its own stratum | `run_experiments.py` |
| Validation | Independent constraint audit; identical sessions and grid hours across strategies; tie-aware ranking preserved across sweeps, and whether the proposed strategy is ever outranked (a tie becoming a win also breaks exact preservation) | Proposal §3.5 | `audit_schedule()`, `run_experiments.py` |

## 1a. Models trained (as of 2026-10-23)

| Model | Trained on | Validation | Result (Oct–Dec 2024 hold-out) |
|---|---|---|---|
| Demand forecast, gradient-boosted trees | 2024 Jan–Sep boundary-corrected hourly demand | Chosen on Jul–Sep; chronological hold-out | MAE 73 MW vs persistence 130 MW (skill 0.44), MAPE 3.9% |
| Solar forecast, ridge + ECMWF forecast | 2024 Mar–Sep modelled available solar (ECMWF archive starts 6 Mar) | Chosen on Jul–Sep (skill 0.14; GFS 0.00, so ECMWF chosen); chronological hold-out | MAE 19.4 MW vs persistence 23.6 MW (skill 0.17); day-2 forecasts only: skill 0.11 |
| Wind and must-take forecasts | — | Ridge lost to persistence on Jul–Sep | Persistence used. Ridge would have scored skill 0.10 and 0.14 on Oct–Dec; the earlier choice of ridge was made on the test months |
| Combined risk-hour detection (2028 scenario) | Selected models above | Precision and recall of C(t) > 0 hours, same 2,006 hours for every plan | Precision 0.82, recall 0.53, risk MAE 4.2 MW (without the weather forecast: 0.72, 0.51, 5.5 MW; persistence: 0.20, 0.15); risk energy under-forecast by 48% (36% without) |
| Session GMM | Not yet trained: needs ACN-Data download | BIC model selection; plausibility check | — |

The LP scheduler and C(t) are optimisation and physical models; they are
solved or computed, not trained.

## 2. Deviations from the approved proposal

| # | Proposal | Implemented | Reason | Approval |
|---|---|---|---|---|
| D1 | CEB published hourly demand data | PUCSL 15-minute dispatch, reconciled to CEB monthly totals | CEB does not publish hourly series; PUCSL does | pending |
| D2 | NL from published (metered) generation | Available solar modelled from NASA POWER | Metered output is recorded after curtailment, so C(t) from it is near zero | pending |
| D3 | NLmin = sum of MSG + reserve + inertia MW | NLmin = committed coal × 150 MW + hydro kept online = 520 MW; reserve as a check; inertia as the LTGEP 75% SNSP limit (Appendix C) | Addition double-counts synchronous units; inertia is not MW; CEB itself states inertia as SNSP | pending |
| D4 | "Hydro retained as dispatchable" | Major hydro dispatchable; mini hydro and biomass must-take | Mini hydro is run-of-river | pending |
| D5 | Session energy unconstrained | Energy capped at max power × dwell, flagged | Otherwise 100% completion is infeasible by construction | pending |
| D6 | Solver PuLP/Pyomo + HiGHS | SciPy `linprog` + HiGHS | Same solver; sparse matrices are ~100× faster to build at national session counts | none needed |
| D8 | Forecasting layer later (WBS 5.8) | Day-ahead ML forecast now; solar uses archived ECMWF forecasts (no NWP for demand, wind or must-take) | Needed to measure how much benefit survives without perfect foresight | pending |
| D7 | Evidence year 2024 | 2024 implemented; 2025 recommended | Real curtailment began in 2025 (April rooftop pause, weekend solar-farm shutdowns), which allows validating C(t) against known events | **decision needed** |
| D9 | Illustrative solar multiplier | Scenario years 2025–2030 from the LTGEP base case: solar, wind, must-take and demand all scaled; central year 2028 | Scaling solar alone ignores demand growth, which cuts 2028 risk from 133 to 31 GWh | pending |
| D10 | One day per week | Weekly sample plus every schedulable risk day | Risk occurs on few days; a weekly sample alone caught 4 of them | pending |

## 3. Still provisional or missing

1. **NLmin and SNSP limit:** derived and cited (Appendix C); needs review by a
   CEB System Control engineer. 2025 operation may have used a higher floor
   after the February blackout (650–780 MW sweep points).
2. **Session data:** register at ev.caltech.edu, download sessions as JSON, run
   `run_experiments.py --acn-json <file>`.
3. **Fleet volume:** `sessions_per_day` awaits Component 1.
4. **2025 data:** PUCSL April–December 2025 workbooks and NASA POWER 2025 for
   Hambantota and Jaffna.
5. **Storage:** LTGEP plans 105 MW of batteries by 2026 and 405 MW by 2029;
   they would absorb part of C(t). Risk is reported before storage.
6. **Wind:** metered wind is used as available wind; wind curtailment is not modelled.
7. **Declared departures:** the most sensitive input. Infeasible days rise
   from 0 of 42 at 100% declaration to 3 at 80% and 20 at 50%, because
   undeclared drivers charging on arrival fill the shared station connection.
   A station-level priority rule is a possible extension.
8. **2024 solar additions:** the LTGEP plan starts in 2025; 2024 additions are
   set to the plan's 2025 rate (200 MW). Multipliers change by less than 0.15
   for 0–400 MW.

## 4. Novelty (what is and is not new)

Not new, and cited as foundations:
- Inferring curtailment from minimum-generation constraints (Denholm & Margolis 2007).
- LP scheduling of EV sessions against a grid signal (Dixon et al. 2020; ACN-Sim).

New in this component:
1. **Scheduling public EV charging against an inferred, constraint-derived
   curtailment signal** in a system that publishes no curtailment record. Prior
   curtailment-aware scheduling (Dixon et al. 2020) uses measured curtailment.
2. **An island-system formulation of the signal.** It uses minimum synchronous
   generation and an SNSP-style inertia limit rather than oversupply or
   congestion, matching the stated cause of Sri Lanka's 2025 curtailment events.
3. **A quantified test of the "solar-following is mistargeted" claim.** It
   compares solar-following against risk-aware scheduling on identical sessions,
   reporting absorption, peak and driver delay together.
4. **Forecast value.** It measures how much benefit survives when the scheduler
   uses a persistence or a day-ahead ML forecast instead of perfect foresight.
5. **A reconciled Sri Lankan grid dataset.** It is the first documented
   reconciliation of PUCSL 15-minute dispatch with the CEB Data Book. This shows
   that PUCSL excludes rooftop and embedded generation, and that the corrected
   demand matches CEB net generation within 0.1%.

**Position against CEB's own EV study.** The LTGEP 2025–2044 has an
"Aggressive EV Scenario" (§10.17, p. 10-39; Fig. 10.28, p. 10-40). It adds an
aggregate hourly EV profile and shifts a flexible share with time-of-use
tariffs, and finds that daytime charging reduces renewable curtailment. This
component does not repeat that study. It differs in three ways:
- It schedules individual sessions under arrival, departure, energy, charger
  and station limits, not an aggregate profile.
- It targets an hour-by-hour risk signal, C(t), rather than a fixed
  time-of-use window.
- It reports the driver-side cost (delay, completion) next to the grid
  benefit.

The LTGEP result is the planning-level motivation; this component tests
whether the benefit holds at session level.

## References

- Denholm, P., Margolis, R. M. (2007). Evaluating the limits of solar photovoltaics (PV) in traditional electric power systems. *Energy Policy* 35(5), 2852–2861.
- Dixon, J., Bukhsh, W., Edmunds, C., Bell, K. (2020). Scheduling electric vehicle charging to minimise carbon emissions and wind curtailment. *Renewable Energy* 161, 1072–1091.
- Lee, Z. J., Li, T., Low, S. H. (2019). ACN-Data: Analysis and applications of an open EV charging dataset. *Proc. ACM e-Energy*.
- Lee, Z. J., Sharma, S., Johansson, D., Low, S. H. (2021). ACN-Sim: An open-source simulator for data-driven electric vehicle charging research. arXiv:2012.02809. [Verify the journal version and author list before citing.]
- EirGrid. Operational Constraints Update (SNSP, RoCoF and minimum-units constraints).
- Ceylon Electricity Board. Sales and Generation Data Book 2024, pp. 73–74.
- Ceylon Electricity Board. Statistical Digest 2024, p. 1.
- Ceylon Electricity Board (2024). Long Term Generation Expansion Plan 2025–2044. Tables 2.1, 2.3, 2.4, 2.7, 3.3, E3; pp. 2-3, 7-7, 7-13, 10-14, 10-39–10-40.
- NASA Langley Research Center. POWER hourly API, accessed 2026-10-19.
- Glahn, H. R., Lowry, D. A. (1972). The use of Model Output Statistics (MOS) in objective weather forecasting. *Journal of Applied Meteorology* 11(8), 1203–1211.
- Open-Meteo. Previous Runs API (archived ECMWF IFS 0.25° and NCEP GFS forecasts), https://open-meteo.com/en/docs/previous-runs-api, accessed 2026-10-23.
- Hong, T., Fan, S. (2016). Probabilistic electric load forecasting: A tutorial review. *International Journal of Forecasting* 32(3), 914–938. [Verify pages.]
- Sri Lankan public and Poya holidays: `holidays` Python package (country code LK).
