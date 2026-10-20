"""Grid Pulse EV, Component 3: scheduling dashboard (WBS 5.11, FR13).

Run from the project root:
    .venv\\Scripts\\streamlit.exe run app\\dashboard.py

Tabs
    Schedule a day     pick a day and settings; the scheduler runs live
    Experiment results the saved SO5/SO6 results (run_experiments.py)
    Scenario years     curtailment risk under the LTGEP base case, 2024-2030
    Assumptions        every parameter with its source

All numbers come from scripts/dashboard_data.py, which calls the same
functions as the experiments.
"""

from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_grid_scenario as bgs  # noqa: E402
import dashboard_data as dd  # noqa: E402
import run_experiments as rx  # noqa: E402

INK, INK_2, GRID_C, RISK = "#0b0b0b", "#52514e", "#e4e3df", "#e34948"
ORDER = ["uncontrolled", "solar_following", "curtailment_aware", "curtailment_aware_persistence",
         "curtailment_aware_ml"]
DISCLAIMER = ("C(t) is an inferred curtailment-risk signal from cited grid limits, not measured "
              "curtailment. EV sessions and fleet size are provisional placeholders.")

st.set_page_config(page_title="Grid Pulse EV: charging scheduler", layout="wide")


# --------------------------------------------------------------------------- #
# Cached data
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner=False)
def config() -> dict:
    return bgs.load_config(bgs.DEFAULT_CONFIG)


@st.cache_resource(show_spinner="Loading the 2024 grid record...")
def inputs():
    return dd.load_inputs(config())


@st.cache_data(show_spinner=False)
def days_for(settings: dd.Settings) -> pd.DataFrame:
    base, days = inputs()
    return dd.day_table(dd.signal(base, settings.apply(config())), days)


@st.cache_resource(show_spinner="Drawing the experiment's session set...")
def pool(sessions_per_day: int, declared: float) -> pd.DataFrame:
    """Depends only on the session settings, so grid changes do not redraw it."""
    base, _ = inputs()
    s = dd.Settings.central(config())
    s = dd.Settings(s.scenario_year, s.nlmin_mw, s.snsp_max, sessions_per_day, declared, s.weights)
    return dd.session_pool(base, config(), s)


@st.cache_data(show_spinner=False)
def solve(day_iso: str, settings: dd.Settings) -> dict:
    base, _ = inputs()
    p = pool(settings.sessions_per_day, settings.declared_departure_fraction)
    return dd.solve_one_day(pd.Timestamp(day_iso, tz=dd.TZ), base, config(), settings, pool=p)


@st.cache_data(show_spinner=False)
def saved() -> dict:
    return dd.experiment_results()


# --------------------------------------------------------------------------- #
# Charts (Altair; times shown as Sri Lankan wall-clock)
# --------------------------------------------------------------------------- #

def _wallclock(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return index.tz_localize(None)


def _style(chart: alt.Chart) -> alt.Chart:
    return (chart.configure_axis(gridColor=GRID_C, domainColor=INK_2, tickColor=INK_2,
                                 labelColor=INK_2, titleColor=INK_2, labelFontSize=11)
            .configure_view(strokeWidth=0)
            .configure_legend(labelColor=INK, titleColor=INK_2, orient="top", labelLimit=320))


def risk_bands(window: pd.DataFrame) -> alt.Chart:
    """Shaded hours with curtailment risk, drawn behind both charts."""
    h = window[window["curtailment_risk_mw"] > 0]
    df = pd.DataFrame({"start": _wallclock(h.index), "end": _wallclock(h.index + pd.offsets.Hour(1))})
    return alt.Chart(df).mark_rect(color=RISK, opacity=0.10).encode(x="start:T", x2="end:T")


def grid_chart(window: pd.DataFrame) -> alt.Chart:
    df = window.copy()
    df["hour"] = _wallclock(df.index).shift(30, freq="min")       # plot at mid-hour
    x = alt.X("hour:T", title=None, axis=alt.Axis(format="%H:%M", tickCount=10))
    # Risk is an hourly quantity: one block per risk hour, from NL up to NLmin,
    # aligned with the hour bands (a line-interpolated area would bleed past them).
    r = window[window["curtailment_risk_mw"] > 0]
    blocks = pd.DataFrame({"start": _wallclock(r.index), "end": _wallclock(r.index).shift(1, freq="h"),
                           "low": r[["net_load_mw", "nlmin_mw"]].min(axis=1).to_numpy(),
                           "nlmin": r["nlmin_mw"].to_numpy()})
    area = alt.Chart(blocks).mark_rect(color=RISK, opacity=0.30).encode(
        x="start:T", x2="end:T", y="low:Q", y2="nlmin:Q")
    nl = alt.Chart(df).mark_line(color=INK, strokeWidth=2).encode(
        x=x, y=alt.Y("net_load_mw:Q", title="MW"))
    floor = alt.Chart(df).mark_line(color=INK_2, strokeWidth=1.5, strokeDash=[5, 4]).encode(
        x=x, y="nlmin_mw:Q")
    hover = alt.selection_point(fields=["hour"], nearest=True, on="pointerover", empty=False)
    rule = alt.Chart(df).mark_rule(color=INK_2).encode(
        x=x, opacity=alt.condition(hover, alt.value(0.6), alt.value(0)),
        tooltip=[alt.Tooltip("hour:T", format="%a %H:%M", title="Hour (mid)"),
                 alt.Tooltip("net_load_mw:Q", format=",.0f", title="Residual load (MW)"),
                 alt.Tooltip("nlmin_mw:Q", format=",.0f", title="NLmin (MW)"),
                 alt.Tooltip("curtailment_risk_mw:Q", format=",.0f", title="Curtailment risk (MW)"),
                 alt.Tooltip("demand_system_mw:Q", format=",.0f", title="Demand (MW)"),
                 alt.Tooltip("solar_mw:Q", format=",.0f", title="Solar (MW)"),
                 alt.Tooltip("wind_mw:Q", format=",.0f", title="Wind (MW)")]).add_params(hover)
    return (risk_bands(window) + area + floor + nl + rule).properties(
        height=280, title=alt.TitleParams(
            "Residual load for coal, oil and major hydro (black) against NLmin (dashed)",
            anchor="start", color=INK, fontSize=13,
            subtitle="Red: hours where the load would fall below NLmin, so renewables would be curtailed",
            subtitleColor=INK_2))


def ev_chart(window: pd.DataFrame, loads: pd.DataFrame) -> alt.Chart:
    df = dd.long_loads(loads.set_axis(_wallclock(loads.index).shift(30, freq="min")))
    x = alt.X("hour:T", title=None, axis=alt.Axis(format="%H:%M", tickCount=10))
    # Legend lists only the strategies drawn; each keeps its fixed color.
    drawn = [s for s in ORDER if s in loads.columns]
    color = alt.Color("label:N", title=None, scale=alt.Scale(
        domain=[rx.LABELS[s] for s in drawn], range=[rx.COLORS[s] for s in drawn]))
    dash = alt.StrokeDash("is_forecast:N", legend=None,
                          scale=alt.Scale(domain=[False, True], range=[[1, 0], [6, 4]]))
    df["is_forecast"] = df["strategy"].str.endswith(rx.FORECAST_SUFFIXES)
    lines = alt.Chart(df).mark_line(strokeWidth=2).encode(
        x=x, y=alt.Y("ev_mw:Q", title="EV charging load (MW)"), color=color, strokeDash=dash)
    hover = alt.selection_point(fields=["hour"], nearest=True, on="pointerover", empty=False)
    wide = df.pivot(index="hour", columns="label", values="ev_mw").reset_index()
    labels = [c for c in wide.columns if c != "hour"]
    rule = alt.Chart(wide).mark_rule(color=INK_2).encode(
        x=x, opacity=alt.condition(hover, alt.value(0.6), alt.value(0)),
        tooltip=[alt.Tooltip("hour:T", format="%a %H:%M", title="Hour (mid)")]
        + [alt.Tooltip(f"{c}:Q", format=".2f", title=f"{c} (MW)") for c in labels]).add_params(hover)
    points = lines.mark_point(size=60, filled=True).encode(
        opacity=alt.condition(hover, alt.value(1), alt.value(0))).transform_filter(hover)
    return (risk_bands(window) + lines + points + rule).properties(
        height=280, title=alt.TitleParams(
            "EV charging load by strategy", anchor="start", color=INK, fontSize=13,
            subtitle="Same sessions for every strategy; shaded hours carry curtailment risk",
            subtitleColor=INK_2))


def tradeoff_chart(tr: pd.DataFrame) -> alt.Chart:
    df = tr.copy()
    df["Delay weight"] = df["delay_weight"].map(lambda v: f"{v:g}")
    dom = list(dict.fromkeys(df.sort_values("delay_weight")["Delay weight"]))
    base = alt.Chart(df).encode(
        x=alt.X("mean_delay_min:Q", title="Mean driver delay (min)", scale=alt.Scale(zero=False)),
        y=alt.Y("evening_peak_reduction_vs_uncontrolled_mw:Q", title="Evening peak reduction (MW)"),
        color=alt.Color("Delay weight:N", scale=alt.Scale(
            domain=dom, range=[rx.COLORS["curtailment_aware"], rx.COLORS["uncontrolled"], "#6250d6"])))
    lines = base.mark_line(strokeWidth=2).encode(order="peak_weight:Q")
    pts = base.mark_point(size=80, filled=True).encode(tooltip=[
        alt.Tooltip("peak_weight:Q", title="Peak weight"), alt.Tooltip("delay_weight:Q", title="Delay weight"),
        alt.Tooltip("absorbed_mwh:Q", format=".1f", title="Absorbed (MWh)"),
        alt.Tooltip("evening_peak_reduction_vs_uncontrolled_mw:Q", format=".2f", title="Peak cut (MW)"),
        alt.Tooltip("mean_delay_min:Q", format=".1f", title="Mean delay (min)")])
    lo, hi = df["absorbed_mwh"].min(), df["absorbed_mwh"].max()
    sub = (f"Absorbed risk energy is {lo:.1f} MWh at every setting" if hi - lo < 0.05
           else f"Absorbed risk energy {lo:.1f}-{hi:.1f} MWh") + "; lines overlap where settings give the same result"
    return (lines + pts).properties(height=300, title=alt.TitleParams(
        "Peak reduction against driver delay (peak weight 0-16)", anchor="start", color=INK,
        fontSize=13, subtitle=sub, subtitleColor=INK_2))


def years_chart(yrs: pd.DataFrame) -> alt.Chart:
    df = yrs.copy()
    df["Year"] = df["scenario_year"].astype(str)
    return alt.Chart(df).mark_bar(color=rx.COLORS["uncontrolled"], cornerRadiusTopLeft=4,
                                  cornerRadiusTopRight=4, size=34).encode(
        x=alt.X("Year:N", title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("risk_energy_gwh:Q", title="Curtailment-risk energy (GWh)"),
        tooltip=[alt.Tooltip("Year:N"), alt.Tooltip("risk_energy_gwh:Q", format=".1f", title="Risk (GWh)"),
                 alt.Tooltip("risk_hours:Q", title="Risk hours"),
                 alt.Tooltip("days_with_risk:Q", title="Days with risk"),
                 alt.Tooltip("solar_multiplier:Q", format=".2f", title="Solar ×"),
                 alt.Tooltip("demand_multiplier:Q", format=".2f", title="Demand ×")]).properties(
        height=300, title=alt.TitleParams(
            "Inferred curtailment risk by LTGEP scenario year", anchor="start", color=INK, fontSize=13,
            subtitle="Complete hours of the 2024 record; NLmin 520 MW, SNSP 75%; before planned storage",
            subtitleColor=INK_2))


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

cfg = config()
central = dd.Settings.central(cfg)
st.title("Grid Pulse EV: curtailment-aware public charging")
st.caption(f"Component 3, J26-DS-331. {DISCLAIMER}")

with st.sidebar:
    st.header("Scenario")
    with st.form("settings"):
        years = [2024] + sorted(int(y) for y in cfg["scenario_years"]["net_generation_forecast_gwh"])
        year = st.selectbox("Scenario year (LTGEP base case)", years, index=years.index(central.scenario_year))
        nlmin = st.slider("NLmin, minimum secure level (MW)", 240, 780, int(central.nlmin_mw), step=10,
                          help="Derived central value 520 MW (Appendix C); sweep 240-780 MW")
        snsp_opts = {"off": None, "50%": 0.5, "65%": 0.65, "75% (LTGEP)": 0.75}
        snsp_label = st.selectbox("SNSP limit", list(snsp_opts), index=3)
        n_sessions = st.select_slider("Public sessions per day", [1000, 3000, 5000, 10000, 20000],
                                      value=central.sessions_per_day)
        declared = st.slider("Share of drivers declaring departure", 0.5, 1.0,
                             float(central.declared_departure_fraction), step=0.05)
        st.subheader("Objective weights")
        w_peak = st.select_slider("Peak weight", [0.0, 0.25, 1.0, 4.0, 16.0], value=central.weights["peak"])
        w_delay = st.select_slider("Delay weight", [0.0, 0.01, 0.1], value=central.weights["delay"])
        st.form_submit_button("Apply", type="primary", width="stretch")
    settings = dd.Settings(year, float(nlmin), snsp_opts[snsp_label], int(n_sessions), float(declared),
                           {"curtailment": 1.0, "peak": float(w_peak), "delay": float(w_delay)})
    st.caption("Defaults are the central case of config/scenarios.json.")

tab_day, tab_res, tab_years, tab_assume = st.tabs(
    ["Schedule a day", "Experiment results", "Scenario years", "Assumptions"])

# ---- Schedule a day --------------------------------------------------------
with tab_day:
    table = days_for(settings)
    only_risk = st.toggle("Only days with curtailment risk", value=bool((table["risk_mwh"] > 0).any()))
    shown = table[table["risk_mwh"] > 0] if only_risk else table
    if shown.empty:
        st.info("No schedulable day carries curtailment risk under these settings. Try a later "
                "scenario year or a higher NLmin, or switch the toggle off.")
        shown = table
    labels = {r.day.strftime("%Y-%m-%d"): f"{r.day:%a %d %b %Y} | risk {r.risk_mwh:,.0f} MWh "
              f"in {r.risk_hours} h" for r in shown.itertuples()}
    default = shown.loc[shown["risk_mwh"].idxmax(), "day"].strftime("%Y-%m-%d")
    day_iso = st.selectbox(f"Day ({len(shown)} available)", list(labels), format_func=labels.get,
                           index=list(labels).index(default))
    with st.spinner(f"Scheduling {settings.sessions_per_day:,} sessions with each strategy..."):
        out = solve(day_iso, settings)
    if out["dropped"]:
        bad = ", ".join(out["dropped"][0]["strategies"])
        st.error(f"Infeasible for: {bad}. With these settings the stations cannot deliver every declared "
                 "driver's full energy; the day is reported, not relaxed. Raise the declared share or "
                 "lower sessions per day.")
    else:
        m = out["metrics"]
        risk_mwh = float(out["window"]["curtailment_risk_mw"].iloc[:24].sum())
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Curtailment-risk energy", f"{risk_mwh:,.1f} MWh")
        ca, sf = m.loc["curtailment_aware"], m.loc["solar_following"]
        k2.metric("Absorbed, curtailment-aware", f"{float(ca['absorbed_mwh']):,.1f} MWh",
                  f"{float(ca['absorbed_mwh']) - float(sf['absorbed_mwh']):+.1f} vs solar-following")
        k3.metric("EV-added evening peak removed",
                  f"{float(ca['evening_peak_reduction_pct_of_ev_increase']):.0f}%",
                  f"solar-following {float(sf['evening_peak_reduction_pct_of_ev_increase']):.0f}%",
                  delta_color="off")
        k4.metric("Mean driver delay", f"{float(ca['mean_delay_min']):.0f} min",
                  f"solar-following {float(sf['mean_delay_min']):.0f} min", delta_color="off")
        st.altair_chart(_style(grid_chart(out["window"])), width="stretch")
        st.altair_chart(_style(ev_chart(out["window"], out["loads"])), width="stretch")
        st.dataframe(dd.metrics_for_display(m), width="stretch")
        same = ("the same sessions as the saved experiment" if out["experiment_sessions"]
                else "a session draw for this day only (not part of the saved experiment)")
        st.caption(f"{out['sessions']:,} sessions, {same}. Every schedule passes the independent "
                   "constraint audit (Violations column). Evening = 17:00-23:59.")

# ---- Experiment results ----------------------------------------------------
with tab_res:
    r = saved()
    if r["risk_days"].empty:
        st.info("No saved results. Run scripts/run_experiments.py first.")
    else:
        summ = r["summary"]
        st.markdown(f"Saved run: git `{summ.get('git_commit', '?')}`, {summ.get('run_at', '?')}. "
                    f"Scenario year {summ.get('scenario', {}).get('scenario_year', '?')}.")
        cols = list(dd.METRIC_COLUMNS)
        st.subheader(f"Every curtailment-risk day ({len(summ.get('risk_days', []))} days)")
        st.dataframe(dd.metrics_for_display(r["risk_days"].set_index("strategy")[cols]),
                     width="stretch")
        if not r["by_day"].empty:
            st.caption("Absorbed risk energy (MWh) per day")
            st.dataframe(r["by_day"].rename(columns=rx.LABELS).set_index("day"), width="stretch")
        st.subheader(f"Year-representative weekly sample ({len(summ.get('central_days', []))} days)")
        st.dataframe(dd.metrics_for_display(r["weekly"].set_index("strategy")[cols]),
                     width="stretch")
        if not r["tradeoff"].empty:
            st.altair_chart(_style(tradeoff_chart(r["tradeoff"])), width="stretch")
        if not r["sensitivity"].empty:
            st.subheader("Sensitivity (SO6)")
            s = r["sensitivity"]
            piv = s.pivot_table(index=["dimension", "value"], columns="strategy", values="absorbed_mwh",
                                sort=False).rename(columns=rx.LABELS)
            flags = s.drop_duplicates(["dimension", "value"]).set_index(["dimension", "value"])
            for col, name in (("ranking_preserved", "Exact ranking kept"),
                              ("curtailment_aware_not_outranked", "Curtailment-aware never outranked"),
                              ("days", "Days")):
                if col in flags:
                    piv[name] = flags[col]
            st.dataframe(piv, width="stretch")
            st.caption("Absorbed risk energy (MWh) on the weekly sample.")
        if not r["scaling"].empty:
            st.subheader("Solver scaling (NFR1, NFR2)")
            st.dataframe(r["scaling"], width="stretch", hide_index=True)

# ---- Scenario years --------------------------------------------------------
with tab_years:
    yrs = saved()["scenario_years"]
    if yrs.empty:
        st.info("Run scripts/build_grid_scenario.py first.")
    else:
        st.altair_chart(_style(years_chart(yrs)), width="stretch")
        st.dataframe(yrs[["scenario_year", "solar_multiplier", "wind_multiplier", "demand_multiplier",
                          "must_take_multiplier", "risk_hours", "risk_energy_gwh", "days_with_risk",
                          "snsp_binding_hours"]], width="stretch", hide_index=True)
        st.caption("Multipliers from the CEB Long Term Generation Expansion Plan 2025-2044 base case: "
                   "installed capacity (Table 2.1), planned additions (Table E3), demand forecast (Table 3.3).")

# ---- Assumptions -----------------------------------------------------------
with tab_assume:
    st.dataframe(dd.assumptions(cfg), width="stretch", hide_index=True)
    st.markdown(
        "**Method in one paragraph.** PUCSL 15-minute dispatch is corrected to the full system with "
        "CEB monthly totals; available solar is modelled from NASA POWER irradiance. Residual load "
        "NL(t) is what coal, oil and major hydro must serve. Curtailment risk C(t) is the larger of "
        "NLmin − NL(t) and VRE − SNSP × demand. A linear program (SciPy + HiGHS) schedules each "
        "session within its arrival, departure, energy, charger and station limits, delivering 100% "
        "of declared energy, and minimises unabsorbed risk + evening peak + delay. Full detail: "
        "docs/methods_and_literature.md and docs/nlmin_derivation.md.")
    st.caption(DISCLAIMER)
