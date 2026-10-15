# PUCSL June–August 2024 dispatch import audit

**Sources:** Original monthly 15-minute Excel workbooks from the PUCSL Generation Information website, uploaded for review. Files were read in place; original spreadsheet measurements were not modified.

| Source file | Observed days | Missing days | Source intervals | Negative source observations | Source-balance exceptions >5.1 MW | Intervals marked for quality review | Max absolute balance residual |
|---|---:|---|---:|---:|---:|---:|---:|
| `15-Minutes-Report-June-2024-Copy-1.xlsx` | 29 | 2024-06-30 | 2,784 | 13 | 160 | 426 | 234 MW |
| `15-Minutes-Report-July-2024-31.xlsx` | 31 | none | 2,976 | 9 | 0 | 9 | 4 MW |
| `15-Minutes-Report-Aug-2024-31-Aug.xlsx` | 31 | none | 2,976 | 11 | 0 | 107 | 5 MW |

## Source-specific exceptions

- **June:** This uploaded workbook contains daily sheets June 1–29 only. It does not contain June 30. The June export MUST use `--allow-partial-month` and be labeled incomplete. On **June 14–29**, **17 secondary time-row (row 41)** cells per day have invalid labels, **AY41:BO41** inclusive (272 affected labels total). The primary time row (row 2) is correct at those positions. The CLI's audited June switch permits exactly those 272 specific cells, and rejects unused approvals or invalid alternative headers. There are 160 balance residuals above the 5.1 MW audit threshold, across June 1, 3, 10, 11, and 14. They are retained with review flags. The union of all interval-level flags is 426, not the sum of individual category counts.
- **July:** Daily worksheets are `01.07` through `07.07` (`DD.MM`), followed by `07.08` through `07.31` (`MM.DD`). The audited mixed-name flag requires exactly that set of 31 sheets. All 31 corresponding source date stamps agree with their interpreted operating dates. Nine negative observations are preserved and flagged. No >5.1 MW balance exceptions were found.
- **August:** The worksheet `11.08` corresponds to August 11, but cell `I1` carries a date of August 10. The exception must be individually approved via `--accept-date-stamp-mismatch 2024-08-11` and is retained in the audit metadata and interval quality flags. Eleven negative observations are preserved; no >5.1 MW balance exceptions were found.

## Daily energy reconciliation

The signed mean of `reported daily total MWh - (sum of 15-minute total dispatch MW × 0.25 hour)` is:

- June (29 observed days): **+2,386.53 MWh/day**.
- July (31 observed days): **+3,015.47 MWh/day**.
- August (31 observed days): **+2,834.52 MWh/day**.

These discrepancies are **not resolved**. Neither the reported daily energy nor quarter-hour dispatch should be relabeled as independently verified national demand. The source solar category must not be assumed to include rooftop photovoltaic generation or true available (pre-curtailment) solar.

## Analysis policy

1. June 30 must remain missing unless an authentic, independently verified PUCSL/CEB source is obtained; never fill it with generated values for the main observed dataset.
2. An eight-month consolidation with `--include-partial` has **243 observed days** (Jan–May 152 + June 29 + July 31 + August 31), **23,328** quarter-hour rows. June is explicitly ineligible for full-month statistics. Without `--include-partial`, June must be excluded, while coverage metadata still records the gap.
3. All flagged intervals remain in the raw-reproduction dataset but must be excluded or separately sensitivity-tested when building validated grid models. Do not interpret source-balance flags as observed renewable curtailment.
4. Find an official source for June 30, 2024, if available. Continue obtaining September–December 2024 and 2024 NASA POWER weather/solar data on a consistent time basis.
