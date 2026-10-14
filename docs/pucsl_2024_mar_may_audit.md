# PUCSL 2024 March–May 15-minute dispatch audit

## Provenance and method

Examined user-provided original PUCSL monthly `.xlsx` dispatch reports:

- `15-Minutes-Report-31-Mar2024.xlsx`
- `15-Minutes-Report-April-2024.xlsx`
- `15-Minutes-Report-May-2024-.xlsx`

Workbook worksheet lists, dated I1 stamps, two time-header rows, eight source-category summary rows, reported `Total MW` and reported daily energy values were checked. A strict importer exposes errors before a controlled, audited ingestion mode is used. The originals were not edited. Interval labels (0:15 through following day 0:00) are provisionally treated as interval-END labels in Asia/Colombo (UTC+05:30).

| Validation | March | April | May |
|---|---:|---:|---:|
| Named daily worksheets | 31 | 30 | 31 |
| Observed 15-minute intervals | 2,976 | 2,880 | 2,976 |
| Missing whole days | 0 | 0 | 0 |
| Worksheet date convention | DD.MM | DD.MM | DD.MM |
| Negative source-category readings | 0 | 0 | 8 |
| Residuals > 5.1 MW | 16 | 0 | 285 |
| Maximum absolute source-total gap | 286 MW | 5 MW | 235 MW |
| Mean reported daily minus integrated MWh | 1,856.70 | 2,299.67 | 2,376.85 |
| Flagged intervals, including date/header issues | 40 | 0 | 388 |

## March 16, 2024: source-total mismatch

All 16 >5.1 MW source-category residuals on this day occur during 15-minute intervals ending 08:30 to 12:15 local time. Residuals range from +159 MW to +286 MW. They should not be assumed to be measurement noise, missing rooftop PV, or curtailment. The imported reported total and each reported generation category are preserved verbatim, with `interval_quality_status=review_required` and `source_balance_exception` flag.

## March 30–31, 2024: secondary time row corrupted

Both worksheets have a correctly timed top header `Time` row 2. The secondary summary-table header row 40 has cells CH40:CS40 labeled `21:01` ... `21:12` rather than corresponding 21:15 ... 00:00. These 24 source-cell exceptions are accepted **only** with `--accept-time-header-mismatch 2024-03-30:ROW40` and the equivalent March 31 option. The importer verifies another valid matching time header exists, records all offending cell addresses in the JSON coverage report, and flags affected intervals. It does not overwrite source headers.

## May 13, 2024: one worksheet date-stamp disagreement

Worksheet `13.05` is placed between May 12 and May 14 and appears as May 13 in the tab name. However, Excel cell I1 contains the serial date **2024-05-12**. Explicit acceptance as provisional May 13 is recorded via `--accept-date-stamp-mismatch 2024-05-13`; all 96 intervals on this day require source attribution review.

## May: widespread category mismatch

The workbook contains 285 intervals with reported total minus generation-by-category differences above 5.1 MW across 12 observed days (May 16–18, May 20–23, May 27–31). Maximum absolute difference is 235 MW. Eight negative Oil–CEB readings are also preserved. These are **not** small rounding errors. Data producers should be asked to explain whether a category was omitted, whether calculations are inconsistent, or whether another reporting convention applies. Do not pick one explanation without confirmation. No totals or categories are overwritten, imputed, scaled, or clipped.

## Modeling/evaluation policy

- `interval_quality_status=review_required` means the observation has a source-integrity exception. Treat it as quarantined for physics/optimization until reviewed. `interval_quality_flags` gives precise reasons.
- `no_interval_flags` is **not** a complete validity guarantee. In particular the reported daily MWh versus quarter-hour integrated MWh discrepancy remains large in every month.
- Full-month status indicates all expected interval dates are present, NOT that all records are suitable for scientific inference.
- The dispatch series is not yet established as **total national demand**, and the reported solar category is not independently established as available renewable generation or measured solar curtailment.
- It may be useful to pursue PUCSL/CEB explanation of date stamps, reporting conventions, and detailed curtailment indicators.

## Reproducibility

See `README_STAGE_2024_MAR_MAY.md` shipped with this patch for exact commands and expected results. Tested March, April and May processing plus January–May consolidation; 39 automated regression/unit tests passed.
