# Grid Pulse EV — Individual Component Starter Project

**Project:** J26-DS-331  
**Approved individual topic:** Optimizing Public EV Charging Schedules for Renewable-Curtailment Absorption and Peak Reduction in Sri Lanka

The full SO1–SO6 pipeline is implemented. NLmin, the SNSP limit and the scenario years are cited to the CEB *Long Term Generation Expansion Plan 2025–2044*; fleet volume and session behaviour are still **provisional** (see `config/scenarios.json` and `docs/methods_and_literature.md`). The grid data are PUCSL 15-minute dispatch reports reconciled to CEB published totals. Raw data are not committed. The example grid data under `tests/fixtures/` is **synthetic** and is strictly for testing.

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

Expected test result: `159 passed` (the exact run time can vary).

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

## Research pipeline (SO1–SO6)

Run from the project root once the PUCSL 2024 data are preprocessed and the NASA POWER 2024 files are downloaded (see `docs/data_inventory.csv`):

```powershell
.\.venv\Scripts\python.exe scripts\build_hourly_net_demand.py --input data\processed\grid\pucsl_2024_consolidated_dispatch_15min.csv --year 2024
.\.venv\Scripts\python.exe scripts\build_grid_scenario.py
.\.venv\Scripts\python.exe scripts\derive_nlmin.py
.\.venv\Scripts\python.exe scripts\forecast_grid.py
.\.venv\Scripts\python.exe scripts\run_experiments.py --quick --workers 4   # smoke test
.\.venv\Scripts\python.exe scripts\run_experiments.py --workers 6           # full run
.\.venv\Scripts\python.exe scripts\benchmark_scaling.py                     # NFR1/NFR2 runtime
```

- `build_grid_scenario.py`: boundary-corrected demand, modelled available solar, the inferred curtailment-risk signal C(t), and the LTGEP scenario years 2025–2030 (SO1/SO2).
- `derive_nlmin.py`: NLmin from the cited coal minimum and the commitment observed in the 2024 stressed hours, with reserve and single-unit checks (SO2, Appendix C).
- `forecast_grid.py`: day-ahead demand, solar, wind and must-take forecasts, kept only where they beat persistence.
- `ev_sessions.py`: session sets, parametric now and a GMM fitted to ACN-Data with `--acn-json` (SO3).
- `schedule_ev_charging.py`: uncontrolled, solar-following and curtailment-aware strategies, plus the constraint audit (SO4).
- `run_experiments.py`: comparison on a weekly sample and on every risk day, weight trade-off, sensitivity, and forecast-based planning (SO5/SO6). `--workers` solves days in parallel. Outputs go to `results/experiments/`.
- `benchmark_scaling.py`: solve time from 1,000 to 30,000 sessions on one day (NFR1, NFR2).

### Prototype app (WBS 5.11, FR13)

```powershell
.\.venv\Scripts\streamlit.exe run app\streamlit_app.py
```

Opens at http://localhost:8501 (this computer only; see `.streamlit/config.toml`). It is written for people who have not read the method, so the strategies have plain names: *Charge on arrival* (today), *Charge when sunny* (current advice) and *Smart charging* (this research).

- **Start here:** the problem, the idea, who it is for, and the headline results of the full-year study.
- **Plan a day:** pick a ready-made day or any usable 2024 day, a scenario year and the number of cars. The scheduler plans every car live and shows:
  - a written verdict for the day;
  - when clean power would be wasted;
  - when the cars charge under each option;
  - a side-by-side table.

  Expert settings (NLmin, SNSP, declared share, weights) are in a collapsed section.
- **A driver's view:** a typical driver from three groups (morning commuter, midday shopper, evening visitor). It shows when their car charges under each option, and that it leaves full.
- **Evidence:** the saved SO5/SO6 results in plain words, with all tables in a collapsed section.
- **How it works:** the method in four steps, what is real, modelled or assumed, every parameter with its source, a glossary and the limits.

With the default settings, a day from the experiment uses exactly the experiment's sessions, so it reproduces the saved results. All page logic is in `scripts/dashboard_data.py`, which is tested and calls the same functions as the experiments. `tests/test_app.py` runs every page headless.

Every assumed parameter is in `config/scenarios.json`. Values tagged `provisional` must be replaced or approved before results are reported.

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
