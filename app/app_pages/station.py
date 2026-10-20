import pandas as pd
import streamlit as st

import shared  # first: puts scripts/ on the import path
import dashboard_data as dd

st.title("Station view", icon=":material/electrical_services:")
st.caption("For charging operators: one station, the cars that use it during the day, and the limit of "
           "its shared grid connection.")

day_iso, settings = shared.picker("station")
with st.spinner("Planning the day..."):
    d = shared.detail(day_iso, settings)
    out = shared.solve(day_iso, settings)
if not d["feasible"]:
    st.warning("This day cannot be planned with these settings. Pick another day or change the settings on "
               "the Plan a day page.", icon=":material/block:")
    st.stop()

ids = dd.station_ids(d)
counts = d["prob"].sessions["station_id"].value_counts()
station = st.selectbox("Station (busiest first)", ids[:40], format_func=lambda k: f"Station {k} · {counts[k]} cars",
                       key="station_pick")
view = dd.station_view(d, station)
cars = view["cars"]

with st.container(horizontal=True):
    st.metric("Cars today", f"{len(cars)}", f"{int(cars['Said when leaving'].sum())} said when they leave",
              delta_color="off", border=True)
    st.metric("Highest station power, smart charging", f"{view['peak_kw']['curtailment_aware']:.1f} kW",
              f"limit {view['capacity_kw']:g} kW", delta_color="off", border=True)
    st.metric("Energy delivered", f"{cars['Energy (kWh)'].sum():,.1f} kWh", "every car full",
              delta_color="off", border=True)

st.altair_chart(shared.style(shared.station_power_chart(view, out["window"])))

rows = [{"label": car, "power": view["smart_kw"][car], "color": shared.rx.COLORS["curtailment_aware"],
         "max_kw": float(cars.loc[k, "Charger (kW)"]), "start": cars.loc[k, "Plugs in"],
         "end": cars.loc[k, "Leaves"]} for k, car in enumerate(cars["Car"])]
st.altair_chart(shared.style(shared.timeline_chart(rows, out["window"]).properties(
    title=shared._title("Each car under smart charging",
                        "Grey: plugged in. Green: charging. Red shading: clean power would be wasted."))))

table = cars.copy()
for col in table.columns:
    if col.startswith(("Plugs in", "Leaves", "Full by")):
        table[col] = pd.DatetimeIndex(table[col]).strftime("%H:%M")
st.dataframe(table, hide_index=True, alt=f"Cars at station {station}",
             column_config={"Energy (kWh)": st.column_config.NumberColumn(format="%.1f"),
                            "Charger (kW)": st.column_config.NumberColumn(format="%.1f")})
st.caption("Cars that do not say when they leave always charge on arrival; the scheduler plans the others "
           "around them. Stations are a model: sessions are spread across stations at random.")
