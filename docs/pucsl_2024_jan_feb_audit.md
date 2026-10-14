# PUCSL January–February 2024 dispatch workbook audit

## Source material
- `15-Minutes-Report-31st-Jan-2024(1).xlsx`: 31 daily worksheets.
- `15-Minutes-Report-Feb-2024-29(1).xlsx`: 29 daily worksheets (2024 leap year).
- The originals were examined without editing their contents.

## Structural validation
| Check | January 2024 | February 2024 |
|---|---:|---:|
| Worksheet date convention | MM.DD | DD.MM (some names have trailing spaces) |
| Daily worksheets | 31 | 29 |
| Expected / observed 15-minute intervals | 2,976 / 2,976 | 2,784 / 2,784 |
| Missing days | 0 | 0 |
| Missing numeric values in summary rows / interval observations | 0 | 0 |
| Negative category observations | 22 (Oil–CEB) | 0 |
| Largest reported-total minus category-sum residual | 4 MW | 6 MW |
| Mean reported daily MWh minus integrated 15-minute dispatch | 2,878.30 MWh/day | 1,868.98 MWh/day |
| Mean reported solar MWh minus integrated 15-minute solar | 1,774.43 MWh/day | 1,517.18 MWh/day |

## Two exceptions requiring documented acceptance
1. **January 20, 2024:** Worksheet `01.20` is named as Jan 20, while its I1 internal date stamp represents **2024-01-19**. Adjacent worksheets are named Jan 19 and Jan 21, and Jan 19/20 contain different numerical data. The importer normally rejects this discrepancy. An explicitly scoped `--accept-date-stamp-mismatch 2024-01-20` permits ingestion but permanently records the source stamp discrepancy in reconciliation and coverage metadata. This is a provisional decision pending producer confirmation, not proof of correct date attribution.
2. **February 29, 2024:** One 15-minute interval ending **21:30 local time** shows a reported dispatch total **6 MW below** the sum of eight source-category readings (residual -6 MW). The default 5.1-MW bound correctly rejects it. `--balance-tolerance-mw 6.1` allows processing for exploration, while exporting the exception to `pucsl_2024_02_balance_flags.csv` and coverage metadata. The value is not corrected.

## Scope limitations
- These are **reported dispatch**, not independently verified national electricity demand.
- Reported daily MWh totals differ materially from quarter-hour integration; the solar gap is particularly important. Do not infer actual solar curtailment directly from the series.
- The source-time `0:15` through `0:00` convention is provisionally treated as interval ending times; confirm with PUCSL/CEB.
- January/February 2024 alone do **not** satisfy the 12-month grid-data requirement.
- No values or dates were imputed, clipped, or corrected.

## Reproducibility
From the project root, with original workbooks inside `data/raw/grid/pucsl/`:

```powershell
.\.venv\Scripts\python.exe scripts\preprocess_pucsl_dispatch.py --input 'data/raw/grid/pucsl/15-Minutes-Report-31st-Jan-2024(1).xlsx' --year 2024 --month 1 --accept-date-stamp-mismatch 2024-01-20
.\.venv\Scripts\python.exe scripts\preprocess_pucsl_dispatch.py --input 'data/raw/grid/pucsl/15-Minutes-Report-Feb-2024-29(1).xlsx' --year 2024 --month 2 --balance-tolerance-mw 6.1
.\.venv\Scripts\python.exe scripts\consolidate_pucsl_dispatch.py --year 2024 --months 1 2 --plot
.\.venv\Scripts\python.exe -m pytest -q
```

Keep the two exception parameters scoped to these verified months. Do **not** apply them automatically to other workbooks.
