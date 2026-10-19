# Appendix C — Derivation worksheet for the minimum secure net-demand level (NLmin)

**Project:** J26-DS-331 · Component 3 · Wijesuriya W A A I (IT23274648)
**Status (2026-10-19):** derived. Every plant and policy value in §3–§5 is
cited to the CEB *Long Term Generation Expansion Plan 2025–2044* (LTGEP,
358 pp.); the operating-practice values come from the 2024 record. Central
**NLmin = 520 MW** (517 MW before rounding), sweep 240–780 MW. It is still
**pending review by a CEB System Control engineer** (proposal §3.5).
Reproduce with `scripts/derive_nlmin.py` →
`results/grid_scenario/nlmin_derivation.json`.

## 1. Purpose

SO2 requires "a documented derivation of NLmin from plant minimum stable
generation, reserve and inertia requirements, and an empirical cross-check
against the observed minimum of NL(t)". This worksheet holds both. Its
arithmetic is meant to be checked by a CEB System Control Center engineer
(proposal §3.5, expert review).

## 2. Definitions

| Symbol | Definition | Source in repo |
|---|---|---|
| D(t) | `total_dispatch_reported_mw`, PUCSL 15-minute report. Excludes rooftop and other embedded generation: 83% of the 2024 daily-vs-15-minute gap is solar, matching CEB rooftop energy. | `scripts/preprocess_pucsl_dispatch.py` |
| D_sys(t) | D(t) plus embedded generation, each source rescaled to CEB 2024 monthly energy. Full-year equivalent 16,786 GWh vs CEB net generation 16,802 GWh (−0.1%). | `scripts/build_grid_scenario.py` |
| R(t) | Available VRE. Solar is modelled from NASA POWER irradiance (Colombo, Hambantota, Jaffna) scaled to CEB monthly grid + rooftop energy. Wind is metered wind × the CEB/PUCSL monthly ratio. | same |
| MT(t) | Must-take: mini hydro (run-of-river) + biomass, rescaled to CEB monthly energy | same |
| NL(t) | D_sys − R − MT: the load left for coal, oil and major hydro. Major hydro is **not** subtracted (dispatchable). With multipliers at 1 this equals observed dispatchable generation. | `scenario_signal()` |
| NLmin | Minimum feasible residual dispatch, under unit-commitment, plant minima, reserve and inertia constraints (§3–§6); scenario-dependent | this worksheet |
| C(t) | max(0, NLmin − NL(t), R(t) − SNSP·D_sys(t)), an inferred risk proxy (§9) | `scenario_signal()`; parameters provisional |

## 3. Minimum stable generation of the must-run fleet

| Plant / unit | Units | Net (MW) | Minimum operating level | Must-run? Why | Source |
|---|---|---|---|---|---|
| Lakvijaya (coal) | 3 | 270 each (used for studies) | **150 MW per unit** (56% of net) | Yes: slow start; 2 units online in 81% of the stressed hours (§6) | LTGEP Table 2.7, p. 2-10 |
| Major hydro | many | — | no technical minimum stated | Operating practice: 217 MW (p10) kept online in stressed hours for regulation | observed 2024 (§6) |
| Oil (CEB / IPP) | — | — | — | No: below 5 MW in 77% of stressed hours | observed 2024 |
| Mini hydro, biomass | 212 + 14 projects | 419 + 54 | — | Not dispatchable: "typically non-dispatchable run-of-river" | LTGEP p. 2-3; Table 2.3, p. 2-7 |

The record agrees with the cited coal minimum: the empirical per-unit lower
quantile is 154–156 MW net (§7). The proposal's sentence that thermal units
"cannot run stably below roughly 40 to 50 percent of rated output" is
replaced by this cited 150/270 MW (56%).

## 4. Spinning reserve

| Item | Value | Source |
|---|---|---|
| Spinning reserve | 5% of demand | LTGEP planning assumption (l), p. 7-13 |
| Largest single unit | ≤ 25% of demand | LTGEP planning assumption (n), p. 7-13 |
| Largest credible contingency | one Lakvijaya unit, 270 MW | LTGEP Table 2.7 |

Reserve is **upward headroom**, so it does not add to NLmin; it limits how
few units may be online. At the central point (2 coal units at 150 MW) the
headroom is 2 × (270 − 150) = 240 MW. The largest reserve requirement in the
stressed hours is 89.8 MW, so the requirement is met in 100% of them, and
270 MW ≤ 25% of demand in 100% of them.

## 5. Inertia / synchronous-machine requirement

| Item | Value | Source |
|---|---|---|
| SNSP planning limit | **75%** non-synchronous share of demand | LTGEP pp. 7-7, 7-13, 10-14 |
| Observed 2024 maximum VRE share of demand | 46.3% | `derive_nlmin.py` |
| Overlap with §3 | none: inertia is not converted to MW | — |

CEB states the inertia requirement as an **SNSP limit**, the same form EirGrid
uses, not as MW. It therefore enters C(t) as its own term,
VRE − 0.75 · D_sys, and is **not** added to NLmin, which would double-count
the same running units. At 75% the SNSP term first binds in scenario year
2029 (1 hour) and binds for 33 hours in 2030; before that the floor binds.

## 6. Constrained derivation (not a sum of independent MW addends)

A simple arithmetic sum of plant minimum output, reserve and an "inertia MW"
term is **not generally valid**: reserve is upward capability while inertia
is a dynamic/security requirement (often specified in MW·s), not extra
energy generation in MW. Generator synchronization can satisfy more than one
requirement simultaneously, so direct addition would double-count.

For each defensible scenario, obtain independently sourced unit data and solve
for the **minimum feasible conventional generation** consistent with demand
balance and plant/system-security restrictions. Illustrative constraints:

- Unit commitment: `u[g,t] ∈ {0,1}`; `Pmin[g] × u[g,t] ≤ P[g,t] ≤ Pmax[g] × u[g,t]`.
- Upward spinning reserve: `Σ (Pmax[g] × u[g,t] − P[g,t]) ≥ R_required[t]`,
  adjusted for eligible providers, deliverability, ramping and operating policy.
- Inertia: `Σ H[g] × S[g] × u[g,t] ≥ I_required[t]` where the cited unit
  parameters and system requirement permit this representation; avoid
  converting inertia to MW using an arbitrary multiplier.
- Hydropower minimum flows, ramp/response constraints, outages, and grid
  restrictions are included when evidence and model scope permit.

Define `NLmin` only after specifying which generation categories meet the
residual demand and which generators are truly must-run. Record the scenario,
assumptions, source citations, solver output, feasible/infeasible status and
sensitivity bounds.

**Implemented derivation (`scripts/derive_nlmin.py`).** Full unit data (H, S,
ramp rates) is not public, so the derivation fixes the commitment the system
actually used in its most stressed hours and applies the cited minima:

    NLmin = n_coal × 150 MW + H_min

- *Stressed hours:* the lowest 2% of observed hourly dispatchable output
  (coal + oil + major hydro) over complete 2024 hours: 167 hours, all at or
  below 853.9 MW.
- *n_coal:* fewest units consistent with observed coal output,
  ⌈coal / 270⌉. The distribution is 1 unit: 4 hours, 2 units: 136, 3 units: 27,
  so the mode is 2.
- *H_min:* major hydro kept online: minimum 90.5 MW, p10 217.0 MW, median
  329.0 MW.

| Scenario | Coal units × 150 MW | Major hydro | NLmin |
|---|---:|---|---:|
| Low | 1 × 150 | minimum (90.5) | 240.5 MW |
| **Central** | **2 × 150** | **p10 (217.0)** | **517.0 MW → 520 MW** |
| High | 3 × 150 | median (329.0) | 779.0 MW |

## 7. Empirical cross-check against the record

Generated by `scripts/analyse_curtailment_pressure.py` and
`scripts/analyse_coal_commitment.py` from the 2024 consolidated data (365 of
366 days; 2024-06-30 missing) and January–18 March 2025. The 2025-02-09
island-wide outage is excluded. These are **observed operating points, not
limits**: they show where the system *did* run, not where it *could not* go.

**Quality filtering (recomputed 2026-10-15):** the 1,263 `review_required`
2024 intervals are now excluded, as are 2024 days whose midday or overnight
window is entirely flagged (2024-01-20, 05-13, 05-31, 06-14, 08-11, 11-11). The
values below did not change materially after filtering. January–March 2025
was re-imported with the current preprocessor on 2026-10-15 (54 flagged
intervals excluded), so both columns are quality-filtered.

| Quantity | 2024 (quality-filtered) | 2025 Jan–Mar 18 (quality-filtered) |
|---|---:|---:|
| Minimum NL proxy, 15-min | 662 MW (2024-09-22 10:30) | 696 MW (2025-02-04 13:00) |
| 1st percentile NL proxy | 944 MW | 847 MW |
| Minimum dispatchable (coal + oil + major hydro) | 565 MW | 548 MW |
| 1st percentile dispatchable | 774 MW | 749 MW |

Exploratory coal-commitment results in the lowest 2% of **reported midday
dispatch** (166 intervals, pooled, after quality filtering):

- Coal output in the record clusters at multiples of ~273 MW net (p99 / 3
  units), consistent with three units. In stressed intervals, an estimated
  **two units** were online in 56–100% of intervals, depending on the
  commitment window.
- Empirical per-unit lower quantile: **154–156 MW net**. Computed using
  inferred unit commitment; not a verified technical limit.
- Mean headroom estimates: **74 MW** (2 h window), **60 MW** (4 h), **19 MW**
  (8 h), **11 MW** (24 h). Rolling-window commitment is a heuristic, not a
  bound, so these are **sensitivity estimates, not upper or lower bounds**. Do
  not report them as physical limits or validated headroom.
- Oil was effectively off (< 5 MW) in about half the stressed intervals. Mean
  metered solar was 65 MW vs 66 MW typical, so there is no visible reduction of
  utility-scale solar.

**Hourly cross-check (boundary-corrected NL, 2026-10-19):** the minimum
hourly observed dispatchable generation in 2024 was 575.5 MW (p01 775 MW). The
earlier placeholder of 700 MW was crossed by observed operation in 32 hours,
so it was too high for 2024. The derived central 520 MW is crossed in **0
hours**: the system really ran above it all year, with a 55 MW margin at the
minimum hour. This is the consistency check SO2 asks for.

**2025 caveat:** CEB curtailed rooftop and utility solar on several 2025
holidays and weekends, and ran more conservatively after the 9 February 2025
blackout. With the 2025 LTGEP scenario and NLmin 520 MW the model shows **no**
risk in 2025. Either operators used a floor nearer the high scenario (780 MW)
in 2025, or 2025 solar grew faster than the plan. The PUCSL April–December
2025 workbooks would test this; until then the 650 and 780 MW sweep points
carry the post-blackout case.

**Reading:** the observed low net-dispatch proxy provides a consistency check,
not a verified physical boundary. If a proposed secure constraint conflicts
with observation, investigate model definitions, incomplete source coverage,
measurement quality and actual operating circumstances; do not tune the limit
to make a desired curtailment result. A zero risk-proxy value never proves that
curtailment did not occur.

## 8. Sweep range

Sweep **[240, 380, 520, 650, 780] MW**: the §6 low, central and high points
plus the two midpoints, rounded to 10 MW (`derive_nlmin.py` writes it). Every
headline result is reported across this range, never at a single value. The
SNSP sweep is [off, 0.50, 0.65, 0.75]; 0.50 is about where the 9 February 2025
blackout occurred.

## 9. Draft wording for C(t) (supervisor approval pending)

Draft, from the proposal (abstract and abbreviations). **To be confirmed with
Prof. Rajapaksha:**

> C(t) is a *scenario-based curtailment-pressure proxy*: a modeled shortfall
> of the defined residual demand below an explicitly derived operational lower
> bound. It is **not** measured renewable curtailment or available renewable
> energy. No suitable time-resolved curtailment measurements have yet been
> validated for this project.

Do not present this draft as agreed wording until supervisor approval.
