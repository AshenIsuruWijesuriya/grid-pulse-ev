# PUCSL January 2025 — Preliminary 15-minute dispatch workbook audit

Source provided by researcher: `15-Minutes-Report-Jan-2025-31.xlsx` (unmodified original). Analysis date: 2026-10-14.

## Structure and content

- 31 worksheets (one each for January 1–31, 2025; some worksheet names contain trailing spaces).
- 96 quarter-hour columns per day: `0:15` ... `23:45`, `0:00`.
- 2,976 quarter-hour observations. The 15-minute source-of-generation and total-dispatch cells audited are numeric at every interval.
- Consolidated rows include Coal, Oil (CEB), Oil (IPP), Major Hydro, Wind, Solar, Biomass, Mini Hydro, `Total MW`, and a separate daily `Energy/(MWh)` summary column.
- Solar in the quarter-hour series is zero during the checked overnight intervals. Maximum quarter-hour solar entry: 78 MW. Overall `Total MW` range: 902–2,585 MW.

## Reconciliation — issue to resolve before modeling

**Two different reconciling questions have different answers.**

1. **Within each quarter-hour column**, the `Total MW` entry and the sum of the eight consolidated generation sources agree within **5 MW**, which is consistent with small differences from displayed rounding.
2. **Across the day**, integrating quarter-hour power values (`sum(MW)*0.25 h`) does **not** reproduce the daily `Energy/(MWh)` column. The daily total reported there is higher on all 31 days (mean difference **3,352.0 MWh/day**). The solar row explains a large share of the difference (mean excess daily solar energy entry over integrated quarter-hour solar values **3,009.9 MWh/day**).

**Example: January 1, 2025**

| Item | Reported daily MWh | Integrated 15-minute MW (MWh) | Reported less integrated |
|---|---:|---:|---:|
| Total | 40,465.8 | 36,730.75 | +3,735.05 |
| Solar | 3,229.8 | 366.0 | +2,863.8 |

Do **not** attribute the discrepancy to rooftop PV, supplementary estimates, transmission accounting, a mistaken formula, etc. without official clarification. The workbook itself does not establish an explanation. Ask PUCSL/CEB what generation types the quarter-hour solar series covers, how the `Energy/(MWh)` column is computed, and whether the total dispatch is equivalent to national system demand for this research.

## Timestamp interpretation

The normalized pilot export interprets the 96 labels as *interval-ending* timestamps: `0:15` represents [00:00, 00:15) local time and the last `0:00` represents [23:45, 00:00) across the following local midnight. This is a **working assumption**, suggested by the ordering, **not a confirmed CEB/PUCSL convention**. Get confirmation before combining with NASA POWER hourly values (local timestamps at HH:30).

## Research suitability (SO1)

- **Useful:** A real, high-frequency dispatch-by-source *candidate* dataset for short-period exploratory analysis and a prototype net-demand construction.
- **Insufficient alone:** Only one month; no demonstrated 12-month coverage in this workbook; unclear whether the dispatch total equals the D(t) concept in the proposal; unclear coverage of behind-the-meter PV and renewable curtailment; severe daily-versus-quarter-hour energy discrepancy.
- **Next action:** Retain the raw workbook unchanged, retain the normalized CSV as *provisional*, request definitions/clarification, and examine other months using the same checks before adopting any inputs as authoritative D(t) or R(t).

## Files

- `pucsl_january_2025_dispatch_15min.csv`: normalized 2,976 quarter-hour rows; power in MW; source spreadsheet and day retained. Interval labels are a documented assumption.
- `pucsl_january_2025_reconciliation.csv`: comparison between integrated quarter-hour energy and workbook's daily energy totals by day and source.
