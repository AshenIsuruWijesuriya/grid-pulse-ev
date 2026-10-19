# Methods, precedents and deviations from the proposal

**Project:** J26-DS-331 · Component 3 · Wijesuriya W A A I (IT23274648)
**Status (2026-10-19):** implemented pipeline; every change from the approved
proposal below is **pending supervisor approval**. Parameters tagged
`provisional` in `config/scenarios.json` are placeholders for sweeps.

## 1. Method → precedent

| Step | Implementation | Precedent / standard practice | Code |
|---|---|---|---|
| Data cleaning | Flag, never impute; publish an hour only when all four quarter-hours are clean | Standard open-data practice | `preprocess_pucsl_dispatch.py`, `build_hourly_net_demand.py` |
| System boundary | Rescale each PUCSL source to CEB monthly energy; add embedded generation back to demand | Reconciliation against published aggregates (proposal SO1, §3.5) | `build_grid_scenario.py` |
| Available solar | NASA POWER irradiance (3 sites) shaped to CEB monthly grid + rooftop energy | Denholm & Margolis (2007) simulate hourly PV rather than use dispatched output | `build_grid_scenario.py` |
| Net load | NL = demand − solar − wind − must-take (mini hydro, biomass) | Net/residual load as used by NREL and CAISO | `scenario_signal()` |
| Curtailment risk | C(t) = max(NLmin − NL, VRE − SNSP·D, 0) | Minimum-generation curtailment (Denholm & Margolis 2007); SNSP and minimum-unit limits used by EirGrid for island inertia | `scenario_signal()` |
| Sessions | GMM over arrival, dwell, energy fitted to ACN-Data; parametric fallback until data is obtained | ACN-Sim (Lee et al. 2021) provides GMM utilities; Monte Carlo sampling as in Ranathunga & Samaliarachchi (2017) | `ev_sessions.py` |
| Uncontrolled baseline | Charge on arrival at max power | "Dumb" charging in Dixon et al. (2020) and ACN-Sim | `asap_profile()` |
| Scheduler | LP, session + station constraints, 100% energy as a hard constraint, HiGHS | Time-coupled LP scheduling (Dixon et al. 2020; ACN-Sim offline optimal) | `solve_lp()` |
| Curtailment term | Epigraph of max(0, ·) so absorbed MWh is exact in the LP | Standard LP reformulation | `solve_lp()` |
| Forecast benchmark | Persistence: plan on yesterday's grid signal, score on today's | Standard naive benchmark (proposal Table 3.2) | `run_experiments.py` |
| Day-ahead forecast | Ridge regression and gradient-boosted trees on calendar, Sri Lankan holiday/Poya and lag features; a model is used only if it beats persistence | Standard short-term load-forecasting practice: calendar and lag regressors, naive benchmarks, chronological hold-out (e.g. Hong & Fan 2016) | `forecast_grid.py` |
| Validation | Independent constraint audit; identical sessions and grid hours across strategies; ranking preserved across sweeps | Proposal §3.5 | `audit_schedule()`, `run_experiments.py` |

## 1a. Models trained (as of 2026-10-19)

| Model | Trained on | Validation | Result (Oct–Dec 2024 hold-out) |
|---|---|---|---|
| Demand forecast, gradient-boosted trees | 2024 Jan–Sep boundary-corrected hourly demand | Chronological hold-out | MAE 75 MW vs persistence 132 MW (skill 0.43) |
| Wind and must-take forecasts, ridge | same | same | Skill 0.09 and 0.12 over persistence |
| Solar forecast | None selected; no learned model beat persistence without weather forecasts | same | Persistence used (MAE 24 MW) |
| Combined risk-hour detection | Selected models above | Precision and recall of C(t) > 0 hours | Precision 0.75, recall 0.59 (persistence: 0.24 and 0.22); risk energy under-forecast by 25% |
| Session GMM | Not yet trained: needs ACN-Data download | BIC model selection; plausibility check | — |

The LP scheduler and C(t) are optimisation and physical models; they are
solved or computed, not trained.

## 2. Deviations from the approved proposal

| # | Proposal | Implemented | Reason | Approval |
|---|---|---|---|---|
| D1 | CEB published hourly demand data | PUCSL 15-minute dispatch, reconciled to CEB monthly totals | CEB does not publish hourly series; PUCSL does | pending |
| D2 | NL from published (metered) generation | Available solar modelled from NASA POWER | Metered output is recorded after curtailment, so C(t) from it is near zero | pending |
| D3 | NLmin = sum of MSG + reserve + inertia MW | Unit-commitment derivation (Appendix C §6) plus optional SNSP limit | Addition double-counts synchronous units; inertia is not MW | pending |
| D4 | "Hydro retained as dispatchable" | Major hydro dispatchable; mini hydro and biomass must-take | Mini hydro is run-of-river | pending |
| D5 | Session energy unconstrained | Energy capped at max power × dwell, flagged | Otherwise 100% completion is infeasible by construction | pending |
| D6 | Solver PuLP/Pyomo + HiGHS | SciPy `linprog` + HiGHS | Same solver; sparse matrices are ~100× faster to build at national session counts | none needed |
| D8 | Forecasting layer later (WBS 5.8) | Day-ahead ML forecast now; no NWP inputs yet | Needed to measure how much benefit survives without perfect foresight | pending |
| D7 | Evidence year 2024 | 2024 implemented; 2025 recommended | Real curtailment began in 2025 (April rooftop pause, weekend solar-farm shutdowns), which allows validating C(t) against known events | **decision needed** |

## 3. Still provisional or missing

1. **NLmin and SNSP limit:** need cited CEB unit data (Appendix C §3–§5). The
   2024 cross-check shows observed dispatchable generation fell below 700 MW in
   32 hours, so a 2024-consistent floor is at or below about 575 MW.
2. **Session data:** register at ev.caltech.edu, download sessions as JSON, run
   `run_experiments.py --acn-json <file>`.
3. **Fleet volume:** `sessions_per_day` awaits Component 1.
4. **2025 data:** PUCSL April–December 2025 workbooks and NASA POWER 2025 for
   Hambantota and Jaffna.
5. **Solar growth:** `solar_multiplier` is illustrative; derive it from installed
   capacity (SLSEA/CEB) for each scenario year.
6. **Wind:** metered wind is used as available wind; wind curtailment is not modelled.

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
   uses a persistence forecast instead of perfect foresight.
5. **A reconciled Sri Lankan grid dataset.** It is the first documented
   reconciliation of PUCSL 15-minute dispatch with the CEB Data Book. This shows
   that PUCSL excludes rooftop and embedded generation, and that the corrected
   demand matches CEB net generation within 0.1%.

## References

- Denholm, P., Margolis, R. M. (2007). Evaluating the limits of solar photovoltaics (PV) in traditional electric power systems. *Energy Policy* 35(5), 2852–2861.
- Dixon, J., Bukhsh, W., Edmunds, C., Bell, K. (2020). Scheduling electric vehicle charging to minimise carbon emissions and wind curtailment. *Renewable Energy* 161, 1072–1091.
- Lee, Z. J., Li, T., Low, S. H. (2019). ACN-Data: Analysis and applications of an open EV charging dataset. *Proc. ACM e-Energy*.
- Lee, Z. J., Sharma, S., Johansson, D., Low, S. H. (2021). ACN-Sim: An open-source simulator for data-driven electric vehicle charging research. arXiv:2012.02809. [Verify the journal version and author list before citing.]
- EirGrid. Operational Constraints Update (SNSP, RoCoF and minimum-units constraints).
- Ceylon Electricity Board. Sales and Generation Data Book 2024, pp. 73–74.
- NASA Langley Research Center. POWER hourly API, accessed 2026-10-19.
- Hong, T., Fan, S. (2016). Probabilistic electric load forecasting: A tutorial review. *International Journal of Forecasting* 32(3), 914–938. [Verify pages.]
- Sri Lankan public and Poya holidays: `holidays` Python package (country code LK).
