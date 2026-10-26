import pandas as pd
import streamlit as st

import shared  # first: puts scripts/ on the import path
import dashboard_data as dd

st.title("How it works", icon=":material/menu_book:")

steps = [
    (":material/database:", "1. Real grid data",
     "Every 15 minutes of 2024 from PUCSL's published dispatch reports, checked against CEB's official "
     "yearly totals (within 0.1%). Solar is rebuilt from NASA satellite sunshine data, because the meters "
     "only record solar after any of it was switched off."),
    (":material/warning:", "2. Find the wasted hours",
     "Coal and hydro plants cannot go below a safe minimum (520 MW, from CEB's expansion plan), and solar "
     "plus wind may supply at most 75% of demand to keep the grid stable. Any clean power beyond those "
     "limits would be switched off."),
    (":material/calculate:", "3. Plan every car",
     "A mathematical optimiser (linear programming) decides how much power each car gets every 15 minutes. "
     "It must respect arrival and leaving times, charger and station limits, and deliver every kWh asked "
     "for. Within those rules it uses as much wasted power as possible and keeps off the evening peak."),
    (":material/query_stats:", "4. Plan without knowing tomorrow",
     "In real life the plan is made at midnight. Demand is forecast with machine learning, solar with the "
     "ECMWF weather forecast published before midnight. The plan can also be made for 20 versions of "
     "tomorrow, built from the forecast's past errors, so it still works when the forecast is wrong."),
    (":material/rule:", "5. Check and compare",
     "An independent audit re-checks every plan. The same cars and the same day are run with each option, "
     "so any difference comes from the option alone."),
]
with st.container(horizontal=True):
    for icon, head, body in steps:
        with st.container(border=True):
            st.markdown(f"#### {icon} {head}")
            st.markdown(body)

st.subheader("What is real, what is modelled, what is assumed")
st.dataframe(pd.DataFrame([
    ("Grid demand and generation, 2024", "Real", "PUCSL 15-minute reports, reconciled to the CEB Data Book"),
    ("Solar available before switch-off", "Modelled", "NASA POWER sunshine, scaled to CEB monthly solar energy"),
    ("Future years 2026-2030", "Scaled", "2024 data × CEB Long Term Generation Expansion Plan 2025-2044"),
    ("Safe minimum (520 MW) and inertia limit (75%)", "Derived from cited values",
     "CEB expansion plan, checked against 2024 operation (never went below 520 MW)"),
    ("Day-ahead forecasts", "Modelled",
     "Trained on January-September 2024 and tested on October-December; solar uses archived ECMWF "
     "weather forecasts (Open-Meteo)"),
    ("Charging sessions", "Placeholder", "Random but realistic-range sessions; to be replaced by real data"),
    ("Number of cars per day", "Placeholder", "10,000 by default; to come from the fleet component"),
], columns=["What", "Kind", "Source"]), hide_index=True, alt="Data sources and their status")

st.subheader("Every setting and its source")
st.dataframe(dd.assumptions(shared.config()), hide_index=True, alt="Model parameters with sources")

st.subheader("Words used in this app")
st.table(pd.DataFrame([
    ("Wasted clean power (curtailment)", "Solar or wind that was available but had to be switched off"),
    ("Evening peak", "The highest demand of the day, between 17:00 and midnight"),
    ("Safe minimum (NLmin)", "The lowest output coal and large hydro plants can run at safely"),
    ("Inertia limit (SNSP)", "The largest share of demand that solar and wind may supply at once"),
    ("Charge on arrival", "Today's behaviour: start charging as soon as the car is plugged in"),
    ("Charge when sunny", "Today's advice: charge in the sunniest hours"),
    ("Smart charging", "This research: charge when clean power would be wasted, avoid the evening peak, "
                       "always deliver the full energy"),
    ("Extra wait", "How much later a car finishes charging than if it had charged at full power on arrival"),
    ("Forecast", "What the grid was expected to do, as known at midnight before the day"),
    ("Planning for forecast errors", "One plan made for 20 versions of tomorrow: the forecast plus the errors "
                                     "it made on similar past days"),
    ("Station rule", "Who charges first when a station's shared connection is full: every car as it arrives "
                     "(default), or drivers who said when they leave"),
], columns=["Term", "Meaning"]).set_index("Term"))

st.subheader("Limits of this prototype")
st.markdown("- Wasted power is **inferred** from published limits; Sri Lanka publishes no record of it.\n"
            "- Charging sessions and the number of cars are **placeholders** until real data arrives.\n"
            "- Future years reuse **2024 weather**, and planned batteries are not included.\n"
            "- The weather-forecast archive starts in **March 2024**; earlier days use yesterday's solar.\n"
            "- The safe minimum still needs **review by CEB System Control**.")
