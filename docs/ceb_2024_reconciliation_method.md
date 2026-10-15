# 2024 CEB–PUCSL generation reconciliation (diagnostic stage)

## Why this check matters

The project has a 2024 consolidated PUCSL 15-minute **reported dispatch** series and individual PUCSL daily reconciliation CSVs. The PUCSL workbook's *reported daily energy* and *15-minute integrated energy* can differ substantially. Do not treat either as validated national electricity demand until the reporting definitions are reconciled.

## Official source and extraction

**Ceylon Electricity Board (CEB), _Sales and Generation Data Book – 2024_**, printed **page 73** (monthly **gross** generation by source and ownership) and printed **page 74** (monthly gross generation, station consumption, and **net** generation). The accompanying `docs/ceb_2024_monthly_reference.csv` was transcribed from those two tables; monthly sum checks against the published annual totals allow for rounding of individual cells.

The CEB _Statistical Digest 2024_, printed **page 1**, independently reports annual net generation of **16,802 GWh** (rounded). Printed **page 2** reports net generation by ownership and source and CEB network rooftop-solar **energy exported**. Rooftop PV **exports** do not capture all behind-the-meter rooftop solar consumption.

Relevant reported 2024 annual numbers (GWh):

| Definition | CEB published amount |
|---|---:|
| System **gross** generation | 17,460.066 |
| System **net** generation | 16,801.627 |
| Station consumption (gross less net) | 658.439 |
| IPP grid-connected solar (gross) | 256.554 |
| Rooftop solar energy (reported in gross generation table) | 866.554 |
| CEB coal (gross) | 6,058.803 |
| CEB coal (net) | 5,481.544 |

All entries use source terminology; figures from different measurement/ownership boundaries must **not** be silently merged.

## Running in VS Code

Copy the script, reference CSV, notebook, and tests into the matching folders of `grid-pulse-ev-starter`. Open a PowerShell terminal in the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\reconcile_ceb_2024.py
```

The script reads (but does not modify):

- `docs/ceb_2024_monthly_reference.csv` – source-transcribed official monthly benchmark.
- `data/processed/grid/pucsl_2024_consolidated_dispatch_15min.csv` – existing 15-minute dispatch and `interval_quality_status`.
- `data/processed/grid/pucsl_2024_??_reconciliation.csv` – existing monthly daily-energy checks.

Outputs (under the project’s already-ignored `results/` directory):

- `results/ceb_2024_reconciliation/ceb_pucsl_2024_monthly_comparison.csv`
- `results/ceb_2024_reconciliation/ceb_pucsl_2024_daily_comparison.csv`
- `results/ceb_2024_reconciliation/ceb_pucsl_2024_reconciliation_metadata.json`

Open `notebooks/CEB_PUCSL_2024_Reconciliation.ipynb`, select `.venv` as its kernel, then **Run All** for analysis tables and charts.

## Calculation and interpretation rules

1. 15-minute MW readings are *interval power*; energy per interval is `MW × 0.25 hours`. Convert to GWh by dividing by 1,000.
2. The source daily totals are in MWh; convert by dividing by 1,000 before comparison.
3. Use **all observed** quarter-hours for one diagnostic integrated total, and a separate **quality-clean** total. Neither is extrapolated to cover missing data. Review-flagged values are never silently clipped or repaired.
4. The full-month CEB benchmark is **not directly comparable** to a PUCSL month with missing days. **June 30, 2024 is missing**, so June must be labeled incomplete. Other months can contain flagged observations.
5. Monthly comparisons of PUCSL integrated solar, PUCSL daily reported solar, CEB grid-connected solar, and CEB rooftop solar are **candidate accounting-boundary comparisons**, not corrections. Even a close match would require source-definition verification.
6. Gross generation, net generation, delivered dispatch, consumer sales, and renewable **availability** are different quantities. None of the comparisons measures renewable curtailment or determines the secure grid minimum.
7. The `daily_non_solar_residual_gap_gwh` is `(reported total – 15-minute integrated total) – (reported solar – integrated solar)`. It is a bookkeeping residual **not** automatically auxiliary load, losses, or another specific source.
8. The CEB monthly total includes the station consumption distinction. Do **not** automatically subtract rooftop solar or station consumption from PUCSL data to force numerical agreement.

## Investigation questions for the data owner/supervisor

- In the PUCSL daily worksheet, does **reported daily solar energy** include rooftop PV exports, modeled rooftop generation, or only centrally dispatched grid-connected solar?
- Is `total_dispatch_reported_mw` a system net injection value, a demand proxy, or a sum of particular plant types? Are embedded generation and distribution losses included?
- Are PUCSL time series **instantaneous 15-minute readings** or averages over the previous 15 minutes? Does the source daily total represent a different settlement/time boundary?
- Does the PUCSL workbook's total daily energy include auxiliary consumption, rooftop exports, or generators omitted from the 15-minute columns?
- How should the 2024-06-30 missing day and review-required intervals be handled in any annual benchmark (without inventing values)?

## Research stage / restrictions

This artifact supports SO1 data validation. The validated 2024 hourly `NL(t)` **proxy** and subsequent `C(t)` scenario model remain subject to data-boundary verification, sourced technical minima, and supervisor approval. No data is imputed by this reconciliation script.
