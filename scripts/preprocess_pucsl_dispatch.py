"""Convert PUCSL's 15-minute daily-dispatch XLSX workbook to audited CSV files.

Designed and initially verified against January 2025 '15-Minutes-Report-Jan-2025-31.xlsx'.
Uses Python's standard-library XML/ZIP reader to preserve source cell values,
including formula cached values. It never modifies the original workbook.

Example from repository root:
    python scripts/preprocess_pucsl_dispatch.py \
        --input data/raw/grid/pucsl/15-Minutes-Report-Jan-2025-31.xlsx \
        --year 2025 --month 1

IMPORTANT:
* The 0:15..0:00 labels are provisionally treated as interval-END labels;
  0:00 is the following day's midnight.
* Reported daily MWh values can differ substantially from quarter-hour MW
  integration. These differences are preserved, never silently 'fixed'.
* Total reported dispatch is NOT independently verified national demand.
* The 'Solar' row should not be assumed to cover all Sri Lankan PV generation.
"""
from __future__ import annotations

import argparse
import calendar
import csv
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from pathlib import Path
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile

MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
DOCREL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKGREL = 'http://schemas.openxmlformats.org/package/2006/relationships'
NS = {'x': MAIN}
LOCAL_TZ = timezone(timedelta(hours=5, minutes=30))
INTERVALS_PER_DAY = 96
DURATION_H = 0.25

# This is the source's bottom generation-by-type table, not the individual
# plant-level rows in the top part of each worksheet.
SOURCE_LABELS = {
    'Coal': 'coal_mw',
    'Oil- CEB owned': 'oil_ceb_mw',
    'Oil-IPP owned': 'oil_ipp_mw',
    'Major Hydro': 'major_hydro_mw',
    'Wind': 'wind_mw',
    'Solar': 'solar_mw',
    'Biomass': 'biomass_mw',
    'Mini Hydro': 'mini_hydro_mw',
}
SOURCES = tuple(SOURCE_LABELS.values())
FIELDNAMES = (
    'source_filename', 'source_sheet', 'operating_date', 'interval_number',
    'interval_start_local', 'interval_end_local', 'source_time_label',
    *SOURCES, 'total_dispatch_reported_mw',
    'sum_generation_by_source_mw', 'dispatch_balance_difference_mw',
)
RECON_FIELDS = (
    'operating_date', 'source_sheet',
    *(x.replace('_mw', '_integrated_mwh') for x in SOURCES),
    'total_dispatch_reported_integrated_mwh',
    'reported_daily_total_energy_mwh',
    'reported_daily_solar_energy_mwh', 'reported_daily_wind_energy_mwh',
    'integrated_solar_energy_mwh', 'integrated_total_energy_mwh',
    'daily_total_energy_difference_mwh', 'daily_solar_energy_difference_mwh',
)


def excel_col(number: int) -> str:
    """1-based column number to Excel letters."""
    result = ''
    while number:
        number, mod = divmod(number - 1, 26)
        result = chr(65 + mod) + result
    return result


def _read_shared_strings(z: ZipFile) -> list[str]:
    if 'xl/sharedStrings.xml' not in z.namelist():
        return []
    root = ET.fromstring(z.read('xl/sharedStrings.xml'))
    return [
        ''.join(text.text or '' for text in si.findall('.//x:t', NS))
        for si in root.findall('x:si', NS)
    ]


def _sheet_refs(z: ZipFile) -> list[tuple[str, str]]:
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    relroot = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    rels = {r.attrib['Id']: r.attrib['Target'] for r in relroot.findall(f'{{{PKGREL}}}Relationship')}
    result: list[tuple[str, str]] = []
    for sheet in wb.findall('x:sheets/x:sheet', NS):
        rid = sheet.attrib[f'{{{DOCREL}}}id']
        target = rels[rid].lstrip('/')
        sheet_path = target if target.startswith('xl/') else posixpath.normpath(posixpath.join('xl', target))
        if sheet_path not in z.namelist():
            raise ValueError(f"Missing worksheet file for {sheet.attrib['name']}: {sheet_path}")
        result.append((sheet.attrib['name'], sheet_path))
    return result


def _read_cells(z: ZipFile, xml_path: str, shared: list[str]) -> dict[str, str | float]:
    root = ET.fromstring(z.read(xml_path))
    result: dict[str, str | float] = {}
    for cell in root.findall('.//x:sheetData/x:row/x:c', NS):
        address = cell.attrib['r']
        v = cell.find('x:v', NS)
        typ = cell.attrib.get('t', 'n')
        if typ == 'inlineStr':
            result[address] = ''.join(t.text or '' for t in cell.findall('.//x:t', NS))
        elif v is None or v.text is None:
            # Formula without cached result or truly empty: audited as missing below.
            continue
        elif typ == 's':
            result[address] = shared[int(v.text)]
        elif typ in ('str', 'e'):
            result[address] = v.text
        else:
            try:
                result[address] = float(v.text)
            except ValueError as exc:
                raise ValueError(f'Non-numeric XLSX value at {address}: {v.text}') from exc
    return result


def _normalized(text: str | float | None) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(text or '').lower())


def _numeric(value: object, label: str, *, allow_negative: bool = False) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f'Missing numeric value at {label}')
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'Invalid numeric value at {label}: {value!r}') from exc
    if not isfinite(result) or (result < 0 and not allow_negative):
        raise ValueError(f'Invalid numeric value at {label}: {result}')
    return result


def _time_minute(value: object) -> int:
    if isinstance(value, (int, float)):
        if not (0 <= value < 1):
            raise ValueError(f'Invalid Excel fractional time: {value}')
        return round(value * 1440) % 1440
    if isinstance(value, str):
        parts = value.strip().split(':')
        if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError(f'Invalid header time: {value!r}')
        hour, minute = int(parts[0]), int(parts[1])
        if hour > 24 or minute > 59 or (hour == 24 and minute):
            raise ValueError(f'Invalid header time: {value!r}')
        return (hour * 60 + minute) % 1440
    raise ValueError(f'Unrecognized time value: {value!r}')


def parse_day(
    cells: dict[str, str | float], sheet_name: str, year: int, month: int,
    filename: str, balance_tolerance_mw: float = 5.1,
) -> tuple[list[dict], dict]:
    m = re.fullmatch(r'\s*(\d{1,2})\.(\d{1,2})\s*', sheet_name)
    if not m:
        raise ValueError(f'Unrecognized day worksheet name {sheet_name!r}')
    day, observed_month = map(int, m.groups())
    if observed_month != month:
        raise ValueError(f'Worksheet {sheet_name!r} not in requested month {month}')
    operating_date = date(year, month, day)

    # Optional workbook date stamp near I1 is an Excel serial date.
    numeric_stamp = cells.get('I1')
    if isinstance(numeric_stamp, (int, float)) and 30000 <= numeric_stamp <= 100000:
        stamped_date = date(1899, 12, 30) + timedelta(days=int(numeric_stamp))
        if stamped_date != operating_date:
            raise ValueError(f'Worksheet date mismatch {sheet_name}: I1={stamped_date}')

    labels: dict[str, int] = {}
    for address, value in cells.items():
        if not re.fullmatch(r'A\d+', address):
            continue
        label = _normalized(value)
        # The top detailed table and the bottom summary table can have similar labels.
        # Prefer exact bottom-row category labels (>= 40 in Jan 2025).
        if label in {_normalized(x) for x in SOURCE_LABELS} and int(address[1:]) >= 40:
            if label in labels:
                raise ValueError(f'Duplicate summary category {value!r} in {sheet_name}')
            labels[label] = int(address[1:])

    missing = [name for name in SOURCE_LABELS if _normalized(name) not in labels]
    if missing:
        raise ValueError(f'Missing categories in {sheet_name}: {missing}')

    total_rows = [int(a[1:]) for a, v in cells.items()
                  if re.fullmatch(r'A\d+', a) and _normalized(v).startswith('totalmw')]
    if len(total_rows) != 1:
        raise ValueError(f'Expected one Total MW row in {sheet_name}, got {total_rows}')
    total_row = total_rows[0]
    if total_row >= min(labels.values()):
        raise ValueError(f'Unexpected source summary position in {sheet_name}')

    # Row 2 and the second Time row should agree; require at least one match.
    time_rows = [2] + [int(a[1:]) for a, v in cells.items()
                       if re.fullmatch(r'A\d+', a) and _normalized(v) == 'time' and int(a[1:]) != 2]
    first = datetime(year, month, day, tzinfo=LOCAL_TZ)
    records: list[dict] = []
    for i in range(INTERVALS_PER_DAY):
        column = excel_col(i + 2)  # B ... CS
        expected_minute = ((i + 1) * 15) % 1440
        seen = [cells.get(f'{column}{r}') for r in time_rows if cells.get(f'{column}{r}') is not None]
        if not seen:
            raise ValueError(f'Missing time header {column} in {sheet_name}')
        for v in seen:
            if _time_minute(v) != expected_minute:
                raise ValueError(f'Incorrect time header {column}: {v!r} in {sheet_name}')

        source_values = {
            output: _numeric(cells.get(f'{column}{labels[_normalized(source)]}'),
                             f'{sheet_name}!{column}{labels[_normalized(source)]}', allow_negative=True)
            for source, output in SOURCE_LABELS.items()
        }
        total = _numeric(cells.get(f'{column}{total_row}'), f'{sheet_name}!{column}{total_row}')
        summed = sum(source_values.values())
        difference = total - summed
        if abs(difference) > balance_tolerance_mw:
            raise ValueError(f'{sheet_name} interval {i+1}: total-source gap {difference:.2f} MW '
                             f'(allowed {balance_tolerance_mw:.2f} MW)')
        start = first + timedelta(minutes=i * 15)
        end = start + timedelta(minutes=15)
        records.append({
            'source_filename': filename, 'source_sheet': sheet_name.strip(),
            'operating_date': operating_date.isoformat(), 'interval_number': i + 1,
            'interval_start_local': start.isoformat(), 'interval_end_local': end.isoformat(),
            'source_time_label': f'{expected_minute // 60}:{expected_minute % 60:02d}',
            **source_values, 'total_dispatch_reported_mw': total,
            'sum_generation_by_source_mw': round(summed, 9),
            'dispatch_balance_difference_mw': round(difference, 9),
        })

    # Negative category values (e.g. Oil-IPP / Oil-CEB in the real January file)
    # are preserved and flagged for research review, never clipped to zero.
    # Daily energy values are independent source entries, not generated from
    # interval series and not assumed to be interchangeable.
    reported_total = _numeric(cells.get(f'CT{total_row}'), f'{sheet_name}!CT{total_row}')
    reported_solar = _numeric(cells.get(f'CT{labels[_normalized("Solar")]}'),
                              f'{sheet_name}!CT{labels[_normalized("Solar")]}')
    reported_wind = _numeric(cells.get(f'CT{labels[_normalized("Wind")]}'),
                             f'{sheet_name}!CT{labels[_normalized("Wind")]}')
    integrated = {
        field.replace('_mw', '_integrated_mwh'): round(sum(r[field] for r in records) * DURATION_H, 6)
        for field in SOURCES
    }
    total_integrated = round(sum(r['total_dispatch_reported_mw'] for r in records) * DURATION_H, 6)
    reconciliation = {
        'operating_date': operating_date.isoformat(), 'source_sheet': sheet_name.strip(),
        **integrated,
        'total_dispatch_reported_integrated_mwh': total_integrated,
        'reported_daily_total_energy_mwh': reported_total,
        'reported_daily_solar_energy_mwh': reported_solar,
        'reported_daily_wind_energy_mwh': reported_wind,
        'integrated_solar_energy_mwh': integrated['solar_integrated_mwh'],
        'integrated_total_energy_mwh': total_integrated,
        'daily_total_energy_difference_mwh': round(reported_total - total_integrated, 6),
        'daily_solar_energy_difference_mwh': round(reported_solar - integrated['solar_integrated_mwh'], 6),
    }
    return records, reconciliation


def _write_csv(path: Path, headings: tuple[str, ...], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=headings)
        writer.writeheader()
        writer.writerows(rows)


def process_workbook(input_path: Path, year: int, month: int,
                     balance_tolerance_mw: float = 5.1) -> tuple[list[dict], list[dict]]:
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    expected_days = set(range(1, calendar.monthrange(year, month)[1] + 1))
    all_rows: list[dict] = []
    daily_reports: list[dict] = []
    processed_days: set[int] = set()
    with ZipFile(input_path) as z:
        shared = _read_shared_strings(z)
        sheets = _sheet_refs(z)
        for sheet_name, sheet_path in sheets:
            match = re.fullmatch(r'\s*(\d{1,2})\.(\d{1,2})\s*', sheet_name)
            if not match:
                raise ValueError(f'Unexpected worksheet {sheet_name!r}; no data silently skipped')
            day, sheet_month = int(match.group(1)), int(match.group(2))
            if sheet_month != month or day not in expected_days or day in processed_days:
                raise ValueError(f'Unexpected or repeated worksheet date: {sheet_name!r}')
            rows, recon = parse_day(_read_cells(z, sheet_path, shared), sheet_name, year, month,
                                   input_path.name, balance_tolerance_mw)
            all_rows.extend(rows)
            daily_reports.append(recon)
            processed_days.add(day)
    if processed_days != expected_days:
        raise ValueError(f'Missing day worksheets: {sorted(expected_days - processed_days)}')
    all_rows.sort(key=lambda r: r['interval_start_local'])
    daily_reports.sort(key=lambda r: r['operating_date'])
    if len(all_rows) != len(expected_days) * INTERVALS_PER_DAY:
        raise ValueError('Incorrect total number of intervals')
    return all_rows, daily_reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='Original PUCSL monthly XLSX file')
    parser.add_argument('--year', type=int, required=True)
    parser.add_argument('--month', type=int, choices=range(1, 13), required=True)
    parser.add_argument('--output-dir', type=Path, default=Path('data/processed/grid'))
    parser.add_argument('--balance-tolerance-mw', type=float, default=5.1)
    args = parser.parse_args()
    if args.balance_tolerance_mw < 0:
        parser.error('balance tolerance must be nonnegative')
    rows, recon = process_workbook(args.input, args.year, args.month,
                                   args.balance_tolerance_mw)
    tag = f'{args.year}_{args.month:02d}'
    dispatch_path = args.output_dir / f'pucsl_{tag}_dispatch_15min.csv'
    recon_path = args.output_dir / f'pucsl_{tag}_reconciliation.csv'
    _write_csv(dispatch_path, FIELDNAMES, rows)
    _write_csv(recon_path, RECON_FIELDS, recon)
    anomalies = [
        {'operating_date': r['operating_date'], 'interval_end_local': r['interval_end_local'],
         'source': source, 'value_mw': r[source], 'issue': 'negative generation observation'}
        for r in rows for source in SOURCES if r[source] < 0
    ]
    anomaly_path = args.output_dir / f'pucsl_{tag}_negative_generation_flags.csv'
    _write_csv(anomaly_path, ('operating_date', 'interval_end_local',
                              'source', 'value_mw', 'issue'), anomalies)
    print(f'Negative-observation flags: {anomaly_path} ({len(anomalies)} rows)')
    print(f'Dispatch CSV: {dispatch_path}')
    print(f'Reconciliation CSV: {recon_path}')
    print(f'Days: {len(recon)} | 15-minute intervals: {len(rows)}')
    largest_gap = max(abs(r['dispatch_balance_difference_mw']) for r in rows)
    avg_energy_gap = sum(r['daily_total_energy_difference_mwh'] for r in recon) / len(recon)
    print(f'Max |reported MW - source sum MW|: {largest_gap:.3f} MW')
    print(f'Mean (reported daily MWh - integrated 15-min MWh): {avg_energy_gap:.2f} MWh/day')
    print('WARNING: Daily MWh discrepancy remains unresolved and is recorded, not corrected.')
    print('WARNING: Reported dispatch has not been established as total national demand.')


if __name__ == '__main__':
    main()
