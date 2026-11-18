import csv, json, sqlite3, sys, time
from datetime import date, timedelta
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DB = ROOT / "data" / "dog_race.sqlite3"
RAW = ROOT / "data" / "raw" / "greyhoundracingapi"
LOG = ROOT / "logs" / "collector.log"
RAW.mkdir(parents=True, exist_ok=True)
LOG.parent.mkdir(parents=True, exist_ok=True)

API = "https://api.greyhoundracingapi.com/v1/results"
KEY = None

def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f: f.write(line + "\n")

def db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS races (
        race_id TEXT PRIMARY KEY, race_date TEXT, track TEXT, track_name TEXT,
        country TEXT, race_no INTEGER, off TEXT, off_utc TEXT, distance_m INTEGER,
        grade TEXT, settled_at TEXT, result_status TEXT, raw_file TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS runners (
        race_id TEXT, pos INTEGER, trap INTEGER, runner TEXT, runner_id TEXT,
        trainer TEXT, time_seconds REAL, margin TEXT, sp TEXT,
        raw_json TEXT, PRIMARY KEY (race_id, trap, runner)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS requests (
        country TEXT, race_date TEXT, page INTEGER, status INTEGER,
        fetched_at TEXT, raw_file TEXT, error TEXT,
        PRIMARY KEY(country, race_date, page)
    )""")
    con.commit()
    return con

def daterange(a,b):
    d = date.fromisoformat(a); end = date.fromisoformat(b)
    while d <= end:
        yield d.isoformat()
        d += timedelta(days=1)

def fetch(con, country, d, page):
    params = {"date": d, "country": country, "page": page}
    headers = {"X-API-Key": KEY} if KEY else {}
    r = requests.get(API, params=params, headers=headers,
                     timeout=CFG["request_timeout_seconds"])
    raw_name = f"{country}_{d}_p{page}.json"
    raw_path = RAW / raw_name
    raw_path.write_text(r.text, encoding="utf-8")
    con.execute("INSERT OR REPLACE INTO requests VALUES (?,?,?,?,?,?,?)",
                (country,d,page,r.status_code,time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 str(raw_path.relative_to(ROOT)), None if r.ok else r.text[:500]))
    con.commit()
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json(), raw_path

def import_rows(con, payload, raw_path):
    data = payload.get("data", []) if isinstance(payload, dict) else []
    races = runners = 0
    for race in data:
        rid = str(race.get("race_id"))
        if not rid or rid == "None": continue
        con.execute("""INSERT OR REPLACE INTO races VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            rid, race.get("date"), race.get("track"), race.get("track_name"),
            race.get("country"), race.get("no"), race.get("off"), race.get("off_utc"),
            race.get("distance_m"), race.get("grade"), race.get("settled_at"),
            race.get("result_status"), str(raw_path.relative_to(ROOT))
        ))
        races += 1
        for rr in race.get("result") or []:
            con.execute("""INSERT OR REPLACE INTO runners
                (race_id,pos,trap,runner,runner_id,trainer,time_seconds,margin,sp,raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (
                rid, rr.get("pos"), rr.get("trap"), rr.get("runner"),
                rr.get("runner_id"), rr.get("trainer"), rr.get("time"),
                rr.get("margin"), rr.get("sp"), json.dumps(rr, ensure_ascii=False)
            ))
            runners += 1
    con.commit()
    return races, runners, len(data)

def main():
    global KEY
    KEY = (sys.argv[1].strip() if len(sys.argv) > 1 else "").strip() or None
    if not KEY:
        log("No API key supplied. Set GRA_API_KEY or pass it as the first argument.")
        log("This build uses the documented Greyhound Racing API; it does not scrape pages.")
        return 2
    con = db()
    total_races = total_runners = 0
    for d in daterange(CFG["start_date"], CFG["end_date"]):
        for country in CFG["countries"]:
            page = 1
            while True:
                done = con.execute(
                    "SELECT status FROM requests WHERE country=? AND race_date=? AND page=?",
                    (country,d,page)).fetchone()
                if done and done[0] == 200:
                    page += 1
                    continue
                try:
                    payload, raw = fetch(con,country,d,page)
                    r,u,n = import_rows(con,payload,raw)
                    total_races += r; total_runners += u
                    log(f"{country} {d} page {page}: {r} races / {u} runners")
                    meta = payload.get("meta", {}) if isinstance(payload,dict) else {}
                    pages = meta.get("total_pages")
                    if pages and page >= int(pages): break
                    if not data_has_next(meta, page, n): break
                    page += 1
                    time.sleep(CFG["sleep_between_requests_seconds"])
                except Exception as e:
                    log(f"ERROR {country} {d} page {page}: {e}")
                    break
    log(f"COMPLETE: {total_races} race rows imported, {total_runners} runner rows imported")
    con.close()
    return 0

def data_has_next(meta, page, n):
    if meta.get("next_cursor"): return True
    if meta.get("total_pages") is not None: return page < int(meta["total_pages"])
    return n >= int(meta.get("per_page", CFG["page_size"]))

if __name__ == "__main__":
    raise SystemExit(main())
