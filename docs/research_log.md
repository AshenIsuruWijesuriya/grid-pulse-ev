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