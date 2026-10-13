# Grid Pulse EV — Individual Component Starter Project

**Project:** J26-DS-331  
**Approved individual topic:** Optimizing Public EV Charging Schedules for Renewable-Curtailment Absorption and Peak Reduction in Sri Lanka

This is **Stage 1: setup and data-feasibility audit**, not a completed system. No measured CEB data is included or claimed. The example grid data under `tests/fixtures/` is **synthetic** and is strictly for testing.

## Research roadmap mapped to the approved proposal

1. **SO1:** collect, validate, align at least 12 months of hourly/half-hourly CEB demand and generation-by-source series. Build observed net demand.
2. **SO2:** construct a defensible minimum secure net-demand scenario; estimate **inferred**, not measured, curtailment risk and peak windows.
3. **SO3:** generate appropriately calibrated charging sessions; measure the uncontrolled charging baseline.
4. **SO4:** solve charging schedules using linear programming, respecting connection windows, session energy, charger power and station capacity. Report infeasible scenarios rather than quietly violating requirements.
5. **SO5:** compare identical sessions and grid conditions for uncontrolled, solar-following and optimized charging. Include grid and customer-service metrics together.
6. **SO6:** study sensitivity to secure-level assumptions, session behavior and declared departure information.
7. Add forecasting, dashboard, group integration, dissertation and expert review.

## First-time setup (Windows PowerShell / VS Code)

1. Unzip this folder. In VS Code use **File → Open Folder** and select `grid-pulse-ev-starter`.
2. Open a terminal in VS Code (Terminal → New Terminal).
3. Install Python 3.11 or 3.12 if not already present; ensure `py --version` or `python --version` works.
4. Run these commands **one at a time**:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
```

If `py -3` is not found, try `python -m venv .venv`. You **do not have to activate** the virtual environment, because the commands above invoke its Python directly.

Expected test result: `4 passed` (the exact run time can vary).

## Optional: download a pilot NASA POWER solar/weather dataset

Once setup and testing work, **on a computer with internet access** run:

```powershell
.\.venv\Scripts\python.exe scripts\download_nasa_power.py --start 20250101 --end 20251231 --lat 6.9271 --lon 79.8612 --name colombo
```

This requests a **single representative geographic point**, not Sri Lanka's national solar generation. It saves the original API JSON to `data/raw/solar/` and a timestamp-normalized CSV to `data/processed/solar/`.

- The original API response and its metadata are preserved.
- API timestamps are explicitly requested in **UTC**. The CSV contains both `timestamp_utc` and `timestamp_sri_lanka` with offsets.
- Irradiance is a **solar-resource variable**, *not* measured solar electricity generation; do not substitute it directly for `solar_actual_mw` in a grid power balance.
- To model the nation we will later justify representative locations, installed PV capacities, and conversion/calibration assumptions.

## Grid-data schema and audit

We cannot construct an evidence-based grid time series until the relevant CEB data is obtained. When you have downloaded suitable source data, keep the original in `data/raw/grid/`. Convert a **copy** to the schema described in `docs/grid_data_dictionary.md` and put it under `data/processed/grid/`.

Audit a properly formatted CSV:

```powershell
.\.venv\Scripts\python.exe scripts\audit_grid_data.py --input data\processed\grid\grid_2025.csv
```

To see what the audit reports on **synthetic test data** (not for publication):

```powershell
.\.venv\Scripts\python.exe scripts\audit_grid_data.py --input tests\fixtures\synthetic_grid_day.csv
```

The synthetic file will correctly **fail** the 12-month data coverage requirement.

## Deliverables for Stage 1

- A working environment (`pytest` passes)
- A completed source and data inventory (`docs/data_inventory.csv`)
- At least one preserved raw source file, with a URL and access date recorded
- A documented **CEB hourly-data availability decision** (confirmed / requested / unavailable)
- Optionally, an untouched NASA POWER API response + cleaned pilot CSV

## Project rules

- Never present synthetic data as CEB measurements or estimated curtailment risk as measured curtailment.
- Keep units explicit: MW is power; MWh = MW × interval duration in hours.
- Power balance inputs must be time-aligned and consistent in system boundary.
- Actual renewable dispatch is not automatically equal to available renewable generation before curtailment.
- Record sources, date ranges, data resolution, missingness, original time zones, interpolation and assumptions.
- Track every change in `docs/research_log.md`. Keep research topic and approved objectives intact.

## Source links

- CEB statistical publications: https://www.ceb.lk/publication-media/statistical-reports/26/en
- CEB generation summary: https://cebcare.ceb.lk/gensum/energysummary2
- NASA POWER hourly API: https://power.larc.nasa.gov/docs/services/api/temporal/hourly/
- ACN-Data: https://ev.caltech.edu/dataset.html

This starter is deliberately minimal. Optimization and ML dependencies will be added when the corresponding methods are implemented and tested.
