import pandas as pd
import streamlit as st

import shared  # first: puts scripts/ on the import path
import dashboard_data as dd

st.title("Plan a day", icon=":material/calendar_month:")
st.caption("Pick a real day and a future year. The scheduler plans every car's charging for that day, "
           "live, and compares it with what happens today.")

day_iso, settings = shared.picker("plan")
shared.advanced_settings()

with st.spinner(f"Planning {settings.sessions_per_day:,} charging sessions for each option..."):
    out = shared.solve(day_iso, settings)
day = pd.Timestamp(day_iso)

if out["dropped"]:
    st.error("With these settings the chargers cannot give every driver who said when they are leaving "
             "their full energy in time, so this day is reported as not possible rather than cutting "
             "anyone short. Try more drivers who say when they leave, or fewer cars.",
             title="This day cannot be planned", icon=":material/block:")
    st.stop()

v = dd.verdict(day, out)
m = out["metrics"]
smart, unc, sunny = (m.loc[s] for s in ("curtailment_aware", "uncontrolled", "solar_following"))

with st.container(border=True):
    st.markdown(f":material/{'solar_power' if v['has_risk'] else 'check_circle'}: {v['grid']}")
    if v["clean"]:
        st.markdown(f":material/battery_charging_full: {v['clean']}")
    st.markdown(f":material/trending_down: {v['peak']}")
    st.markdown(f":material/schedule: {v['drivers']}")

with st.container(horizontal=True):
    hours = int((out["window"]["curtailment_risk_mw"].iloc[:24] > 0).sum())
    st.metric("Clean power that would be wasted", f"{v['risk_mwh']:,.0f} MWh",
              f"in {hours} hour{'s' if hours != 1 else ''}", delta_color="off", border=True)
    st.metric("Put into cars by smart charging", f"{float(smart['absorbed_mwh']):,.1f} MWh",
              f"{float(smart['absorbed_mwh']) - float(unc['absorbed_mwh']):+,.1f} vs on arrival", border=True)
    st.metric("Evening peak added by EVs, removed",
              f"{int(round(float(smart['evening_peak_reduction_pct_of_ev_increase']))) + 0}%",
              f"charging when sunny: {int(round(float(sunny['evening_peak_reduction_pct_of_ev_increase']))) + 0}%",
              delta_color="off", border=True)
    st.metric("Extra wait, on average", f"{float(smart['mean_delay_min']):.0f} min",
              f"charging when sunny: {float(sunny['mean_delay_min']):.0f} min", delta_color="off", border=True)

st.altair_chart(shared.style(shared.grid_chart(out["window"])))

options = [s for s in shared.ORDER if s in out["loads"].columns]
shown = st.pills("Show", options, default=["uncontrolled", "solar_following", "curtailment_aware"],
                 selection_mode="multi", format_func=shared.friendly, key="plan_shown")
if shown:
    st.altair_chart(shared.style(shared.ev_chart(out["window"], out["loads"], shown)))
else:
    st.caption("Choose at least one option to draw.")

st.subheader("Side by side")
table = pd.DataFrame({
    "Option": [shared.friendly(s) for s in options],
    "What it means": [dd.EXPLAIN.get(s, "") for s in options],
    "Clean power used (MWh)": [float(m.loc[s, "absorbed_mwh"]) for s in options],
    "Evening peak added by EVs (MW)": [float(m.loc[s, "mean_evening_peak_increase_mw"]) for s in options],
    "Average extra wait (min)": [float(m.loc[s, "mean_delay_min"]) for s in options],
    "Cars fully charged": [float(m.loc[s, "completion_rate"]) * 100 for s in options],
})
st.dataframe(table, hide_index=True, alt="Comparison of charging options for the chosen day",
             column_config={
                 "What it means": st.column_config.TextColumn(width="large"),
                 "Clean power used (MWh)": st.column_config.NumberColumn(format="%.1f"),
                 "Evening peak added by EVs (MW)": st.column_config.NumberColumn(format="%.2f"),
                 "Average extra wait (min)": st.column_config.NumberColumn(format="%.0f"),
                 "Cars fully charged": st.column_config.ProgressColumn(format="%.2f%%", min_value=0,
                                                                       max_value=100),
             })
same = ("the same sessions as the saved full-year study" if out["experiment_sessions"]
        else "sessions drawn for this day only")
st.caption(f"{out['sessions']:,} charging sessions ({same}). Every plan is re-checked by an independent "
           "audit: no charger over its limit, no car charged while unplugged, every promised kWh delivered.")

with st.expander("Download the results", icon=":material/download:"):
    st.caption("Plain CSV and JSON in the format described in docs/export_schema.md. Times are the start "
               "of each interval, Sri Lanka time. The two larger files take a few seconds to prepare.")
    base, _ = shared.inputs()
    p = shared.pool(settings.sessions_per_day, settings.declared_departure_fraction)
    tag = f"{day_iso}_{settings.scenario_year}"
    held: dict = {}

    def _detail():                       # one solve shared by both large files
        if "d" not in held:
            held["d"] = dd.day_detail(pd.Timestamp(day_iso, tz=dd.TZ), base, shared.config(), settings, p)
        return held["d"]

    with st.container(horizontal=True):
        st.download_button("Day summary (JSON)",
                           dd.export_summary(day, settings, out, v, shared.config()),
                           file_name=f"day_summary_{tag}.json", mime="application/json",
                           icon=":material/description:", on_click="ignore")
        st.download_button("Hourly grid and EV load (CSV)", dd.export_hourly(out),
                           file_name=f"hourly_{tag}.csv", mime="text/csv", icon=":material/table:",
                           on_click="ignore")
        st.download_button("Every car's outcome (CSV)", lambda: dd.export_sessions(_detail()),
                           file_name=f"sessions_{tag}.csv", mime="text/csv", icon=":material/directions_car:",
                           on_click="ignore")
        st.download_button("Smart-charging schedule, every 15 min (CSV)",
                           lambda: dd.export_schedule(_detail()),
                           file_name=f"schedule_smart_{tag}.csv", mime="text/csv", icon=":material/schedule:",
                           on_click="ignore")

with st.expander("For experts: every metric", icon=":material/table_chart:"):
    st.dataframe(dd.metrics_for_display(m), alt="All metrics for the chosen day")
    st.page_link("app_pages/drivers.py", label="See how individual drivers are affected",
                 icon=":material/directions_car:")
