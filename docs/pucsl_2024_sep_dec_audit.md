# PUCSL 2024 September–December: Original Workbook Audit

**Project:** Grid Pulse EV, J26-DS-331  
**Period:** 1 September–31 December 2024  
**Source:** Four PUCSL 15-minute generator dispatch Excel workbooks uploaded by the researcher.  
**Method:** Existing `scripts/preprocess_pucsl_dispatch.py` from the June–August 2024 audited update, which reads original XLSX worksheet cell data, verifies 96 quarter-hour readings per observed day, checks primary and secondary time labels and internal date stamps, compares reported total MW against eight category sums, and emits source-quality flags. Source workbooks were not altered.

## Verified coverage and exceptions

| Workbook | Days available/expected | Intervals | Negative generation readings | Source-total differences >5.1 MW | Intervals needing review | Max abs. source balance gap | Mean daily reported MWh minus integrated MW-based MWh |
|---|---:|---:|---:|---:|---:|---:|---:|
| Sept 2024 | 30/30 | 2,880 | 17 | 0 | 17 | 5 MW | 2,978.37 MWh/day |
| Oct 2024 | 31/31 | 2,976 | 0 | 1 | 1 | 7 MW | 2,939.43 MWh/day |
| Nov 2024 | 30/30 | 2,880 | 15 | 0 | 111 | 4 MW | 3,479.21 MWh/day |
| Dec 2024 | 31/31 | 2,976 | 45 | 1 | 45 | 6 MW | 3,517.59 MWh/day |
| **Four-month total** | **122/122** | **11,712** | **77** | **2** | **174** | **7 MW** | **Not pooled** |

All four workbooks use `DD.MM` worksheet names (some with trailing whitespace). Each observed day has 96 quarter-hour readings.

### Specifically verified anomalies

1. **October 16, 2024**, interval ending **07:30 local (UTC+05:30)**: `total_dispatch_reported_mw - sum_generation_by_source_mw = -7 MW`. Retain the original measurements and use `--quarantine-balance-exceptions` when processing October.
2. **November 11, 2024**, worksheet `11.11`: internal Excel date stamp at `I1` resolves to **November 10**, while the worksheet name and placement identify the operating date as November 11. Permit only `--accept-date-stamp-mismatch 2024-11-11`, record the mismatch in the coverage JSON, and mark that day's 96 intervals for review.
3. **December 19, 2024**, interval ending **20:00 local (UTC+05:30)**: `total_dispatch_reported_mw - sum_generation_by_source_mw = +6 MW`. Retain the original measurements and use `--quarantine-balance-exceptions` when processing December.
4. **Negative category readings**: September 17 observations; November 15; December 45. Do not clip negative MW values to zero. The November review count of 111 comprises 96 date-stamp-flagged intervals plus 15 other intervals with negative source readings. December's single balance exception coincides with another quality flag; consequently the month's review count is 45 rather than 46.
5. **Daily MWh reconciliation** remains significantly different from integrated quarter-hour reported dispatch in every month. The physical/accounting definitions of these fields are not yet verified; do not silently reconcile them.

## Reproducible processing commands

Run the following from the existing `grid-pulse-ev-starter` repository root after copying the four original workbooks into `data/raw/grid/pucsl/`:

```powershell
.\.venv\Scripts\python.exe scripts\preprocess_pucsl_dispatch.py --input "data/raw/grid/pucsl/15-Minutes-Report-Sept-2024-30.xlsx" --year 2024 --month 9
.\.venv\Scripts\python.exe scripts\preprocess_pucsl_dispatch.py --input "data/raw/grid/pucsl/15-Minutes-Report-Oct-2024-31.xlsx" --year 2024 --month 10 --quarantine-balance-exceptions
.\.venv\Scripts\python.exe scripts\preprocess_pucsl_dispatch.py --input "data/raw/grid/pucsl/15-Minutes-Report-Nov-2024-30.xlsx" --year 2024 --month 11 --accept-date-stamp-mismatch 2024-11-11
.\.venv\Scripts\python.exe scripts\preprocess_pucsl_dispatch.py --input "data/raw/grid/pucsl/15-Minutes-Report-Dec-2024-31.xlsx" --year 2024 --month 12 --quarantine-balance-exceptions
.\.venv\Scripts\python.exe scripts\consolidate_pucsl_dispatch.py --year 2024 --months 1 2 3 4 5 6 7 8 9 10 11 12 --include-partial --plot
```

## Full-year consolidation reproduction

I independently processed all twelve uploaded 2024 PUCSL workbooks (January–December) using the same validated importer and consolidation script. The output was:

- **35,040** quarter-hour observations across **365** observed local dates.
- Expected leap-year coverage: **35,136** quarter-hour intervals across **366** dates.
- The only missing date in the uploaded workbooks is **2024-06-30**, missing **96** intervals. June remains `partial`, not `complete`.
- **1,263 distinct intervals** are marked `review_required` in the consolidated output. This counts intervals, not distinct anomaly types; intervals can have more than one flag.
- Reproduced consolidated output files: `pucsl_2024_consolidated_dispatch_15min.csv`, `pucsl_2024_consolidated_observed_daily_summary.csv` and `pucsl_2024_consolidated_coverage_quality.json`. They should be regenerated locally from the original source workbooks rather than committed as authoritative measurements.

## Scientific use limitations

- **Reported dispatch is not verified total national electricity demand.** Behind-the-meter PV, network losses and accounting boundaries may matter, but the precise explanation needs authoritative CEB/PUCSL documentation.
- A dispatch solar measurement is **not necessarily available renewable energy** and cannot on its own quantify actual curtailment.
- The systematic daily MWh discrepancies are unresolved across all months, and the high-magnitude source-balance differences observed earlier in March, May, and June still need investigation.
- The complete annual *span* is available but one calendar day is absent; for any 12-month continuous time-series requirement, either recover June 30 from an authoritative source or document incomplete coverage and adapt analyses accordingly.
- Quality-flagged intervals should be excluded from clean-data reference fits by explicit filtering or investigated and resolved; never silently impute, drop, or overwrite original figures.

**Conclusion:** September–December can be imported with the existing code and targeted exception options; no importer code modifications are necessary for this batch. Full-year data collection is close to complete, but SO1 is not yet scientifically validated.
