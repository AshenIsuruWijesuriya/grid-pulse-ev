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