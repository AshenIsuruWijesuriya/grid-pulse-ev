"""Tests of PUCSL consolidation; generated fixtures are synthetic, not real grid data."""
from __future__ import annotations

import calendar
import csv
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts.consolidate_pucsl_dispatch import (
    SOURCE_COLS, REQUIRED_COLS, _check_coverage, consolidate,
)

TZ = timezone(timedelta(hours=5, minutes=30))


def write_csv(path, rows, cols):
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def make_month(tmp_path, year=2025, month=2, available_days=None, negative=False):
    days_in_month = calendar.monthrange(year, month)[1]
    available_days = available_days or list(range(1, days_in_month+1))
    available = [date(year, month, d).isoformat() for d in available_days]
    missing = [date(year, month, d).isoformat() for d in range(1, days_in_month+1)
               if d not in available_days]
    partial = bool(missing)
    stem = f'pucsl_{year}_{month:02d}_'
    variant = 'partial_' if partial else ''
    coverage = dict(year=year,month=month,expected_days=days_in_month,
                    available_days=len(available),intervals=len(available)*96,
                    status='incomplete' if partial else 'complete',
                    resolution_minutes=15,available_dates=available,missing_dates=missing)
    (tmp_path / f'{stem}coverage.json').write_text(json.dumps(coverage),encoding='utf-8')
    intervals, recon, flags = [], [], []
    for day in available:
        for i in range(96):
            start = datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=TZ) + timedelta(minutes=15*i)
            end = start + timedelta(minutes=15)
            values = {c: 10.0 for c in SOURCE_COLS}
            if negative and i == 0 and day == available[0]:
                values['oil_ceb_mw'] = -1.0
                flags.append({'operating_date': day,'interval_end_local': end.isoformat(),
                              'source':'oil_ceb_mw','value_mw':-1,'issue':'negative'})
            total = sum(values.values())
            intervals.append(dict(source_filename='synthetic.xlsx', source_sheet=day,
                                  operating_date=day,interval_number=i+1,
                                  interval_start_local=start.isoformat(),
                                  interval_end_local=end.isoformat(),source_time_label='0:15',
                                  **values,total_dispatch_reported_mw=total,
                                  sum_generation_by_source_mw=total,dispatch_balance_difference_mw=0))
        daily_total = 96*80*.25 - (11*.25 if negative and day == available[0] else 0)
        recon.append(dict(operating_date=day,integrated_total_energy_mwh=daily_total,
                          reported_daily_total_energy_mwh=daily_total+10,
                          daily_total_energy_difference_mwh=10))
    write_csv(tmp_path / f'{stem}{variant}dispatch_15min.csv', intervals, list(REQUIRED_COLS))
    write_csv(tmp_path / f'{stem}{variant}reconciliation.csv', recon,
              ['operating_date','integrated_total_energy_mwh',
               'reported_daily_total_energy_mwh','daily_total_energy_difference_mwh'])
    write_csv(tmp_path / f'{stem}{variant}negative_generation_flags.csv', flags,
              ['operating_date','interval_end_local','source','value_mw','issue'])
    return tmp_path / f'{stem}{variant}dispatch_15min.csv'


def test_complete_month_accepts_96_intervals_per_day(tmp_path):
    make_month(tmp_path, negative=True)
    rows, days, info = consolidate(tmp_path, 2025, [2], include_partial=False)
    assert len(rows) == 2688
    assert len(days) == 28
    assert info['months'][0]['negative_generation_observations'] == 1
    assert days[0]['integrated_dispatch_mwh'] == 1917.25
    assert info['months'][0]['eligible_for_full_month_statistics'] is True


def test_partial_month_excluded_by_default(tmp_path):
    make_month(tmp_path, month=3, available_days=[1, 2])
    make_month(tmp_path, month=2)
    rows, days, info = consolidate(tmp_path, 2025, [2, 3], include_partial=False)
    assert len(rows) == 2688
    assert len(days) == 28
    march = info['months'][1]
    assert march['coverage_status'] == 'partial'
    assert march['included_in_interval_export'] is False
    assert len(march['missing_dates']) == 29


def test_partial_month_included_only_when_opted_in(tmp_path):
    make_month(tmp_path, month=3, available_days=[1, 2])
    rows, days, info = consolidate(tmp_path, 2025, [3], include_partial=True)
    assert len(rows) == 192
    assert len(days) == 2
    assert all(r['month_coverage_status'] == 'partial' for r in rows)
    assert all(r['eligible_for_full_month_statistics'] == 'false' for r in rows)


def test_missing_day_never_inserted_as_zero(tmp_path):
    make_month(tmp_path, month=3, available_days=[1, 3])
    _, days, _ = consolidate(tmp_path, 2025, [3], include_partial=True)
    assert [r['operating_date'] for r in days] == ['2025-03-01', '2025-03-03']


def test_repeated_csv_interval_rejected(tmp_path):
    path = make_month(tmp_path)
    with path.open(newline='',encoding='utf-8') as f:
        reader=csv.DictReader(f); rows=list(reader); cols=reader.fieldnames
    rows[2]['interval_start_local'] = rows[1]['interval_start_local']
    rows[2]['interval_end_local'] = rows[1]['interval_end_local']
    write_csv(path, rows, cols)
    with pytest.raises(ValueError, match='Duplicate interval'):
        consolidate(tmp_path, 2025, [2], False)


def test_coverage_status_mismatch_rejected():
    info=dict(year=2025,month=3,resolution_minutes=15,
              available_dates=['2025-03-01'],
              missing_dates=['2025-03-02'],expected_days=31,available_days=1,
              intervals=96,status='complete')
    with pytest.raises(ValueError,match='partition'):
        _check_coverage(info,2025,3)


def test_reconciliation_mismatch_rejected(tmp_path):
    make_month(tmp_path)
    path=tmp_path/'pucsl_2025_02_reconciliation.csv'
    with path.open(newline='',encoding='utf-8') as f:
        reader=csv.DictReader(f); rows=list(reader);cols=reader.fieldnames
    rows[0]['integrated_total_energy_mwh'] = 999
    write_csv(path,rows,cols)
    with pytest.raises(ValueError,match='Integrated daily dispatch'):
        consolidate(tmp_path,2025,[2],False)
