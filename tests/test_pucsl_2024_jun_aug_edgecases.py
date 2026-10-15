"""Regression tests for the explicitly reviewed PUCSL June-August 2024 exceptions."""
from datetime import date
import pytest
from scripts.preprocess_pucsl_dispatch import (
    audited_july_2024_date_orders,
    audited_june_2024_time_cells,
    parse_day,
    excel_col,
)


def correct_july_names():
    return [f'{d:02d}.07' for d in range(1, 8)] + [f'07.{d:02d}' for d in range(8, 32)]


def test_july_mixed_names_are_explicit_and_cover_31_distinct_dates():
    result = audited_july_2024_date_orders(correct_july_names(), 2024, 7)
    assert len(result) == 31
    assert result['01.07'] == 'dd.mm'
    assert result['07.07'] == 'dd.mm'
    assert result['07.08'] == 'mm.dd'
    assert result['07.31'] == 'mm.dd'


def test_july_missing_or_unexpected_sheet_names_rejected():
    with pytest.raises(ValueError, match='differ from audited pattern'):
        audited_july_2024_date_orders(correct_july_names()[:-1], 2024, 7)
    changed = correct_july_names()
    changed[-1] = '31.07'
    with pytest.raises(ValueError, match='differ from audited pattern'):
        audited_july_2024_date_orders(changed, 2024, 7)


def test_july_approval_cannot_apply_to_other_months():
    with pytest.raises(ValueError, match='only for 2024-07'):
        audited_july_2024_date_orders(correct_july_names(), 2025, 7)


def test_june_whitelist_is_exactly_272_secondary_header_cells():
    allowed = audited_june_2024_time_cells(2024, 6)
    assert len(allowed) == 272
    assert (date(2024, 6, 14), 'AY41') in allowed
    assert (date(2024, 6, 29), 'BO41') in allowed
    assert (date(2024, 6, 14), 'AY2') not in allowed
    assert (date(2024, 6, 30), 'AY41') not in allowed
    assert (date(2024, 6, 14), 'BX41') not in allowed
    with pytest.raises(ValueError, match='only for 2024-06'):
        audited_june_2024_time_cells(2024, 5)


def june_day_example():
    d = date(2024, 6, 14)
    cells = {'A2': 'Time', 'A40': 'Total MW', 'A41': 'Time',
             'I1': (d - date(1899, 12, 30)).days, 'CT40': 1920}
    for row, label in enumerate(('Coal', 'Oil- CEB owned', 'Oil-IPP owned',
                                 'Major Hydro', 'Wind', 'Solar', 'Biomass', 'Mini Hydro'), 42):
        cells[f'A{row}'] = label
        cells[f'CT{row}'] = 240
    for i in range(96):
        col = excel_col(i+2)
        minute = (i+1)*15%1440
        cells[f'{col}2'] = f'{minute//60}:{minute%60:02d}'
        cells[f'{col}41'] = f'{minute//60}:{minute%60:02d}'
        for row in range(42,50):
            cells[f'{col}{row}'] = 10
        cells[f'{col}40'] = 80
    cells['AY41'] = '12:16'
    return cells


def test_june_requires_exact_approval_and_correct_primary_header():
    cells = june_day_example()
    with pytest.raises(ValueError, match='Incorrect time header'):
        parse_day(cells, '14.06', 2024, 6, 'sample.xlsx')
    records, recon = parse_day(cells, '14.06', 2024, 6, 'sample.xlsx',
                               accepted_time_header_cells=audited_june_2024_time_cells(2024, 6))
    assert len(records) == 96
    assert recon['accepted_time_header_cells'] == 'AY41'
    assert 'accepted_time_header_mismatch' in records[49]['interval_quality_flags']
    cells['AY2'] = '12:16'
    with pytest.raises(ValueError, match='Incorrect time header AY2'):
        parse_day(cells, '14.06', 2024, 6, 'sample.xlsx',
                  accepted_time_header_cells=audited_june_2024_time_cells(2024, 6))
