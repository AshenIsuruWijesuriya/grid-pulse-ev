import datetime as dt

import pandas as pd
import streamlit as st

import shared  # first: puts scripts/ on the import path
import dashboard_data as dd

st.title("Plan my charge", icon=":material/edit_calendar:")
st.caption("Tell us your trip. Your car is added to the day's other cars and everyone is planned together, "
           "so you see what smart charging would really do for you.")

day_iso, settings = shared.picker("mine")
s = st.session_state
s.setdefault("trip", {"arrive": dt.time(8, 0), "leave": dt.time(17, 0), "kwh": 20.0, "kw": 7.2})

with st.form("trip_form"):
    with st.container(horizontal=True, vertical_alignment="bottom"):
        arrive = st.time_input("I plug in at", s.trip["arrive"], step=900)
        leave = st.time_input("I need to leave at", s.trip["leave"], step=900,
                              help="Earlier than plugging in means the next morning (latest 09:00)")
        kwh = st.slider("Energy I need (kWh)", 5.0, 60.0, s.trip["kwh"], step=1.0,
                        help="About 6 km of driving per kWh (0.16 kWh/km)")
        kw = st.segmented_control("My charger", [3.6, 7.2, 11.0], default=s.trip["kw"],
                                  format_func=lambda v: f"{v:g} kW")
    if st.form_submit_button("Plan my charge", type="primary", icon=":material/bolt:"):
        s.trip = {"arrive": arrive, "leave": leave, "kwh": kwh, "kw": kw or s.trip["kw"]}

trip = s.trip
st.caption(f"{trip['kwh']:.0f} kWh is about {trip['kwh'] / 0.16:,.0f} km of driving.")
try:
    with st.spinner("Adding your car and planning the whole day..."):
        mc = shared.my_charge(day_iso, settings, trip["arrive"].strftime("%H:%M"),
                              trip["leave"].strftime("%H:%M"), float(trip["kwh"]), float(trip["kw"]))
except ValueError as err:
    st.error(f"{str(err).capitalize()}. Please change your times.", icon=":material/schedule:")
    st.stop()

if not mc["feasible"]:
    st.error("With these settings the chargers cannot give every driver their full energy in time, so this "
             "day is reported as not possible. Try another day or fewer cars.", icon=":material/block:")
    st.stop()

day = pd.Timestamp(day_iso, tz=dd.TZ)
per = mc["per_strategy"]
smart, unc, sunny = per["curtailment_aware"], per["uncontrolled"], per["solar_following"]
peak_label = shared.peak_hours_label(mc["grid"], day)
if mc["capped"]:
    st.warning(f"Your charger can deliver at most {mc['deliverable_kwh']:.1f} kWh while you are plugged in, "
               f"so the plan gives you {mc['energy_kwh']:.1f} kWh.", icon=":material/info:")


def full_by(r: dict) -> str:
    spare = (mc["departure"].value - r["finish"].value) / 60e9
    when = r["finish"].strftime("%H:%M") + (" (next day)" if r["finish"].normalize() > day else "")
    gap = f"{spare / 60:.1f} hours" if spare >= 60 else f"{spare:.0f} minutes"
    return f"{when}, {gap} before you leave" if spare > 0 else f"{when}, just as you leave"


with st.container(border=True):
    st.markdown(f":material/battery_full: With smart charging your car is full by **{full_by(smart)}**. "
                f"Charging on arrival it would be full by {full_by(unc)}.")
    if mc["risk_in_window"]:
        st.markdown(f":material/solar_power: **{smart['clean_share'] * 100:.0f}%** of your energy comes from "
                    "clean power that would otherwise be switched off "
                    f"({unc['clean_share'] * 100:.0f}% if you charge on arrival, "
                    f"{sunny['clean_share'] * 100:.0f}% if you charge when sunny).")
    else:
        st.markdown(":material/solar_power: No clean power is wasted while you are plugged in on this day, so "
                    "smart charging is about the evening peak instead.")
    st.markdown(f":material/trending_down: You add **{smart['peak_kwh']:.1f} kWh** to the grid's busiest "
                f"hours ({peak_label}), against {unc['peak_kwh']:.1f} kWh charging on arrival.")
    st.markdown(f":material/check_circle: You leave with **all {smart['delivered_kwh']:.1f} kWh** you asked for.")

with st.container(horizontal=True):
    st.metric("Full by (smart charging)", smart["finish"].strftime("%H:%M"),
              f"{smart['extra_wait_min']:.0f} min later than on arrival", delta_color="off", border=True)
    st.metric("From power that would be wasted", f"{smart['clean_share'] * 100:.0f}%",
              f"on arrival: {unc['clean_share'] * 100:.0f}%", delta_color="off", border=True)
    st.metric(f"Charged in the busiest hours ({peak_label})", f"{smart['peak_kwh']:.1f} kWh",
              f"on arrival: {unc['peak_kwh']:.1f} kWh", delta_color="off", border=True)
    st.metric("Energy you leave with", f"{smart['delivered_kwh']:.1f} kWh",
              f"of {mc['energy_kwh']:.1f} kWh requested", delta_color="off", border=True)

out = shared.solve(day_iso, settings)
example = {"power_kw": mc["power_kw"], "charger_kw": mc["charger_kw"], "arrival": mc["arrival"],
           "departure": mc["departure"]}
st.altair_chart(shared.style(shared.driver_chart(example, out["window"]).properties(
    title=shared._title("When your car charges",
                        "Grey: plugged in. Red shading: clean power would be wasted. "
                        "Lighter blocks: below full charger power."))))
st.caption(f"Your car joined station {mc['station_id']}, the least busy that day, which shares a "
           f"{shared.config()['central']['station_capacity_kw']:g} kW connection with its other cars. "
           "Nobody else is cut short to fit you in: if that were needed, the day would be reported as not possible.")
