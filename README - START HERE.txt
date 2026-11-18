DOG RACE v1.6 — GBGB RESULTS NORMALISER (18 NOV 2026)
=================================================
The 27-meeting GBGB field audit identified the official API structure:
meeting JSON list -> races[] -> traps[]; includes results and first sectionals.

IMPORTANT NEXT STEP — NO INTERNET DOWNLOADS REQUIRED
1. Close other DOG RACE CMD processes; keep data/ and your databases intact.
2. Copy this release's DOG RACE files over the old ones (replace matching files).
3. Run RUN_TESTS.bat (offline unit tests; expect 38 passing).
4. Run GBGB_BUILD_RACE_DATABASE.bat to convert the 27 saved meeting JSON records
   into gbgb_races and gbgb_runners within data/gbgb.sqlite3.
5. Read data/reports/gbgb_normalized_quality.txt and check counts.
6. If everything matches your audit and there are no unmapped meetings, you can
   use RUN_GBGB_3_YEARS.bat under your provider authorisation.

Each NEW meeting is now normalised during downloading. All original source JSON
is kept and reimport is idempotent by SHA256 digest. Reporting never estimates
missing times or sectionals. No data is uploaded or pushed to GitHub.

CAUTION: The GBGB sample reports 323 meeting races, but the date index has 321
unique race IDs. We do not silently reconcile that discrepancy; the quality
report flags races missing from the index. GBGB sectionals are first splits;
exact split DISTANCES are not in the JSON, so no sectional speeds are computed.

The importer has been exercised on a truncated REAL GBGB audit preview plus
constructed fixtures, not on the user's full 27-meeting database. That final
coverage/quality verification must run on the user's PC.

GBGB PILOT RESULT / NEXT REQUIRED STEP (v1.5, 18 Nov 2026)
--------------------------------------------------------
Pilot returned 321 index records, 27 meeting JSON payloads, 2 dates.
Run GBGB_AUDIT_FIELDS.bat before starting the 3-year GBGB collector.
The audit reads your local database without network requests and produces
field-mapping reports in data\reports. Share gbgb_field_audit.txt,
gbgb_timing_candidates.csv, and gbgb_meeting_structure_samples.json.
The raw meeting JSON has NOT yet been normalised into trustworthy
finish-time, sectional, margin and runner tables.

DOG RACE v1.2 GBGB COLLECTOR FIX (18 November 2026)

The previous GBGB written-permission TOKEN FILE check has been removed
on the basis of the user's stated access authorisation for personal research.
No GBGB_PERMISSION.txt is needed.

New BAT: RUN_GBGB_PILOT.bat (small live test before 3 years).
Then use RUN_GBGB_3_YEARS.bat for 2023-11-18 to 2026-11-17.
All downloaded records remain local. No external uploads or commits.
Actual 401/403/429 responses stop immediately. Minimum 3s delay.

See GBGB_ACCESS_GUIDE.txt. The full meeting-to-runner mapping still needs
inspection of a genuine live sample before trusting speed figures.

DOG RACE - UK AND AUSTRALIAN GREYHOUND DATA COLLECTOR
Version 1.4, 18 November 2026

HOW TO START (WINDOWS)
1. Extract the ZIP and put the folder named DOG RACE wherever you want (Desktop is fine).
2. Install Python 3.10+ from https://www.python.org/downloads/ if your PC does not already have Python.
   During installation, tick 'Add Python to PATH'. No pip packages or API keys needed.
3. Double-click RUN_TESTS.bat - the offline tests should say OK.
4. Double-click RUN_PILOT.bat - it checks a small selection of online files.
5. Read the screen; if providers reject access, do not force or bypass it.
6. If the pilot succeeds, double-click RUN_3_YEARS.bat.
7. Double-click CHECK_STATUS.bat for database counts and CSV coverage reports.
8. Later use RUN_OLDER_YEARS.bat to go back from Nov 2023 toward Nov 2020.

DATE WINDOW
First pass: 18 Nov 2023 to 17 Nov 2026 inclusive (3 years).
Second pass: 18 Nov 2020 to 17 Nov 2023 inclusive.
Files and records are filtered to date where publisher rows provide a valid date.
Original published annual/monthly archives may include older dates in the raw files.
Each date window is checkpointed separately so the later backward run correctly
re-imports overlapping 2023 archives without unnecessary re-downloading.

WHAT THE JOB DOES
- Discovers Betfair Australia's published ANZ Greyhounds monthly/yearly files and imports dog,
  track, race, prices, winner outcome, distance and metadata where present.
- Discovers Racing Queensland official sectional CSV/ZIP links from the published index,
  downloads CSV/ZIP files and PDF-only meetings; keeps every available CSV cell in
  a staging table (many different
  layouts exist; unverified cells are NOT mislabelled as speed figures).
- Downloads TrapStats free 90-day UK/Irish race+runner sample; imports race/winner times,
  runner times and first sectionals where the published fields are actually present.
- Downloads Australian Racing Open Data's aggregated Queensland 50-metre sectionals,
  Australian track geometry, venue weather and Victorian dog weights
  (some data is only forward-collected).
- Saves all untouched source files in data/raw/, logs in data/logs/ and database at
  data/dog_race.sqlite3. Data quality/coverage CSVs go to data/reports/.
- Keeps a status for each archive so re-running resumes without re-downloading successes.
- Stops on individual blocked/unavailable sources rather than bypassing protection.
- No automatic upload to Supabase, no GitHub pushes or source changes.

WHAT THIS DOES *NOT* GUARANTEE
- 3 years of UK greyhound results, dog-by-dog sectionals or complete AU race coverage.
  UK free TrapStats history is only approximately 90 days, and the historical RQ link
  list begins in 2024; only a subset have CSV or ZIP.
- Betfair's published market snapshots are not full dog speed/finish-time records.
- Row-level entity joining across different publishers is not attempted; identical dog
  names, race times and tracks can be ambiguous and need a deterministic match system.
- The older-history collection will NOT recover source files that never existed.
- This package has been syntax and offline integration-tested; live downloading will need
  the user's PC's internet connection and may hit provider rate limits/format changes.

RELIABILITY AND RIGHTS
- Use personal research only until applicable provider/data rights are checked.
- Do not bypass access-denied, 403 or 429. Program respects low request frequency.
- TrapStats open sample requires attribution; scraping pages outside published sample is
  expressly prohibited. The public sample is CC BY 4.0; see its source page.
- Australian Racing Open Data requires attribution as ODC-BY 1.0, and some geometry
  derived from OpenStreetMap has separate ODbL conditions (not downloaded here).
- Racing Queensland may charge/restrict commercial use even for otherwise free files.
- Betfair source files are free to DOWNLOAD, but they retain data/database rights;
  rights to commercially exploit or redistribute them are NOT implied. Check their terms.
- GBGB module is included for the user's stated authorised personal research, subject to the scope of the permission.

SOURCES
https://betfair-datascientists.github.io/data/dataListing/
https://betfair-datascientists.github.io/modelling/dataSources/
https://www.racingqueensland.com.au/industry/greyhound/greyhound-sectionals
https://www.racingqueensland.com.au/about/reports-and-data/open-data
https://trapstats.co.uk/en/data
https://trapstats.co.uk/en/acceptable-use
https://github.com/bamboostack/au-racing-open-data

RESTART / TROUBLESHOOTING
If you close the CMD window or interrupt with Ctrl+C, run the same BAT again.
If you see 'HTTP 403' or '429', leave that source alone and check permission.
If you see 'INDEX FAILED', the source listing could not be accessed; this isn't
proof there were no races. Verify internet access and the published URL.
If you see 'schema not recognised', KEEP the downloaded ZIP/CSV and send the
headings from data/reports/schema_columns.csv or a sample file for parser tuning.
If a previously downloaded source file changed, its raw file is retained. For
future updates, copy the database as a backup before deleting a raw file.

Advanced:
  python collector.py collect --start 2024-01-01 --end 2024-01-07 --sources queensland --delay 2
  python collector.py collect --start 2025-01-01 --end 2025-12-31 --sources betfair
  python collector.py status
  python collector.py selftest
  python audit.py

GBGB 1.3 ADDENDUM - LOCAL DATABASE LOCK REPAIR
GBGB UK history now uses its own data/gbgb.sqlite3 database. The Australian
collector still uses data/dog_race.sqlite3. Do NOT delete the old database,
raw data, or SQLite WAL/SHM files. Existing GBGB raw JSON is reprocessed when
RUN_GBGB_PILOT.bat / RUN_GBGB_3_YEARS.bat resume. To run both regions, use
separate BAT files; two GBGB collection processes in the same folder are blocked.
If this folder is synced by OneDrive, move the whole DOG RACE folder to a local,
non-synced location before long runs. See WHAT_CHANGED_v1_3.txt.

GBGB 1.4 ADDENDUM - WINDOWS TEST CLEANUP REPAIR
A temporary SQLite connection in a test was not explicitly closed. On Windows,
RUN_TESTS.bat could then fail with WinError 32 deleting temp/gbgb.sqlite3.
Fixed and covered by an explicit-close regression test. The main AU collector
and CSV audit tool now also explicitly close their connections. No saved data
is deleted or reset. See WHAT_CHANGED_v1_4.txt.
