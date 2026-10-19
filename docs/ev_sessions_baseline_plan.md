# EV charging-session modelling — Step 1 (prototype)

Research component: **Optimizing Public EV Charging Schedules for Renewable-Curtailment Absorption and Peak Reduction in Sri Lanka**.

## Status and evidence

- This step is a **synthetic, uncalibrated engineering prototype** only. It is **not observed Sri Lankan EV charging demand**, a national forecast, or an optimized schedule.
- The reproducible generator uses random seed 42 by default; generated arrival times, parking durations, charging power and energy requested are illustrative assumptions, not empirical distributions.
- `scripts/ev_charging_baseline.py` models a baseline: a plugged-in EV starts charging immediately at its per-session maximum power and stops when either its demand is met or it leaves.
- Sampling is at **15-minute resolution**, so `energy_kwh = charging_kw × 0.25 h`.
- The baseline assumes every session has a charger, without shared-site EVSE-count, transformer-capacity, grid-security, or tariff constraints. A future implementation must introduce these explicitly.
- An EV may depart before receiving its requested energy. Such unmet demand is **reported**, not silently dropped.

## Data schema

| Field | Unit | Meaning |
| --- | --- | --- |
| session_id | text | Unique synthetic session identifier |
| arrival_local | Asia/Colombo timestamp | Plug-in/arrival, aligned to quarter hour |
| departure_local | Asia/Colombo timestamp | Unplug/departure, aligned to quarter hour |
| energy_request_kwh | kWh | Assumed charging request |
| max_power_kw | kW | Maximum power for this vehicle/session |
| data_origin | text | Marks scenario as uncalibrated synthetic data |

## Demonstration

From the project root (Windows PowerShell):

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\ev_charging_baseline.py --start-date 2024-04-01 --days 7 --sessions-per-day 20 --seed 42
```

Then open `notebooks/EV_Session_Generation_and_Uncontrolled_Baseline.ipynb`, select your `.venv` kernel, and click Run All. Outputs under `results/ev_baseline/` are diagnostics ignored by Git in the existing project.

Do not use this prototype's metrics as claims about Sri Lanka, the PUCSL grid, curtailment or peak reduction; it has no optimizer yet.

## Data provenance: proposed next empirical input

The official [ACN-Data research dataset](https://ev.caltech.edu/dataset) reports `connectionTime`, `disconnectTime`, `kWhDelivered`, `siteID`, etc. For some sessions there are `userInputs`, including `kWhRequested` and `requestedDeparture`; these are **not always present**. `kWhDelivered` is **measured energy delivered** and must **not** be silently relabeled as the originally requested energy. The site operator's arrival/dwell pattern may not transfer to Sri Lankan public EV charging. ACN-Data timestamps are UTC in the API and accompanied by site timezone; carefully convert them to `Asia/Colombo` for model scenarios. ACN requires registration/token for API access; do not commit the token. Review the site's terms and cite the source if used.

## Planned follow-up

1. Acquire a documented public/session-level dataset through a permitted source and inventory its fields and missingness.
2. Create a separate **empirical-source adapter** and tests. Preserve observed fields and impute or simulate only in explicitly labeled sensitivity cases.
3. Derive alternative session distributions (weekday/weekend; public charging vs workplace), record assumptions and sample sizes, and conduct Sri Lankan transferability sensitivity analysis.
4. Add per-station occupancy and shared site/transformer capacity; preserve energy completion and departure feasibility.
5. Integrate quality-filtered PUCSL **dispatch-derived** net-demand proxy, without treating it as verified national demand or measured curtailment; use approved C(t)/NLmin definitions only after supervisor review.
6. Develop controlled LP baseline/optimizer with testable peak reduction and inferred-risk absorption metrics. Keep `uncontrolled` as comparator.

## Repository safety

Files in this package have new names, so they need not overwrite your current scripts, notebooks or tests. Avoid `git add .` until you have reviewed `git status --short`. Raw empirical datasets and tokens should stay excluded.
