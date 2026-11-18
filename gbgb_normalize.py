#!/usr/bin/env python3
"""Normalise GBGB meeting JSON into durable UK race/runner tables.

Grounded in the observed GBGB structure: a list of meetings, each containing
races[], each containing traps[]. No hypothetical split-distance assumptions.
Raw JSON stays in gbgb_meetings for reproducibility.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys

DEFAULT_DB = Path(__file__).resolve().parent / 'data' / 'gbgb.sqlite3'
DEFAULT_REPORT = Path(__file__).resolve().parent / 'data' / 'reports'

class NormalizationError(ValueError):
    """Unrecognised/unsafe meeting structure; preserve source and do not claim an import."""


def text_value(v):
    if v is None:
        return None
    return str(v).strip() or None


def float_value(v, positive=False):
    if v is None or isinstance(v, bool) or isinstance(v, (dict, list)):
        return None
    try:
        f = float(str(v).strip())
    except (ValueError, TypeError):
        return None
    if not math.isfinite(f) or (positive and f <= 0):
        return None
    return f


def int_value(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        i = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return i


def race_date(v):
    if not v:
        return None
    s = str(v).strip()
    for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def ensure_schema(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS gbgb_races (
      race_id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL,
      race_date TEXT, scheduled_time TEXT, track_name TEXT,
      race_number TEXT, race_title TEXT, race_type TEXT, race_class TEXT,
      race_distance_m REAL, is_handicap INTEGER, race_going_raw TEXT,
      race_prizes_raw TEXT, forecast_raw TEXT, tricast_raw TEXT,
      source_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS gbgb_races_meeting_id ON gbgb_races(meeting_id);
    CREATE INDEX IF NOT EXISTS gbgb_races_date_track ON gbgb_races(race_date, track_name);
    CREATE INDEX IF NOT EXISTS gbgb_races_track_distance ON gbgb_races(track_name, race_distance_m);
    CREATE TABLE IF NOT EXISTS gbgb_runners (
      race_id TEXT NOT NULL, trap_number TEXT NOT NULL,
      dog_id TEXT, dog_name TEXT, dog_sire TEXT, dog_dam TEXT,
      dog_born_raw TEXT, dog_origin TEXT, dog_colour TEXT, dog_sex TEXT,
      dog_season_raw TEXT, trainer_name TEXT, owner_name TEXT,
      trap_handicap_raw TEXT, sp_raw TEXT, price_numerator INTEGER,
      price_denominator INTEGER, market_position INTEGER,
      market_count INTEGER, finish_position INTEGER,
      beaten_distance_raw TEXT, sectional_time_s REAL,
      run_time_s REAL, adjusted_time_s REAL, dog_weight_kg REAL,
      result_comment TEXT, source_json TEXT NOT NULL,
      PRIMARY KEY (race_id, trap_number)
    );
    CREATE INDEX IF NOT EXISTS gbgb_runners_dog_id ON gbgb_runners(dog_id);
    CREATE INDEX IF NOT EXISTS gbgb_runners_finish ON gbgb_runners(race_id, finish_position);
    CREATE TABLE IF NOT EXISTS gbgb_meeting_imports (
      meeting_id TEXT PRIMARY KEY, json_sha256 TEXT NOT NULL,
      imported_utc TEXT NOT NULL, race_count INTEGER NOT NULL,
      trap_count INTEGER NOT NULL, result_count INTEGER NOT NULL,
      timed_count INTEGER NOT NULL, first_sectional_count INTEGER NOT NULL,
      warning_count INTEGER NOT NULL, warning_examples TEXT NOT NULL
    );
    ''')
    conn.commit()


def is_imported(conn, meeting_id, digest):
    try:
        row = conn.execute('SELECT json_sha256 FROM gbgb_meeting_imports WHERE meeting_id=?',
                           (str(meeting_id),)).fetchone()
        return bool(row and row[0] == digest)
    except sqlite3.OperationalError as exc:
        if 'no such table' in str(exc).lower():
            return False
        raise


def normalize_meeting(conn, meeting_id, payload, digest=None):
    """Atomically replace one meeting's normalised rows. Idempotent by raw SHA256.

    The source may contain vacant traps, non-runners and missing sectionals.
    Missing values remain NULL; no guessed splits or beaten-distance seconds.
    """
    mid = str(meeting_id)
    if not mid.isdigit():
        raise NormalizationError(f'Invalid meeting ID {mid!r}')
    if not isinstance(payload, list) or not payload:
        raise NormalizationError(f'Meeting {mid} expected a non-empty JSON list')
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    actual_digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    if digest and digest != actual_digest:
        raise NormalizationError(f'Meeting {mid}: stored JSON digest does not match payload')
    digest = actual_digest
    ensure_schema(conn)
    if is_imported(conn, mid, digest):
        return {'meeting': mid, 'state': 'unchanged'}

    race_rows, runner_rows, warnings = [], [], []
    seen_races, seen_traps = set(), set()
    for group in payload:
        if not isinstance(group, dict):
            raise NormalizationError(f'Meeting {mid} contains a non-object')
        group_id = group.get('meetingId')
        if group_id is None or str(group_id) != mid:
            raise NormalizationError(f'Meeting {mid}: response group has meetingId={group_id!r}')
        meet_date = race_date(group.get('meetingDate'))
        track_name = text_value(group.get('trackName'))
        races = group.get('races')
        if not isinstance(races, list):
            raise NormalizationError(f'Meeting {mid}: expected races[]')
        for race in races:
            if not isinstance(race, dict):
                raise NormalizationError(f'Meeting {mid}: invalid race object')
            rid = text_value(race.get('raceId'))
            if rid is None or not rid.isdigit():
                raise NormalizationError(f'Meeting {mid}: race has no numeric raceId')
            if rid in seen_races:
                raise NormalizationError(f'Meeting {mid}: duplicate raceId {rid}')
            seen_races.add(rid)
            day = race_date(race.get('raceDate')) or meet_date
            if not day:
                raise NormalizationError(f'Meeting {mid}: race {rid} has no valid date')
            if meet_date and day != meet_date:
                warnings.append(f'race {rid}: race date differs from meeting date')
            distance = float_value(race.get('raceDistance'), positive=True)
            if distance is None:
                warnings.append(f'race {rid}: missing or invalid distance')
            traps = race.get('traps')
            if not isinstance(traps, list):
                raise NormalizationError(f'Meeting {mid} race {rid}: expected traps[]')
            race_rows.append((rid, mid, day, text_value(race.get('raceTime')),
                track_name, text_value(race.get('raceNumber')),
                text_value(race.get('raceTitle')), text_value(race.get('raceType')),
                text_value(race.get('raceClass')), distance,
                int(bool(race.get('raceHandicap'))) if race.get('raceHandicap') is not None else None,
                text_value(race.get('raceGoing')), text_value(race.get('racePrizes')),
                text_value(race.get('raceForecast')), text_value(race.get('raceTricast')),
                json.dumps(race, ensure_ascii=False, separators=(',', ':'))))
            for dog in traps:
                if not isinstance(dog, dict):
                    raise NormalizationError(f'Meeting {mid} race {rid}: invalid trap record')
                trap = text_value(dog.get('trapNumber'))
                if trap is None:
                    raise NormalizationError(f'Meeting {mid} race {rid}: missing trap number')
                key = (rid, trap)
                if key in seen_traps:
                    raise NormalizationError(f'Meeting {mid} race {rid}: duplicate trap {trap}')
                seen_traps.add(key)
                fields = {}
                for col, field in (
                    ('sectional_time_s', 'resultSectionalTime'),
                    ('run_time_s', 'resultRunTime'),
                    ('adjusted_time_s', 'resultAdjustedTime'),
                    ('dog_weight_kg', 'resultDogWeight')):
                    val = dog.get(field)
                    fields[col] = float_value(val, positive=True)
                    if val is not None and fields[col] is None:
                        warnings.append(f'race {rid} trap {trap}: invalid {field}={str(val)[:20]!r}')
                if (fields['sectional_time_s'] is not None and
                    fields['run_time_s'] is not None and
                    fields['sectional_time_s'] >= fields['run_time_s']):
                    warnings.append(f'race {rid} trap {trap}: sectional not shorter than run time')
                runner_rows.append((rid, trap,
                    text_value(dog.get('dogId')), text_value(dog.get('dogName')),
                    text_value(dog.get('dogSire')), text_value(dog.get('dogDam')),
                    text_value(dog.get('dogBorn')), text_value(dog.get('dogOrigin')),
                    text_value(dog.get('dogColour')), text_value(dog.get('dogSex')),
                    text_value(dog.get('dogSeason')), text_value(dog.get('trainerName')),
                    text_value(dog.get('ownerName')), text_value(dog.get('trapHandicap')),
                    text_value(dog.get('SP')), int_value(dog.get('resultPriceNumerator')),
                    int_value(dog.get('resultPriceDenominator')),
                    int_value(dog.get('resultMarketPos')), int_value(dog.get('resultMarketCnt')),
                    int_value(dog.get('resultPosition')),
                    text_value(dog.get('resultBtnDistance')),
                    fields['sectional_time_s'], fields['run_time_s'],
                    fields['adjusted_time_s'], fields['dog_weight_kg'],
                    text_value(dog.get('resultComment')),
                    json.dumps(dog, ensure_ascii=False, separators=(',', ':'))))

    if not race_rows:
        warnings.append(f'meeting {mid}: zero races in source')
    # Prepare all records before deleting any old normalised data. Validate cross-meeting
    # collisions rather than silently overwriting a race belonging elsewhere.
    if seen_races:
        for rid in seen_races:
            other = conn.execute('SELECT meeting_id FROM gbgb_races WHERE race_id=?', (rid,)).fetchone()
            if other and other[0] != mid:
                raise NormalizationError(f'race {rid} already belongs to meeting {other[0]}, not {mid}')
    imported_utc = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    counts = (len(race_rows), len(runner_rows),
              sum(row[19] is not None for row in runner_rows),
              sum(row[22] is not None for row in runner_rows),
              sum(row[21] is not None for row in runner_rows))
    conn.execute('SAVEPOINT gbgb_normalize_meeting')
    try:
        conn.execute('DELETE FROM gbgb_runners WHERE race_id IN (SELECT race_id FROM gbgb_races WHERE meeting_id=?)', (mid,))
        conn.execute('DELETE FROM gbgb_races WHERE meeting_id=?', (mid,))
        conn.executemany('''INSERT INTO gbgb_races VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', race_rows)
        conn.executemany('''INSERT INTO gbgb_runners VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', runner_rows)
        conn.execute('''INSERT INTO gbgb_meeting_imports VALUES (?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(meeting_id) DO UPDATE SET
              json_sha256=excluded.json_sha256, imported_utc=excluded.imported_utc,
              race_count=excluded.race_count, trap_count=excluded.trap_count,
              result_count=excluded.result_count,timed_count=excluded.timed_count,
              first_sectional_count=excluded.first_sectional_count,
              warning_count=excluded.warning_count,warning_examples=excluded.warning_examples''',
             (mid, digest, imported_utc, *counts, len(warnings),
              json.dumps(warnings[:30], ensure_ascii=False)))
        conn.execute('RELEASE SAVEPOINT gbgb_normalize_meeting')
        conn.commit()
    except Exception:
        conn.execute('ROLLBACK TO SAVEPOINT gbgb_normalize_meeting')
        conn.execute('RELEASE SAVEPOINT gbgb_normalize_meeting')
        conn.rollback()
        raise
    return {'meeting': mid, 'state': 'imported', 'races': counts[0],
            'traps': counts[1], 'finished': counts[2],
            'timed': counts[3], 'sectionals': counts[4],
            'warnings': len(warnings)}


def backfill(conn, print_progress=True):
    ensure_schema(conn)
    if not table_exists(conn, 'gbgb_meetings'):
        raise NormalizationError('No staged meeting table; run the GBGB pilot first')
    total = conn.execute('SELECT count(*) FROM gbgb_meetings').fetchone()[0]
    finished = changed = 0
    for mid, digest, json_raw in conn.execute('SELECT meeting_id,json_sha256,payload_json FROM gbgb_meetings ORDER BY meeting_id'):
        try:
            obj = json.loads(json_raw)
            result = normalize_meeting(conn, mid, obj, digest)
        except (NormalizationError, sqlite3.Error, json.JSONDecodeError) as exc:
            raise NormalizationError(f'Failed to map meeting {mid}: {exc}') from exc
        finished += 1
        if result['state'] == 'imported':
            changed += 1
        if print_progress and (finished <= 3 or finished % 50 == 0 or finished == total):
            print(f'  [{finished}/{total}] meeting {mid}: {result["state"]}', flush=True)
    return total, changed


def table_exists(conn, name):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def coverage_report(conn, outdir=DEFAULT_REPORT):
    ensure_schema(conn)
    outdir.mkdir(parents=True, exist_ok=True)
    names = ['gbgb_meetings', 'gbgb_meeting_imports', 'gbgb_races', 'gbgb_runners']
    counts = {t: conn.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in names}
    raw_matches = conn.execute('''SELECT COUNT(*) FROM gbgb_meetings m
      JOIN gbgb_meeting_imports i ON m.meeting_id=i.meeting_id AND m.json_sha256=i.json_sha256''').fetchone()[0]
    metric = conn.execute('''SELECT
       SUM(CASE WHEN dog_id IS NOT NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN finish_position IS NOT NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN run_time_s IS NOT NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN sectional_time_s IS NOT NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN adjusted_time_s IS NOT NULL THEN 1 ELSE 0 END)
       FROM gbgb_runners''').fetchone()
    metric = tuple(i or 0 for i in metric)
    tracks = conn.execute('''SELECT r.track_name, COUNT(DISTINCT r.race_id), COUNT(u.trap_number),
       SUM(CASE WHEN u.finish_position IS NOT NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN u.run_time_s IS NOT NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN u.sectional_time_s IS NOT NULL THEN 1 ELSE 0 END)
       FROM gbgb_races r LEFT JOIN gbgb_runners u ON r.race_id=u.race_id
       GROUP BY r.track_name ORDER BY r.track_name''').fetchall()
    dates = conn.execute('''SELECT r.race_date,COUNT(DISTINCT r.race_id),COUNT(u.trap_number),
       SUM(CASE WHEN u.run_time_s IS NOT NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN u.sectional_time_s IS NOT NULL THEN 1 ELSE 0 END)
       FROM gbgb_races r LEFT JOIN gbgb_runners u ON r.race_id=u.race_id
       GROUP BY r.race_date ORDER BY r.race_date''').fetchall()
    with (outdir/'gbgb_track_coverage.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer=csv.writer(f)
        writer.writerow(['track','races','traps','finishing_positions','individual_times','first_sectionals'])
        writer.writerows(tracks)
    with (outdir/'gbgb_normalized_dates.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer=csv.writer(f)
        writer.writerow(['race_date','races','traps','individual_times','first_sectionals'])
        writer.writerows(dates)
    indexes = 0
    missing_index = None
    missing_meeting = None
    if table_exists(conn, 'gbgb_index'):
        indexes = conn.execute('SELECT count(DISTINCT race_id) FROM gbgb_index WHERE race_id IS NOT NULL').fetchone()[0]
        missing_rows = conn.execute('''SELECT r.race_id,r.meeting_id,r.race_date,r.track_name,r.race_distance_m
            FROM gbgb_races r WHERE NOT EXISTS
            (SELECT 1 FROM gbgb_index i WHERE i.race_id=r.race_id)
            ORDER BY r.race_date,r.track_name,r.race_id''').fetchall()
        missing_index = len(missing_rows)
        with (outdir/'gbgb_meeting_races_missing_from_index.csv').open(
                'w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f)
            writer.writerow(['race_id','meeting_id','race_date','track','race_distance_m'])
            writer.writerows(missing_rows)
        missing_meeting = conn.execute('''SELECT COUNT(DISTINCT i.race_id) FROM gbgb_index i
            WHERE i.race_id IS NOT NULL AND NOT EXISTS
            (SELECT 1 FROM gbgb_races r WHERE r.race_id=i.race_id)''').fetchone()[0]
    text = [
        'GBGB NORMALISED DATABASE QUALITY REPORT',
        'Data from locally stored GBGB meeting JSON; no network requests.',
        '',
        f'Staged meeting JSON: {counts["gbgb_meetings"]:,}',
        f'Mapped meetings matching source digest: {raw_matches:,}',
        f'Normalised races: {counts["gbgb_races"]:,}',
        f'Trap records (including vacancies/non-runners): {counts["gbgb_runners"]:,}',
        f'Trap records with dog IDs: {metric[0]:,}',
        f'Finishing position records: {metric[1]:,}',
        f'Individual finishing times: {metric[2]:,}',
        f'First sectional times: {metric[3]:,}',
        f'Source adjusted times: {metric[4]:,}',
        f'Distinct race IDs in index: {indexes:,}',
        f'Normalised race IDs absent from index: {missing_index if missing_index is not None else "unavailable"}',
        f'Indexed race IDs absent from normalised meetings: {missing_meeting if missing_meeting is not None else "unavailable"}',
        '',
        'Important: resultSectionalTime is a FIRST SPLIT, not measured last-section speed.',
        'Its physical marker distance is not supplied by the meeting JSON.',
        'raceGoing and resultBtnDistance are preserved raw, not converted into seconds.',
        'Adjusted time is the GBGB-provided value, not our own derived rating.',
        'Races and traps without a recorded result are retained, not filled with fabricated results.',
        'Review differences between index and meeting race counts before making coverage claims.',
        'Track standards and speed figures have NOT been calculated.',
    ]
    if raw_matches != counts['gbgb_meetings']:
        text.append('WARNING: one or more staged meetings have not been mapped to this source version.')
    if missing_index or missing_meeting:
        text.append('WARNING: date-index/meeting race ID mismatch; inspect gbgb_meeting_races_missing_from_index.csv.')
    (outdir/'gbgb_normalized_quality.txt').write_text('\n'.join(text)+'\n', encoding='utf-8')
    return {'counts': counts, 'matched_meetings': raw_matches,
            'times': metric[2], 'sectionals': metric[3], 'reports': outdir,
            'missing_index': missing_index}


def main(argv=None):
    p = argparse.ArgumentParser(description='Local GBGB race & runner normaliser (no web requests)')
    sub = p.add_subparsers(dest='command', required=True)
    for n in ('backfill', 'report'):
        parser=sub.add_parser(n)
        parser.add_argument('--db', type=Path, default=DEFAULT_DB)
        parser.add_argument('--out', type=Path, default=DEFAULT_REPORT)
    args=p.parse_args(argv)
    if not args.db.is_file():
        print('No existing GBGB database found. Run RUN_GBGB_PILOT.bat first.')
        return 2
    try:
        with contextlib.closing(sqlite3.connect(args.db, timeout=45)) as conn:
            conn.execute('PRAGMA busy_timeout=45000')
            conn.execute('PRAGMA journal_mode=WAL')
            if args.command == 'backfill':
                total, changed = backfill(conn)
                print(f'GBGB: checked {total} staged meetings; mapped/updated {changed}.', flush=True)
            stats = coverage_report(conn, args.out)
        print(f'Normalised races: {stats["counts"]["gbgb_races"]:,} | '
              f'traps: {stats["counts"]["gbgb_runners"]:,} | '
              f'individual times: {stats["times"]:,} | '
              f'first sectionals: {stats["sectionals"]:,}',flush=True)
        print('Quality reports:', args.out.resolve(), flush=True)
        return 0
    except (sqlite3.Error, NormalizationError, OSError, ValueError) as exc:
        print(f'GBGB NORMALISER STOPPED: {exc}',flush=True)
        return 2

if __name__ == '__main__':
    sys.exit(main())
