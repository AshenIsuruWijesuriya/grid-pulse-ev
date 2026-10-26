import streamlit as st

import shared

st.title("A driver's view", icon=":material/directions_car:")
st.caption("What does smart charging mean for one person? Follow a typical driver from three groups. "
           "The grey band is when the car is plugged in; red shading marks hours when clean power "
           "would be wasted.")

day_iso, settings = shared.picker("drivers")

with st.spinner("Planning the day and picking typical drivers..."):
    out = shared.solve(day_iso, settings)
    examples = shared.drivers(day_iso, settings) if not out["dropped"] else []

if not examples:
    st.warning("This day cannot be planned with these settings, so there are no drivers to show. "
               "Pick another day or change the Expert settings on the Plan a day page.", icon=":material/block:")
    st.stop()

for e in examples:
    with st.container(border=True):
        st.subheader(e["group"], icon=":material/person:")
        st.caption(f"{e['blurb']}. Plugs in at **{e['arrival']:%H:%M}**, leaves at **{e['departure']:%H:%M}**, "
                   f"needs **{e['energy_kwh']:.1f} kWh** on a {e['charger_kw']:g} kW charger.")
        left, right = st.columns([2, 1], vertical_alignment="center")
        with left:
            st.altair_chart(shared.style(shared.driver_chart(e, out["window"])))
        with right:
            for s in ("uncontrolled", "solar_following", "curtailment_aware"):
                done = e["finish"][s]
                spare = (e["departure"].value - done.value) / 60e9          # minutes
                st.markdown(f"**{shared.friendly(s)}:** full by {done:%H:%M}"
                            + (f", {spare / 60:.1f} h before leaving" if spare >= 60 else
                               f", {spare:.0f} min before leaving" if spare > 0 else ", just as they leave"))
            got = e["delivered_kwh"]["curtailment_aware"]
            st.badge(f"Leaves with {got:.1f} of {e['energy_kwh']:.1f} kWh", icon=":material/battery_full:",
                     color="green")
        st.caption(f"A typical driver in this group: the middle one of the {e['group_moved']:,} of "
                   f"{e['group_size']:,} drivers whose charging time smart charging moves.")

st.caption(shared.undeclared_note(settings))
