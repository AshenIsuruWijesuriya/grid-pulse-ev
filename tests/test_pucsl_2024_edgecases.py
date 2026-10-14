"""Regressions for the two verified quirks in Jan/Feb 2024 PUCSL reports."""
from datetime import date
from pytest import raises
from scripts.preprocess_pucsl_dispatch import (
    parse_day, detect_sheet_date_order, excel_col
)


def sample_cells(day: date, stamped: date | None = None):
    src = ('Coal', 'Oil- CEB owned', 'Oil-IPP owned', 'Major Hydro',
           'Wind', 'Solar', 'Biomass', 'Mini Hydro')
    cells = {'A2': 'Time', 'A40': 'Total MW ****', 'A41': 'Time',
             'I1': ((stamped or day) - date(1899, 12, 30)).days,
             'CT40': 1920.0}
    for i, name in enumerate(src, start=42):
        cells[f'A{i}'] = name
        cells[f'CT{i}'] = 240.0
    for interval in range(96):
        column = excel_col(interval + 2)
        minute = ((interval + 1) * 15) % 1440
        cells[f'{column}2'] = f'{minute//60}:{minute%60:02d}'
        cells[f'{column}41'] = minute / 1440
        for row in range(42, 50):
            cells[f'{column}{row}'] = 10.0
        cells[f'{column}40'] = 80.0
    return cells


def test_2024_jan_mmdd_full_month_detected():
    sheets = [f'01.{day:02d}' for day in range(1, 32)]
    assert detect_sheet_date_order(sheets, 2024, 1) == 'mm.dd'


def test_2024_feb_leap_year_ddmm_trailing_spaces_detected():
    sheets = [f'{day:02d}.02  ' for day in range(1, 30)]
    assert detect_sheet_date_order(sheets, 2024, 2) == 'dd.mm'


def test_internal_date_mismatch_is_strict_by_default():
    cells = sample_cells(date(2024, 1, 20), date(2024, 1, 19))
    with raises(ValueError, match='Worksheet date mismatch'):
        parse_day(cells, '01.20', 2024, 1, 'test.xlsx',
                  sheet_date_order='mm.dd')


def test_only_explicitly_whitelisted_internal_date_mismatch_is_accepted():
    cells = sample_cells(date(2024, 1, 20), date(2024, 1, 19))
    rows, recon = parse_day(cells, '01.20', 2024, 1, 'test.xlsx',
                            sheet_date_order='mm.dd',
                            accepted_stamp_mismatch_dates=frozenset({date(2024,1,20)}))
    assert len(rows) == 96
    assert recon['sheet_internal_date_stamp'] == '2024-01-19'
    assert recon['date_stamp_mismatch_accepted'] is True
    with raises(ValueError, match='Worksheet date mismatch'):
        parse_day(cells, '01.20', 2024, 1, 'test.xlsx',
                  sheet_date_order='mm.dd',
                  accepted_stamp_mismatch_dates=frozenset({date(2024,1,21)}))


def test_six_mw_balance_gap_strict_by_default_but_can_be_reviewed():
    cells = sample_cells(date(2024,2,29))
    cells['B40'] = 74.0  # -6 MW reported minus category sum
    with raises(ValueError, match='total-source gap'):
        parse_day(cells, '29.02', 2024,2,'test.xlsx',
                  sheet_date_order='dd.mm')
    rows, _ = parse_day(cells, '29.02', 2024,2,'test.xlsx',
                        balance_tolerance_mw=6.1,
                        sheet_date_order='dd.mm')
    assert rows[0]['dispatch_balance_difference_mw'] == -6.0
