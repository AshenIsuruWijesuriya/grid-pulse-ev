"""Regression checks for reviewed March-May 2024 importer exceptions."""
from datetime import date
import pytest
from scripts.preprocess_pucsl_dispatch import parse_day, excel_col


def cells_for_day(day):
    names = ('Coal', 'Oil- CEB owned', 'Oil-IPP owned', 'Major Hydro',
             'Wind', 'Solar', 'Biomass', 'Mini Hydro')
    cells = {'A2': 'Time', 'A40': 'Total MW ****', 'A41': 'Time',
             'I1': (day - date(1899, 12, 30)).days, 'CT40': 1920.0}
    for j, label in enumerate(names, 42):
        cells[f'A{j}'] = label
        cells[f'CT{j}'] = 240.0
    for i in range(96):
        c = excel_col(i + 2)
        minute = (i + 1) * 15 % 1440
        cells[f'{c}2'] = f'{minute//60}:{minute%60:02d}'
        cells[f'{c}41'] = minute / 1440
        for row in range(42, 50):
            cells[f'{c}{row}'] = 10.0
        cells[f'{c}40'] = 80.0
    return cells


def test_large_balance_gap_requires_explicit_quarantine():
    d = date(2024, 3, 16)
    cells = cells_for_day(d)
    cells['B40'] = 300.0
    with pytest.raises(ValueError, match='total-source gap'):
        parse_day(cells, '16.03', 2024, 3, 'test.xlsx')
    rows, _ = parse_day(cells, '16.03', 2024, 3, 'test.xlsx',
                        quarantine_balance_exceptions=True)
    assert len(rows) == 96
    assert rows[0]['dispatch_balance_difference_mw'] == 220.0
    assert rows[0]['interval_quality_status'] == 'review_required'
    assert 'source_balance_exception' in rows[0]['interval_quality_flags']
    assert rows[1]['interval_quality_status'] == 'no_interval_flags'


def test_accepted_header_error_requires_correct_alternate_header():
    d = date(2024, 3, 30)
    cells = cells_for_day(d)
    cells['B41'] = '21:01'
    with pytest.raises(ValueError, match='Incorrect time header'):
        parse_day(cells, '30.03', 2024, 3, 'test.xlsx')
    records, recon = parse_day(cells, '30.03', 2024, 3, 'test.xlsx',
                        accepted_time_header_cells=frozenset({(d, 'ROW41')}))
    assert records[0]['interval_quality_status'] == 'review_required'
    assert records[0]['interval_quality_flags'] == 'accepted_time_header_mismatch'
    assert recon['accepted_time_header_cells'] == 'B41'
    cells['B2'] = '21:01'
    with pytest.raises(ValueError, match='No valid alternate time header'):
        parse_day(cells, '30.03', 2024, 3, 'test.xlsx',
                  accepted_time_header_cells=frozenset({(d, 'ROW41'), (d, 'B2')}))


def test_2024_may_date_stamp_mismatch_explicitly_reviewed():
    d = date(2024, 5, 13)
    cells = cells_for_day(d)
    cells['I1'] = (date(2024, 5, 12) - date(1899,12,30)).days
    with pytest.raises(ValueError, match='Worksheet date mismatch'):
        parse_day(cells, '13.05', 2024, 5, 'test.xlsx')
    records, recon = parse_day(cells, '13.05', 2024, 5, 'test.xlsx',
                        accepted_stamp_mismatch_dates=frozenset({d}))
    assert len(records) == 96
    assert all(r['interval_quality_status'] == 'review_required' for r in records)
    assert recon['sheet_internal_date_stamp'] == '2024-05-12'


def test_six_mw_over_baseline_still_flagged_with_higher_tolerance():
    d = date(2024,2,29)
    cells = cells_for_day(d)
    cells['B40'] = 74
    rows, _ = parse_day(cells,'29.02',2024,2,'test.xlsx',balance_tolerance_mw=6.1)
    assert rows[0]['interval_quality_flags'] == 'source_balance_exception'
