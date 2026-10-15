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
* Worksheet dates may use DD.MM, MM.DD, or DD-MM-YYYY. Infer the naming
  convention across the entire workbook and reject unrecognized populated tabs.
* Incomplete months fail by default. --allow-partial-month permits a clearly
  flagged, partial export with a separate coverage report.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import json
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
    'interval_quality_status', 'interval_quality_flags',
)
RECON_FIELDS = (
    'operating_date', 'source_sheet',
    *(x.replace('_mw', '_integrated_mwh') for x in SOURCES),
    'total_dispatch_reported_integrated_mwh',
    'reported_daily_total_energy_mwh',
    'reported_daily_solar_energy_mwh', 'reported_daily_wind_energy_mwh',
    'integrated_solar_energy_mwh', 'integrated_total_energy_mwh',
    'daily_total_energy_difference_mwh', 'daily_solar_energy_difference_mwh',
    'sheet_internal_date_stamp', 'date_stamp_mismatch_accepted',
    'accepted_time_header_cells',
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


def sheet_operating_date(sheet_name: str, year: int, month: int,
                         sheet_date_order: str) -> date:
    """Parse a date using an explicit workbook-wide naming convention."""
    sheet_name = sheet_name.strip()
    if sheet_date_order == 'dd-mm-yyyy':
        match = re.fullmatch(r'(\d{1,2})-(\d{1,2})-(\d{4})', sheet_name)
        if not match:
            raise ValueError(f'Unrecognized day worksheet name {sheet_name!r}')
        day, stated_month, stated_year = map(int, match.groups())
        if stated_year != year:
            raise ValueError(f'Worksheet {sheet_name!r} not in requested year {year}')
    else:
        match = re.fullmatch(r'(\d{1,2})\.(\d{1,2})', sheet_name)
        if not match:
            raise ValueError(f'Unrecognized day worksheet name {sheet_name!r}')
        first, second = map(int, match.groups())
        if sheet_date_order == 'dd.mm':
            day, stated_month = first, second
        elif sheet_date_order == 'mm.dd':
            stated_month, day = first, second
        else:
            raise ValueError(f'Unsupported sheet date order: {sheet_date_order!r}')

    if stated_month != month:
        raise ValueError(f'Worksheet {sheet_name!r} not in requested month {month} '
                         f'(interpreted as {sheet_date_order})')
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ValueError(f'Invalid worksheet date: {sheet_name!r} '
                         f'(interpreted as {sheet_date_order})') from exc


def detect_sheet_date_order(sheet_names: list[str], year: int, month: int,
                            *, allow_partial: bool = False) -> str:
    """Infer workbook date order only when exactly one format matches all sheets."""
    expected_days = set(range(1, calendar.monthrange(year, month)[1] + 1))
    candidates = []
    for order in ('dd.mm', 'mm.dd', 'dd-mm-yyyy'):
        try:
            days = [sheet_operating_date(name, year, month, order).day
                    for name in sheet_names]
        except ValueError:
            continue
        no_repeats = len(set(days)) == len(days)
        if (no_repeats and days and set(days) <= expected_days
                and (allow_partial or set(days) == expected_days)):
            candidates.append(order)
    if len(candidates) != 1:
        raise ValueError(
            'Cannot uniquely determine worksheet date convention for '
            f'{year}-{month:02d}. Candidates: {candidates}; '
            f'example worksheet names: {sheet_names[:6]}. '
            'Inspect the original workbook; if needed specify '
            '--sheet-date-order dd.mm, mm.dd or dd-mm-yyyy.'
        )
    return candidates[0]



def audited_july_2024_date_orders(sheet_names: list[str], year: int, month: int) -> dict[str, str]:
    """Approve only the observed mixed-name pattern in the official July 2024 workbook.

    Days 1-7 have DD.MM worksheet names, while days 8-31 have MM.DD.
    Require EXACTLY the audited 31-sheet pattern, without duplicates or extras.
    """
    if (year, month) != (2024, 7):
        raise ValueError('Audited July 2024 mixed naming is valid only for 2024-07')
    expected = {f'{d:02d}.07': 'dd.mm' for d in range(1, 8)}
    expected.update({f'07.{d:02d}': 'mm.dd' for d in range(8, 32)})
    trimmed = [name.strip() for name in sheet_names]
    if len(trimmed) != len(set(trimmed)) or set(trimmed) != set(expected):
        raise ValueError('July 2024 mixed worksheet names differ from audited pattern; '
                         f'missing={sorted(set(expected)-set(trimmed))}; '
                         f'unexpected={sorted(set(trimmed)-set(expected))}')
    return expected


def audited_june_2024_time_cells(year: int, month: int) -> frozenset[tuple[date, str]]:
    """Approve only 17 incorrect secondary time cells on each June 14-29 sheet.

    The authoritative row-2 time labels MUST still match, verified by parse_day.
    Every approval MUST be used, verified by process_workbook.
    """
    if (year, month) != (2024, 6):
        raise ValueError('Audited June 2024 time approvals valid only for 2024-06')
    return frozenset((date(2024, 6, d), f'{excel_col(col)}41')
                     for d in range(14, 30) for col in range(51, 68))


def _is_date_sheet(name: str) -> bool:
    """Only dates shaped like one of the documented formats qualify as data tabs."""
    return bool(re.fullmatch(r'\s*(?:\d{1,2}\.\d{1,2}|'
                             r'\d{1,2}-\d{1,2}-\d{4})\s*', name))


def parse_day(
    cells: dict[str, str | float], sheet_name: str, year: int, month: int,
    filename: str, balance_tolerance_mw: float = 5.1,
    sheet_date_order: str = 'dd.mm',
    accepted_stamp_mismatch_dates: frozenset[date] = frozenset(),
    accepted_time_header_cells: frozenset[tuple[date, str]] = frozenset(),
    quarantine_balance_exceptions: bool = False,
) -> tuple[list[dict], dict]:
    operating_date = sheet_operating_date(sheet_name, year, month, sheet_date_order)
    day = operating_date.day

    # Optional workbook date stamp near I1 is an Excel serial date.
    numeric_stamp = cells.get('I1')
    stamped_date = None
    accepted_stamp_discrepancy = False
    if isinstance(numeric_stamp, (int, float)) and 30000 <= numeric_stamp <= 100000:
        stamped_date = date(1899, 12, 30) + timedelta(days=int(numeric_stamp))
        if stamped_date != operating_date:
            if operating_date not in accepted_stamp_mismatch_dates:
                raise ValueError(f'Worksheet date mismatch {sheet_name}: I1={stamped_date}')
            accepted_stamp_discrepancy = True

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
    accepted_time_cells: set[str] = set()
    for i in range(INTERVALS_PER_DAY):
        column = excel_col(i + 2)  # B ... CS
        expected_minute = ((i + 1) * 15) % 1440
        seen = [cells.get(f'{column}{r}') for r in time_rows if cells.get(f'{column}{r}') is not None]
        if not seen:
            raise ValueError(f'Missing time header {column} in {sheet_name}')
        interval_header_exceptions = []
        for header_row in time_rows:
            address = f'{column}{header_row}'
            v = cells.get(address)
            if v is not None and _time_minute(v) != expected_minute:
                if ((operating_date, address) not in accepted_time_header_cells
                        and (operating_date, f"ROW{header_row}") not in accepted_time_header_cells):
                    raise ValueError(f'Incorrect time header {address}: {v!r} in {sheet_name}')
                # Never allow all available time headers to be incorrect.
                if not any(_time_minute(other) == expected_minute for other in seen):
                    raise ValueError(f'No valid alternate time header in {sheet_name} {address}')
                interval_header_exceptions.append(address)
                accepted_time_cells.add(address)

        source_values = {
            output: _numeric(cells.get(f'{column}{labels[_normalized(source)]}'),
                             f'{sheet_name}!{column}{labels[_normalized(source)]}', allow_negative=True)
            for source, output in SOURCE_LABELS.items()
        }
        total = _numeric(cells.get(f'{column}{total_row}'), f'{sheet_name}!{column}{total_row}')
        summed = sum(source_values.values())
        difference = total - summed
        is_balance_exception = abs(difference) > 5.1
        if abs(difference) > balance_tolerance_mw and not quarantine_balance_exceptions:
            raise ValueError(f'{sheet_name} interval {i+1}: total-source gap {difference:.2f} MW '
                             f'(allowed {balance_tolerance_mw:.2f} MW)')
        quality_flags = []
        if is_balance_exception:
            quality_flags.append('source_balance_exception')
        if any(value < 0 for value in source_values.values()):
            quality_flags.append('negative_source_observation')
        if accepted_stamp_discrepancy:
            quality_flags.append('accepted_date_stamp_mismatch')
        if interval_header_exceptions:
            quality_flags.append('accepted_time_header_mismatch')
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
            'interval_quality_status': 'review_required' if quality_flags else 'no_interval_flags',
            'interval_quality_flags': ';'.join(quality_flags),
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
        'sheet_internal_date_stamp': stamped_date.isoformat() if stamped_date else '',
        'date_stamp_mismatch_accepted': accepted_stamp_discrepancy,
        'accepted_time_header_cells': ';'.join(sorted(accepted_time_cells)),
    }
    return records, reconciliation


def _write_csv(path: Path, headings: tuple[str, ...], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=headings)
        writer.writeheader()
        writer.writerows(rows)


def process_workbook(input_path: Path, year: int, month: int,
                     balance_tolerance_mw: float = 5.1,
                     sheet_date_order: str = 'auto',
                     allow_partial_month: bool = False,
                     accepted_stamp_mismatch_dates: frozenset[date] = frozenset(),
                     accepted_time_header_cells: frozenset[tuple[date, str]] = frozenset(),
                     quarantine_balance_exceptions: bool = False,
                     audited_july_2024_mixed_names: bool = False,
                     ) -> tuple[list[dict], list[dict]]:
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    expected_days = set(range(1, calendar.monthrange(year, month)[1] + 1))
    all_rows: list[dict] = []
    daily_reports: list[dict] = []
    processed_days: set[int] = set()
    with ZipFile(input_path) as z:
        shared = _read_shared_strings(z)
        sheets = _sheet_refs(z)
        data_sheets = []
        for sheet_name, sheet_path in sheets:
            if _is_date_sheet(sheet_name):
                data_sheets.append((sheet_name, sheet_path))
            else:
                # Ignore *only* genuinely empty non-date worksheets (March's Sheet2).
                if _read_cells(z, sheet_path, shared):
                    raise ValueError(f'Unrecognized non-empty worksheet: {sheet_name!r}')
                print(f'Ignoring empty non-date worksheet: {sheet_name!r}')
        if not data_sheets:
            raise ValueError('No recognizable day worksheets found')
        if audited_july_2024_mixed_names:
            if sheet_date_order != 'auto':
                raise ValueError('Audited July mixed names cannot be combined with --sheet-date-order')
            per_sheet_order = audited_july_2024_date_orders(
                [name for name, _ in data_sheets], year, month)
            order = 'audited-mixed-2024-07 (days 1-7: dd.mm; days 8-31: mm.dd)'
        else:
            if sheet_date_order == 'auto':
                order = detect_sheet_date_order([name for name, _ in data_sheets],
                                                year, month, allow_partial=True)
            elif sheet_date_order in ('dd.mm', 'mm.dd', 'dd-mm-yyyy'):
                order = sheet_date_order
            else:
                raise ValueError(f'Unsupported sheet date order: {sheet_date_order!r}')
            per_sheet_order = {name.strip(): order for name, _ in data_sheets}
        print(f'Worksheet date convention: {order}')
        for sheet_name, sheet_path in data_sheets:
            day_order = per_sheet_order[sheet_name.strip()]
            operating_date = sheet_operating_date(sheet_name, year, month, day_order)
            day = operating_date.day
            if day not in expected_days or day in processed_days:
                raise ValueError(f'Unexpected or repeated worksheet date: {sheet_name!r}')
            rows, recon = parse_day(_read_cells(z, sheet_path, shared), sheet_name, year, month,
                                   input_path.name, balance_tolerance_mw,
                                   sheet_date_order=day_order,
                                   accepted_stamp_mismatch_dates=accepted_stamp_mismatch_dates,
                                   accepted_time_header_cells=accepted_time_header_cells,
                                   quarantine_balance_exceptions=quarantine_balance_exceptions)
            all_rows.extend(rows)
            daily_reports.append(recon)
            processed_days.add(day)
    missing = sorted(expected_days - processed_days)
    if missing and not allow_partial_month:
        raise ValueError(f'Missing day worksheets for {year}-{month:02d}: {missing}. '
                         'Refusing to export incomplete month; use --allow-partial-month '
                         'only when deliberately producing a labeled partial dataset.')
    if missing:
        print(f'WARNING: INCOMPLETE MONTH: missing days {missing}')
    all_rows.sort(key=lambda r: r['interval_start_local'])
    daily_reports.sort(key=lambda r: r['operating_date'])
    if len(all_rows) != len(processed_days) * INTERVALS_PER_DAY:
        raise ValueError('Incorrect total number of intervals')
    actually_accepted = {
        (date.fromisoformat(r['operating_date']), cell)
        for r in daily_reports for cell in r['accepted_time_header_cells'].split(';') if cell
    }
    unused = {
        (d, approval) for (d, approval) in accepted_time_header_cells
        if (d, approval) not in actually_accepted and not (
            approval.startswith("ROW") and approval[3:].isdigit() and
            any(recorded_date == d and re.fullmatch(r"[A-Z]+" + approval[3:], address)
                for recorded_date, address in actually_accepted)
        )
    }
    if unused:
        raise ValueError(f'Unused time-header exception approvals: {sorted(unused)}')
    return all_rows, daily_reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='Original PUCSL monthly XLSX file')
    parser.add_argument('--year', type=int, required=True)
    parser.add_argument('--month', type=int, choices=range(1, 13), required=True)
    parser.add_argument('--output-dir', type=Path, default=Path('data/processed/grid'))
    parser.add_argument('--balance-tolerance-mw', type=float, default=5.1)
    parser.add_argument('--sheet-date-order', choices=('auto', 'dd.mm', 'mm.dd', 'dd-mm-yyyy'),
                        default='auto',
                        help='Worksheet date naming convention (auto checks all dates)')
    parser.add_argument('--allow-partial-month', action='store_true',
                        help='Export with missing dates explicitly flagged; do NOT treat as full month')
    parser.add_argument('--quarantine-balance-exceptions', action='store_true',
                        help='Explicitly retain but flag source-total mismatched intervals; not validated for modeling')
    parser.add_argument('--accept-audited-july-2024-mixed-sheet-names', action='store_true',
                        help='Explicitly accept ONLY the verified July 2024 sheet naming pattern')
    parser.add_argument('--accept-audited-june-2024-secondary-time-labels', action='store_true',
                        help='Explicitly accept ONLY 272 verified secondary time errors, June 14-29')
    parser.add_argument('--accept-time-header-mismatch', action='append', default=[],
                        metavar='YYYY-MM-DD:CH40|YYYY-MM-DD:ROW40',
                        help='Approve one audited erroneous secondary time-label cell, with a valid matching header')
    parser.add_argument('--accept-date-stamp-mismatch', action='append', default=[],
                        metavar='YYYY-MM-DD',
                        help='Allow and document only a specifically reviewed sheet I1 date discrepancy. '
                             'Never use this for unreviewed sheets.')
    args = parser.parse_args()
    if args.balance_tolerance_mw < 0:
        parser.error('balance tolerance must be nonnegative')
    try:
        accepted_dates = frozenset(date.fromisoformat(x) for x in args.accept_date_stamp_mismatch)
    except ValueError as exc:
        parser.error(f'Invalid --accept-date-stamp-mismatch: {exc}')
    if any(d.year != args.year or d.month != args.month for d in accepted_dates):
        parser.error('Accepted date-stamp mismatches must be in the requested month')
    accepted_headers = set()
    for item in args.accept_time_header_mismatch:
        match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}):([A-Z]+\d+)', item)
        if not match:
            parser.error(f'Invalid --accept-time-header-mismatch: {item!r}')
        d = date.fromisoformat(match.group(1))
        if d.year != args.year or d.month != args.month:
            parser.error('Accepted time-header mismatches must be in the requested month')
        accepted_headers.add((d, match.group(2)))
    if args.accept_audited_june_2024_secondary_time_labels:
        if (args.year, args.month) != (2024, 6):
            parser.error('Audited June time-header approval only applies to 2024-06')
        accepted_headers.update(audited_june_2024_time_cells(args.year, args.month))
    if args.accept_audited_july_2024_mixed_sheet_names and (args.year, args.month) != (2024, 7):
        parser.error('Audited July naming approval only applies to 2024-07')
    rows, recon = process_workbook(args.input, args.year, args.month,
                                   args.balance_tolerance_mw,
                                   sheet_date_order=args.sheet_date_order,
                                   allow_partial_month=args.allow_partial_month,
                                   accepted_stamp_mismatch_dates=accepted_dates,
                                   accepted_time_header_cells=frozenset(accepted_headers),
                                   quarantine_balance_exceptions=args.quarantine_balance_exceptions,
                                   audited_july_2024_mixed_names=args.accept_audited_july_2024_mixed_sheet_names)
    tag = f'{args.year}_{args.month:02d}'
    available_days = {int(r['operating_date'][-2:]) for r in recon}
    expected_days = set(range(1, calendar.monthrange(args.year, args.month)[1] + 1))
    missing = sorted(expected_days - available_days)
    suffix = '_partial' if missing else ''
    dispatch_path = args.output_dir / f'pucsl_{tag}{suffix}_dispatch_15min.csv'
    recon_path = args.output_dir / f'pucsl_{tag}{suffix}_reconciliation.csv'
    _write_csv(dispatch_path, FIELDNAMES, rows)
    _write_csv(recon_path, RECON_FIELDS, recon)
    anomalies = [
        {'operating_date': r['operating_date'], 'interval_end_local': r['interval_end_local'],
         'source': source, 'value_mw': r[source], 'issue': 'negative generation observation'}
        for r in rows for source in SOURCES if r[source] < 0
    ]
    anomaly_path = args.output_dir / f'pucsl_{tag}{suffix}_negative_generation_flags.csv'
    _write_csv(anomaly_path, ('operating_date', 'interval_end_local',
                              'source', 'value_mw', 'issue'), anomalies)
    # Even when the operator explicitly accepts a slightly higher tolerance,
    # preserve every observation exceeding the original strict 5.1 MW check.
    balance_flags = [
        {'operating_date': r['operating_date'],
         'interval_end_local': r['interval_end_local'],
         'difference_mw': r['dispatch_balance_difference_mw'],
         'issue': 'Source-total residual exceeds 5.1 MW baseline audit limit'}
        for r in rows if abs(r['dispatch_balance_difference_mw']) > 5.1
    ]
    balance_flags_path = args.output_dir / f'pucsl_{tag}{suffix}_balance_flags.csv'
    _write_csv(balance_flags_path,
               ('operating_date', 'interval_end_local', 'difference_mw', 'issue'),
               balance_flags)
    date_stamp_exceptions = [
        {'operating_date': r['operating_date'], 'source_sheet': r['source_sheet'],
         'sheet_internal_date_stamp': r['sheet_internal_date_stamp']}
        for r in recon if r['date_stamp_mismatch_accepted']
    ]
    accepted_time_header_exceptions = [
        {'operating_date': r['operating_date'], 'cell': cell}
        for r in recon for cell in r['accepted_time_header_cells'].split(';') if cell
    ]
    review_required = sum(r['interval_quality_status'] == 'review_required' for r in rows)
    coverage = {
        'source_file': args.input.name, 'year': args.year, 'month': args.month,
        'status': 'incomplete' if missing else 'complete',
        'available_dates': [r['operating_date'] for r in recon],
        'missing_dates': [date(args.year, args.month, d).isoformat() for d in missing],
        'expected_days': len(expected_days), 'available_days': len(available_days),
        'intervals': len(rows), 'resolution_minutes': 15,
        'timestamp_convention': 'provisional interval-end source labels',
        'worksheet_date_convention': ('audited-mixed-2024-07' if args.accept_audited_july_2024_mixed_sheet_names
                                      else args.sheet_date_order),
        'audited_june_2024_secondary_time_labels_accepted': args.accept_audited_june_2024_secondary_time_labels,
        'date_stamp_exceptions_accepted': date_stamp_exceptions,
        'balance_threshold_mw_used': args.balance_tolerance_mw,
        'balance_exceptions_over_5_1_mw': len(balance_flags),
        'balance_exceptions_quarantined': args.quarantine_balance_exceptions,
        'time_header_exceptions_accepted': accepted_time_header_exceptions,
        'intervals_requiring_quality_review': review_required,
        'interval_quality_warning': 'No interval flags does not establish national-demand, energy or physical validity',
        'limitations': [
            'Reported daily energy differs from quarter-hour integrated power',
            'Reported dispatch is not independently verified national demand',
            'PV may be underrepresented in reported quarter-hour dispatch',
        ],
    }
    coverage_path = args.output_dir / f'pucsl_{tag}_coverage.json'
    coverage_path.write_text(json.dumps(coverage, indent=2) + '\n', encoding='utf-8')
    print(f'Coverage report: {coverage_path} ({coverage["status"]})')
    print(f'Negative-observation flags: {anomaly_path} ({len(anomalies)} rows)')
    print(f'Balance exceptions exceeding 5.1 MW: {balance_flags_path} ({len(balance_flags)} rows)')
    print(f'Intervals flagged for quality review: {review_required}')
    if len(accepted_time_header_exceptions) > 24:
        print(f'WARNING: {len(accepted_time_header_exceptions)} AUDITED TIME HEADER ERRORS ACCEPTED; '
              'see coverage JSON for every cell')
    else:
        for issue in accepted_time_header_exceptions:
            print('WARNING: ACCEPTED TIME HEADER MISMATCH:', issue)
    for issue in date_stamp_exceptions:
        print('WARNING: ACCEPTED SOURCE DATE STAMP MISMATCH:', issue)
    print(f'Dispatch CSV: {dispatch_path}')
    print(f'Reconciliation CSV: {recon_path}')
    print(f'Days: {len(recon)} | 15-minute intervals: {len(rows)}')
    largest_gap = max(abs(r['dispatch_balance_difference_mw']) for r in rows)
    avg_energy_gap = sum(r['daily_total_energy_difference_mwh'] for r in recon) / len(recon)
    print(f'Max |reported MW - source sum MW|: {largest_gap:.3f} MW')
    print(f'Mean (reported daily MWh - integrated 15-min MWh): {avg_energy_gap:.2f} MWh/day')
    if missing:
        print(f'WARNING: EXCLUDE incomplete {args.year}-{args.month:02d} from full-month analysis; '
              f'missing dates: {coverage["missing_dates"]}')
    if balance_flags:
        print('WARNING: Source-total residual exceptions present; quarantined or admitted by explicit settings. Investigate flags.')
    print('WARNING: Daily MWh discrepancy remains unresolved and is recorded, not corrected.')
    print('WARNING: Reported dispatch has not been established as total national demand.')


if __name__ == '__main__':
    main()
