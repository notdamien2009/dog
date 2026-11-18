#!/usr/bin/env python3
"""GBGB connector for DOG RACE. For research authorised by the data provider.

Read-only inspect: one page only, prints schema, doesn't retain remote response.
Collect: paginated, resumable, serial requests; no permission-token file required.
Import: accepts local JSON dumps and retains their original content.
Full source JSON is staged; verified race/runner fields are also normalised.
The first sectional marker distance is unknown; no speed figures are invented.
"""
from __future__ import annotations
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen, Request

import collector
import gbgb_normalize

BASE = 'https://api.gbgb.org.uk/api/results'
RAW_ROOT = collector.ROOT / 'data' / 'raw' / 'gbgb'
GBGB_DB = collector.ROOT / 'data' / 'gbgb.sqlite3'  # separate from AU/shared collector database
RUN_LOCK = collector.ROOT / 'data' / 'gbgb-run.lock'
MIN_DELAY = 3.0
MAX_RESPONSE = 20_000_000
AGENT = 'DOG-RACE-GBGB-Research/1.6 (personal research, serial)'

class GBGBError(Exception): pass
class AccessStopped(GBGBError): pass

@contextlib.contextmanager
def exclusive_run_lock(path=None):
    """Prevent two GBGB historical jobs from running against the same database.

    OS-managed lock: released automatically if the process exits, including
    crashes. Never delete database journal/WAL files to fix a lock.
    """
    path = path or RUN_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as lockfile:
        lockfile.seek(0, 2)
        if lockfile.tell() == 0:
            lockfile.write(b'\0')
            lockfile.flush()
        lockfile.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise GBGBError('Another GBGB collection is running in this folder. '
                            'Close the other GBGB CMD window before starting again.') from exc
        try:
            yield
        finally:
            lockfile.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lockfile.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN)

def open_db(db=None):
    # Older versions shared data/dog_race.sqlite3 with the Australian collector.
    # This caused DB-lock contention; use a separate GBGB-only database.
    # The shared database and previously downloaded raw JSON remain unchanged.
    path = Path(db) if db is not None else GBGB_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=45)
    try:
        conn.execute('PRAGMA busy_timeout=45000')
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.executescript('''
    CREATE TABLE IF NOT EXISTS gbgb_requests (
      request_key TEXT PRIMARY KEY, url TEXT NOT NULL, kind TEXT NOT NULL,
      race_date TEXT, meeting_id TEXT, local_path TEXT,
      status TEXT NOT NULL, error TEXT, updated_utc TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS gbgb_req_date ON gbgb_requests(race_date);
    CREATE TABLE IF NOT EXISTS gbgb_index (
      race_date TEXT NOT NULL, item_key TEXT NOT NULL, meeting_id TEXT,
      race_id TEXT, dog_id TEXT, dog_name TEXT, track_name TEXT,
      race_distance_m REAL, race_class TEXT, result_position TEXT,
      payload_json TEXT NOT NULL,
      PRIMARY KEY(race_date,item_key)
    );
    CREATE INDEX IF NOT EXISTS gbgb_index_meeting ON gbgb_index(meeting_id);
    CREATE TABLE IF NOT EXISTS gbgb_meetings (
      meeting_id TEXT PRIMARY KEY, first_seen_date TEXT,
      json_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS gbgb_date_coverage (
      race_date TEXT PRIMARY KEY, indexed_items INTEGER NOT NULL,
      meetings_found INTEGER NOT NULL, pages_found INTEGER NOT NULL,
      all_pages_saved INTEGER NOT NULL, updated_utc TEXT NOT NULL
    );
    ''')
        conn.commit()
        return conn
    except Exception:
        conn.close()
        raise

def request_json(url, timeout=35):
    """One standard read-only GET. No proxy, cookies, challenge bypass or retry on blocks."""
    req=Request(url,headers={'User-Agent':AGENT,'Accept':'application/json'})
    try:
        with urlopen(req,timeout=timeout) as res:
            if res.status != 200: raise GBGBError(f'HTTP {res.status}')
            data=res.read(MAX_RESPONSE+1)
            if len(data)>MAX_RESPONSE: raise GBGBError('Response too large')
            content_type=res.headers.get('Content-Type','').lower()
            if 'html' in content_type: raise GBGBError('HTML returned, not JSON')
            return json.loads(data)
    except HTTPError as exc:
        if exc.code in (401,403,429):
            raise AccessStopped(f'HTTP {exc.code}. Stop: access denied/rate limited; no bypass attempted.') from exc
        raise GBGBError(f'HTTP {exc.code}: {url}') from exc
    except (URLError,TimeoutError) as exc:
        raise GBGBError(f'Connection problem: {exc}') from exc
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise GBGBError('API did not return valid JSON') from exc

def index_url(date, page, page_size=100):
    return BASE+'?'+urlencode({'page':page,'itemsPerPage':page_size,'date':date,'race_type':'race'})

def meeting_url(mid):
    if not str(mid).isdigit(): raise GBGBError('Invalid meeting ID; expected numeric ID')
    return f'{BASE}/meeting/{mid}?'+urlencode({'meeting':mid})

def extract_data(obj):
    # Existing and independently wrapped interfaces can use different envelopes.
    if isinstance(obj,dict) and isinstance(obj.get('data'),dict):
        obj=obj['data']
    if not isinstance(obj,dict) or not isinstance(obj.get('items'),list):
        raise GBGBError('Unexpected index schema: expected an object with items[]')
    meta=obj.get('meta') if isinstance(obj.get('meta'),dict) else {}
    return obj['items'],meta

def index_entry(item, date, index):
    if not isinstance(item,dict): raise GBGBError('Unexpected index item type')
    mid=item.get('meetingId')
    race_id=item.get('raceId')
    dog_id=item.get('dogId')
    # Preserve full individual payload; the index isn't all runners for a race.
    hashkey=hashlib.sha256(json.dumps(item,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    unique='|'.join(str(x or '') for x in [mid,race_id,dog_id,item.get('trapNumber'),item.get('resultPosition')])
    key=unique if mid and race_id and dog_id else hashkey
    dist=item.get('raceDistance')
    try: dist=float(dist) if dist is not None else None
    except (ValueError,TypeError): dist=None
    return (date,key,str(mid) if mid is not None else None,
      str(race_id) if race_id is not None else None,
      str(dog_id) if dog_id is not None else None,
      item.get('dogName'),item.get('trackName'),dist,item.get('raceClass'),
      str(item['resultPosition']) if item.get('resultPosition') is not None else None,
      json.dumps(item,ensure_ascii=False,separators=(',',':')))

def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')

def save_json(path, obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.part')
    data=json.dumps(obj,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    with tmp.open('wb') as fh: fh.write(data)
    tmp.replace(path)
    return hashlib.sha256(data).hexdigest()

def read_json(path):
    with path.open('r',encoding='utf-8') as fh: return json.load(fh)

def request_state(conn,key,url,kind,date,mid,path,status,error=None):
    conn.execute('''INSERT INTO gbgb_requests VALUES (?,?,?,?,?,?,?, ?,?)
      ON CONFLICT(request_key) DO UPDATE SET status=excluded.status,
      error=excluded.error,updated_utc=excluded.updated_utc,local_path=excluded.local_path''',
      (key,url,kind,date,mid,str(path.relative_to(collector.ROOT)) if path and path.exists() and path.is_relative_to(collector.ROOT) else (str(path) if path and path.exists() else None),
        status,str(error)[:500] if error else None,now()))
    conn.commit()

def fetch_cached(conn, key, url, kind, date, mid, path, delay, getter=request_json):
    if path.exists():
        try:
            obj=read_json(path)
            print(f'    cached {key}',flush=True)
            request_state(conn,key,url,kind,date,mid,path,'complete')
            return obj
        except (OSError,ValueError,json.JSONDecodeError):
            # Broken local raw file: preserve it for analysis, not silently trust it.
            broken=path.with_suffix(path.suffix+'.invalid')
            path.rename(broken)
    print('    requesting '+url,flush=True)
    obj=getter(url)
    if kind=='day': extract_data(obj)  # reject an unexpected API shape before caching it
    elif kind=='meeting' and not isinstance(obj,(dict,list)):
        raise GBGBError('Unexpected meeting response: expected JSON object/list')
    save_json(path,obj)
    request_state(conn,key,url,kind,date,mid,path,'complete')
    if delay: time.sleep(delay)
    return obj

def import_index(conn,date,page,obj):
    items,meta=extract_data(obj)
    rows=[index_entry(i,date,n) for n,i in enumerate(items)]
    conn.executemany('''INSERT OR REPLACE INTO gbgb_index
       (race_date,item_key,meeting_id,race_id,dog_id,dog_name,track_name,
       race_distance_m,race_class,result_position,payload_json)
       VALUES (?,?,?,?,?,?,?,?,?,?,?)''',rows)
    conn.commit()
    return len(rows),meta

def import_meeting(conn,mid,date,obj):
    raw=json.dumps(obj,ensure_ascii=False,separators=(',',':'))
    digest=hashlib.sha256(raw.encode('utf-8')).hexdigest()
    conn.execute('INSERT OR REPLACE INTO gbgb_meetings VALUES (?,?,?,?)',(str(mid),date,digest,raw))
    conn.commit()  # raw stage is retained even if field normalisation requires a future patch
    try:
        return gbgb_normalize.normalize_meeting(conn, mid, obj, digest)
    except gbgb_normalize.NormalizationError as exc:
        raise GBGBError(f'Meeting {mid} staged safely but race/runner schema rejected: {exc}') from exc

def inspect(date,page_size=5,getter=request_json):
    dt.date.fromisoformat(date)
    url=index_url(date,1,page_size)
    print('GBGB ONE-REQUEST API SCHEMA INSPECTION\n'+'-'*65)
    print('Date:',date,'| URL:',url)
    print('This reads one page and DOES NOT save the response.\n')
    obj=getter(url)
    items,meta=extract_data(obj)
    print('Top-level keys:',sorted(obj.keys()) if isinstance(obj,dict) else type(obj).__name__)
    print('Meta:',meta)
    print('Items on page:',len(items))
    if items:
        print('First item fields:',sorted(items[0].keys()) if isinstance(items[0],dict) else type(items[0]).__name__)
        safe={k:v for k,v in items[0].items() if k in ('meetingId','raceId','raceDate','raceTime','raceDistance','trackName','raceClass','resultPosition','trapNumber')}
        print('Example identifier/metadata fields:',json.dumps(safe,indent=2))
    print('No bulk collection performed.')

def inspect_meeting(mid,getter=request_json):
    url=meeting_url(mid)
    print('GBGB ONE-REQUEST MEETING SCHEMA INSPECTION\n'+'-'*65)
    print('Meeting:',mid,'| URL:',url)
    print('Prints field paths only; does not save the response.\n')
    obj=getter(url)
    paths=[]
    def visit(value,prefix='',depth=0):
        if depth>5 or len(paths)>180:return
        if isinstance(value,dict):
            for key,child in value.items():
                name=f'{prefix}.{key}' if prefix else str(key)
                paths.append(name)
                visit(child,name,depth+1)
        elif isinstance(value,list) and value:
            visit(value[0],prefix+'[0]',depth+1)
    visit(obj)
    for k in paths[:160]:print('  '+k)
    print(f'Field paths displayed: {min(len(paths),160)}. No results were saved.')

def daterange(start,end):
    day=dt.date.fromisoformat(start);last=dt.date.fromisoformat(end)
    if last<day: raise ValueError('End precedes start')
    while day<=last:
        yield day.isoformat()
        day+=dt.timedelta(days=1)

def collect(conn,start,end,delay=3.0,max_pages=100,limit_days=0,getter=request_json):
    delay=max(MIN_DELAY,delay)
    days=list(daterange(start,end))
    if limit_days: days=days[:limit_days]
    print(f'GBGB local research: {len(days)} days; delay at least {delay}s; Ctrl+C to interrupt.',flush=True)
    for n,date in enumerate(days,1):
        completed=conn.execute('SELECT all_pages_saved FROM gbgb_date_coverage WHERE race_date=?',(date,)).fetchone()
        if completed and completed[0]:
            meetings_missing=conn.execute('''SELECT count(DISTINCT i.meeting_id) FROM gbgb_index i
               LEFT JOIN gbgb_meetings m ON i.meeting_id=m.meeting_id
               WHERE i.race_date=? AND i.meeting_id IS NOT NULL AND m.meeting_id IS NULL''',(date,)).fetchone()[0]
            meetings_outdated=conn.execute('''SELECT count(DISTINCT m.meeting_id) FROM gbgb_index i
               JOIN gbgb_meetings m ON i.meeting_id=m.meeting_id
               LEFT JOIN gbgb_meeting_imports norm ON norm.meeting_id=m.meeting_id
               WHERE i.race_date=? AND (norm.meeting_id IS NULL OR norm.json_sha256!=m.json_sha256)''',(date,)).fetchone()[0] if gbgb_normalize.table_exists(conn,'gbgb_meeting_imports') else 1
            if meetings_missing==0 and meetings_outdated==0:
                print(f'[{n}/{len(days)}] {date}: already completed (race/runner tables current)',flush=True)
                continue
        print(f'[{n}/{len(days)}] {date}: collecting index pages',flush=True)
        page=1;count=0;totalpages=0
        try:
            while page<=max_pages:
                url=index_url(date,page)
                key=f'day_{date}_page_{page}'
                path=RAW_ROOT/'days'/date/f'page_{page:03d}.json'
                obj=fetch_cached(conn,key,url,'day',date,None,path,delay,getter)
                qty,meta=import_index(conn,date,page,obj)
                count+=qty;totalpages+=1
                pages=meta.get('pageCount')
                if pages is not None:
                    if not str(pages).isdigit(): raise GBGBError('Invalid pageCount in metadata')
                    if int(pages)==0 and qty==0: break
                    if int(pages)<1: raise GBGBError('Invalid pageCount in metadata')
                    if qty==0 and page<int(pages):
                        raise GBGBError('Empty index page before advertised last page; refusing to mark day complete')
                    if page >= int(pages): break
                elif qty==0: break
                elif qty<100: break
                page+=1
            else:
                raise GBGBError(f'Pagination exceeded safety cap {max_pages}; increase only after reviewing')
            mids=[row[0] for row in conn.execute('SELECT DISTINCT meeting_id FROM gbgb_index WHERE race_date=? AND meeting_id IS NOT NULL',(date,))]
            if count and not mids:
                raise GBGBError('Index returned items but no meeting IDs; schema requires inspection before continuing')
            for j,mid in enumerate(mids,1):
                staged=conn.execute('SELECT json_sha256,payload_json FROM gbgb_meetings WHERE meeting_id=?',(mid,)).fetchone()
                if staged:
                    if not gbgb_normalize.is_imported(conn,mid,staged[0]):
                        try:
                            mapped=gbgb_normalize.normalize_meeting(conn,mid,json.loads(staged[1]),staged[0])
                        except gbgb_normalize.NormalizationError as exc:
                            raise GBGBError(f'Cached meeting {mid} cannot be mapped: {exc}') from exc
                        print(f'    mapped cached meeting {mid}: {mapped.get("races",0)} races, {mapped.get("traps",0)} traps',flush=True)
                    continue
                if not mid.isdigit(): raise GBGBError(f'Invalid meeting ID in index: {mid!r}')
                print(f'    meeting {j}/{len(mids)}: {mid}',flush=True)
                url=meeting_url(mid)
                path=RAW_ROOT/'meetings'/f'{mid}.json'
                obj=fetch_cached(conn,f'meeting_{mid}',url,'meeting',date,mid,path,delay,getter)
                import_meeting(conn,mid,date,obj)
            conn.execute('INSERT OR REPLACE INTO gbgb_date_coverage VALUES (?,?,?,?,?,?)',
                 (date,count,len(mids),totalpages,1,now()))
            conn.commit()
            print(f'    DAY COMPLETE: index records={count}, meetings={len(mids)}, pages={totalpages}; runner tables current',flush=True)
        except (AccessStopped,GBGBError,ValueError,TypeError,KeyError) as exc:
            print(f'    STOPPED at {date}: {exc}',flush=True)
            raise

def import_local(conn,folder):
    root=Path(folder)
    if not root.is_dir(): raise GBGBError(f'Not a folder: {root}')
    count=0
    for path in sorted(root.rglob('*.json')):
        obj=read_json(path)
        if 'meetings' in path.parts and path.stem.isdigit():
            import_meeting(conn,path.stem,None,obj);count+=1;continue
        # local filenames: days/YYYY-MM-DD/page_001.json
        if path.parent.name and len(path.parent.name)==10:
            try: dt.date.fromisoformat(path.parent.name)
            except ValueError: continue
            if path.stem.startswith('page_'):
                import_index(conn,path.parent.name,int(path.stem[5:]),obj);count+=1
    print(f'Imported/normalised {count} authorised raw JSON files from {root}')

def status(conn):
    print('\nGBGB STATUS — verified race and runner fields; no speed figures yet')
    print('  GBGB database:', GBGB_DB)
    for table in ('gbgb_index','gbgb_meetings','gbgb_date_coverage'):
        print(f'  {table}: {conn.execute("SELECT count(*) FROM "+table).fetchone()[0]:,}')
    if gbgb_normalize.table_exists(conn,'gbgb_races'):
        for table in ('gbgb_meeting_imports','gbgb_races','gbgb_runners'):
            print(f'  {table}: {conn.execute("SELECT count(*) FROM "+table).fetchone()[0]:,}')
    row=conn.execute('SELECT min(race_date),max(race_date) FROM gbgb_date_coverage').fetchone()
    print('  Completed date span:',row[0] or '-', 'to',row[1] or '-')
    if conn.execute('SELECT 1 FROM gbgb_index LIMIT 1').fetchone():
        print('  Sample parsed index record:')
        print('  ',conn.execute('SELECT race_date,track_name,dog_name,race_distance_m,race_class FROM gbgb_index LIMIT 1').fetchone())

def main(argv=None):
    ap=argparse.ArgumentParser(description='GBGB UK greyhound results connector')
    sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('inspect',help='One request only; no response retained')
    p.add_argument('--date',default='2024-06-01')
    p=sub.add_parser('inspect-meeting',help='One meeting schema inspection; no response retained')
    p.add_argument('--meeting',default='415753')
    p=sub.add_parser('collect',help='Resumable historical collection for authorised personal research')
    p.add_argument('--start',default='2023-11-18');p.add_argument('--end',default='2026-11-17')
    p.add_argument('--delay',type=float,default=3.0);p.add_argument('--max-pages',type=int,default=100)
    p.add_argument('--limit-days',type=int,default=0)
    p=sub.add_parser('import',help='Import authorised JSON export folder')
    p.add_argument('folder')
    sub.add_parser('status')
    args=ap.parse_args(argv)
    try:
        if args.cmd=='inspect': inspect(args.date);return 0
        if args.cmd=='inspect-meeting':inspect_meeting(args.meeting);return 0
        if args.cmd=='collect':
            with exclusive_run_lock():
                with contextlib.closing(open_db()) as conn:
                    print('GBGB database:', GBGB_DB, flush=True)
                    print('Separate from Australian collector; existing files kept.', flush=True)
                    collect(conn,args.start,args.end,args.delay,args.max_pages,args.limit_days)
        else:
            with contextlib.closing(open_db()) as conn:
                if args.cmd=='import': import_local(conn,args.folder)
                else: status(conn)
        return 0
    except KeyboardInterrupt:
        print('\nInterrupted. Existing work retained. Run again to resume.');return 130
    except sqlite3.OperationalError as exc:
        if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
            print('GBGB DATABASE BUSY:', GBGB_DB)
            print('Close other GBGB collection windows and SQLite viewers, '
                  'then retry. Do NOT delete the database or -wal/-shm files.')
        else:
            print('GBGB SQLITE ERROR:', exc)
        return 2
    except (AccessStopped,GBGBError,ValueError,URLError,sqlite3.Error) as exc:
        print('GBGB:',exc);return 2

if __name__=='__main__': sys.exit(main())
