# Research implementation and decision log

## Session 1 — project setup and data feasibility

**Approved research topic:** Optimizing Public EV Charging Schedules for Renewable-Curtailment Absorption and Peak Reduction in Sri Lanka.

### Planned actions
- [ ] Create the repository and VS Code Python environment
- [ ] Run starter unit tests
- [ ] Review official CEB available files and record hourly-data access status
- [ ] Request/source compatible hourly grid data, if necessary
- [ ] Download one NASA POWER pilot dataset and inspect time alignment
- [ ] Show supervisor the data-availability matrix and modeling assumptions

### Scientific guardrails
1. Keep topic and SO1–SO6 unchanged. Correct formulas and assumptions during implementation.
2. Distinguish observed electricity generation, modeled renewable availability, and inferred curtailment.
3. Record every approximation or external dataset distribution.
4. Do not claim national scheduling effectiveness before feasible-session and scenario experiments.

### Decisions and evidence
Fill in after each meeting or experiment: Date | Decision | Evidence | Impact | Supervisor approval (if relevant).

## 2026-10-14 — PUCSL Dispatch Data Preprocessing

**Activity:** Developed and tested a reusable Python
preprocessing module for PUCSL 15-minute electricity
dispatch data.

**Dataset:** January 2025 PUCSL dispatch workbook.

**Results:**
- Processed 31 daily worksheets.
- Extracted 2,976 quarter-hour observations.
- Generated normalized dispatch and reconciliation CSVs.
- Flagged 30 negative generation observations.
- Successfully passed 11 automated project tests.

**Limitations:**
- Daily energy reconciliation discrepancy remains unresolved.
- Reported dispatch is not yet verified as national demand.
- Negative observations require further investigation.

**Next task:** Process additional monthly PUCSL workbooks
and investigate the reporting discrepancies.

## 2026-10-15 — Curtailment-pressure analysis and coal-commitment correction

**Activity:** Reproduced `scripts/analyse_curtailment_pressure.py` on 2024 +
Jan–Mar 2025 consolidated data (42,432 intervals; 2025-02-09 outage excluded).
All headline figures reproduced.

**Correction:** The earlier verdict that ~160 MW of coal headroom remained in
the lowest midday intervals compared two-unit operation against a floor set by
one-unit periods. New `scripts/analyse_coal_commitment.py` measures headroom
against the units actually online. Upper-bound mean headroom is 60–74 MW (2–4 h
window) and ~20 MW (8–24 h). Coal was within 10% of its commitment floor in
27–60% of stressed intervals. The per-unit floor (151–156 MW) is observed, not
a cited technical minimum.

**Also:** LP solver confirmed (PuLP 3.3.2 + HiGHS 1.15.1; infeasibility
reported explicitly); PuLP pinned <4 because of breaking API changes. NASA
POWER Colombo 2025 insolation is 5.46 kWh/m²/day (expected 5.0–5.5).
`docs/nlmin_derivation.md` (Appendix C) created with an empirical cross-check:
minimum NL(t) was 662 MW in 2024 and 696 MW in 2025.

**Impact:** "Floor not binding" weakens to "floor close, binding in a share of
intervals". The forward-looking framing still holds but needs this nuance.

**Supervisor approval:** pending; reframing not yet discussed.

**Next:** fill §3–§5 of the NLmin worksheet from cited CEB sources; discuss the
reframing with the supervisor; then build NL(t)/C(t) at hourly resolution (SO1/SO2).

### Addendum — methodological review of the coal-commitment analysis

**Review findings (accepted):** (1) the rolling-window unit count is a
heuristic, not a lower bound, so the earlier "upper bound on headroom" claim was
unjustified; (2) off or quality-flagged coal intervals could feed the rolling
window and the per-unit floor; (3) `review_required` intervals were not
excluded from the analyses; (4) the curtailment-pressure script printed a
floor-binding verdict that the data cannot support.

**Changes:** both analysis scripts now exclude `review_required` intervals,
require complete 96-interval days, never assign units to off or flagged
intervals, and print no curtailment verdict.
`tests/test_analysis_quality_regressions.py` adds 9 regression tests (79 total
passing). Appendix C §6 replaces the additive NLmin formula with a constrained
unit-commitment derivation; §9 wording is marked as a draft pending supervisor
approval.

**Re-run results:** essentially unchanged after filtering. There are 168
stressed intervals (was 179). Mean coal headroom is 74 / 60 / 20 / 12 MW for
2 / 4 / 8 / 24 h windows, now reported as sensitivity estimates, not bounds.
The NL-proxy minima are unchanged (662 MW in 2024, 696 MW in 2025).

**New issue:** the 2025 consolidated file has no interval-quality flags
(`legacy_unflagged`), so all 2025 figures, including the year-over-year trend,
are unaudited. **Next:** re-import Jan–Mar 2025 with the current preprocessor.

## 2026-10-15 — Scope decision and hourly NL(t) series (SO1)

**Decision (author):** the evidence base is 2024 only; 12 months satisfies SO1.
January–March 2025 are not re-imported. Consequently the 2025 year-over-year
comparison (unaudited) is not dissertation evidence.

**Activity:** `scripts/build_hourly_net_demand.py` builds hourly NL(t) =
D(t) − solar − wind from the 2024 consolidated 15-minute data, with one row per
local hour. A value is published only when all four quarter-hours are present
and unflagged; nothing is imputed. 9 new tests (88 total passing).

**Result:** 8,784 hours: 8,326 complete, 434 containing flagged intervals,
24 missing (2024-06-30). June is the weakest month (153 unusable hours). Hourly
energy equals the consolidated daily integrated MWh on all 255 fully complete
days. Usable hourly NL(t): min 690, p01 949, median 1,634, p99 2,395,
max 2,542 MW.

**Limitations:** D(t) is not verified national demand; reconciliation against
published CEB aggregates is still required for SO1.

**Next:** obtain CEB published 2024 monthly/annual generation figures for the
reconciliation; fill NLmin parameters (Appendix C); start the ACN-Data session
generator (SO3).

## 2026-10-15 — Research Status Verification, 2025 Quality Audit, and Updated Grid Analysis

### Activity

Reviewed and verified the current Grid Pulse EV research implementation following the PUCSL data-processing, coal-commitment, curtailment-pressure and hourly net-demand development stages.

Confirmed that the previous 2025 data-quality issue was resolved. The historical project log is retained for traceability, but the outdated statements regarding unaudited 2025 data and coal-headroom bounds are superseded by this entry.

### 1. Research Dataset Scope

**Primary research dataset:** January–December 2024 PUCSL 15-minute electricity dispatch data.

- Calendar-year intervals expected: 35,136.
- Observed intervals: 35,040.
- Observed days: 365 of 366.
- Missing date: June 30, 2024.
- Intervals requiring quality review: 1,263.
- Calendar-year interval coverage: 99.73%.

January–March 2025 data are retained as **supplementary comparison data**, not as part of the primary 2024 dataset.

### 2. January–March 2025 Quality Re-import — Completed

Reprocessed the three original 2025 PUCSL workbooks using the current quality-aware preprocessing pipeline.

| Month | Observations | Quality-flagged intervals | Coverage |
|---|---:|---:|---|
| January 2025 | 2,976 | 30 | Complete |
| February 2025 | 2,688 | 10 | Complete |
| March 2025 | 1,728 | 14 | Partial, March 1–18 |
| **Total** | **7,392** | **54** | **77 observed days** |

Verified that the monthly and consolidated datasets preserve quality status through the `interval_quality_status` column.

The previous statement that the 2025 dataset was unaudited is therefore superseded.

### 3. Quality-Audited Curtailment-Pressure Analysis

Re-executed `scripts/analyse_curtailment_pressure.py` using the quality-audited 2024 and 2025 consolidated datasets.

Total input observations: 42,432 across 442 observed days.

The analysis used 359 days from 2024 and 76 days from 2025 after applying the relevant quality and system-event exclusions.

**February comparison:**

- 2024: 5/29 days (17%) had midday reported dispatch below overnight dispatch.
- 2025: 14/27 days (52%) showed this pattern.

**Matched March 1–18 comparison:**

- 2024: 3/18 days (16.7%).
- 2025: 11/18 days (61.1%).
- Difference: +44.4 percentage points.
- Mean daily midday-minimum dispatch: 1,731.7 MW in 2024 and 1,390.4 MW in 2025.
- Observed reduction: approximately 341.3 MW.

These are descriptive changes in reported electricity dispatch. They do not establish that rooftop solar growth caused the differences or that renewable curtailment occurred.

### 4. Coal Commitment Analysis — Updated Results

Executed `scripts/analyse_coal_commitment.py` using quality-audited dispatch records.

The latest analysis selected 166 stressed midday intervals.

| Rolling window | Mean estimated headroom | Share near empirical floor |
|---|---:|---:|
| 2 hours | 74 MW | 27% |
| 4 hours | 60 MW | 36% |
| 8 hours | 19 MW | 60% |
| 24 hours | 11 MW | 61% |

The older approximately 160 MW fleet-wide headroom estimate is superseded.

The updated values remain **heuristic sensitivity estimates** because unit commitment is inferred from aggregate coal dispatch.

The empirical per-unit loading floor is not a verified technical minimum stable generation constraint.

Neither the coal analysis nor the curtailment-pressure analysis establishes measured renewable curtailment.

### 5. Hourly Net-Demand Proxy — Implemented

Developed `scripts/build_hourly_net_demand.py` to generate an hourly dispatch-derived net-demand series from the 2024 PUCSL data.

The working definition is:

`NL(t) = D(t) - solar_mw(t) - wind_mw(t)`

where `D(t)` is PUCSL reported total dispatch, not independently verified national electricity demand.

**Results:**

- Total calendar hours: 8,784.
- Complete and unflagged hours: 8,326.
- Hours containing flagged observations: 434.
- Missing hours: 24 (June 30, 2024).
- Usable hourly coverage: approximately 94.8%.
- Minimum usable hourly net-demand proxy: 690 MW.
- Median: 1,634 MW.
- Maximum: 2,542 MW.

The hourly calculations were internally reconciled with the consolidated daily integrated energy on 255 fully complete days, with no numerical difference.

This is an arithmetic validation of the hourly aggregation, not independent validation against published CEB energy totals.

### 6. Automated Testing

The latest local automated test execution completed successfully.

**Result: 88 tests passed.**

Tests cover preprocessing, quality checks, consolidation, coal commitment, curtailment-pressure analysis and hourly net-demand construction.

### 7. Outstanding Scientific Limitations

The following items remain unresolved:

1. June 30, 2024 is missing from the PUCSL dispatch source.
2. Differences between reported daily MWh and integrated 15-minute MWh require investigation.
3. PUCSL reported dispatch has not been independently established as total national electricity demand.
4. Centrally reported solar generation must not be treated as total available renewable generation.
5. The technical minimum stable generation, reserve and inertia constraints require cited CEB evidence and appropriate mathematical modeling.
6. Actual renewable curtailment cannot be claimed from the available dispatch measurements alone.

### 8. Next Research Tasks

- Reconcile the 2024 PUCSL dataset against official CEB annual or monthly generation statistics.
- Investigate daily energy-accounting discrepancies and source definitions.
- Obtain matching NASA POWER solar and weather observations for 2024.
- Complete the minimum secure generation derivation using cited plant and system-security parameters.
- Review the proposed curtailment-risk formulation with the research supervisor.
- Develop the public EV charging-session dataset and uncontrolled charging baseline.
- Continue toward the constrained EV charging schedule optimization model.

**Supervisor approval:** Pending for the minimum secure generation formulation, assumptions concerning curtailment risk, and optimization scope.

**Progress Presentation I:** October 23, 2026.

## 2026-10-15 — January 2024 PUCSL Source-Level Validation

Activity:
Executed the PUCSL source-level audit notebook against the
original January 2024 dispatch workbook.

Results:
- All 31 daily worksheets aligned with processed records.
- Total daily-reported versus integrated energy gap: 89.227 GWh.
- Sum of eight generation-source gaps: 89.183 GWh.
- Unattributed arithmetic residual: approximately 0.045 GWh.
- Largest source gaps:
  - Solar: +55.007 GWh
  - Mini hydro: +24.337 GWh
  - Biomass: +9.094 GWh
  - Wind: +3.162 GWh

Timestamp validation:
The original 00:15–00:00 labels are provisionally treated as
interval-ending timestamps. Confirmation is pending.

Conclusion:
The differences are present in the original source data.
Their physical and accounting explanations remain unresolved.
No adjustment or imputation has been applied.

Next:
Document reporting-boundary uncertainties, seek CEB/PUCSL
clarification, and begin EV charging-session modeling.

## 2026-10-19 — Synthetic EV Charging Baseline Implementation

**Activity:** Implemented and validated a reproducible
synthetic EV charging-session generator and uncontrolled
charging baseline.

**Implementation:**
- Developed scripts/ev_charging_baseline.py.
- Created an EV baseline analysis Jupyter notebook.
- Generated 140 synthetic sessions for April 1–7, 2024.
- Used 15-minute charging intervals and random seed 42.
- Modeled immediate charging at each session's maximum power.

**Results:**
- Total requested energy: 2,561.87 kWh.
- Total delivered energy: 2,400.09 kWh.
- Total unmet energy: 161.78 kWh.
- Fully served sessions: 121 of 140 (86.43%).
- Peak incremental charging load: 65.2 kW.
- Maximum concurrently connected vehicles: 12.
- Automated tests: 103 passed, without warnings.

**Limitations:**
- Sessions are synthetic and not calibrated to Sri Lankan data.
- No charger-port or site-level power-capacity constraints.
- No grid-aware optimization has been implemented.
- Results cannot establish real-world national EV charging behavior.

**Next:** Implement charger availability and site-capacity
constraints, followed by a constrained scheduling optimizer.

## 2026-10-19 — Cited NLmin, LTGEP scenario years and full experiments (SO2, SO5, SO6)

**Activity:** Replaced the uncited grid parameters with values from the CEB
*Long Term Generation Expansion Plan 2025–2044* (LTGEP), and re-ran the full
experiment set with corrected sweeps.

**NLmin derivation (`scripts/derive_nlmin.py`, Appendix C):**
- Cited inputs:
  - Lakvijaya: 3 × 270 MW units, minimum operating level 150 MW each (Table 2.7, p. 2-10).
  - Spinning reserve 5% of demand; no single unit above 25% of demand (p. 7-13).
  - SNSP limit 75% (pp. 7-7, 7-13, 10-14).
- Observed 2024 commitment in the 167 most stressed hours: 2 coal units online
  in 136 of them; major hydro p10 217 MW; oil off in 77%.
- NLmin = 2 × 150 + 217 = 517 MW → central 520 MW. Low 240 MW, high 779 MW;
  sweep 240/380/520/650/780 MW.
- Checks:
  - Reserve met by coal headroom in 100% of stressed hours.
  - Single-unit rule met in 100%.
  - Observed 2024 operation fell below 520 MW in 0 hours. The earlier 700 MW
    placeholder was crossed in 32 hours.

**Scenario years (`build_grid_scenario.ltgep_scenario`):**
- Each scenario year scales solar, wind, must-take and demand by multipliers
  derived from the LTGEP base case:
  - installed capacity at end-2023 (Table 2.1);
  - planned additions (Table E3);
  - net-generation forecast (Table 3.3).
- Inferred risk (complete 2024 hours, NLmin 520 MW, SNSP 75%):

  | Year | Risk energy |
  |---|---|
  | 2025 | 0 GWh |
  | 2026 | 0.1 GWh |
  | 2027 | 6.4 GWh |
  | 2028 | 31.0 GWh |
  | 2030 | 117.6 GWh |

- SNSP first binds in 2029.
- Central year: 2028.
- Scaling solar alone (no demand growth) would have given 133 GWh for 2028.

**Evaluation design:**
- Weekly sample (42 days; 39 usable): year-representative peak and delay.
- Risk-day set: all 10 usable risk days of 2028; used for absorption and the
  weight trade-off.
- Sweeps run on all central days. Rankings are tie-aware (0.5 MWh / 0.05 MW).
- Days are solved in parallel (`--workers`). A test checks that the result
  equals serial solving.

**Results (10,000 sessions/day, parametric sessions — provisional):**
- Risk days (5,265 MWh of risk):

  | Strategy | Absorbed | Evening-peak increase removed | Mean delay |
  |---|---|---|---|
  | Uncontrolled | 445.3 MWh | — | — |
  | Solar-following | 512.0 MWh | 28% | 44 min |
  | Curtailment-aware | 520.5 MWh | 60% | 15 min |
  | ML-forecast planning | 477.3 MWh | 59% | — |
  | Persistence planning | 453.3 MWh | 56% | — |

- The whole curtailment-aware vs solar-following absorption difference
  (8.4 MWh) comes from 9 June 2024. That is a monsoon day where wind and falling
  demand put the risk at 13:00–14:00, away from the 12:00 solar peak. On the
  other 9 days the two strategies tie.
- 9 of 10 risk days follow a day without risk, so persistence planning fails.
- Weekly sample:
  - Uncontrolled charging adds 12.8 MW to the evening peak.
  - Solar-following and curtailment-aware absorb the same (270.2 MWh).
  - Curtailment-aware removes 60% of the added peak (solar-following 31%), at
    8 min mean delay (solar-following 45 min).
- Trade-off:
  - Absorption is 520.5 MWh at all 15 weight settings.
  - The only trade-off is peak against delay: 0 MW at ~7 min, and the full
    7.75 MW at ~15 min for any peak weight ≥ 0.25.
- Sensitivity (16 settings; weekly sample):
  - Curtailment-aware is never outranked (16 of 16).
  - The exact ranking is kept in 11 of 16. The other 5 are curtailment-aware
    moving from tied to ahead of solar-following (NLmin 780 MW, +18.6 MWh;
    SNSP 0.5; years 2027 and 2030), or all strategies at zero risk (2026).
  - The evening-peak order is CA < SF < U in every setting.
  - Declared-departure share is the most sensitive input. Infeasible days:
    0 of 42 at 100%, 3 at 80%, 20 at 50%. Undeclared drivers charging on
    arrival fill the 50 kW station connection.
- Every reported LP is optimal. Infeasible days are reported, not relaxed.
  0 audit violations.
- Runtime (`scripts/benchmark_scaling.py`, single process, 3 Nov 2024): all
  optimal with 0 violations:

  | Sessions | Solve time |
  |---|---|
  | 1,000 | 0.2 s |
  | 3,000 | 0.8 s |
  | 10,000 | 4.7 s |
  | 30,000 | 31.8 s |

  Growth is about sessions^1.5. NFR1 (< 60 s) and NFR2 (one order of
  magnitude) are met.
- Tests: 146 passed.

**Limitations:**
- NLmin awaits CEB System Control review. 2025 operation after the blackout may
  have used a higher floor.
- Planned battery storage is not modelled; risk is reported before storage.
- 2024 weather is reused for every scenario year.
- Sessions and fleet volume are still provisional.

**Next:**
- Send Appendix C to CEB for review.
- Download the ACN-Data sessions and the PUCSL April–December 2025 workbooks.
- Obtain fleet volumes per scenario year from Component 1.

## 2026-10-20 — Interactive scheduling dashboard (WBS 5.11, FR13)

**Activity:** Built the dashboard view required by FR13 as a Streamlit app
(`app/dashboard.py`). Its data layer (`scripts/dashboard_data.py`) calls the
same functions as the experiments.

**What it shows:**
- *Schedule a day:* any of the 206 usable 2024 days, with adjustable:
  - scenario year;
  - NLmin (240–780 MW);
  - SNSP limit;
  - sessions per day;
  - declared-departure share;
  - objective weights.

  The LP runs live. The view shows residual load against NLmin with the risk
  hours marked, the EV load of each strategy on common axes, KPI tiles and the
  full metrics table. Infeasible days are reported, not relaxed.
- *Experiment results:* the saved risk-day and weekly-sample comparisons, the
  per-day absorption table, the trade-off chart, the sensitivity table
  (including "never outranked") and the scaling benchmark.
- *Scenario years:* LTGEP risk by year.
- *Assumptions:* every parameter with its source (NFR6).

**Reproducibility fix:**
- Session draws depend on which days are drawn together.
- The experiment's day sets are now built by one function,
  `run_experiments.experiment_days`, which the dashboard reuses.
- With default settings, the dashboard therefore reproduces the saved
  experiment exactly. Example: 9 June 2024, curtailment-aware absorbed
  68.11 MWh in both.

**Validation:**
- The page runs headless under Streamlit `AppTest` with no exceptions and all
  four charts rendered.
- Charts were rendered to PNG and inspected.
- The live server answers the health check and listens on 127.0.0.1 only.
- Tests: 150 passed (4 new for the data layer).

**Limitations:**
- One day with 10,000 sessions and four strategies takes about 6 s to solve;
  results are cached.
- Still not done:
  - group-wide export scheme (FR12);
  - expert usability review (NFR5, WBS 5.12).

## 2026-10-20 — User-friendly prototype (WBS 5.11, FR13, NFR5)

**Activity:** Replaced the single analyst dashboard with a guided five-page
prototype, `app/streamlit_app.py`. It is written for people who have not read
the method.

**Design choices:**
- *Plain names.* The strategies are called *Charge on arrival* (today),
  *Charge when sunny* (current advice) and *Smart charging* (this research).
  Charts and tables say "clean power wasted", "evening peak added by EVs" and
  "extra wait".
- *Ready-made days* (all from the 2024 record), plus any usable day:
  - Sunny Sunday, 3 Nov: most wasted power in 2028;
  - Monsoon wind day, 9 June: risk after the solar peak;
  - Ordinary weekday, 5 Nov: no waste, same week.
- *A written verdict* for each planned day (`dashboard_data.verdict`). It says
  when charging-when-sunny does as well and why (the surplus sits on the
  sunniest hours).
- *A driver's view* (`dashboard_data.driver_examples`). It follows a typical
  morning commuter, midday shopper and evening visitor, chosen as the median
  driver whose charging smart charging moves, not the most extreme. A timeline
  shows each option on its own row, because overlaid lines hid each other.
  Each driver is shown leaving with their full energy.
- *Expert settings* (NLmin, SNSP, declared share, weights) sit in a collapsed
  section. Full tables sit in collapsed sections on the Evidence page.
- *Pages:*
  - Start here;
  - Plan a day;
  - A driver's view;
  - Evidence;
  - How it works (method in four steps; real, modelled or assumed; sources;
    glossary; limits).

**Validation:**
- Every page runs headless under Streamlit `AppTest` with no exceptions,
  errors or warnings (`tests/test_app.py`).
- Every chart was rendered to PNG and inspected. This led to two fixes:
  - bar labels were clipped;
  - overlapping driver lines were replaced by the timeline.
- The verdict and driver examples have synthetic tests:
  - every example driver leaves with exactly the energy requested;
  - no charging happens while unplugged.
- Tests: 159 passed.

**Next:** Usability check with a planner (NFR5, WBS 5.12). Record a fallback
screen capture before the presentation.
\n
## 2026-10-21 — Prototype extended: own trip, station view, exports (FR7, FR12)

**Activity:** Added two pages and data export to the prototype.

**What was added:**
- *Plan my charge* (`dashboard_data.my_charge`). A visitor enters a trip:
  - plug-in time and leaving time (leaving earlier means the next morning,
    latest 09:00);
  - energy needed;
  - charger.

  The car joins the least busy station that day, declares its departure, and
  is planned together with the day's other cars. The request is capped at what
  the charger can deliver, with a warning when it is. The page reports:
  - when the car is full;
  - the share of its energy charged while clean power would be wasted;
  - the energy charged in the grid's peak window;
  - that it leaves with its full energy.

  Example, 3 Nov 2028 scenario, 08:00–17:00, 20 kWh, 7.2 kW: smart charging
  gives 100% from wasted power (64% charging on arrival), full by 12:00.
- *Station view* (`dashboard_data.station_view`): one station's cars, its
  total power under each option against the 50 kW connection limit, and a
  timeline of each car under smart charging. The busiest station on 3 Nov
  (18 cars) reaches the limit and never exceeds it.
- *Downloads* on Plan a day:
  - day summary (JSON);
  - hourly grid and EV load;
  - every car's outcome;
  - the 15-minute smart-charging schedule.

  The large files are generated only when clicked. The format is proposed in
  `docs/export_schema.md` for group agreement (FR12). It also includes a
  proposed fleet-scenario input file for Component 1.
- `dashboard_data.day_detail` keeps every car's 15-minute schedule. The driver
  view, station view and exports share one cached solve.

**Correction during the work:** "evening peak" for a single car was first
measured as energy charged from 17:00 to midnight. That was misleading, because
the optimiser keeps down only the peak hours, and charging at 22:00 does not
raise the peak. It now uses the grid's own peak window: the hours within 3% of
the day's maximum net load (18:00–20:00 on 3 Nov).

**Validation:**
- New data-layer tests:
  - visitor trip rules (quarter hours, 09:00 limit, capping, least busy
    station);
  - the visitor leaves with exactly the energy requested and never charges
    while unplugged;
  - the station never exceeds its connection limit;
  - each export has the documented columns;
  - the schedule energy per car sums to its request.
- Every page runs headless.
- Overnight, invalid and capped trips were checked on the page.
- Charts were rendered and inspected.
- Tests: 166 passed.
