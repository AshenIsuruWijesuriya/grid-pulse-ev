"""Headless run of every prototype page (Streamlit AppTest).

Needs the processed 2024 grid data and saved results, which are not in git;
the test is skipped when they are missing.
"""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app" / "streamlit_app.py")
NEEDED = [ROOT / "data/processed/grid/grid_base_2024_hourly.csv",
          ROOT / "results/experiments/run_summary.json"]
pytestmark = pytest.mark.skipif(not all(p.is_file() for p in NEEDED),
                                reason="processed grid data or saved results not present")
PAGES = ["app_pages/home.py", "app_pages/plan_a_day.py", "app_pages/my_charge.py", "app_pages/drivers.py",
         "app_pages/station.py", "app_pages/evidence.py", "app_pages/how_it_works.py"]


@pytest.fixture(scope="module")
def app():
    at = AppTest.from_file(APP, default_timeout=600)
    at.session_state["fleet"] = 1000            # keep the live solves quick
    return at.run()


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_errors(app, page):
    app.switch_page(page).run()
    assert not app.exception, [e.value for e in app.exception]
    assert len(app.title) == 1


def test_plan_a_day_writes_a_verdict_and_metrics(app):
    app.switch_page("app_pages/plan_a_day.py").run()
    text = " ".join(m.value for m in app.markdown)
    assert "Sunday 3 November" in text and "fully charged" in text
    assert [m.label for m in app.metric][:2] == ["Clean power that would be wasted",
                                                "Put into cars by smart charging"]


def test_plan_my_charge_reports_a_full_battery(app):
    app.switch_page("app_pages/my_charge.py").run()
    text = " ".join(m.value for m in app.markdown)
    assert "You leave with **all 20.0 kWh**" in text
    assert app.metric[0].label == "Full by (smart charging)"
