"""Grid Pulse EV, Component 3: smart public EV charging prototype (WBS 5.11, FR13).

Run from the project root:
    .venv\\Scripts\\streamlit.exe run app\\streamlit_app.py

Pages (app_pages/)
    Start here        the problem, the idea and the headline result in plain words
    Plan a day        pick a real day; the scheduler plans every car live
    A driver's view   three typical drivers: when their car charges, and that it leaves full
    Evidence          the saved full-year results explained
    How it works      method, assumptions with sources, glossary, limits
"""

import streamlit as st

import shared

st.set_page_config(page_title="Grid Pulse EV: smart charging", page_icon=":material/ev_station:",
                   layout="wide")
shared.init_state()

page = st.navigation(
    [
        st.Page("app_pages/home.py", title="Start here", icon=":material/home:", default=True),
        st.Page("app_pages/plan_a_day.py", title="Plan a day", icon=":material/calendar_month:"),
        st.Page("app_pages/drivers.py", title="A driver's view", icon=":material/directions_car:"),
        st.Page("app_pages/evidence.py", title="Evidence", icon=":material/fact_check:"),
        st.Page("app_pages/how_it_works.py", title="How it works", icon=":material/menu_book:"),
    ],
    position="top",
)
page.run()
st.caption(shared.DISCLAIMER)
