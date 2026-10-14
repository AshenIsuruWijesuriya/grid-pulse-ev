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


def test_sheet_order_detects_january_dd_mm():
    from scripts.preprocess_pucsl_dispatch import detect_sheet_date_order
    names = [f'{d:02d}.01' for d in range(1, 32)]
    assert detect_sheet_date_order(names, 2025, 1) == 'dd.mm'


def test_sheet_order_detects_february_mm_dd():
    from scripts.preprocess_pucsl_dispatch import detect_sheet_date_order
    names = [f'02.{d:02d}' for d in range(1, 29)]
    assert detect_sheet_date_order(names, 2025, 2) == 'mm.dd'


def test_february_first_day_parses_correctly():
    from scripts.preprocess_pucsl_dispatch import sheet_operating_date
    assert sheet_operating_date('02.01', 2025, 2, 'mm.dd') == date(2025, 2, 1)
    with pytest.raises(ValueError, match='requested month'):
        sheet_operating_date('02.01', 2025, 2, 'dd.mm')


def test_inconsistent_sheet_conventions_fail():
    from scripts.preprocess_pucsl_dispatch import detect_sheet_date_order
    names = [f'02.{d:02d}' for d in range(1, 29)]
    names[3] = '04.02'  # another convention mixed in
    with pytest.raises(ValueError, match='Cannot uniquely determine'):
        detect_sheet_date_order(names, 2025, 2)


def test_february_parse_day_with_date_stamp():
    from scripts.preprocess_pucsl_dispatch import parse_day
    cells = make_day()
    cells['I1'] = (date(2025, 2, 1) - date(1899, 12, 30)).days
    rows, reconciliation = parse_day(cells, '02.01', 2025, 2, 'fake.xlsx',
                                     sheet_date_order='mm.dd')
    assert len(rows) == 96
    assert rows[0]['operating_date'] == '2025-02-01'
    assert reconciliation['operating_date'] == '2025-02-01'


def test_mismatched_date_stamp_rejected():
    from scripts.preprocess_pucsl_dispatch import parse_day
    cells = make_day()  # I1 is January 1, not February 1
    with pytest.raises(ValueError, match='Worksheet date mismatch'):
        parse_day(cells, '02.01', 2025, 2, 'fake.xlsx',
                  sheet_date_order='mm.dd')


def test_march_full_date_name():
    from scripts.preprocess_pucsl_dispatch import sheet_operating_date
    assert sheet_operating_date('10-03-2025 ', 2025, 3, 'dd-mm-yyyy') == date(2025,3,10)
    with pytest.raises(ValueError, match='requested year'):
        sheet_operating_date('10-03-2024', 2025, 3, 'dd-mm-yyyy')


def test_march_partial_format_detection():
    from scripts.preprocess_pucsl_dispatch import detect_sheet_date_order
    names = [f'{d:02d}-03-2025' for d in range(1,19)]
    assert detect_sheet_date_order(names, 2025,3, allow_partial=True) == 'dd-mm-yyyy'
    with pytest.raises(ValueError, match='Cannot uniquely determine'):
        detect_sheet_date_order(names, 2025,3)


def test_march_parse_day_with_date_stamp():
    from scripts.preprocess_pucsl_dispatch import parse_day
    cells = make_day()
    cells['I1'] = (date(2025,3,1) - date(1899,12,30)).days
    rows, recon = parse_day(cells, '01-03-2025',2025,3,'fake.xlsx',
                            sheet_date_order='dd-mm-yyyy')
    assert len(rows) == 96
    assert recon['operating_date'] == '2025-03-01'


def test_mixed_march_date_formats_are_rejected():
    from scripts.preprocess_pucsl_dispatch import detect_sheet_date_order
    names = ['01-03-2025','03.02']
    with pytest.raises(ValueError, match='Cannot uniquely determine'):
        detect_sheet_date_order(names, 2025,3,allow_partial=True)
