#!/usr/bin/env python3
"""Read-only audit of locally cached GBGB meeting JSON.

Makes no HTTP requests and does not mutate the source database or JSON files.
Reports real field names and examples rather than presuming the meaning of a
finish-time or sectional-time field.
"""
from __future__ import annotations
import argparse
import contextlib
from collections import Counter, defaultdict
import csv
import datetime as dt
import json
from pathlib import Path
import re
import sqlite3

BASE = Path(__file__).resolve().parent
DEFAULT_DB = BASE / 'data' / 'gbgb.sqlite3'
DEFAULT_OUT = BASE / 'data' / 'reports'
CANDIDATE = re.compile(r'(?i)(section|split|time|finish|margin|distance|position|placing|bend|run.?home|trap|box|going|weight|grade|speed|track|result)')
MAX_DEPTH = 15
MAX_VISITS_PER_DOC = 200_000


def read_conn(db: Path):
    db = db.resolve()
    if not db.is_file():
        raise FileNotFoundError(f'GBGB database not found: {db}. Complete RUN_GBGB_PILOT.bat first.')
    # Query-only + mode=ro: cannot modify the live SQLite database.
    uri = db.as_uri() + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.execute('PRAGMA query_only=ON')
    return conn


def tree_paths(obj):
    """Yield a leaf path and value, collapsing dynamic array indexes to []."""
    stack = [('$', obj, 0)]
    visits = 0
    while stack:
        path, value, depth = stack.pop()
        visits += 1
        if visits > MAX_VISITS_PER_DOC:
            raise ValueError('A meeting JSON exceeded the 200,000-node audit limit')
        if depth > MAX_DEPTH:
            yield path, '<DEPTH_LIMIT>'
        elif isinstance(value, dict):
            if not value:
                yield path, '<EMPTY_OBJECT>'
            else:
                for key, child in reversed(list(value.items())):
                    stack.append((f'{path}.{key}', child, depth + 1))
        elif isinstance(value, list):
            if not value:
                yield path + '[]', '<EMPTY_ARRAY>'
            else:
                for child in reversed(value):
                    stack.append((path + '[]', child, depth + 1))
        else:
            yield path, value


def short_example(value):
    if isinstance(value, str):
        text = value.replace('\n', '\\n').replace('\r', '\\r')
    else:
        text = json.dumps(value, ensure_ascii=False)
    return text[:160]


def preview(value, depth=0):
    if depth >= 9:
        return '<NESTING_LIMIT>'
    if isinstance(value, dict):
        return {str(k): preview(v, depth + 1) for k, v in list(value.items())[:70]}
    if isinstance(value, list):
        sample = [preview(v, depth + 1) for v in value[:2]]
        if len(value) > 2:
            sample.append(f'<{len(value)-2} more array items omitted>')
        return sample
    if isinstance(value, str):
        return value[:500] + ('…' if len(value) > 500 else '')
    return value


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def run_audit(db: Path, output: Path):
    summary = []
    fields = defaultdict(lambda: {'count': 0, 'meeting_docs': set(), 'types': Counter(), 'examples': []})
    source_shapes = Counter()
    previews = []
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    with contextlib.closing(read_conn(db)) as conn:
        available = tables(conn)
        required = {'gbgb_index', 'gbgb_meetings', 'gbgb_date_coverage'}
        if not required.issubset(available):
            raise RuntimeError(f'Collector schema incomplete; missing: {sorted(required - available)}')
        index_records = conn.execute('SELECT count(*) FROM gbgb_index').fetchone()[0]
        meeting_count = conn.execute('SELECT count(*) FROM gbgb_meetings').fetchone()[0]
        dates = conn.execute('SELECT race_date,indexed_items,meetings_found,pages_found,all_pages_saved FROM gbgb_date_coverage ORDER BY race_date').fetchall()
        distinct_races = conn.execute('SELECT count(DISTINCT race_id) FROM gbgb_index WHERE race_id IS NOT NULL').fetchone()[0]
        distinct_meeting_ids = conn.execute('SELECT count(DISTINCT meeting_id) FROM gbgb_index WHERE meeting_id IS NOT NULL').fetchone()[0]
        missing_meeting_ids = conn.execute('''SELECT count(DISTINCT i.meeting_id) FROM gbgb_index i
                 LEFT JOIN gbgb_meetings m ON i.meeting_id=m.meeting_id
                 WHERE i.meeting_id IS NOT NULL AND m.meeting_id IS NULL''').fetchone()[0]
        summary.extend([
            'GBGB CACHED DATA FIELD AUDIT — READ ONLY',
            f'Generated (UTC): {now}',
            f'Database: {db.resolve()}',
            'No new web requests were made. No database rows were changed.',
            '',
            f'Index records (not necessarily races): {index_records:,}',
            f'Distinct race IDs found in index: {distinct_races:,}',
            f'Meeting JSON records: {meeting_count:,}',
            f'Distinct meeting IDs in index: {distinct_meeting_ids:,}',
            f'Indexed meetings without a saved meeting JSON: {missing_meeting_ids:,}',
            f'Fully indexed dates recorded: {len(dates)}',
            'Dates (index rows / meeting IDs / pages / marked complete):'
        ])
        for date, rows, mids, pages, complete in dates:
            summary.append(f'  {date}: {rows} / {mids} / {pages} / {"yes" if complete else "NO"}')
        summary.append('')
        # Iterate row by row to avoid loading a large archive into memory.
        iterator = conn.execute('SELECT meeting_id, first_seen_date, payload_json FROM gbgb_meetings ORDER BY meeting_id')
        for meeting_id, race_date, raw in iterator:
            try:
                obj = json.loads(raw)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f'Corrupt meeting JSON in SQLite, meeting {meeting_id}: {exc}') from exc
            top_keys = tuple(sorted(obj.keys())) if isinstance(obj, dict) else (type(obj).__name__,)
            shape = '|'.join(top_keys)
            source_shapes[shape] += 1
            if len(previews) < 4 and not any(p['top_keys'] == list(top_keys) for p in previews):
                previews.append({'meeting_id': meeting_id, 'date': race_date,
                                 'top_keys': list(top_keys), 'first_items_preview': preview(obj)})
            seen_this_doc = set()
            for path, value in tree_paths(obj):
                record = fields[path]
                record['count'] += 1
                record['types'][type(value).__name__] += 1
                if path not in seen_this_doc:
                    record['meeting_docs'].add(str(meeting_id))
                    seen_this_doc.add(path)
                example = short_example(value)
                if example not in record['examples'] and len(record['examples']) < 3:
                    record['examples'].append(example)
        summary.append('Meeting JSON top-level shapes (count, keys):')
        for shape, n in source_shapes.most_common():
            summary.append(f'  {n} x {shape}')
        summary.extend([
            '',
            f'Distinct meeting JSON leaf-field paths: {len(fields):,}',
            'The separate gbgb_normalize.py component maps observed race/runner fields (v1.6).',
            'This field audit itself inspects raw JSON and does not prove a mapping succeeded.',
            'Run GBGB_BUILD_RACE_DATABASE.bat then review gbgb_normalized_quality.txt.',
            'Do not calculate speed figures or claim complete historical coverage from staging counts.',
            '',
            'Outputs:',
            '  gbgb_all_fields.csv — every observed leaf path, sample values and frequency',
            '  gbgb_timing_candidates.csv — possible timing/results fields for manual inspection',
            '  gbgb_meeting_structure_samples.json — truncated example structure from real cached data',
        ])
    output.mkdir(parents=True, exist_ok=True)
    headers = ['json_path', 'value_count', 'meeting_documents', 'value_types', 'example_1', 'example_2', 'example_3']
    all_rows = []
    for path, record in sorted(fields.items()):
        examples = record['examples'] + [''] * 3
        all_rows.append([path, record['count'], len(record['meeting_docs']),
                         ';'.join(f'{t}:{c}' for t, c in sorted(record['types'].items())), *examples[:3]])
    for name, rows in [('gbgb_all_fields.csv', all_rows),
                       ('gbgb_timing_candidates.csv', [row for row in all_rows if CANDIDATE.search(row[0])])]:
        with (output / name).open('w', newline='', encoding='utf-8-sig') as fh:
            w = csv.writer(fh)
            w.writerow(headers)
            w.writerows(rows)
    (output / 'gbgb_meeting_structure_samples.json').write_text(
        json.dumps({'generated_utc': now, 'samples': previews}, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'gbgb_field_audit.txt').write_text('\n'.join(summary) + '\n', encoding='utf-8')
    return {'meetings': meeting_count, 'index_rows': index_records,
            'dates': len(dates), 'field_paths': len(fields),
            'candidates': sum(1 for r in all_rows if CANDIDATE.search(r[0])), 'output': output}


def main(argv=None):
    p = argparse.ArgumentParser(description='Read-only GBGB cached JSON field audit, no web requests')
    p.add_argument('--db', type=Path, default=DEFAULT_DB)
    p.add_argument('--out', type=Path, default=DEFAULT_OUT)
    args = p.parse_args(argv)
    try:
        result = run_audit(args.db, args.out)
    except (FileNotFoundError, RuntimeError, ValueError, sqlite3.Error, OSError) as exc:
        print(f'GBGB FIELD AUDIT FAILED: {exc}', flush=True)
        return 1
    print('GBGB LOCAL DATA AUDIT FINISHED — NO NETWORK REQUESTS', flush=True)
    print(f"Indexed records: {result['index_rows']:,} | Meeting JSON: {result['meetings']:,} | Dates: {result['dates']}")
    print(f"Observed field paths: {result['field_paths']:,} | Potential results/timing fields: {result['candidates']:,}")
    print('Reports saved in:', result['output'].resolve())
    print('Please share gbgb_field_audit.txt and gbgb_timing_candidates.csv.')
    print('If possible, also share gbgb_meeting_structure_samples.json to confirm the full runner mapping.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
