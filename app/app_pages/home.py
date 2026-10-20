import streamlit as st

import shared

st.title("Charge EVs when the grid has clean power to spare", icon=":material/ev_station:")
st.markdown("A prototype for planning **public EV charging in Sri Lanka** so that cars use solar power "
            "that would otherwise be switched off, and stay off the evening peak, while every driver "
            "still leaves with a full battery.")

with st.container(horizontal=True):
    with st.container(border=True):
        st.subheader("The problem", icon=":material/wb_sunny:")
        st.markdown("At midday on weekends and holidays Sri Lanka has **more solar power than it can "
                    "use**. Coal and hydro plants cannot turn down below a safe minimum, so CEB has to "
                    "switch solar off. In the evening the opposite happens: demand peaks and expensive "
                    "plants run. Most EVs charge as soon as they plug in, which adds to that peak.")
    with st.container(border=True):
        st.subheader("The idea", icon=":material/lightbulb:")
        st.markdown("Drivers tell the charger when they will leave. The scheduler then decides **when** "
                    "each car charges: in the hours when clean power would be wasted, and away from the "
                    "evening peak. It is a guarantee, not a hope: every car gets **all** the energy it "
                    "asked for before it leaves.")
    with st.container(border=True):
        st.subheader("Who it is for", icon=":material/groups:")
        st.markdown("- **CEB planners:** how much charging flexibility is worth to the grid\n"
                    "- **Charging operators:** when to run their chargers\n"
                    "- **Policy makers (PUCSL, SLSEA):** evidence for time-of-day charging tariffs")

r = shared.saved()
risk = r["risk_days"].set_index("strategy") if not r["risk_days"].empty else None
st.subheader("What the full-year study found", icon=":material/insights:")
if risk is None:
    st.caption("Run scripts/run_experiments.py to fill in these results.")
else:
    smart, unc, sunny = (risk.loc[s] for s in ("curtailment_aware", "uncontrolled", "solar_following"))
    summ = r["summary"]
    lead = summ.get("so6_curtailment_aware_not_outranked_share")
    with st.container(horizontal=True):
        st.metric("Evening peak added by EVs, removed", f"{smart['evening_peak_reduction_pct_of_ev_increase']:.0f}%",
                  f"charging when sunny: {sunny['evening_peak_reduction_pct_of_ev_increase']:.0f}%",
                  delta_color="off", border=True)
        st.metric("Extra wait per driver, on average", f"{smart['mean_delay_min']:.0f} min",
                  f"charging when sunny: {sunny['mean_delay_min']:.0f} min", delta_color="off", border=True)
        st.metric("Clean power put into cars", f"{smart['absorbed_mwh']:,.0f} MWh",
                  f"+{smart['absorbed_mwh'] - unc['absorbed_mwh']:,.0f} MWh vs charging on arrival", border=True)
        st.metric("Cars that leave fully charged", f"{smart['completion_rate'] * 100:.0f}%",
                  "a hard guarantee", delta_color="off", border=True)
    st.caption(f"The {len(summ.get('risk_days', []))} days of 2024 that would waste clean power in "
               f"scenario year {summ.get('scenario', {}).get('scenario_year', 2028)}, "
               f"{int(smart['sessions']):,} charging sessions."
               + (f" Smart charging is never beaten in {lead * 100:.0f}% of the 16 tested settings."
                  if lead is not None else ""))

st.subheader("Try it", icon=":material/play_circle:")
with st.container(horizontal=True):
    st.page_link("app_pages/plan_a_day.py", label="Plan a real day", icon=":material/calendar_month:")
    st.page_link("app_pages/drivers.py", label="Follow a driver", icon=":material/directions_car:")
    st.page_link("app_pages/evidence.py", label="See the evidence", icon=":material/fact_check:")
    st.page_link("app_pages/how_it_works.py", label="How it works", icon=":material/menu_book:")
