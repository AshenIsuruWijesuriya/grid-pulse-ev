import pandas as pd
import streamlit as st

import shared
import dashboard_data as dd

# ---- Hero ------------------------------------------------------------------------
st.badge("Research prototype  ·  Grid Pulse EV  ·  Component 3", icon=":material/science:", color="green")
st.title("Charge EVs when the grid has clean power to spare", anchor=False)
st.markdown("Plan public EV charging in Sri Lanka so that cars use **solar power that would otherwise be "
            "switched off**, stay away from the evening peak, and every driver still leaves with a "
            "**full battery**.")
with st.container(horizontal=True):
    if st.button("Plan a real day", type="primary", icon=":material/calendar_month:"):
        st.switch_page("app_pages/plan_a_day.py")
    if st.button("Plan my own charge", icon=":material/edit_calendar:"):
        st.switch_page("app_pages/my_charge.py")
    if st.button("How it works", type="tertiary", icon=":material/menu_book:"):
        st.switch_page("app_pages/how_it_works.py")

st.space("medium")

# ---- The idea in three steps --------------------------------------------------------
st.subheader("In 30 seconds", anchor=False)
steps = [
    (":material/wb_sunny:", "1  ·  Too much sun at midday",
     "On weekends and holidays coal and hydro cannot turn down any further, so solar is switched off."),
    (":material/schedule:", "2  ·  Charge at the right time",
     "Each car charges in those hours and stays away from the evening peak, when demand is highest."),
    (":material/battery_full:", "3  ·  Every car leaves full",
     "Drivers say when they will leave. The plan always delivers all the energy they asked for."),
]
cols = st.columns(3)
for col, (icon, head, body) in zip(cols, steps):
    with col.container(border=True, height="stretch"):
        st.markdown(f"#### {icon} {head}")
        st.markdown(body)

st.space("medium")

# ---- Results -------------------------------------------------------------------------
r = shared.saved()
risk = r["risk_days"].set_index("strategy") if not r["risk_days"].empty else None
st.subheader("What smart charging achieves", anchor=False)
if risk is None:
    st.caption("Run scripts/run_experiments.py to fill in these results.")
else:
    smart, unc, sunny = (risk.loc[s] for s in ("curtailment_aware", "uncontrolled", "solar_following"))
    summ = r["summary"]
    lead = summ.get("so6_curtailment_aware_not_outranked_share")
    n_days = len(summ.get("risk_days", []))
    year = summ.get("scenario", {}).get("scenario_year", 2028)
    per_day = int(smart["sessions"]) // max(n_days, 1)
    st.markdown(f"We replayed the **{n_days} days that would waste the most solar** on the {year} grid, with "
                f"about **{per_day:,} cars charging each day**. Every number below compares smart charging "
                "with **what happens today: each car starts charging the moment it plugs in.**")
    gain = smart["absorbed_mwh"] - unc["absorbed_mwh"]
    mwh_per_car = smart["ev_energy_mwh"] / smart["sessions"]
    cards = [
        (":material/trending_down:", "Evening peak",
         f"{smart['evening_peak_reduction_pct_of_ev_increase']:.0f}% lower",
         "Of the extra load EVs put on the grid in the evening peak hours, this share moves to other times."),
        (":material/solar_power:", "More solar into cars", f"+{gain:,.0f} MWh",
         f"Solar that would have been switched off now charges cars: enough for about "
         f"**{round(gain / mwh_per_car, -2):,.0f} full charges**. "
         f"(Today {unc['absorbed_mwh']:,.0f} MWh, smart charging {smart['absorbed_mwh']:,.0f} MWh.)"),
        (":material/schedule:", "Battery full a little later", f"{smart['mean_delay_min']:.0f} min later",
         "On average, compared with charging at full power from the moment the car plugs in. "
         "The car is parked anyway: drivers still leave at their own time, fully charged."),
        (":material/battery_full:", "Cars full when they leave", f"{smart['completion_rate'] * 100:.0f}%",
         f"Guaranteed by every plan. Today {unc['completion_rate'] * 100:.2f}%: a few cars "
         "leave before they are full."),
    ]
    cols = st.columns(4)
    for col, (icon, label, value, note) in zip(cols, cards):
        with col.container(border=True, height="stretch"):
            st.metric(label, value, icon=icon)
            st.caption(note)
    with st.expander("How does it compare with the other ways of charging?", icon=":material/compare_arrows:"):
        st.markdown(f"**Charge when sunny** (a simple rule: charge during daylight) also uses solar, but only "
                    f"cuts the evening peak by {sunny['evening_peak_reduction_pct_of_ev_increase']:.0f}% and "
                    f"fills batteries {sunny['mean_delay_min']:.0f} min later. Smart charging plans around the "
                    "grid's actual surplus, so it does better on both.")
        st.dataframe(pd.DataFrame([{
            "Way of charging": dd.FRIENDLY[s], "Evening peak lower": f"{x['evening_peak_reduction_pct_of_ev_increase']:.0f}%",
            "Solar used (MWh)": f"{x['absorbed_mwh']:,.0f}", "Battery full later by": f"{x['mean_delay_min']:.0f} min",
            "Cars full": f"{x['completion_rate'] * 100:.2f}%"}
            for s, x in (("uncontrolled", unc), ("solar_following", sunny), ("curtailment_aware", smart))]),
            hide_index=True)
        if lead is not None:
            st.caption(f"Smart charging was never beaten in {round(lead * 16)} of 16 tested settings. "
                       "Full details on the Evidence page.")

st.space("medium")

# ---- Why now ---------------------------------------------------------------------------
yrs = r["scenario_years"]
if not yrs.empty:
    left, right = st.columns([2, 3], vertical_alignment="center")
    with left:
        st.subheader("Why now", anchor=False)
        st.markdown("Today the grid still uses almost all of its solar. As CEB builds the solar and wind "
                    "in its own expansion plan, the midday surplus grows faster than demand.")
        g = yrs.set_index("scenario_year")["risk_energy_gwh"]
        st.markdown(f"- **2026:** {g.get(2026, 0):.1f} GWh would be wasted\n"
                    f"- **2028:** {g.get(2028, 0):.0f} GWh\n"
                    f"- **2030:** {g.get(2030, 0):.0f} GWh")
        st.caption("Planning when EVs charge is worth building before the problem arrives, not after.")
    with right:
        st.altair_chart(shared.style(shared.years_chart(yrs).properties(height=260)))

st.space("medium")

# ---- Explore ------------------------------------------------------------------------------
st.subheader("Explore the prototype", anchor=False)
pages = [
    ("app_pages/plan_a_day.py", ":material/calendar_month:", "Plan a day",
     "Pick a real day and watch the scheduler plan 10,000 cars."),
    ("app_pages/my_charge.py", ":material/edit_calendar:", "Plan my charge",
     "Enter your own trip and see when your car would charge."),
    ("app_pages/drivers.py", ":material/directions_car:", "A driver's view",
     "Follow three typical drivers through the day."),
    ("app_pages/station.py", ":material/electrical_services:", "Station view",
     "One station's cars and its 50 kW connection limit."),
    ("app_pages/evidence.py", ":material/fact_check:", "Evidence",
     "The full-year results, tested and explained."),
    ("app_pages/how_it_works.py", ":material/menu_book:", "How it works",
     "The method, the data sources and every assumption."),
]
for row in (pages[:3], pages[3:]):
    cols = st.columns(3)
    for col, (path, icon, title, body) in zip(cols, row):
        with col.container(border=True, height="stretch"):
            st.markdown(f"#### {icon} {title}")
            st.caption(body)
            st.page_link(path, label="Open", icon=":material/arrow_forward:", icon_position="right")

st.space("medium")

# ---- Audience -------------------------------------------------------------------------------
st.subheader("Who it is for", anchor=False)
cols = st.columns(3)
for col, (icon, who, why) in zip(cols, [
        (":material/bolt:", "CEB planners", "How much charging flexibility is worth to the grid"),
        (":material/ev_station:", "Charging operators", "When to run their chargers, within the station limit"),
        (":material/policy:", "Policy makers (PUCSL, SLSEA)", "Evidence for time-of-day charging tariffs")]):
    with col.container(border=True, height="stretch"):
        st.markdown(f"**{icon} {who}**")
        st.caption(why)
