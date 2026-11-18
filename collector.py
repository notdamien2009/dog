#!/usr/bin/env python3
"""DOG RACE: conservative public-download collector. Python 3.9+, stdlib only.
Sources are publisher-linked downloads, not hidden endpoints or login bypasses.
"""
from __future__ import annotations
import contextlib
import argparse
import csv
import datetime as dt
import hashlib
import html
from html.parser import HTMLParser
import io
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, unquote, urlparse
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parent
RAW = ROOT / 'data' / 'raw'
DB = ROOT / 'data' / 'dog_race.sqlite3'
LOG_DIR = ROOT / 'data' / 'logs'
BETFAIR_INDEX = 'https://betfair-datascientists.github.io/data/dataListing/'
RQ_INDEX = 'https://www.racingqueensland.com.au/industry/greyhound/greyhound-sectionals'
TRAPSTATS_ZIP = 'https://trapstats.co.uk/api/v1/open-data/latest.zip'
PANDA_BASE = 'https://raw.githubusercontent.com/bamboostack/au-racing-open-data/main/datasets'
PANDA_PATHS = {
    'qld_sectionals': 'greyhound_sectionals_qld/greyhound_sectionals_qld.csv',
    'track_geometry': 'track_geometry/track_geometry.csv',
    'vic_weights': 'greyhound_weights_vic/greyhound_weights_vic.csv',
}
DEFAULT_START, DEFAULT_END = '2023-11-18', '2026-11-17'
MAX_BYTES = 2_000_000_000  # limit each compressed file to 2 GB
MAX_UNZIPPED_MEMBER = 2_000_000_000
AGENT = 'DOG-RACE-Collector/1.0 (public-download research; polite request pacing)'

class DownloadError(Exception):
    pass

class HTMLLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.text = ''
        self.anchor = None
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.anchor = dict(attrs).get('href', '')
            self.anchor_text = ''
        elif tag in ('li', 'div', 'p', 'section', 'br', 'h2', 'h3'):
            self.text += ' '
    def handle_data(self, data):
        self.text += data
        if self.anchor is not None:
            self.anchor_text += data
    def handle_endtag(self, tag):
        if tag == 'a' and self.anchor is not None:
            self.links.append((self.anchor, self.anchor_text.strip(), self.text[-320:]))
            self.anchor = None
        elif tag in ('li','p','div','section'):
            self.text += ' '

def normalize_key(value):
    return re.sub(r'[^a-z0-9]', '', str(value or '').lower())

def value_of(row, *names):
    norm = {normalize_key(k): v for k, v in row.items() if k is not None}
    for name in names:
        val = norm.get(normalize_key(name))
        if val is not None and str(val).strip() != '':
            return str(val).strip()
    return None

def to_iso(value):
    if not value:
        return None
    s = str(value).strip()
    for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%d/%m/%Y', '%d-%m-%Y', '%d/%m/%y', '%Y/%m/%d'):
        try: return dt.datetime.strptime(s[:len(dt.datetime.now().strftime(fmt))], fmt).date().isoformat()
        except ValueError: pass
    # Common ISO date prefixes, including timezone-bearing timestamps
    match = re.match(r'^(20\d\d-\d\d-\d\d)', s)
    return match.group(1) if match else None

def float_or_none(value):
    try: return float(str(value).replace(',', '')) if value not in (None, '', 'nan', 'NaN') else None
    except (ValueError, TypeError): return None

def in_range(date_s, start, end):
    return date_s is None or start <= date_s <= end

def parse_betfair_links(page: str, start: str, end: str):
    parser = HTMLLinks(); parser.feed(page)
    items = {}
    # Only linked files on publisher's official listing; never guess unavailable months.
    for href, _, _ in parser.links:
        url = urljoin(BETFAIR_INDEX, html.unescape(href))
        if urlparse(url).netloc != 'betfair-datascientists.github.io':
            continue
        match = re.search(r'ANZ_Greyhounds_(20\d\d)(?:_(\d\d))?\.(zip|csv)(?:\?.*)?$', url, flags=re.I)
        if not match: continue
        year, month, ext = match.groups(); year = int(year)
        lo = f'{year:04d}-{int(month):02d}-01' if month else f'{year:04d}-01-01'
        hi = (dt.date(year, int(month), 1).replace(day=28)+dt.timedelta(days=4)).replace(day=1)-dt.timedelta(days=1) if month else dt.date(year, 12, 31)
        if hi.isoformat() < start or lo > end: continue
        key = f'ANZ_Greyhounds_{year:04d}' + (f'_{month}' if month else '')
        items[key] = ('betfair_anz', key, url, ext)
    return sorted(items.values(), key=lambda t: t[1])

def parse_rq_links(page: str, start: str, end: str):
    parser = HTMLLinks(); parser.feed(page)
    items = {}
    for href, _, before in parser.links:
        url = urljoin(RQ_INDEX, html.unescape(href))
        parsed = urlparse(url)
        if parsed.netloc not in ('www.racingqueensland.com.au', 'racingqueensland.com.au'): continue
        if 'RacingFile.ashx' not in parsed.path: continue
        decoded = unquote(url)
        if '/Sectional/' not in decoded: continue
        match = re.search(r'([0-9]{8})_([A-Za-z0-9_-]+)\.(csv|zip|pdf)(?:[&?]|$)', decoded, re.I)
        if not match: continue
        d8, code, ext = match.groups()
        try: date_s = dt.datetime.strptime(d8, '%Y%m%d').date().isoformat()
        except ValueError: continue
        if not start <= date_s <= end: continue
        recent = re.findall(r'\b\d{2}/\d{2}/\d{4}\s+([^<>\r\n]+)', before)
        track = re.sub(r'\s*(Sectionals|PDF|CSV|ZIP).*$', '', recent[-1], flags=re.I).strip() if recent else code
        key = f'{d8}_{code}.{ext.lower()}'
        items[key] = ('racing_qld', key, url, ext.lower(), date_s, track)
    # Preserve PDFs when a meeting has no usable published CSV/ZIP.
    structured = {t[1].rsplit('.',1)[0] for t in items.values() if t[3] in ('csv','zip')}
    items = {k:v for k,v in items.items() if v[3] != 'pdf' or k.rsplit('.',1)[0] not in structured}
    return sorted(items.values(), key=lambda t: t[1])

def initialise(conn):
    conn.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS downloads (
      source TEXT NOT NULL, item_key TEXT NOT NULL, url TEXT NOT NULL,
      local_path TEXT, status TEXT NOT NULL, bytes INTEGER DEFAULT 0,
      sha256 TEXT, rows_imported INTEGER DEFAULT 0, updated_utc TEXT NOT NULL, error TEXT,
      PRIMARY KEY(source,item_key));
    CREATE TABLE IF NOT EXISTS market_runners (
      source TEXT NOT NULL, row_key TEXT NOT NULL, date TEXT,
      country TEXT, track TEXT, race_number TEXT, market_id TEXT,
      selection_id TEXT, dog TEXT, box TEXT, distance_m REAL,
      win_result TEXT, win_bsp REAL, scheduled_time TEXT, actual_off_time TEXT,
      source_file TEXT NOT NULL, PRIMARY KEY(source,row_key));
    CREATE INDEX IF NOT EXISTS idx_market_date_track ON market_runners(date,track);
    CREATE TABLE IF NOT EXISTS races (
      source TEXT NOT NULL, race_id TEXT NOT NULL, date TEXT, country TEXT,
      track TEXT, distance_m REAL, grade TEXT, going TEXT, winning_time_s REAL,
      source_file TEXT NOT NULL, payload_json TEXT NOT NULL,
      PRIMARY KEY(source,race_id));
    CREATE INDEX IF NOT EXISTS idx_races_date_track ON races(date,track);
    CREATE TABLE IF NOT EXISTS runners (
      source TEXT NOT NULL, runner_id TEXT NOT NULL, race_id TEXT,
      dog TEXT, box TEXT, finish_position TEXT, finish_time_s REAL,
      first_sectional_s REAL, beaten_distance TEXT, weight_kg REAL,
      source_file TEXT NOT NULL, payload_json TEXT NOT NULL,
      PRIMARY KEY(source,runner_id));
    CREATE INDEX IF NOT EXISTS idx_runners_race ON runners(source,race_id);
    CREATE TABLE IF NOT EXISTS qld_sectional_summary (
      source TEXT NOT NULL, row_key TEXT NOT NULL, date TEXT,
      track TEXT, race TEXT, dog TEXT, box TEXT, split_50m_s REAL,
      position_50m TEXT, top_speed_kmh REAL, finish_position TEXT,
      source_file TEXT NOT NULL, payload_json TEXT NOT NULL,
      PRIMARY KEY(source,row_key));
    CREATE TABLE IF NOT EXISTS rq_csv_cells (
      source_file TEXT NOT NULL, line_number INTEGER NOT NULL, cells_json TEXT NOT NULL,
      PRIMARY KEY(source_file,line_number));
    CREATE TABLE IF NOT EXISTS source_schemas (
      source TEXT NOT NULL, file_path TEXT NOT NULL, member TEXT NOT NULL,
      columns_json TEXT NOT NULL, row_count INTEGER NOT NULL,
      PRIMARY KEY(source,file_path,member));
    ''')
    conn.commit()

def stamp(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')

def register(conn, source, key, url, path, status, rows=0, error=None, digest=None):
    size = path.stat().st_size if path and path.exists() else 0
    conn.execute('''INSERT INTO downloads(source,item_key,url,local_path,status,bytes,sha256,rows_imported,updated_utc,error)
       VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,item_key) DO UPDATE SET
       url=excluded.url, local_path=excluded.local_path, status=excluded.status,
       bytes=excluded.bytes, sha256=excluded.sha256, rows_imported=excluded.rows_imported,
       updated_utc=excluded.updated_utc,error=excluded.error''',
       (source,key,url,str(path.relative_to(ROOT)) if path and path.exists() else None,
        status,size,digest,rows,stamp(),str(error)[:700] if error else None))
    conn.commit()

def fetch(url: str, path: Path, wait: float, retries: int=2):
    """Write partial file then rename atomically, limit size, respect block responses."""
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and path.stat().st_size:
        valid = True
        if path.suffix.lower() == '.zip': valid=zipfile.is_zipfile(path)
        elif path.suffix.lower() == '.pdf':
            with path.open('rb') as h: valid=(h.read(5)==b'%PDF-')
        elif path.suffix.lower() == '.csv':
            with path.open('rb') as h:
                head=h.read(500).lstrip().lower()
                valid = not head.startswith((b'<!doctype',b'<html',b'{"error"'))
        if valid: return file_sha(path)
        logging.warning('Corrupted/non-data cache file, retrying: %s',path)
        path.rename(path.with_name(path.name+'.invalid'))
    tmp = path.with_name(path.name+'.part')
    for attempt in range(retries+1):
        try:
            req = Request(url, headers={'User-Agent':AGENT,'Accept':'text/csv, application/zip, text/html, */*'})
            with urlopen(req, timeout=50) as response, tmp.open('wb') as handle:
                ctype = response.headers.get('Content-Type','').lower()
                if 'text/html' in ctype and path.suffix.lower() != '.html':
                    raise DownloadError('Expected data file, got HTML page')
                total=0
                while True:
                    block=response.read(256*1024)
                    if not block: break
                    total += len(block)
                    if total>MAX_BYTES: raise DownloadError('File too big; safety limit 2GB')
                    handle.write(block)
            if not total: raise DownloadError('Empty response')
            if path.suffix.lower()=='.zip' and not zipfile.is_zipfile(tmp):
                raise DownloadError('Bad ZIP (possibly a website error page)')
            if path.suffix.lower()=='.pdf':
                with tmp.open('rb') as handle:
                    if not handle.read(5)==b'%PDF-': raise DownloadError('Expected PDF, received different response')
            if path.suffix.lower()=='.csv':
                with tmp.open('rb') as handle: head=handle.read(500).lstrip().lower()
                if head.startswith((b'<!doctype',b'<html',b'{"error"')):
                    raise DownloadError('Got error HTML/JSON, not CSV')
            tmp.replace(path)
            time.sleep(max(0,wait))
            return file_sha(path)
        except HTTPError as exc:
            if exc.code in (401,403,429):
                raise DownloadError(f'HTTP {exc.code}: access denied / limited; stop and seek access') from exc
            if exc.code==404: raise DownloadError('HTTP 404: publisher file unavailable') from exc
            error=exc
        except (URLError, OSError, TimeoutError, DownloadError) as exc:
            error=exc
        finally:
            if tmp.exists(): tmp.unlink(missing_ok=True)
        if attempt<retries: time.sleep(3*(attempt+1))
    raise DownloadError(str(error))

def file_sha(path):
    h=hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def read_csv_stream(stream):
    wrapper=io.TextIOWrapper(stream,encoding='utf-8-sig',errors='replace',newline='')
    return csv.DictReader(wrapper)

def iter_csvs(path):
    if path.suffix.lower()=='.csv':
        with path.open('rb') as handle:
            yield path.name, read_csv_stream(handle)
    elif path.suffix.lower()=='.zip':
        with zipfile.ZipFile(path) as zf:
            for entry in zf.infolist():
                if not entry.filename.lower().endswith('.csv') or entry.is_dir(): continue
                if entry.file_size>MAX_UNZIPPED_MEMBER: raise DownloadError('Uncompressed member too large')
                with zf.open(entry) as handle:
                    yield entry.filename, read_csv_stream(handle)

def record_schema(conn,source,path,member,keys,count):
    conn.execute('INSERT OR REPLACE INTO source_schemas VALUES (?,?,?,?,?)',
                 (source,str(path.relative_to(ROOT)),member,json.dumps(keys or []),count))

def import_betfair(conn,path,start,end):
    total=0;seen_csv=False
    for member,reader in iter_csvs(path):
        seen_csv=True
        headers=reader.fieldnames or []
        if not any(normalize_key(h)=='selectionname' for h in headers):
            raise DownloadError(f'{member}: no SELECTION_NAME column. Schema not recognised: {headers[:12]}')
        count=0; batch=[]
        for i,row in enumerate(reader,2):
            count+=1
            date_s=to_iso(value_of(row,'LOCAL_MEETING_DATE','EVENT_DT','EVENT_DATE'))
            if date_s is None or not in_range(date_s,start,end): continue
            market=value_of(row,'WIN_MARKET_ID','MARKET_ID') or ''
            sel=value_of(row,'SELECTION_ID') or ''
            race=value_of(row,'RACE_NO','RACE_NUMBER') or ''
            dog=value_of(row,'SELECTION_NAME') or ''
            track=value_of(row,'TRACK','VENUE') or ''
            if not dog: continue
            key=hashlib.sha1(f'{date_s}|{market}|{sel}|{track}|{race}|{dog}'.encode()).hexdigest()
            batch.append(('betfair_anz',key,date_s,value_of(row,'COUNTRY_CODE','COUNTRY') or 'AU/NZ',track,race,
                market,sel,dog,value_of(row,'TAB_NUMBER','BOX'),float_or_none(value_of(row,'DISTANCE')),
                value_of(row,'WIN_RESULT','WIN_LOSE'),float_or_none(value_of(row,'WIN_BSP','BSP')),
                value_of(row,'SCHEDULED_RACE_TIME'),value_of(row,'ACTUAL_OFF_TIME'),str(path.relative_to(ROOT))))
            if len(batch)>=1000:
                conn.executemany('INSERT OR REPLACE INTO market_runners VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',batch)
                total+=len(batch);batch.clear()
        if batch:
            conn.executemany('INSERT OR REPLACE INTO market_runners VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',batch)
            total+=len(batch)
        record_schema(conn,'betfair_anz',path,member,headers,count)
        logging.info('  %s rows read=%s accepted=%s',member,count,total)
    if not seen_csv: raise DownloadError('Publisher archive contains no CSV member; not imported')
    conn.commit(); return total

def import_trapstats(conn,path,start,end):
    total=0;seen=set()
    for member,reader in iter_csvs(path):
        file_name=Path(member).name.lower()
        headers=reader.fieldnames or []
        count=0; batch=[]
        if file_name not in ('races.csv','runners.csv'): continue
        seen.add(file_name)
        for i,row in enumerate(reader,2):
            count+=1
            # Runner-level dates are often only available by joining the race ID.
            payload=json.dumps(row,ensure_ascii=False,separators=(',',':'))
            rid=value_of(row,'race_id','raceid')
            if file_name=='races.csv':
                date_s=to_iso(value_of(row,'date','race_date','meeting_date'))
                if date_s is not None and not in_range(date_s,start,end): continue
                rid=rid or hashlib.sha1(f'{payload}'.encode()).hexdigest()
                batch.append(('trapstats',rid,date_s,value_of(row,'country','country_code'),value_of(row,'track','track_name'),
                   float_or_none(value_of(row,'distance','distance_m','race_distance')),value_of(row,'grade'),value_of(row,'going'),
                   float_or_none(value_of(row,'winning_time','winning_time_s','win_time')),str(path.relative_to(ROOT)),payload))
                if len(batch)>=1000:
                    conn.executemany('INSERT OR REPLACE INTO races VALUES (?,?,?,?,?,?,?,?,?,?,?)',batch)
                    total+=len(batch);batch.clear()
            else:
                date_s=to_iso(value_of(row,'date','race_date','meeting_date'))
                if date_s is not None and not in_range(date_s,start,end): continue
                runner_id=value_of(row,'runner_id','result_id') or hashlib.sha1(f'{member}|{rid}|{i}|{payload}'.encode()).hexdigest()
                batch.append(('trapstats',runner_id,rid,value_of(row,'dog','dog_name','name','greyhound'),
                    value_of(row,'trap','box','trap_number'),value_of(row,'finish_position','position','place'),
                    float_or_none(value_of(row,'finish_time','finish_time_s','run_time')),
                    float_or_none(value_of(row,'sectional','sectional_time','first_split')),
                    value_of(row,'distance_beaten','beaten_distance','margin'),float_or_none(value_of(row,'weight','weight_kg')),
                    str(path.relative_to(ROOT)),payload))
                if len(batch)>=1000:
                    conn.executemany('INSERT OR REPLACE INTO runners VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',batch)
                    total+=len(batch);batch.clear()
        if batch:
            table='races' if file_name=='races.csv' else 'runners'
            conn.executemany(f'INSERT OR REPLACE INTO {table} VALUES ('+','.join(['?']*(11 if table=='races' else 12))+')',batch)
            total+=len(batch)
        record_schema(conn,'trapstats',path,member,headers,count)
        logging.info('  %s rows read=%s',member,count)
    if seen != {'races.csv','runners.csv'}:
        raise DownloadError(f'TrapStats ZIP is missing required race/runner CSV: found {sorted(seen)}')
    conn.commit();return total

def import_panda(conn,path,start,end):
    if path.name!='greyhound_sectionals_qld.csv':
        with path.open('r',encoding='utf-8-sig',errors='replace',newline='') as h:
            reader=csv.DictReader(h); n=sum(1 for _ in reader);keys=reader.fieldnames or []
        record_schema(conn,'panda_data',path,path.name,keys,n);conn.commit();return 0
    total=0;count=0;batch=[]
    with path.open('r',encoding='utf-8-sig',errors='replace',newline='') as h:
        reader=csv.DictReader(h);headers=reader.fieldnames or []
        for i,row in enumerate(reader,2):
            count+=1
            date_s=to_iso(value_of(row,'date','race_date'))
            if not date_s or not in_range(date_s,start,end): continue
            key=hashlib.sha1(json.dumps(row,sort_keys=True).encode()).hexdigest()
            batch.append(('panda_data',key,date_s,value_of(row,'track'),value_of(row,'race','race_no'),
                  value_of(row,'greyhound','dog','name'),value_of(row,'box','trap'),
                  float_or_none(value_of(row,'split_50m_sec')),value_of(row,'position_50m'),
                  float_or_none(value_of(row,'top_speed_kmh')),value_of(row,'finish_position'),
                  str(path.relative_to(ROOT)),json.dumps(row,ensure_ascii=False)))
            if len(batch)>=1000:
                conn.executemany('INSERT OR REPLACE INTO qld_sectional_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',batch)
                total+=len(batch);batch.clear()
        if batch:
            conn.executemany('INSERT OR REPLACE INTO qld_sectional_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',batch)
            total+=len(batch)
    record_schema(conn,'panda_data',path,path.name,headers,count)
    conn.commit();return total

def import_rq(conn,path):
    # The RQ publisher has multiple incompatible CSV layouts across years. Preserve cells
    # verbatim as a staging table. Never manufacture finish times from a mixed report.
    count=0
    # DictReader would lose duplicate/blank header columns, so retain original rows.
    if path.suffix.lower()=='.pdf':
        record_schema(conn,'racing_qld',path,path.name,['pdf_raw_only'],0)
        conn.commit()
        return 0
    if path.suffix.lower()=='.csv':
        with path.open('r',encoding='utf-8-sig',errors='replace',newline='') as h:
            rows=csv.reader(h)
            conn.execute('DELETE FROM rq_csv_cells WHERE source_file=?',(str(path.relative_to(ROOT)),))
            for line,cells in enumerate(rows,1):
                conn.execute('INSERT INTO rq_csv_cells VALUES (?,?,?)',
                             (str(path.relative_to(ROOT)),line,json.dumps(cells,ensure_ascii=False)))
                count+=1
    elif path.suffix.lower()=='.zip':
        # Retain entire zip unmodified; an accompanying CSV can be analysed separately.
        with zipfile.ZipFile(path) as zf:
            for entry in zf.infolist():
                if not entry.filename.lower().endswith('.csv') or entry.is_dir(): continue
                if entry.file_size>MAX_UNZIPPED_MEMBER: raise DownloadError('ZIP member size exceeds safety limit')
                with zf.open(entry) as h:
                    reader=csv.reader(io.TextIOWrapper(h,encoding='utf-8-sig',errors='replace',newline=''))
                    source_name=f'{str(path.relative_to(ROOT))}::{entry.filename}'
                    conn.execute('DELETE FROM rq_csv_cells WHERE source_file=?',(source_name,))
                    for line,cells in enumerate(reader,1):
                        conn.execute('INSERT INTO rq_csv_cells VALUES (?,?,?)',
                                 (source_name,line,json.dumps(cells,ensure_ascii=False)))
                        count+=1
    record_schema(conn,'racing_qld',path,path.name,['raw_csv_cells'],count)
    conn.commit();return count

def downloaded(conn, source, key, path):
    record=conn.execute('SELECT status FROM downloads WHERE source=? AND item_key=?',(source,key)).fetchone()
    return bool(record and record[0]=='parsed' and path.exists() and path.stat().st_size>0)

def process_one(conn,item,start,end,delay):
    source,key,url,ext,*other=item
    path=RAW/source/(key+'.'+ext if not key.lower().endswith('.'+ext) else key)
    status_key = f'{key}@{start}:{end}'
    if downloaded(conn,source,status_key,path): return 'skip',0
    try:
        digest=fetch(url,path,delay)
        register(conn,source,status_key,url,path,'downloaded',digest=digest)
        if source=='betfair_anz': rows=import_betfair(conn,path,start,end)
        elif source=='trapstats': rows=import_trapstats(conn,path,start,end)
        elif source=='panda_data': rows=import_panda(conn,path,start,end)
        elif source=='racing_qld': rows=import_rq(conn,path)
        else: raise ValueError('Unknown source')
        register(conn,source,status_key,url,path,'parsed',rows,digest=digest)
        return 'ok',rows
    except KeyboardInterrupt:
        conn.commit()
        raise
    except Exception as exc:
        conn.rollback()
        register(conn,source,status_key,url,path,'failed',error=exc)
        logging.error('FAIL %s/%s: %s',source,key,exc)
        return 'fail',0

def get_html(url,delay):
    # Index pages are less than 20 MB in these two cases.
    req=Request(url,headers={'User-Agent':AGENT,'Accept':'text/html'})
    with urlopen(req,timeout=50) as h:
        content=h.read(15_000_000+1)
    if len(content)>15_000_000: raise DownloadError('Index page exceeds safety limit')
    time.sleep(max(0,delay))
    return content.decode('utf-8',errors='replace')

def construct_items(args):
    sources=args.sources.split(',')
    items=[]
    for source in sources:
        source=source.strip().lower()
        try:
            if source=='betfair':
                found=parse_betfair_links(get_html(BETFAIR_INDEX,args.delay),args.start,args.end)
                logging.info('BETFAIR AU/NZ: %s published archives discovered',len(found))
                items+=found
            elif source=='queensland':
                found=parse_rq_links(get_html(RQ_INDEX,args.delay),args.start,args.end)
                logging.info('RACING QUEENSLAND: %s sectional files discovered',len(found))
                items+=found
            elif source=='trapstats':
                items.append(('trapstats','latest_open_90_days',TRAPSTATS_ZIP,'zip'))
            elif source=='panda':
                items += [('panda_data',name,PANDA_BASE+'/'+rel,'csv') for name,rel in PANDA_PATHS.items()]
                for year in range(int(args.start[:4]),int(args.end[:4])+1):
                    name=f'track_weather_{year}'
                    items.append(('panda_data',name,PANDA_BASE+f'/track_weather/track_weather_{year}.csv','csv'))
            else:
                logging.error('Unknown source %s',source)
        except (HTTPError,URLError,OSError,DownloadError) as exc:
            logging.error('INDEX FAILED [%s]: %s',source,exc)
    return items

def status(conn):
    print('\nDOG RACE STATUS')
    print('-'*78)
    for source,files,good,failed,bytes_,rows in conn.execute('''SELECT source, count(*),
       sum(CASE WHEN status='parsed' THEN 1 ELSE 0 END),
       sum(CASE WHEN status='failed' THEN 1 ELSE 0 END),sum(bytes),sum(rows_imported)
       FROM downloads GROUP BY source ORDER BY source'''):
        print(f'{source:18} files={files:5} imported={good or 0:5} failed={failed or 0:4} bytes={(bytes_ or 0)/1048576:8.1f}MB rows={rows or 0:,}')
    print('\nDATABASE TABLES:')
    for table in ('market_runners','races','runners','qld_sectional_summary','rq_csv_cells'):
        print(f'  {table:24} {conn.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]:,}')
    for table in ('market_runners','races','qld_sectional_summary'):
        lo,hi=conn.execute(f'SELECT MIN(date),MAX(date) FROM {table}').fetchone()
        print(f'  {table:24} date range={lo or "unknown"} to {hi or "unknown"}')
    print('\nRecent failures:')
    failures=conn.execute('SELECT source,item_key,error FROM downloads WHERE status="failed" ORDER BY updated_utc DESC LIMIT 8').fetchall()
    for s,k,e in failures: print(f'  [{s}] {k}: {e}')
    if not failures: print('  none')
    print('\nRaw downloaded files remain in data/raw even after import.')


def run(args):
    start=dt.date.fromisoformat(args.start)
    end=dt.date.fromisoformat(args.end)
    if end<start: raise ValueError('End date precedes start date')
    items=construct_items(args)
    if args.pilot:
        # Representative mix, deliberately small: one recent AU market, two RQ CSVs,
        # one UK snapshot and the public AU sectionals file.
        betfair=[t for t in items if t[0]=='betfair_anz']
        rq=[t for t in reversed(items) if t[0]=='racing_qld' and t[3]=='csv']
        ts=[t for t in items if t[0]=='trapstats']
        panda=[t for t in items if t[0]=='panda_data' and t[1]=='qld_sectionals']
        items=betfair[-1:]+rq[:2]+ts[:1]+panda[:1]
    print(f'\nDATE WINDOW: {start} through {end} (inclusive)')
    print(f'PUBLISHED DATA FILES QUEUED: {len(items)}')
    print(f'PILOT: {"YES" if args.pilot else "NO"} | REQUEST DELAY: {args.delay}s')
    if not items:
        print('No matching published downloads found. Review index failure logs and date coverage.');return
    success=fail=skip=rows=0;times=[]
    for n,item in enumerate(items,1):
        began=time.monotonic()
        label=f'{item[0]}/{item[1]}'
        eta=(sum(times)/len(times)*(len(items)-n)) if times else None
        print(f'\n[{n:04d}/{len(items):04d}] {label}'+(f' | ETA ~{eta/60:.1f}min' if eta is not None else ''),flush=True)
        status_,count=process_one(args.conn,item,args.start,args.end,args.delay)
        if status_=='ok': success+=1;rows+=count;print(f'  OK: imported {count:,} rows',flush=True)
        elif status_=='skip': skip+=1; print('  SKIPPED: previously completed',flush=True)
        else: fail+=1
        times.append(time.monotonic()-began)
    print(f'\nCOMPLETE: downloaded/imported={success}, already complete={skip}, failed={fail}, processed rows={rows:,}')
    status(args.conn)

def main(argv=None):
    parser=argparse.ArgumentParser(description='DOG RACE free-data historical importer')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('collect',help='Fetch public download files and import')
    p.add_argument('--start',default=DEFAULT_START);p.add_argument('--end',default=DEFAULT_END)
    p.add_argument('--sources',default='betfair,queensland,trapstats,panda',help='Comma-separated: betfair,queensland,trapstats,panda')
    p.add_argument('--delay',type=float,default=1.5)
    p.add_argument('--pilot',action='store_true')
    sub.add_parser('status');sub.add_parser('selftest')
    args=parser.parse_args(argv)
    RAW.mkdir(parents=True,exist_ok=True);LOG_DIR.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',
      handlers=[logging.FileHandler(LOG_DIR/f'run_{dt.datetime.now():%Y%m%d}.log',encoding='utf-8'),logging.StreamHandler(sys.stdout)])
    with contextlib.closing(sqlite3.connect(DB,timeout=90)) as conn, conn:
        initialise(conn)
        if args.command=='collect':
            args.conn=conn
            try: run(args)
            except KeyboardInterrupt: print('\nINTERRUPTED. Downloads and imported files are saved. Re-run the BAT to resume.')
        elif args.command=='status':status(conn)
        else:
            import unittest
            suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py')
            result=unittest.TextTestRunner(verbosity=2).run(suite)
            return 0 if result.wasSuccessful() else 1
    return 0

if __name__=='__main__': sys.exit(main())
