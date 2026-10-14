"""Unit tests for workbook-independent PUCSL daily sheet parsing."""
from datetime import date

import pytest

from scripts.preprocess_pucsl_dispatch import _time_minute, excel_col, parse_day


def make_day(day='01.01'):
    # Matching the Jan-2025 workbook layout, but using entirely synthetic values.
    import datetime
    cells = {'A2': 'Time', 'A40': 'Total MW ****', 'A41': 'Time',
             'I1': (date(2025,1,1) - date(1899,12,30)).days}
    labels = ['Coal','Oil- CEB owned','Oil-IPP owned','Major Hydro',
              'Wind','Solar','Biomass','Mini Hydro']
    for row, name in enumerate(labels, start=42):
        cells[f'A{row}'] = name
        cells[f'CT{row}'] = 96 * 10 * 0.25
    cells['CT40'] = 96 * 80 * 0.25
    for slot in range(96):
        col = excel_col(slot+2)
        minute = ((slot+1)*15)%1440
        cells[f'{col}2'] = f'{minute//60}:{minute%60:02d}'
        cells[f'{col}41'] = minute/1440
        for row in range(42,50):
            cells[f'{col}{row}'] = 10
        cells[f'{col}40'] = 80
    return cells


def test_normal_day():
    rows, reconciliation = parse_day(make_day(), '01.01', 2025,1,'fake.xlsx')
    assert len(rows) == 96
    assert rows[0]['interval_start_local'] == '2025-01-01T00:00:00+05:30'
    assert rows[-1]['interval_end_local'] == '2025-01-02T00:00:00+05:30'
    assert rows[0]['source_time_label'] == '0:15'
    assert rows[-1]['source_time_label'] == '0:00'
    assert reconciliation['daily_total_energy_difference_mwh'] == 0


def test_excel_time_header():
    assert _time_minute(0.5) == 720
    assert _time_minute('0:00') == 0
    assert excel_col(97) == 'CS'


def test_invalid_time_detected():
    cells = make_day()
    cells['B2'] = '0:30'
    with pytest.raises(ValueError, match='Incorrect time header'):
        parse_day(cells, '01.01', 2025, 1, 'fake.xlsx')


def test_missing_source_detected():
    cells = make_day()
    cells.pop('A47')
    with pytest.raises(ValueError, match='Missing categories'):
        parse_day(cells, '01.01', 2025, 1, 'fake.xlsx')


def test_unbalanced_total_detected():
    cells = make_day()
    cells['B40'] = 100
    with pytest.raises(ValueError, match='total-source gap'):
        parse_day(cells, '01.01', 2025, 1, 'fake.xlsx')


def test_missing_interval_source_value():
    cells = make_day()
    cells.pop('B47')
    with pytest.raises(ValueError, match='Missing numeric'):
        parse_day(cells, '01.01', 2025, 1, 'fake.xlsx')


def test_negative_source_preserved_for_audit():
    cells = make_day()
    cells['B44'] = -6
    cells['B40'] = 64
    rows, _ = parse_day(cells, '01.01', 2025, 1, 'fake.xlsx')
    assert rows[0]['oil_ipp_mw'] == -6
