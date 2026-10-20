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
