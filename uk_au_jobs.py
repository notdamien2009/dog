#!/usr/bin/env python3
"""DOG RACE | Two independent jobs: Betfair UK historical markets and Australian official performances.

Safe design: read-only provider requests, originals on disk, idempotent local SQLite,
measured source coverage (never assumed), no live wagering. Python 3.10+.
"""
from __future__ import annotations

import argparse
import bz2
import calendar
import csv
import datetime as dt
import hashlib
import html
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import random
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
DATA = HERE / 'data'
RAW = DATA / 'raw'
DB = DATA / 'dog_uk_au.sqlite3'
REPORTS = DATA / 'reports'
BETFAIR = 'https://historicdata.betfair.com/api'
RQ_URL = 'https://www.racingqueensland.com.au/industry/greyhound/greyhound-sectionals'
JURISDICTIONS = ('NSW', 'QLD', 'VIC', 'SA', 'WA', 'TAS', 'NT')
START = dt.date(2023, 11, 18)
END = dt.date(2026, 11, 17)


def log(msg: str):
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def err_msg(ex):
    if isinstance(ex, urllib.error.HTTPError):
        return f'HTTP {ex.code} {ex.reason}'
    return str(ex)


def norm_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value[:10])


def date_windows(start: dt.date, end: dt.date):
    """Yield inclusive calendar-month windows clipped exactly to desired range."""
    cur = dt.date(start.year, start.month, 1)
    while cur <= end:
        last = dt.date(cur.year, cur.month, calendar.monthrange(cur.year, cur.month)[1])
        yield max(start, cur), min(end, last)
        cur = (last + dt.timedelta(days=1)).replace(day=1)


def connection():
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=40)
    db.execute('PRAGMA busy_timeout=40000')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS source_files (
            job TEXT NOT NULL, source_key TEXT NOT NULL, path TEXT NOT NULL,
            bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, downloaded_utc TEXT NOT NULL,
            parsed_rows INTEGER DEFAULT 0, PRIMARY KEY(job, source_key));
        CREATE TABLE IF NOT EXISTS job_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL,
            source_key TEXT, status TEXT NOT NULL, details TEXT,
            at_utc TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bf_markets (
            market_id TEXT PRIMARY KEY, market_start TEXT, event_id TEXT,
            venue TEXT, country TEXT, event_name TEXT, market_name TEXT,
            market_type TEXT, status TEXT, inplay_seen INTEGER DEFAULT 0,
            source_key TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bf_runners (
            market_id TEXT NOT NULL, selection_id TEXT NOT NULL,
            runner_name TEXT, runner_status TEXT, bsp REAL,
            source_key TEXT NOT NULL,
            PRIMARY KEY(market_id, selection_id));
        CREATE TABLE IF NOT EXISTS au_topaz_runs (
            jurisdiction TEXT NOT NULL, run_id TEXT NOT NULL, race_id TEXT,
            dog_id TEXT, dog_name TEXT, meeting_date TEXT, track TEXT,
            distance_m REAL, race_number INTEGER, box_number INTEGER,
            finish_position INTEGER, finish_time_s REAL, beaten_lengths REAL,
            margin_text TEXT, weight_kg REAL, first_split_s REAL,
            second_split_s REAL, starting_price REAL, race_grade TEXT,
            comments TEXT, source_key TEXT NOT NULL, original_json TEXT NOT NULL,
            PRIMARY KEY(jurisdiction, run_id));
        CREATE INDEX IF NOT EXISTS idx_au_date ON au_topaz_runs(meeting_date);
        CREATE INDEX IF NOT EXISTS idx_au_race ON au_topaz_runs(race_id);
        CREATE TABLE IF NOT EXISTS au_qld_sectional_rows (
            source_key TEXT NOT NULL, row_no INTEGER NOT NULL,
            source_date TEXT, source_track TEXT, raw_json TEXT NOT NULL,
            PRIMARY KEY(source_key,row_no));
        CREATE INDEX IF NOT EXISTS idx_qld_date ON au_qld_sectional_rows(source_date);
    ''')
    db.commit()
    return db


def event(db, job, source_key, status, details=''):
    with db:
        db.execute('INSERT INTO job_events(job,source_key,status,details,at_utc) VALUES(?,?,?,?,?)',
                   (job, source_key, status, str(details)[:1000], dt.datetime.now(dt.timezone.utc).isoformat()))


def atomic_write(target: Path, data: bytes):
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_suffix(target.suffix + '.part')
    with staging.open('wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(staging, target)


def record_file(db, job, key, path, n):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with db:
        db.execute('''INSERT INTO source_files VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(job,source_key) DO UPDATE SET path=excluded.path,
            bytes=excluded.bytes,sha256=excluded.sha256,
            downloaded_utc=excluded.downloaded_utc,parsed_rows=excluded.parsed_rows''',
            (job, key, str(path.relative_to(HERE)), path.stat().st_size,
             digest, dt.datetime.now(dt.timezone.utc).isoformat(), n))


def request_bytes(url, *, method='GET', payload=None, headers=None, attempts=3, delay=2.0):
    h = {'User-Agent': 'DOG-RACE-ResearchCollector/2.0 (personal research; courteous downloads)',
         'Accept': 'application/json,text/html,text/csv,application/octet-stream,*/*'}
    if headers:
        h.update(headers)
    body = None if payload is None else json.dumps(payload).encode('utf-8')
    if body is not None:
        h['Content-Type'] = 'application/json'
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, body, h, method=method)
            with urllib.request.urlopen(req, timeout=90) as res:
                return res.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise RuntimeError(f'{exc.code}: authentication/permission denied for {url.split("?")[0]}') from exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts:
                raise
            retry_after = exc.headers.get('Retry-After')
            try:
                wait = min(120, max(delay * attempt, float(retry_after)))
            except (TypeError, ValueError):
                wait = delay * attempt * 4
            log(f'HTTP {exc.code}: cooling down {wait:.1f}s ({attempt}/{attempts})')
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts:
                raise
            time.sleep(delay * attempt)
    raise RuntimeError('request failed')


def bf_filters(start, end):
    return {
        'sport': 'Greyhound Racing', 'plan': 'Basic Plan',
        'fromDay': start.day, 'fromMonth': start.month, 'fromYear': start.year,
        'toDay': end.day, 'toMonth': end.month, 'toYear': end.year,
        'eventId': None, 'eventName': None,
        'marketTypesCollection': ['WIN', 'PLACE'],
        'countriesCollection': ['GB'], 'fileTypeCollection': ['M']
    }


def bf_post(operation, token, payload=None):
    if not token:
        raise RuntimeError('Missing Betfair session token (DOG_BETFAIR_SSOID)')
    raw = request_bytes(f'{BETFAIR}/{operation}', method='POST', payload=payload,
                        headers={'ssoid': token})
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError(f'{operation}: unexpected response (not JSON); check account login')
    if isinstance(result, dict) and ('error' in result or 'exception' in result):
        raise RuntimeError(f'{operation}: {str(result)[:250]}')
    return result


def bf_get_file(file_path, token):
    # Never accept arbitrary download hosts or arbitrary filesystem paths.
    if not re.fullmatch(r'/data/xds/historic/[A-Za-z0-9_./-]+\.bz2', file_path):
        raise ValueError(f'Rejected unexpected Betfair source path: {file_path[:120]}')
    u = f'{BETFAIR}/DownloadFile?' + urllib.parse.urlencode({'filePath': file_path})
    raw = request_bytes(u, headers={'ssoid': token}, attempts=4)
    if not raw.startswith(b'BZh'):
        raise ValueError('Betfair returned a non-BZip2 file: token may be expired')
    return raw


def parse_bf_file(raw):
    """Conservative market metadata/settlement mapper for Betfair BASIC stream.

    Never claims market-winning status without marketDefinition runner results;
    BSP is only captured when a numeric actual SP is in stream runner changes.
    """
    markets = {}
    runner_meta = {}
    actual_bsp = {}
    inplay = set()
    seen = 0
    try:
        stream = bz2.BZ2File(io.BytesIO(raw))
        for line in stream:
            if not line.strip():
                continue
            d = json.loads(line)
            if not isinstance(d, dict) or d.get('op') != 'mcm':
                continue
            for mc in d.get('mc') or []:
                mid = str(mc.get('id') or '')
                if not mid:
                    continue
                seen += 1
                md = mc.get('marketDefinition') or {}
                if md:
                    prev = markets.get(mid, {})
                    prev.update({k: v for k, v in md.items() if v is not None})
                    markets[mid] = prev
                    for r in md.get('runners') or []:
                        sid = str(r.get('id') or '')
                        if sid:
                            runner_meta[(mid, sid)] = {**runner_meta.get((mid,sid), {}), **r}
                    if md.get('inPlay'):
                        inplay.add(mid)
                for r in mc.get('rc') or []:
                    sid = str(r.get('id') or '')
                    if not sid:
                        continue
                    # 'spn' is a projected NEAR SP, never actual BSP.
                    # Actual BSP (when published) lives in marketDefinition.runners[].bsp.
                    pass
    except (OSError, EOFError, ValueError, UnicodeDecodeError) as ex:
        raise ValueError(f'Invalid Betfair BZip2 stream: {ex}') from ex
    if seen == 0 or not markets or not runner_meta:
        raise ValueError('Betfair BZip2 stream lacks valid market definitions or runner information')
    for pair,r in runner_meta.items():
        bsp = r.get('bsp')
        if isinstance(bsp, (int,float)) and 1 < bsp < 1001:
            actual_bsp[pair] = float(bsp)
    return markets, runner_meta, actual_bsp, inplay


def import_bf(db, key, path):
    markets, runners, bsps, inplay = parse_bf_file(path.read_bytes())
    with db:
        for mid, d in markets.items():
            db.execute('''INSERT INTO bf_markets VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(market_id) DO UPDATE SET
                market_start=excluded.market_start,event_id=excluded.event_id,
                venue=excluded.venue,country=excluded.country,event_name=excluded.event_name,
                market_name=excluded.market_name,market_type=excluded.market_type,
                status=excluded.status,inplay_seen=excluded.inplay_seen,source_key=excluded.source_key''',
                (mid, d.get('marketTime'), str(d.get('eventId') or ''), d.get('venue'),
                 d.get('countryCode'), d.get('eventName'), d.get('name'), d.get('marketType'),
                 d.get('status'), int(mid in inplay), key))
        for (mid,sid), r in runners.items():
            db.execute('''INSERT INTO bf_runners VALUES(?,?,?,?,?,?)
                ON CONFLICT(market_id,selection_id) DO UPDATE SET
                runner_name=excluded.runner_name,runner_status=excluded.runner_status,
                bsp=excluded.bsp,source_key=excluded.source_key''',
                (mid, sid, r.get('name'), r.get('status'), bsps.get((mid,sid)), key))
    return len(markets), len(runners)


def collect_uk(db, start, end, token, *, pilot=False, limit_files=0, delay=0.4):
    job = 'betfair_uk'
    if not token:
        log('UK BETFAIR: SKIPPED - no DOG_BETFAIR_SSOID. Add free Basic data to My Data first.')
        return 'skipped'
    try:
        mydata = bf_post('GetMyData', token)
        owned = [x for x in mydata if isinstance(x,dict) and
                 'greyhound' in str(x.get('sport','')).lower() and
                 'basic' in str(x.get('plan','')).lower()]
        if not owned:
            log('UK BETFAIR: SKIPPED - no Greyhound Racing Basic package visible in My Data.')
            event(db,job,'','no_entitlement','Acquire Basic months in Betfair My Data')
            return 'skipped'
    except Exception as ex:
        log(f'UK BETFAIR: ERROR during entitlement check: {err_msg(ex)}')
        event(db,job,'','error',err_msg(ex))
        return 'failed'
    total=0; fetched=0; errors=0; files_seen=set()
    for lo,hi in date_windows(start,end):
        manifest = RAW/'betfair_uk'/'manifests'/f'{lo:%Y-%m}_{lo.day:02d}-{hi.day:02d}.json'
        if manifest.exists():
            try: file_list = json.loads(manifest.read_text(encoding='utf-8'))
            except (ValueError,OSError): file_list = None
        else:
            file_list = None
        if file_list is None:
            try:
                file_list = bf_post('DownloadListOfFiles',token,bf_filters(lo,hi))
                if not isinstance(file_list,list): raise ValueError('Expected a file path list')
                file_list = [p for p in file_list if isinstance(p,str)]
                atomic_write(manifest,json.dumps(file_list,indent=2).encode())
            except Exception as ex:
                log(f'UK BETFAIR {lo}..{hi}: file discovery failed: {err_msg(ex)}')
                event(db,job,f'{lo:%Y-%m}','error',err_msg(ex)); errors+=1
                continue
        log(f'UK BETFAIR {lo}..{hi}: {len(file_list)} market files in authorised archive')
        if not file_list:
            event(db,job,f'{lo:%Y-%m}','empty','API returned zero files; not proof no racing')
        for i,key in enumerate(file_list,1):
            if key in files_seen: continue
            files_seen.add(key)
            mid = Path(key).name
            # Use a short hash to ensure unique names across event IDs.
            out = RAW/'betfair_uk'/f'{lo:%Y-%m}'/(hashlib.sha1(key.encode()).hexdigest()[:12]+'_'+mid)
            try:
                if not out.exists() or out.stat().st_size<10:
                    raw = bf_get_file(key, token)
                    atomic_write(out, raw); fetched+=1
                    time.sleep(delay)
                else:
                    raw = out.read_bytes()
                    if not raw.startswith(b'BZh'):
                        raise ValueError('Cached file invalid; remove this specific file to retry')
                m, runners=import_bf(db,key,out)
                record_file(db,job,key,out,runners)
                total+=1
                if i == 1 or i%25==0 or i==len(file_list):
                    log(f'UK BETFAIR [{i}/{len(file_list)}] {lo:%Y-%m}: '
                        f'saved/verified {total} files; latest {m} markets, {runners} runners; new downloads {fetched}')
            except Exception as ex:
                errors+=1; event(db,job,key,'error',err_msg(ex))
                log(f'UK BETFAIR ERROR {mid}: {err_msg(ex)}')
                if 'authentication' in str(ex).lower() or 'permission' in str(ex).lower():
                    return 'failed'
            if limit_files and total>=limit_files:
                log(f'UK BETFAIR pilot limit reached ({total} files)')
                return 'failed' if errors else 'ok'
        if pilot and not file_list:
            break
    log(f'UK BETFAIR SUMMARY: {total} imported files, {fetched} newly downloaded, {errors} errors')
    return 'failed' if errors else 'ok'


class RQLinks(HTMLParser):
    """Only extracts URLs actually advertised on Queensland's sectional index page."""
    def __init__(self):
        super().__init__(); self.links=[]
    def handle_starttag(self, tag, attrs):
        if tag != 'a': return
        href = dict(attrs).get('href') or ''
        link = urllib.parse.urljoin('https://www.racingqueensland.com.au',html.unescape(href))
        if '/RacingFile.ashx?' not in link or '.csv' not in link.lower(): return
        parsed=urllib.parse.urlparse(link)
        if parsed.hostname not in ('www.racingqueensland.com.au','racingqueensland.com.au'):
            return
        src=urllib.parse.parse_qs(parsed.query).get('path',[''])[0]
        m=re.search(r'(?P<date>20\d{6})_(?P<track>[A-Z0-9]+)_G\.csv$',src,re.I)
        if m:
            try: d=dt.datetime.strptime(m.group('date'),'%Y%m%d').date()
            except ValueError: return
            self.links.append((d,m.group('track').upper(),link))


def discover_qld():
    parser=RQLinks()
    parser.feed(request_bytes(RQ_URL).decode('utf-8',errors='replace'))
    # Date+track as key (in case the index repeats link).
    dedup={ (d,code):url for d,code,url in parser.links }
    return sorted(((d,code,url) for (d,code),url in dedup.items()),reverse=True)


def parse_qld_csv(contents):
    txt=contents.decode('utf-8-sig',errors='replace')
    # RQ sometimes uses a descriptive banner before the actual CSV header;
    # preserve the source rows rather than pretending every row is a runner.
    rows=list(csv.reader(io.StringIO(txt)))
    return rows


def collect_qld(db,start,end,*,pilot=False,limit_files=0,delay=1.0):
    job='au_qld_sectionals'; total=0; new=0; errors=0
    try: links=discover_qld()
    except Exception as ex:
        log('AU QLD: ERROR loading official sectionals listing: '+err_msg(ex))
        event(db,job,'','error',err_msg(ex))
        return 'failed'
    eligible=[(d,t,u) for d,t,u in links if start<=d<=end]
    log(f'AU QLD: {len(eligible)} listed CSV reports for {start}..{end}; '
        f'index holds {len(links)} total reports')
    if not eligible:
        event(db,job,'','no_coverage','No official sectional CSV links in requested range')
        return 'no_coverage'
    for i,(date,track,url) in enumerate(eligible,1):
        key=f'{date:%Y%m%d}_{track}_G.csv'
        out=RAW/'au_qld'/f'{date:%Y}'/key
        try:
            if not out.exists() or out.stat().st_size<10:
                raw=request_bytes(url); atomic_write(out,raw); new+=1; time.sleep(delay)
            else: raw=out.read_bytes()
            rows=parse_qld_csv(raw)
            if len(rows)<2:
                raise ValueError('Sectional CSV empty or malformed')
            # Store original row arrays losslessly. No speculative sectional mapping.
            with db:
                db.execute('DELETE FROM au_qld_sectional_rows WHERE source_key=?',(key,))
                for pos,row in enumerate(rows,1):
                    db.execute('INSERT INTO au_qld_sectional_rows VALUES(?,?,?,?,?)',
                               (key,pos,date.isoformat(),track,json.dumps(row,ensure_ascii=False)))
            record_file(db,job,key,out,len(rows))
            total+=1
            if i==1 or i%25==0 or i==len(eligible):
                log(f'AU QLD [{i}/{len(eligible)}]: {key}: {len(rows)} original CSV lines; '
                    f'{total} reports stored, {new} downloaded')
        except Exception as ex:
            errors+=1; event(db,job,key,'error',err_msg(ex))
            log(f'AU QLD ERROR {key}: {err_msg(ex)}')
            if isinstance(ex,RuntimeError) and '403' in str(ex):
                return 'failed'
        if limit_files and total>=limit_files: break
    log(f'AU QLD SUMMARY: {total} reports saved/verified, {new} newly downloaded, {errors} errors')
    return 'failed' if errors else 'ok'


def number(v, *, positive=False):
    if v is None or str(v).strip() in ('','-','N/A','NBT','nan','None'): return None
    try: result=float(v)
    except (TypeError,ValueError): return None
    if not (-1e10<result<1e10): return None
    if positive and result<=0:return None
    return result


def integer(v):
    f=number(v)
    return int(f) if f is not None and int(f)==f else None


def first(row, *names):
    for name in names:
        v=row.get(name)
        if v is not None and str(v).strip()!='': return v
    return None


def parse_lengths(v):
    if v is None: return None
    txt=str(v).strip().replace('½','.5').replace('¼','.25').replace('¾','.75')
    m=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(?:L|lengths?)?\s*',txt,re.I)
    return float(m.group(1)) if m else None


def import_topaz_csv(db,path,jurisdiction,source_key,start,end):
    with path.open('r',encoding='utf-8-sig',newline='') as fp:
        reader=csv.DictReader(fp)
        if not reader.fieldnames or 'meetingDate' not in reader.fieldnames:
            raise ValueError('Topaz file missing meetingDate header')
        # All-or-nothing DB import for a month's CSV.
        rows=0; times=0; skipped=0
        with db:
            for line_no,row in enumerate(reader,2):
                try: date=norm_date(row['meetingDate'])
                except (TypeError,ValueError):
                    skipped+=1;continue
                if not start<=date<=end:continue
                rid=first(row,'runId','runnerId')
                if rid is None:
                    # Missing runId cannot be uniquely tied to dog's racing performance.
                    skipped+=1;continue
                runner_id=str(rid)
                time_s=number(first(row,'resultTime','finishTime'),positive=True)
                margin_txt=first(row,'resultMarginLengths','marginLengths','beatenMargin')
                db.execute('''INSERT INTO au_topaz_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(jurisdiction,run_id) DO UPDATE SET
                    race_id=excluded.race_id,dog_id=excluded.dog_id,dog_name=excluded.dog_name,
                    meeting_date=excluded.meeting_date,track=excluded.track,distance_m=excluded.distance_m,
                    race_number=excluded.race_number,box_number=excluded.box_number,
                    finish_position=excluded.finish_position,finish_time_s=excluded.finish_time_s,
                    beaten_lengths=excluded.beaten_lengths,margin_text=excluded.margin_text,
                    weight_kg=excluded.weight_kg,first_split_s=excluded.first_split_s,
                    second_split_s=excluded.second_split_s,starting_price=excluded.starting_price,
                    race_grade=excluded.race_grade,comments=excluded.comments,
                    source_key=excluded.source_key,original_json=excluded.original_json''',
                    (jurisdiction,runner_id,str(first(row,'raceId') or ''),
                     str(first(row,'dogId') or ''),first(row,'dogName','greyhoundName'),
                     date.isoformat(),first(row,'track','trackName'),
                     number(first(row,'distance','distanceInMetres'),positive=True),
                     integer(row.get('raceNumber')),integer(first(row,'boxNumber','rugNumber')),
                     integer(first(row,'place','finishPosition')),time_s,
                     parse_lengths(margin_txt),margin_txt,
                     number(first(row,'weightInKg','dogWeight'),positive=True),
                     number(first(row,'firstSplitTime'),positive=True),
                     number(first(row,'secondSplitTime'),positive=True),
                     number(first(row,'startPrice'),positive=True),
                     first(row,'raceType','raceGrade'),first(row,'comment','runComment'),
                     source_key,json.dumps(row,ensure_ascii=False)))
                rows+=1;times+=int(time_s is not None)
    return rows,times,skipped


def collect_topaz(db,start,end,api_key,*,pilot=False,limit_files=0,delay=1.0):
    job='au_topaz'
    if not api_key:
        log('AU TOPAZ: SKIPPED - no DOG_TOPAZ_KEY. QLD public sectionals run separately.')
        return 'skipped'
    try:
        from topaz import TopazAPI
    except ImportError:
        log('AU TOPAZ: ERROR - install optional dependency with INSTALL_TOPAZ_SUPPORT.bat')
        return 'failed'
    api=TopazAPI(api_key)
    total=0; new=0; errors=0
    for code in JURISDICTIONS:
        for lo,hi in date_windows(start,end):
            key=f'{code}_{lo:%Y-%m}'
            out=RAW/'au_topaz'/code/f'{key}.csv'
            try:
                if not out.exists() or out.stat().st_size<20:
                    log(f'AU TOPAZ: requesting {code} {lo:%Y-%m} (official monthly bulk data)')
                    frame=api.get_bulk_runs_by_month(owning_authority_code=code,
                                                     year=lo.year,month=lo.month)
                    if frame is None or frame.empty:
                        event(db,job,key,'empty','Topaz returned no rows; do not mark complete')
                        log(f'AU TOPAZ: no rows {key}')
                        continue
                    out.parent.mkdir(parents=True,exist_ok=True)
                    part=out.with_suffix('.csv.part')
                    frame.to_csv(part,index=False)
                    os.replace(part,out)
                    new+=1;time.sleep(delay)
                rows,times,skipped=import_topaz_csv(db,out,code,key,start,end)
                record_file(db,job,key,out,rows)
                total+=1
                log(f'AU TOPAZ: {key} - {rows} runs, {times} finish times, '
                    f'{skipped} invalid rows; files {total}, new {new}')
            except Exception as ex:
                errors+=1; event(db,job,key,'error',err_msg(ex))
                log(f'AU TOPAZ ERROR {key}: {err_msg(ex)}')
                if '401' in str(ex) or '403' in str(ex):
                    log('AU TOPAZ: stopping after access denial')
                    return 'failed'
            if limit_files and total>=limit_files:
                return 'failed' if errors else 'ok'
        if pilot: break
    log(f'AU TOPAZ SUMMARY: {total} months saved/verified, {new} new, {errors} errors')
    return 'failed' if errors else 'ok'


def status(db):
    REPORTS.mkdir(parents=True,exist_ok=True)
    lines=['DOG RACE — UK BETFAIR + AU PERFORMANCE STATUS',
           f'Generated UTC: {dt.datetime.now(dt.timezone.utc).isoformat()}',
           f'Database: {DB}', '',
           'IMPORTANT: file counts are not proof of complete historical racing coverage.','']
    for job in ['betfair_uk','au_topaz','au_qld_sectionals']:
        n,total=db.execute('SELECT COUNT(*), COALESCE(SUM(parsed_rows),0) FROM source_files WHERE job=?',(job,)).fetchone()
        lines.append(f'{job}: {n} source files, {total} stored runner/CSV rows')
        latest=db.execute('SELECT status, COUNT(*) FROM job_events WHERE job=? GROUP BY status',(job,)).fetchall()
        if latest:lines.append('  status events: '+', '.join(f'{status}={n}' for status,n in latest))
    def query(label,sql):
        lines.append(label+': '+', '.join(str(v) for v in db.execute(sql).fetchone()))
    query('UK Betfair markets','SELECT COUNT(*) FROM bf_markets')
    query('UK Betfair runners','SELECT COUNT(*) FROM bf_runners')
    query('UK Betfair BSP prices','SELECT COUNT(*) FROM bf_runners WHERE bsp IS NOT NULL')
    query('AU Topaz results','SELECT COUNT(*) FROM au_topaz_runs')
    query('AU Topaz recorded finish times','SELECT COUNT(*) FROM au_topaz_runs WHERE finish_time_s IS NOT NULL')
    query('AU Topaz first splits','SELECT COUNT(*) FROM au_topaz_runs WHERE first_split_s IS NOT NULL')
    query('AU QLD raw CSV rows','SELECT COUNT(*) FROM au_qld_sectional_rows')
    lines.extend(['', 'DATE COVERAGE:'])
    for label,sql in [
        ('UK Betfair','SELECT MIN(substr(market_start,1,10)),MAX(substr(market_start,1,10)),COUNT(DISTINCT substr(market_start,1,10)) FROM bf_markets'),
        ('AU Topaz','SELECT MIN(meeting_date),MAX(meeting_date),COUNT(DISTINCT meeting_date) FROM au_topaz_runs'),
        ('AU QLD','SELECT MIN(source_date),MAX(source_date),COUNT(DISTINCT source_date) FROM au_qld_sectional_rows')]:
        query(label,sql)
    lines.extend(['','NEXT CHECKS:',
                  '1. Verify Betfair My Data includes each desired UK Greyhound Basic month.',
                  '2. Confirm number of QLD source CSV reports by date/track and actual field coverage.',
                  '3. To cover Australian states comprehensively, obtain Topaz or authorised equivalents.',
                  '4. Validate Betfair UK markets against GBGB by venue, race date, off time and runner.',
                  '5. Do not compute speed/strength ratings from price-only records.'])
    report='\n'.join(lines)+'\n'
    (REPORTS/'uk_au_collection_status.txt').write_text(report,encoding='utf-8')
    with (REPORTS/'uk_au_monthly_coverage.csv').open('w',newline='',encoding='utf-8') as fh:
        writer=csv.writer(fh)
        writer.writerow(['year_month','UK_Betfair_markets','AU_Topaz_runs','AU_Topaz_finish_times','AU_QLD_source_reports'])
        month_keys={f'{x:%Y-%m}' for x,_ in date_windows(START,END)}
        for month in sorted(month_keys):
            uk=db.execute('SELECT COUNT(*) FROM bf_markets WHERE substr(market_start,1,7)=?',(month,)).fetchone()[0]
            au= db.execute('SELECT COUNT(*), SUM(CASE WHEN finish_time_s IS NOT NULL THEN 1 ELSE 0 END) '
                           'FROM au_topaz_runs WHERE substr(meeting_date,1,7)=?',(month,)).fetchone()
            qld=db.execute('SELECT COUNT(*) FROM source_files WHERE job=? AND substr(source_key,1,6)=?',
                           ('au_qld_sectionals',month.replace('-',''))).fetchone()[0]
            writer.writerow([month,uk,au[0],au[1] or 0,qld])
    with (REPORTS/'au_topaz_by_state.csv').open('w',newline='',encoding='utf-8') as fh:
        writer=csv.writer(fh)
        writer.writerow(['state','runners','finish_times','first_split_times','months_with_runs'])
        for code in JURISDICTIONS:
            row=db.execute('''SELECT COUNT(*),SUM(CASE WHEN finish_time_s IS NOT NULL THEN 1 ELSE 0 END),
                          SUM(CASE WHEN first_split_s IS NOT NULL THEN 1 ELSE 0 END),
                          COUNT(DISTINCT substr(meeting_date,1,7)) FROM au_topaz_runs WHERE jurisdiction=?''',(code,)).fetchone()
            writer.writerow([code,row[0],row[1] or 0,row[2] or 0,row[3]])
    print('\n'+report)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Two jobs: UK Betfair odds and Australian performance.')
    parser.add_argument('command',choices=['pilot','collect','status'])
    parser.add_argument('--start',default=START.isoformat())
    parser.add_argument('--end',default=END.isoformat())
    parser.add_argument('--uk-limit',type=int,default=2)
    parser.add_argument('--au-limit',type=int,default=2)
    parser.add_argument('--uk',action='store_true',help='Run only the UK Betfair job')
    parser.add_argument('--au',action='store_true',help='Run only the AU performance job')
    args=parser.parse_args(argv)
    try: start=dt.date.fromisoformat(args.start);end=dt.date.fromisoformat(args.end)
    except ValueError:parser.error('Dates must be YYYY-MM-DD')
    if start>end:parser.error('Start must not exceed end')
    db=connection()
    try:
        if args.command=='status': status(db);return 0
        pilot=args.command=='pilot'
        if pilot:
            # date-scoped pilot for UK Betfair and selected live Queensland report period.
            log('PILOT MODE: only a few files, never a full-history run')
        log(f'Collection window: {start} to {end}. Prior source data and GBGB database untouched.')
        results=[]
        if not args.au:
            key=os.environ.get('DOG_BETFAIR_SSOID','').strip()
            results.append(collect_uk(db,start,end,key,pilot=pilot,
                                      limit_files=args.uk_limit if pilot else 0))
        if not args.uk:
            qld_start=max(start,dt.date(2026,11,1)) if pilot else start
            qld_end=min(end,dt.date(2026,11,18)) if pilot else end
            if qld_start<=qld_end:
                results.append(collect_qld(db,qld_start,qld_end,pilot=pilot,
                                           limit_files=args.au_limit if pilot else 0))
            results.append(collect_topaz(db,start,end,os.environ.get('DOG_TOPAZ_KEY','').strip(),
                                         pilot=pilot,limit_files=1 if pilot else 0))
        status(db)
        if 'failed' in results:
            log('Finished with errors. Other successful job results were retained; rerun to resume.')
            return 1
        if any(x in ('skipped','no_coverage') for x in results):
            log('PARTIAL COLLECTION ONLY: at least one source was skipped or has no coverage.')
            log('Other source data was saved; fix access/coverage and rerun to fill gaps.')
            return 2
        log('Available-source jobs completed. Review track/date gaps; full racing coverage is not guaranteed.')
        return 0
    finally:
        db.close()


if __name__=='__main__':
    try: sys.exit(main())
    except KeyboardInterrupt:
        log('Interrupted safely. Rerun to resume; completed files and rows are retained.')
        sys.exit(130)
