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
main = [s for s in ("uncontrolled", "solar_following", "curtailment_aware", "curtailment_aware_ml") if s in risk.index]
cols = st.columns(3)
for col, (field, title, fmt, sub) in zip(cols, [
        ("absorbed_mwh", "Clean power used (MWh)", ",.0f", "more is better"),
        ("mean_evening_peak_increase_mw", "Evening peak added by EVs (MW)", ".1f", "less is better"),
        ("mean_delay_min", "Average extra wait (min)", ".0f", "less is better")]):
    with col:
        st.altair_chart(shared.style(shared.compare_bars(risk.loc[main], field, title, fmt, sub)))
by_day = r["by_day"]
if not by_day.empty:
    diff = (by_day["curtailment_aware"] - by_day["solar_following"]).round(2)
    wins = by_day.loc[diff > 0.5, "day"].tolist()
    st.markdown(
        "- Smart charging removes about **twice as much** of the evening peak as charging when sunny, "
        "with about **a third** of the waiting.\n"
        f"- It uses at least as much clean power on every day. It uses **more** on "
        f"{len(wins)} of {len(by_day)} days ({', '.join(pd.Timestamp(d).strftime('%d %b') for d in wins)}), "
        "when monsoon wind moved the surplus away from the sunniest hour. On the other days the surplus "
        "sits on the sunny hours, so both catch it.\n"
        "- With a **forecast** instead of perfect knowledge, most of the peak benefit remains but some of "
        "the clean-power benefit is lost.")

st.subheader("3. Is it robust?", icon=":material/verified:")
lead = summ.get("so6_curtailment_aware_not_outranked_share")
sens = r["sensitivity"]
n_cases = len(sens.drop_duplicates(["dimension", "value"])) if not sens.empty else 0
with st.container(horizontal=True):
    if lead is not None:
        st.metric("Settings where smart charging is never beaten", f"{round(lead * n_cases)} of {n_cases}",
                  "grid limits, years, driver behaviour", delta_color="off", border=True)
    if not r["scaling"].empty:
        ten = r["scaling"].set_index("sessions").loc[10000]
        st.metric("Time to plan 10,000 cars", f"{ten['total_s']:.1f} s", "requirement: under 60 s",
                  delta_color="off", border=True)
    st.metric("Rule breaks found by the independent audit", "0", "every plan, every run", delta_color="off", border=True)
st.markdown("The study was repeated with a lower and higher safe minimum for the power stations, "
            "different inertia limits, different years, and fewer drivers saying when they leave. Smart "
            "charging never came out behind. The input that matters most is **how many drivers say when "
            "they will leave**: below about 80%, some stations can no longer promise everyone a full battery.")
if not r["tradeoff"].empty:
    st.altair_chart(shared.style(shared.tradeoff_chart(r["tradeoff"])))

fa, fs, fh = (r.get(k, empty) for k, empty in (("forecast_accuracy", pd.DataFrame()), ("forecast_summary", {}),
                                                ("forecast_holdout", pd.DataFrame())))
if not fa.empty and fs:
    st.subheader("4. How accurate is the forecast?", icon=":material/query_stats:")
    st.markdown("In real life the scheduler plans at midnight for the next day, so it only has a "
                "**forecast** of the grid. The models were trained on **January–September 2024** and tested "
                "on **October–December 2024**, months they never saw. A model is used only if it beats the "
                "simple rule *tomorrow = today*.")
    table = dd.forecast_table(fa, fs.get("selected_model_per_target", {}))
    acc = fa.set_index(["target", "model"])
    chosen = fs.get("selected_model_per_target", {}).get("demand_system_mw", "gbm")
    demand = acc.loc[("demand_system_mw", chosen)]
    ref = acc.loc[("demand_system_mw", "persistence")]
    det = fs.get("holdout_curtailment_risk_detection", {}).get("selected_per_target", {})
    with st.container(horizontal=True):
        st.metric("Average error of the demand forecast", f"{demand['mape_pct']:.1f}%",
                  f"{demand['mae']:.0f} MW, against {ref['mae']:.0f} MW for tomorrow = today",
                  delta_color="off", border=True)
        st.metric("Better than tomorrow = today", f"{demand['skill_vs_persistence'] * 100:.0f}%",
                  f"demand, {dd.FORECAST_MODELS[chosen].lower()}", delta_color="off", border=True)
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
    notes = ["Errors are the average miss in MW per hour (MAE) on October–December 2024; lower is better."]
    if not fh.empty:
        same = dd.forecast_skill_same_hours(fh, "demand_system_mw")
        notes.append(f"On exactly the hours both demand forecasts cover ({same['hours']:,}), the chosen model "
                     f"is {same['skill'] * 100:.0f}% better than tomorrow = today.")
    notes.append("For solar no model beat tomorrow = today, because clouds need a weather forecast: that is "
                 "the next step.")
    if det and det.get("actual_risk_mwh"):
        notes.append(f"The forecast also under-estimates the wasted clean power by about "
                     f"{(1 - det['forecast_risk_mwh'] / det['actual_risk_mwh']) * 100:.0f}%.")
    st.caption(" ".join(notes))

with st.expander("All result tables", icon=":material/table_chart:"):
    cols_ = list(dd.METRIC_COLUMNS)
    st.markdown("**Curtailment-risk days**")
    st.dataframe(dd.metrics_for_display(r["risk_days"].set_index("strategy")[cols_]), alt="Risk-day results")
    if not by_day.empty:
        st.markdown("**Clean power used per day (MWh)**")
        st.dataframe(by_day.rename(columns=shared.friendly).set_index("day"), alt="Per-day results")
    st.markdown("**Weekly sample of the whole year**")
    st.dataframe(dd.metrics_for_display(r["weekly"].set_index("strategy")[cols_]), alt="Weekly-sample results")
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
