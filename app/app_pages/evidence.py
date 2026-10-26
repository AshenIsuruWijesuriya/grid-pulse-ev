import pandas as pd
import streamlit as st

import shared  # first: puts scripts/ on the import path
import dashboard_data as dd

st.title("Evidence", icon=":material/fact_check:")
st.caption("The saved results of the full-year study, in plain words. These come from "
           "scripts/run_experiments.py, not from this page.")

r = shared.saved()
if r["risk_days"].empty:
    st.info("Run scripts/run_experiments.py to produce the results shown here.", icon=":material/info:")
    st.stop()
summ = r["summary"]
year = summ.get("scenario", {}).get("scenario_year", 2028)

st.subheader("1. The problem is coming", icon=":material/trending_up:")
if not r["scenario_years"].empty:
    left, right = st.columns([3, 2], vertical_alignment="center")
    with left:
        st.altair_chart(shared.style(shared.years_chart(r["scenario_years"])))
    with right:
        st.markdown("Today the grid can still use almost all its clean power. As CEB builds the solar "
                    "and wind in its own expansion plan, the midday surplus grows faster than demand. "
                    f"**{year}** is the first year it becomes large, so the study uses it as its main case.")

st.subheader(f"2. On the {len(summ.get('risk_days', []))} days that waste clean power", icon=":material/solar_power:")
risk = r["risk_days"].set_index("strategy")
main = [s for s in ("uncontrolled", "solar_following", "curtailment_aware", "curtailment_aware_ml",
                     "curtailment_aware_robust") if s in risk.index]
cols = st.columns(3)
for col, (field, title, fmt, sub) in zip(cols, [
        ("absorbed_mwh", "Clean power used (MWh)", ",.0f", "more is better"),
        ("mean_evening_peak_increase_mw", "Evening peak added by EVs (MW)", ".1f", "less is better"),
        ("mean_delay_min", "Average extra wait (min)", ".0f", "less is better")]):
    with col:
        st.altair_chart(shared.style(shared.compare_bars(risk.loc[main], field, title, fmt, sub)))
by_day = r["by_day"]
smart, sunny = risk.loc["curtailment_aware"], risk.loc["solar_following"]
pct = "evening_peak_reduction_pct_of_ev_increase"
points = [f"Smart charging removes **{smart[pct]:.0f}%** of the evening peak that EVs add, against "
          f"{sunny[pct]:.0f}% when charging when sunny, with **{smart['mean_delay_min']:.0f} minutes** of "
          f"extra waiting instead of {sunny['mean_delay_min']:.0f}."]
if not by_day.empty:
    diff = (by_day["curtailment_aware"] - by_day["solar_following"]).round(2)
    wins = by_day.loc[diff > 0.5, "day"].tolist()
    behind = int((diff < -0.5).sum())
    points.append(
        ("It uses at least as much clean power on every day. " if not behind else
         f"It uses less clean power on {behind} of {len(by_day)} days. ")
        + (f"It uses **more** on {len(wins)} of {len(by_day)} days "
           f"({', '.join(pd.Timestamp(d).strftime('%d %b') for d in wins)}), when the surplus did not sit on "
           "the sunniest hours (for example monsoon wind). On the other days both catch it."
           if wins else "Both catch the same clean power, because the surplus sits on the sunny hours."))
if "curtailment_aware_ml" in risk.index:
    fc = risk.loc["curtailment_aware_ml"]
    points.append(f"Planning on a **forecast** instead of perfect knowledge still removes {fc[pct]:.0f}% of "
                  f"the evening peak (perfect knowledge: {smart[pct]:.0f}%) and uses {fc['absorbed_mwh']:,.0f} "
                  f"instead of {smart['absorbed_mwh']:,.0f} MWh of clean power.")
st.markdown("\n".join(f"- {p}" for p in points))
if not by_day.empty:
    if {"curtailment_aware_ml", "curtailment_aware_robust"} <= set(risk.index):
        perfect, fc, rob = (risk.loc[s] for s in ("curtailment_aware", "curtailment_aware_ml",
                                                   "curtailment_aware_robust"))
        lost = perfect["absorbed_mwh"] - fc["absorbed_mwh"]
        back = rob["absorbed_mwh"] - fc["absorbed_mwh"]
        if lost > 0:
            st.markdown(
                f"- Forecasts are wrong in ways that repeat. Making one plan that also works if tomorrow goes "
                f"like similar past days where the forecast was wrong wins back **{back:,.0f} of the "
                f"{lost:,.0f} MWh** the forecast loses, and adds {rob['mean_evening_peak_increase_mw']:.1f} MW "
                f"to the evening peak (the forecast alone: {fc['mean_evening_peak_increase_mw']:.1f} MW). The "
                f"price is waiting: {rob['mean_delay_min']:.0f} min on average instead of "
                f"{fc['mean_delay_min']:.0f}.")

st.subheader("3. Is it robust?", icon=":material/verified:")
sens = r["sensitivity"]
n_cases, n_lead = dd.sensitivity_cases(sens)
breaks, checked = dd.audit_violations(r)
with st.container(horizontal=True):
    if n_cases:
        st.metric("Settings where smart charging is never beaten", f"{n_lead} of {n_cases}",
                  "grid limits, years, driver behaviour", delta_color="off", border=True)
    if not r["scaling"].empty:
        ten = r["scaling"].set_index("sessions").loc[10000]
        st.metric("Time to plan 10,000 cars", f"{ten['total_s']:.1f} s", "requirement: under 60 s",
                  delta_color="off", border=True)
    if checked:
        st.metric("Rule breaks found by the independent audit", f"{breaks:,}",
                  f"across {checked} saved results", delta_color="off", border=True)
st.markdown("The study was repeated with a lower and higher safe minimum for the power stations, "
            "different inertia limits, different years, and fewer drivers saying when they leave."
            + (" Smart charging never came out behind." if n_cases and n_lead == n_cases else
               f" Smart charging was never beaten in {n_lead} of {n_cases} settings."))
rules = dd.station_rule_table(sens, shared.config()["central"].get("station_rule", "arrival_first"))
if not rules.empty:
    st.markdown("The input that matters most is **how many drivers say when they will leave**. Drivers who "
                "do not say charge on arrival and can fill a station's shared connection. Two rules for who "
                "charges first were compared on the weekly sample:")
    st.dataframe(rules, hide_index=True, alt="Station rules compared by share of drivers who say when they leave",
                 column_config={"Drivers who say when they leave": st.column_config.NumberColumn(format="percent"),
                                "Others leave full": st.column_config.NumberColumn(format="percent"),
                                "Clean power used (MWh)": st.column_config.NumberColumn(format="%.1f"),
                                "Evening peak added (MW)": st.column_config.NumberColumn(format="%.2f")})
    st.caption("Days not possible: some declared driver could not be given their full energy, so the day is "
               "reported rather than planned. Others leave full: drivers who did not say when they leave.")
if not r["tradeoff"].empty:
    st.altair_chart(shared.style(shared.tradeoff_chart(r["tradeoff"])))

fa, fs, fh = (r.get(k, empty) for k, empty in (("forecast_accuracy", pd.DataFrame()), ("forecast_summary", {}),
                                                ("forecast_holdout", pd.DataFrame())))
if not fa.empty and fs:
    st.subheader("4. How accurate is the forecast?", icon=":material/query_stats:")
    st.markdown("In real life the scheduler plans at midnight for the next day, so it only has a "
                "**forecast** of the grid. The models were trained on **January–September 2024** and tested "
                "on **October–December 2024**, months they never saw. Each model was chosen on "
                "**July–September**, before the test months were looked at, and only if it beat the "
                "simple rule *tomorrow = today*.")
    selected = fs.get("selected_model_per_target", {})
    table = dd.forecast_table(fa, selected)
    acc = fa.set_index(["target", "model"])
    chosen = selected.get("demand_system_mw", "gbm")
    demand = acc.loc[("demand_system_mw", chosen)]
    ref = acc.loc[("demand_system_mw", "persistence")]
    solar_model = selected.get("solar_available_mw", "persistence")
    solar, solar_ref = acc.loc[("solar_available_mw", solar_model)], acc.loc[("solar_available_mw", "persistence")]
    det = fs.get("holdout_curtailment_risk_detection", {}).get("selected_per_target", {})
    with st.container(horizontal=True):
        st.metric("Average error of the demand forecast", f"{demand['mape_pct']:.1f}%",
                  f"{demand['mae']:.0f} MW, against {ref['mae']:.0f} MW for tomorrow = today",
                  delta_color="off", border=True)
        st.metric("Better than tomorrow = today", f"{demand['skill_vs_persistence'] * 100:.0f}%",
                  f"demand, {dd.FORECAST_MODELS[chosen].lower()}", delta_color="off", border=True)
        if solar_model.endswith("_nwp"):
            st.metric("Solar forecast better than tomorrow = today", f"{solar['skill_vs_persistence'] * 100:.0f}%",
                      f"{solar['mae']:.0f} MW against {solar_ref['mae']:.0f} MW, using a weather forecast",
                      delta_color="off", border=True)
        if det:
            st.metric("Waste warnings that were right", f"{int(det['precision'] * 100 + 0.5)}%",
                      f"and it finds {det['recall'] * 100:.0f}% of the waste hours", delta_color="off", border=True)
        if "curtailment_aware_ml" in risk.index:
            st.metric("Evening peak removed when planning on the forecast",
                      f"{risk.loc['curtailment_aware_ml', 'evening_peak_reduction_pct_of_ev_increase']:.1f}%",
                      f"{risk.loc['curtailment_aware', 'evening_peak_reduction_pct_of_ev_increase']:.0f}% "
                      "with perfect knowledge", delta_color="off", border=True)
    if not fh.empty:
        names = {v: k for k, v in dd.FORECAST_TARGETS.items()}
        weeks = pd.date_range("2024-10-07", "2024-12-23", freq="7D", tz=dd.TZ)
        with st.container(horizontal=True, vertical_alignment="bottom"):
            pick = st.segmented_control("Forecast of", list(names), default="Demand", key="ev_ftarget")
            week = st.select_slider("Week", weeks, value=pd.Timestamp("2024-10-28", tz=dd.TZ), key="ev_fweek",
                                    format_func=lambda d: f"{d:%d %b} – {d + pd.offsets.Day(6):%d %b}")
        st.altair_chart(shared.style(shared.forecast_chart(fh, names[pick or "Demand"], week)))
        st.caption("Black: what actually happened. Pink: the forecast made at midnight by the chosen model. "
                   "Dashed: tomorrow = today. The closer pink is to black, the better.")
    st.dataframe(table, hide_index=True, alt="Forecast error of every model on October to December 2024",
                 column_config={**{m: st.column_config.NumberColumn(f"{m} (MW)", format="%.0f")
                                   for m in dd.FORECAST_MODELS.values()},
                                "Better than tomorrow = today": st.column_config.NumberColumn(format="percent")})
    notes = ["Errors are the average miss in MW per hour (MAE) on October–December 2024, with every model "
             "scored on the same hours; lower is better."]
    nwp = fs.get("nwp") or {}
    if solar_model.endswith("_nwp"):
        source = "ECMWF" if "ecmwf" in nwp.get("source", "") else "GFS"
        notes.append(f"The solar forecast uses the {source} weather forecast for three sites, taken only from "
                     "forecast runs that were published before midnight.")
    else:
        notes.append("For solar no model beat tomorrow = today; clouds need a weather forecast.")
    if det and det.get("actual_risk_mwh"):
        notes.append(f"The forecast also under-estimates the wasted clean power by about "
                     f"{(1 - det['forecast_risk_mwh'] / det['actual_risk_mwh']) * 100:.0f}%.")
    st.caption(" ".join(notes))

rk = r["robust_k"]
if not rk.empty and "curtailment_aware_robust" in risk.index:
    st.subheader("5. Planning for forecast errors", icon=":material/stacked_line_chart:")
    st.markdown("A forecast is wrong in ways that repeat. Instead of trusting it, the scheduler can plan one "
                "schedule against several versions of tomorrow: the forecast plus the errors it made on the "
                "most similar days of other months. Every driver who said when they leave still leaves full.")
    st.altair_chart(shared.style(shared.scenario_count_chart(rk)))
    k_used = shared.config()["central"].get("forecast_scenarios", 20)
    causal = rk[(rk["day_set"] == "risk days with past analogs") & (rk["scenarios"] == k_used)
                & (rk["strategy"] == "curtailment_aware_robust")].set_index("past_months_only")
    if {True, False} <= set(causal.index):
        n_days = int(causal.loc[True, "days"])
        st.markdown(f"Check for look-ahead: on the {n_days} risk days that have enough earlier days, taking the "
                    f"errors only from **earlier months** gives {causal.loc[True, 'absorbed_mwh']:,.1f} MWh "
                    f"and +{causal.loc[True, 'mean_evening_peak_increase_mw']:.2f} MW, against "
                    f"{causal.loc[False, 'absorbed_mwh']:,.1f} MWh and "
                    f"+{causal.loc[False, 'mean_evening_peak_increase_mw']:.2f} MW with errors from any other month.")
    st.caption(f"The study uses {k_used} scenarios. Planning for errors costs waiting time, because the plan "
               "spreads charging over every hour that might have spare clean power.")

with st.expander("All result tables", icon=":material/table_chart:"):
    cols_ = list(dd.METRIC_COLUMNS)
    st.markdown("**Curtailment-risk days**")
    st.dataframe(dd.metrics_for_display(r["risk_days"].set_index("strategy").reindex(columns=cols_)),
                 alt="Risk-day results")
    if not by_day.empty:
        st.markdown("**Clean power used per day (MWh)**")
        st.dataframe(by_day.rename(columns=shared.friendly).set_index("day"), alt="Per-day results")
    st.markdown("**Weekly sample of the whole year**")
    st.dataframe(dd.metrics_for_display(r["weekly"].set_index("strategy").reindex(columns=cols_)),
                 alt="Weekly-sample results")
    if not r["robust_k"].empty:
        st.markdown("**Planning for forecast errors: number of scenarios and past-only errors**")
        st.dataframe(r["robust_k"].assign(strategy=r["robust_k"]["strategy"].map(shared.friendly)),
                     hide_index=True, alt="Forecast-scenario planning by number of scenarios")
    if not sens.empty:
        st.markdown("**Sensitivity (clean power used, MWh)**")
        piv = sens.pivot_table(index=["dimension", "value"], columns="strategy", values="absorbed_mwh",
                               sort=False).rename(columns=shared.friendly)
        flags = sens.drop_duplicates(["dimension", "value"]).set_index(["dimension", "value"])
        for c, name in (("curtailment_aware_not_outranked", "Smart charging never beaten"), ("days", "Days")):
            if c in flags:
                piv[name] = flags[c]
        st.dataframe(piv, alt="Sensitivity results")
    if not r["scaling"].empty:
        st.markdown("**Speed**")
        st.dataframe(r["scaling"], hide_index=True, alt="Solver speed by number of sessions")
    st.caption(f"Saved run: git {summ.get('git_commit', '?')}, {summ.get('run_at', '?')}.")
