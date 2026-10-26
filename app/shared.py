"""Shared pieces of the prototype: cached data, the day/scenario picker and charts.

Pages import this module; all numbers come from scripts/dashboard_data.py.
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
         "curtailment_aware_ml", "curtailment_aware_robust"]
YEARS = [2026, 2027, 2028, 2030]
FLEETS = [1000, 3000, 10000, 20000]
DISCLAIMER = ("Prototype. Wasted clean power is inferred from published grid limits (CEB), not measured. "
              "Charging sessions and fleet size are placeholders until real data arrives.")


# --------------------------------------------------------------------------- #
# Cached data
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner=False)
def config() -> dict:
    return bgs.load_config(bgs.DEFAULT_CONFIG)


@st.cache_resource(show_spinner="Loading the 2024 grid record...")
def inputs():
    return dd.load_inputs(config())


@st.cache_resource(show_spinner="Preparing the charging sessions...", max_entries=6)
def pool(sessions_per_day: int, declared: float) -> pd.DataFrame:
    """Depends only on the session settings, so grid changes do not redraw it."""
    base, _ = inputs()
    c = dd.Settings.central(config())
    s = dd.Settings(c.scenario_year, c.nlmin_mw, c.snsp_max, sessions_per_day, declared, c.weights)
    return dd.session_pool(base, config(), s)


@st.cache_data(show_spinner=False, max_entries=48)
def days_for(settings: dd.Settings) -> pd.DataFrame:
    base, days = inputs()
    return dd.day_table(dd.signal(base, settings.apply(config())), days)


@st.cache_data(show_spinner=False, max_entries=48)
def solve(day_iso: str, settings: dd.Settings, with_scenarios: bool = False) -> dict:
    """The day under every option; with_scenarios adds planning on 20 forecast-error scenarios."""
    base, _ = inputs()
    p = pool(settings.sessions_per_day, settings.declared_departure_fraction)
    strategies = dd.DAY_STRATEGIES + (["curtailment_aware_robust"] if with_scenarios else [])
    return dd.solve_one_day(pd.Timestamp(day_iso, tz=dd.TZ), base, config(), settings, strategies, pool=p)


@st.cache_resource(show_spinner=False, max_entries=6)
def detail(day_iso: str, settings: dd.Settings) -> dict:
    """The day planned with every car's 15-minute schedule (shared by several pages)."""
    base, _ = inputs()
    p = pool(settings.sessions_per_day, settings.declared_departure_fraction)
    return dd.day_detail(pd.Timestamp(day_iso, tz=dd.TZ), base, config(), settings, p)


@st.cache_data(show_spinner=False, max_entries=24)
def drivers(day_iso: str, settings: dd.Settings) -> list[dict]:
    return dd.driver_examples(pd.Timestamp(day_iso, tz=dd.TZ), None, config(), settings,
                              detail=detail(day_iso, settings))


@st.cache_data(show_spinner=False, max_entries=32)
def my_charge(day_iso: str, settings: dd.Settings, arrival: str, departure: str, energy_kwh: float,
              charger_kw: float) -> dict:
    base, _ = inputs()
    p = pool(settings.sessions_per_day, settings.declared_departure_fraction)
    return dd.my_charge(pd.Timestamp(day_iso, tz=dd.TZ), base, config(), settings, p, arrival, departure,
                        energy_kwh, charger_kw)


def peak_hours_label(grid: pd.DataFrame, day: pd.Timestamp) -> str:
    """The day's peak window as text, e.g. '18:00-20:00'."""
    h = grid.index[grid["is_peak_window"].fillna(False).astype(bool) & (grid.index.normalize() == day)]
    if not len(h):
        return "the peak hours"
    return f"{h.min():%H:%M}-{h.max() + pd.offsets.Hour(1):%H:%M}"


@st.cache_data(show_spinner=False)
def saved() -> dict:
    return dd.experiment_results()


# --------------------------------------------------------------------------- #
# Shared state and the day / scenario picker
# --------------------------------------------------------------------------- #

def init_state() -> None:
    """Choices shared by every page (plain keys, so they survive page changes)."""
    c = dd.Settings.central(config())
    defaults = {"choice": "Sunny Sunday", "other_day": None, "year": c.scenario_year,
                "fleet": c.sessions_per_day, "nlmin": int(c.nlmin_mw), "snsp": c.snsp_max,
                "declared": float(c.declared_departure_fraction), "rule": c.station_rule,
                "w_peak": c.weights["peak"], "w_delay": c.weights["delay"]}
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


def settings() -> dd.Settings:
    s = st.session_state
    return dd.Settings(int(s.year), float(s.nlmin), s.snsp, int(s.fleet), float(s.declared),
                       {"curtailment": 1.0, "peak": float(s.w_peak), "delay": float(s.w_delay)}, s.rule)


def _keep(name: str, key: str) -> None:
    """Copy a widget's value to the shared choice; clicking an option off keeps the last one."""
    if st.session_state[key] is None:
        st.session_state[key] = st.session_state[name]
    else:
        st.session_state[name] = st.session_state[key]


def _seed(key: str, value) -> None:
    """Start a page's widget from the shared choice (widget state is per page)."""
    if key not in st.session_state:
        st.session_state[key] = value


def picker(page: str) -> tuple[str, dd.Settings]:
    """Example-day, year and fleet controls. Returns (day ISO date, settings)."""
    s = st.session_state
    for name in ("choice", "year", "fleet"):
        _seed(f"{page}_{name}", s[name])
    with st.container(horizontal=True, vertical_alignment="bottom"):
        st.segmented_control("Day", list(dd.PRESETS) + ["Another day"], key=f"{page}_choice",
                             on_change=_keep, args=("choice", f"{page}_choice"),
                             help="Real days from the 2024 grid record")
        st.segmented_control("Year", YEARS, key=f"{page}_year",
                             on_change=_keep, args=("year", f"{page}_year"),
                             help="How much solar and wind CEB's expansion plan has built by then")
        st.segmented_control("Cars charging per day", FLEETS, key=f"{page}_fleet",
                             format_func=lambda n: f"{n:,}", on_change=_keep,
                             args=("fleet", f"{page}_fleet"),
                             help="Public charging sessions per day across Sri Lanka (placeholder)")
    current = settings()
    if s.choice == "Another day":
        table = days_for(current)
        labels = {r.day.strftime("%Y-%m-%d"): f"{r.day:%a %d %b %Y}  ·  "
                  + (f"{r.risk_mwh:,.0f} MWh wasted" if r.risk_mwh > 0 else "no wasted power")
                  for r in table.sort_values("risk_mwh", ascending=False).itertuples()}
        keys = list(labels)
        if s.other_day not in labels:
            s.other_day = keys[0]
        _seed(f"{page}_other", s.other_day)
        st.selectbox(f"Pick any of the {len(keys)} usable days (most wasted power first)", keys,
                     format_func=labels.get, key=f"{page}_other",
                     on_change=_keep, args=("other_day", f"{page}_other"))
        return s.other_day, current
    iso, note = dd.PRESETS[s.choice]
    st.caption(f"{pd.Timestamp(iso):%A %d %B} (2024 weather), scenario year {s.year}. {note}")
    return iso, current


def advanced_settings() -> None:
    """Expert parameters, in a form so the scheduler runs once per change."""
    s = st.session_state
    with st.expander("Expert settings", icon=":material/tune:"):
        with st.form("expert"):
            nlmin = st.slider("Lowest safe output of the big power stations, NLmin (MW)", 240, 780,
                              int(s.nlmin), step=10,
                              help="Derived as 520 MW from CEB's expansion plan (Appendix C)")
            snsp_opts = {"off": None, "50%": 0.5, "65%": 0.65, "75% (CEB plan)": 0.75}
            snsp_now = next(k for k, v in snsp_opts.items() if v == s.snsp)
            snsp = st.segmented_control("Inertia limit (SNSP)", list(snsp_opts), default=snsp_now)
            declared = st.slider("Share of drivers who say when they will leave", 0.5, 1.0,
                                 float(s.declared), step=0.05)
            rule = st.segmented_control(
                "When a station's connection is full, who charges first", list(dd.RULES),
                default=s.rule, format_func=dd.RULES.get,
                help="By default every car charges as it arrives and declared drivers are planned "
                     "around them. The other rule plans declared drivers first; drivers who did not say "
                     "when they leave then get what is left and may leave before they are full.")
            with st.container(horizontal=True):
                w_peak = st.select_slider("Peak weight", [0.0, 0.25, 1.0, 4.0, 16.0], value=s.w_peak)
                w_delay = st.select_slider("Waiting weight", [0.0, 0.01, 0.1], value=s.w_delay)
            with st.container(horizontal=True):
                applied = st.form_submit_button("Apply", type="primary", icon=":material/check:")
                reset = st.form_submit_button("Reset to the research defaults", icon=":material/restart_alt:")
        if applied:
            s.update(nlmin=nlmin, snsp=snsp_opts[snsp or snsp_now], declared=declared,
                     rule=rule or s.rule, w_peak=w_peak, w_delay=w_delay)
            st.rerun()
        if reset:
            for k in ("nlmin", "snsp", "declared", "rule", "w_peak", "w_delay"):
                del s[k]
            init_state()
            st.rerun()


def friendly(s: str) -> str:
    return dd.FRIENDLY.get(s, s)


def undeclared_note(settings: dd.Settings) -> str:
    """What happens to drivers who do not say when they leave, under the chosen station rule."""
    share = f"{(1 - settings.declared_departure_fraction) * 100:.0f}%"
    if settings.station_rule == "declared_first":
        return (f"Drivers who do not say when they leave ({share} here) charge on arrival in whatever "
                "connection capacity the plan leaves, so some may leave before they are full. Every driver "
                "who said when they leave gets their full energy.")
    return (f"Drivers who do not say when they leave ({share} here) always charge on arrival. Nobody is "
            "ever cut short: if a plan cannot give every driver their full energy, the day is reported "
            "as not possible.")


# --------------------------------------------------------------------------- #
# Charts (Altair; times shown as Sri Lankan wall-clock)
# --------------------------------------------------------------------------- #

def _wallclock(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return index.tz_localize(None)


def style(chart: alt.Chart) -> alt.Chart:
    return (chart.configure_axis(gridColor=GRID_C, domainColor=INK_2, tickColor=INK_2,
                                 labelColor=INK_2, titleColor=INK_2, labelFontSize=12, titleFontSize=12)
            .configure_view(strokeWidth=0)
            .configure_legend(labelColor=INK, titleColor=INK_2, orient="top", labelLimit=360,
                              labelFontSize=12))


def _title(text: str, sub: str) -> alt.TitleParams:
    return alt.TitleParams(text, anchor="start", color=INK, fontSize=14, subtitle=sub,
                           subtitleColor=INK_2, subtitleFontSize=12)


def risk_bands(window: pd.DataFrame) -> alt.Chart:
    h = window[window["curtailment_risk_mw"] > 0]
    df = pd.DataFrame({"start": _wallclock(h.index), "end": _wallclock(h.index).shift(1, freq="h")})
    return alt.Chart(df).mark_rect(color=RISK, opacity=0.10).encode(x="start:T", x2="end:T")


def grid_chart(window: pd.DataFrame) -> alt.Chart:
    df = window.copy()
    df["hour"] = _wallclock(df.index).shift(30, freq="min")              # plot at mid-hour
    x = alt.X("hour:T", title=None, axis=alt.Axis(format="%H:%M", tickCount=10))
    r = window[window["curtailment_risk_mw"] > 0]
    blocks = pd.DataFrame({"start": _wallclock(r.index), "end": _wallclock(r.index).shift(1, freq="h"),
                           "low": r[["net_load_mw", "nlmin_mw"]].min(axis=1).to_numpy(),
                           "nlmin": r["nlmin_mw"].to_numpy()})
    wasted = alt.Chart(blocks).mark_rect(color=RISK, opacity=0.35).encode(
        x="start:T", x2="end:T", y="low:Q", y2="nlmin:Q")
    need = alt.Chart(df).mark_line(color=INK, strokeWidth=2.5).encode(
        x=x, y=alt.Y("net_load_mw:Q", title="MW"))
    floor = alt.Chart(df).mark_line(color=INK_2, strokeWidth=1.5, strokeDash=[6, 4]).encode(
        x=x, y="nlmin_mw:Q")
    hover = alt.selection_point(fields=["hour"], nearest=True, on="pointerover", empty=False)
    rule = alt.Chart(df).mark_rule(color=INK_2).encode(
        x=x, opacity=alt.condition(hover, alt.value(0.6), alt.value(0)),
        tooltip=[alt.Tooltip("hour:T", format="%H:%M", title="Time"),
                 alt.Tooltip("net_load_mw:Q", format=",.0f", title="Big stations must supply (MW)"),
                 alt.Tooltip("nlmin_mw:Q", format=",.0f", title="Lowest safe level (MW)"),
                 alt.Tooltip("curtailment_risk_mw:Q", format=",.0f", title="Clean power wasted (MW)"),
                 alt.Tooltip("solar_mw:Q", format=",.0f", title="Solar (MW)"),
                 alt.Tooltip("wind_mw:Q", format=",.0f", title="Wind (MW)")]).add_params(hover)
    return (risk_bands(window) + wasted + floor + need + rule).properties(
        height=300, title=_title("When would clean power be wasted?",
                                 "Black: power the big stations must supply. Dashed: the lowest they can "
                                 "safely go. Red: solar and wind that would be switched off."))


def ev_chart(window: pd.DataFrame, loads: pd.DataFrame, shown: list[str]) -> alt.Chart:
    drawn = [s for s in ORDER if s in loads.columns and s in shown]
    wide = loads[drawn].set_axis(_wallclock(loads.index).shift(30, freq="min")).rename_axis("hour")
    df = wide.reset_index().melt("hour", var_name="strategy", value_name="ev_mw")
    df["label"] = df["strategy"].map(friendly)
    df["is_forecast"] = df["strategy"].str.endswith(rx.FORECAST_SUFFIXES)
    x = alt.X("hour:T", title=None, axis=alt.Axis(format="%H:%M", tickCount=10))
    color = alt.Color("label:N", title=None, scale=alt.Scale(
        domain=[friendly(s) for s in drawn], range=[rx.COLORS[s] for s in drawn]))
    dash = alt.StrokeDash("is_forecast:N", legend=None,
                          scale=alt.Scale(domain=[False, True], range=[[1, 0], [6, 4]]))
    lines = alt.Chart(df).mark_line(strokeWidth=2.5).encode(
        x=x, y=alt.Y("ev_mw:Q", title="Charging power, all cars (MW)"), color=color, strokeDash=dash)
    hover = alt.selection_point(fields=["hour"], nearest=True, on="pointerover", empty=False)
    tip = wide.rename(columns=friendly).reset_index()
    rule = alt.Chart(tip).mark_rule(color=INK_2).encode(
        x=x, opacity=alt.condition(hover, alt.value(0.6), alt.value(0)),
        tooltip=[alt.Tooltip("hour:T", format="%H:%M", title="Time")]
        + [alt.Tooltip(f"{c}:Q", format=".1f", title=f"{c} (MW)") for c in tip.columns if c != "hour"]
    ).add_params(hover)
    points = lines.mark_point(size=70, filled=True).encode(
        opacity=alt.condition(hover, alt.value(1), alt.value(0))).transform_filter(hover)
    return (risk_bands(window) + lines + points + rule).properties(
        height=300, title=_title("When do the cars charge?",
                                 "Same cars and same energy for every option. Shaded: hours when clean "
                                 "power would be wasted. Evening peak: 17:00 to midnight."))


def timeline_chart(rows: list[dict], window: pd.DataFrame) -> alt.LayerChart:
    """Charging as a timeline: one row each, a block for every 15 minutes of charging.

    rows: dicts with label, power (kW series on the slot index), color, max_kw,
    start and end (plug-in window, drawn grey). Rows rather than overlaid lines,
    because rows that charge at the same time would hide each other. Lighter
    blocks mean the charger ran below full power.
    """
    lo = min(r["start"] for r in rows) - pd.offsets.Hour(1)
    hi = max(r["end"] for r in rows) + pd.offsets.Hour(1)
    labels = [r["label"] for r in rows]
    blocks, plugs = [], []
    for r in rows:
        p = r["power"][(r["power"].index >= lo) & (r["power"].index < hi)]
        for ts, kw in zip(_wallclock(p.index), p.to_numpy()):
            if kw > 1e-3:
                blocks.append({"start": ts, "end": ts + pd.offsets.Minute(15), "label": r["label"],
                               "kw": float(kw), "share": float(kw) / r["max_kw"]})
        plugs.append({"start": r["start"].tz_localize(None), "end": r["end"].tz_localize(None),
                      "label": r["label"]})
    x_dom = [lo.tz_localize(None).isoformat(), hi.tz_localize(None).isoformat()]
    x = alt.X("start:T", title=None, scale=alt.Scale(domain=x_dom), axis=alt.Axis(format="%H:%M", tickCount=10))
    y = alt.Y("label:N", title=None, sort=labels, axis=alt.Axis(labelLimit=220, labelFontSize=12))
    plug = alt.Chart(pd.DataFrame(plugs)).mark_rect(color=INK_2, opacity=0.09, height=30).encode(
        x=x, x2="end:T", y=y)
    w = window[(window.index >= lo.floor("h")) & (window.index < hi)]
    colors = list(dict.fromkeys((r["label"], r["color"]) for r in rows))
    bars = alt.Chart(pd.DataFrame(blocks)).mark_rect(height=22, cornerRadius=2).encode(
        x=x, x2="end:T", y=y,
        color=alt.Color("label:N", legend=None, scale=alt.Scale(domain=[c[0] for c in colors],
                                                                range=[c[1] for c in colors])),
        opacity=alt.Opacity("share:Q", legend=None, scale=alt.Scale(domain=[0, 1], range=[0.35, 1])),
        tooltip=[alt.Tooltip("label:N", title=" "), alt.Tooltip("start:T", format="%H:%M", title="From"),
                 alt.Tooltip("end:T", format="%H:%M", title="To"),
                 alt.Tooltip("kw:Q", format=".1f", title="Charging power (kW)")])
    return (risk_bands(w) + plug + bars).properties(height=38 * len(rows) + 30)


def driver_chart(example: dict, window: pd.DataFrame) -> alt.LayerChart:
    """One driver's charging under each option (a row per option)."""
    rows = [{"label": friendly(s), "power": example["power_kw"][s], "color": rx.COLORS[s],
             "max_kw": example["charger_kw"], "start": example["arrival"], "end": example["departure"]}
            for s in ORDER if s in example["power_kw"].columns]
    return timeline_chart(rows, window)


def station_power_chart(view: dict, window: pd.DataFrame) -> alt.LayerChart:
    """The station's total power under each option against its connection limit."""
    tot = view["totals_kw"]
    used = tot[(tot.sum(axis=1) > 1e-3)]
    lo, hi = used.index.min() - pd.offsets.Hour(1), used.index.max() + pd.offsets.Hour(1)
    tot = tot[(tot.index >= lo) & (tot.index < hi)]
    drawn = [s for s in ORDER if s in tot.columns]
    df = (tot.set_axis(_wallclock(tot.index)).rename_axis("t").reset_index()
          .melt("t", var_name="strategy", value_name="kw"))
    df["label"] = df["strategy"].map(friendly)
    x = alt.X("t:T", title=None, axis=alt.Axis(format="%H:%M", tickCount=10))
    lines = alt.Chart(df).mark_line(strokeWidth=2.5, interpolate="step-after").encode(
        x=x, y=alt.Y("kw:Q", title="Station power (kW)",
                     scale=alt.Scale(domain=[0, view["capacity_kw"] * 1.15])),
        color=alt.Color("label:N", title=None, scale=alt.Scale(
            domain=[friendly(s) for s in drawn], range=[rx.COLORS[s] for s in drawn])),
        tooltip=[alt.Tooltip("t:T", format="%H:%M", title="From"), alt.Tooltip("label:N", title="Option"),
                 alt.Tooltip("kw:Q", format=".1f", title="kW")])
    cap = alt.Chart(pd.DataFrame({"cap": [view["capacity_kw"]]})).mark_rule(
        color=INK, strokeDash=[6, 4], strokeWidth=1.5).encode(y="cap:Q")
    cap_text = alt.Chart(pd.DataFrame({"cap": [view["capacity_kw"]],
                                       "t": [_wallclock(pd.DatetimeIndex([lo]))[0]]})).mark_text(
        align="left", dx=4, dy=-8, color=INK, fontSize=12).encode(
        x="t:T", y="cap:Q", text=alt.value(f"Connection limit {view['capacity_kw']:g} kW"))
    w = window[(window.index >= lo.floor("h")) & (window.index < hi)]
    return (risk_bands(w) + cap + cap_text + lines).properties(
        height=280, title=_title("Power drawn by the whole station",
                                 "Every option stays under the station's connection limit (dashed)."))


def years_chart(yrs: pd.DataFrame) -> alt.Chart:
    df = yrs[yrs["scenario_year"] >= 2025].copy()
    df["Year"] = df["scenario_year"].astype(str)
    return alt.Chart(df).mark_bar(color=rx.COLORS["uncontrolled"], cornerRadiusTopLeft=4,
                                  cornerRadiusTopRight=4, size=36).encode(
        x=alt.X("Year:N", title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("risk_energy_gwh:Q", title="Clean power that would be wasted (GWh)"),
        tooltip=[alt.Tooltip("Year:N"), alt.Tooltip("risk_energy_gwh:Q", format=".1f", title="Wasted (GWh)"),
                 alt.Tooltip("days_with_risk:Q", title="Days with waste"),
                 alt.Tooltip("solar_multiplier:Q", format=".2f", title="Solar vs 2024 (×)"),
                 alt.Tooltip("demand_multiplier:Q", format=".2f", title="Demand vs 2024 (×)")]
    ).properties(height=280, title=_title(
        "Wasted clean power grows as CEB's plan is built",
        "Before any new batteries. Lowest safe level 520 MW, inertia limit 75%."))


def forecast_chart(holdout: pd.DataFrame, target: str, start: pd.Timestamp, days: int = 7) -> alt.Chart:
    """Actual vs forecast for one week of the hold-out (months the models never saw)."""
    h = holdout[(holdout["target"] == target) & (holdout.index >= start)
                & (holdout.index < start + pd.offsets.Day(days))]
    lines = {"Actual": "actual_mw", "Forecast (chosen model)": "forecast_mw",
             "Tomorrow = today": "persistence_mw"}
    wide = h[list(lines.values())].set_axis(list(lines), axis=1).set_axis(_wallclock(h.index)).rename_axis("hour")
    df = wide.reset_index().melt("hour", var_name="series", value_name="mw")
    x = alt.X("hour:T", title=None, axis=alt.Axis(format="%a %d %b", tickCount=days))
    color = alt.Color("series:N", title=None, scale=alt.Scale(
        domain=list(lines), range=[INK, rx.COLORS["curtailment_aware_ml"], rx.COLORS["curtailment_aware_persistence"]]))
    dash = alt.StrokeDash("series:N", title=None, scale=alt.Scale(
        domain=list(lines), range=[[1, 0], [1, 0], [5, 4]]))
    chart = alt.Chart(df).mark_line(strokeWidth=2).encode(
        x=x, y=alt.Y("mw:Q", title="MW", scale=alt.Scale(zero=False)), color=color, strokeDash=dash)
    hover = alt.selection_point(fields=["hour"], nearest=True, on="pointerover", empty=False)
    rule = alt.Chart(wide.reset_index()).mark_rule(color=INK_2).encode(
        x=x, opacity=alt.condition(hover, alt.value(0.6), alt.value(0)),
        tooltip=[alt.Tooltip("hour:T", format="%a %d %b %H:%M", title="Time")]
        + [alt.Tooltip(f"{c}:Q", format=",.0f", title=f"{c} (MW)") for c in lines]).add_params(hover)
    return (chart + rule).properties(height=300, title=_title(
        "Forecast made at midnight vs what actually happened",
        "A week from October–December 2024, months the model never saw while training."))


def compare_bars(table: pd.DataFrame, col: str, title: str, fmt: str, sub: str) -> alt.Chart:
    """Horizontal bars, one per option; the axis names each bar, so no legend."""
    df = table[[col]].reset_index().rename(columns={"index": "strategy", "strategy": "strategy"})
    df.columns = ["strategy", "value"]
    df["label"] = df["strategy"].map(friendly)
    drawn = [s for s in ORDER if s in set(df["strategy"])]
    y = alt.Y("label:N", title=None, sort=[friendly(s) for s in drawn], axis=alt.Axis(labelLimit=220))
    top = float(df["value"].max()) * 1.22 or 1.0          # headroom for the value labels
    bars = alt.Chart(df).mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, size=22).encode(
        x=alt.X("value:Q", title=None, scale=alt.Scale(domain=[0, top], nice=False)), y=y,
        color=alt.Color("label:N", legend=None, scale=alt.Scale(
            domain=[friendly(s) for s in drawn], range=[rx.COLORS[s] for s in drawn])),
        tooltip=[alt.Tooltip("label:N", title="Option"), alt.Tooltip("value:Q", format=fmt, title=title)])
    text = bars.mark_text(align="left", dx=4, color=INK).encode(text=alt.Text("value:Q", format=fmt),
                                                                color=alt.value(INK))
    return (bars + text).properties(height=34 * len(drawn) + 20, title=_title(title, sub))


def tradeoff_chart(tr: pd.DataFrame) -> alt.Chart:
    df = tr.copy()
    df["Waiting weight"] = df["delay_weight"].map(lambda v: f"{v:g}")
    dom = list(dict.fromkeys(df.sort_values("delay_weight")["Waiting weight"]))
    enc = alt.Chart(df).encode(
        x=alt.X("mean_delay_min:Q", title="Average extra wait (min)", scale=alt.Scale(zero=False)),
        y=alt.Y("evening_peak_reduction_vs_uncontrolled_mw:Q", title="Evening peak removed (MW)"),
        color=alt.Color("Waiting weight:N", scale=alt.Scale(
            domain=dom, range=[rx.COLORS["curtailment_aware"], rx.COLORS["uncontrolled"], "#6250d6"])))
    pts = enc.mark_point(size=90, filled=True).encode(tooltip=[
        alt.Tooltip("peak_weight:Q", title="Peak weight"), alt.Tooltip("delay_weight:Q", title="Waiting weight"),
        alt.Tooltip("absorbed_mwh:Q", format=".1f", title="Clean power used (MWh)"),
        alt.Tooltip("evening_peak_reduction_vs_uncontrolled_mw:Q", format=".2f", title="Peak removed (MW)"),
        alt.Tooltip("mean_delay_min:Q", format=".1f", title="Average wait (min)")])
    return (enc.mark_line(strokeWidth=2).encode(order="peak_weight:Q") + pts).properties(
        height=280, title=_title("Cutting the evening peak costs a few minutes of waiting",
                                 f"Clean power used stays at {df['absorbed_mwh'].min():.1f} MWh at every "
                                 "setting; lines overlap where settings give the same result"))


def scenario_count_chart(rk: pd.DataFrame) -> alt.Chart:
    """Clean power used on the risk days against the number of forecast scenarios."""
    df = rk[rk["day_set"] == "all risk days"].copy()
    df["label"] = df["strategy"].map(friendly)
    drawn = [s for s in ORDER if s in set(df["strategy"])]
    enc = alt.Chart(df).encode(
        x=alt.X("scenarios:Q", title="Forecast scenarios planned for", scale=alt.Scale(type="log", base=2),
                axis=alt.Axis(values=sorted(df["scenarios"].unique()), format="d")),
        y=alt.Y("absorbed_mwh:Q", title="Clean power used (MWh)", scale=alt.Scale(zero=False)),
        color=alt.Color("label:N", title=None, scale=alt.Scale(
            domain=[friendly(s) for s in drawn], range=[rx.COLORS[s] for s in drawn])))
    pts = enc.mark_point(size=80, filled=True).encode(tooltip=[
        alt.Tooltip("label:N", title="Option"), alt.Tooltip("scenarios:Q", title="Scenarios"),
        alt.Tooltip("absorbed_mwh:Q", format=",.1f", title="Clean power used (MWh)"),
        alt.Tooltip("mean_evening_peak_increase_mw:Q", format=".2f", title="Evening peak added (MW)"),
        alt.Tooltip("mean_delay_min:Q", format=".0f", title="Average wait (min)")])
    return (enc.mark_line(strokeWidth=2.5) + pts).properties(height=280, title=_title(
        "How many versions of tomorrow are enough?",
        "Risk days. Only the plan for forecast errors depends on the number; the other two lines are flat."))
