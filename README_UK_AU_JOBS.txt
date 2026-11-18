DOG RACE v1.7 — UK BETFAIR + AU PERFORMANCE
==================================================

ONE BAT FILE. TWO INDEPENDENT JOBS.
Run RUN_UK_AU_PILOT.bat first. Read data/reports/uk_au_collection_status.txt.
Then run RUN_UK_AU_JOBS.bat to collect 2023-11-18 through 2026-11-17.
Running again reuses cached files. Existing GBGB/Betfair AU data are untouched.

JOB 1: UK BETFAIR (EXCHANGE ODDS AND SETTLED RESULTS)
--------------------------------------------------
SOURCE: Official Betfair Historical Data service:
  https://historicdata.betfair.com/
  https://support.developer.betfair.com/hc/en-us/articles/12859956891932-How-Can-I-Make-HTTP-Requests-to-the-Historical-Data-API

Before automation:
1. Sign into https://historicdata.betfair.com/ with your Betfair account.
2. Buy/add the FREE Basic Plan Greyhound Racing months you need to My Data.
   "Free" is still an item requiring account acquisition; this script cannot do it.
3. Create a Betfair SSO session token from Betfair's supported API login.
4. Set DOG_BETFAIR_SSOID using access.local.bat (see below) or Windows environment.
5. Keep the token private. It expires; refresh if the job reports authentication failure.

This job uses POST /api/GetMyData; /api/DownloadListOfFiles; GET /api/DownloadFile
with an ssoid header. Filters: Greyhound Racing, Basic Plan, GB only, WIN/PLACE,
market files (M). It stores original .bz2 market-stream files and extracts
market details, settlement outcomes (when present), and numeric actual BSP from
stream runner changes (when present). A missing BSP stays NULL, not fabricated.
No live bets are placed. No in-play trading is used.

NOTE: Archive Basic streaming data and downloaded Betfair ANZ processed CSVs are
not identical products. We deliberately store these under separate sources.
There is no claim of tick-level price data from Basic. Verify completeness of
owned months and account-level eligibility with the status report.

JOB 2: AUSTRALIAN RACE PERFORMANCE
---------------------------------
A. QUEENSLAND: public Racing Queensland CSV listing:
   https://www.racingqueensland.com.au/industry/greyhound/greyhound-sectionals
   Downloads ONLY the exact existing CSV links listed on that official index.
   Stores raw CSV, every original row, date, track. Race-level/runner-level
   parsing is NOT falsely inferred from an unverified CSV layout.
   Coverage only for dates/venues actually listed by Racing Queensland.

B. TOPAZ: official historical bulk race performances for states NSW, QLD,
   VIC, SA, WA, TAS and NT. Topaz is optional and REQUIRES a granted API key.
   Reference https://betfair-datascientists.github.io/modelling/topazTutorial/
   Personal research access is subject to the provider's actual permissions.
   Betfair AU's programme has eligibility criteria; it is NOT automatically
   available to UK accounts. Enquire: automation@betfair.com.au.
   GRV can be contacted separately for read-only research use.

   If you have a key:
   - Run INSTALL_TOPAZ_SUPPORT.bat once (topaz_api and pandas packages).
   - Set DOG_TOPAZ_KEY in access.local.bat.
   - Source monthly CSV files are kept, with a normalised SQLite table.
   - FINISH TIMES, first/second split, run time, dog weight, box, margins,
     prices and finishing position use values exactly as Topaz provides.
   - NULL signifies missing source data; never estimate it as a real reading.

ACCESS CONFIGURATION
--------------------
Copy 'access.local.TEMPLATE.bat' to 'access.local.bat', then edit the new file.
It is NOT included in ZIP and should stay ONLY on your personal Windows PC.
It contains unencrypted secrets. Never share or upload access.local.bat.
Alternatively set Windows environment variables DOG_BETFAIR_SSOID and
DOG_TOPAZ_KEY in the CMD session and launch uk_au_jobs.py directly.
If either token is missing, that source is skipped; the other continues.

OPERATIONS
----------
RUN_UK_AU_PILOT.bat     First step, small official-source sample only
RUN_UK_AU_JOBS.bat      Three-year collector (safe to restart)
CHECK_UK_AU_STATUS.bat  Print local rows, date coverage and error counts
INSTALL_TOPAZ_SUPPORT.bat    Optional Topaz dependency installer
RUN_TESTS.bat          Existing tests + new offline tests

Use Python 3.10+ on Windows. The default Betfair + Queensland job uses only
built-in Python modules. Topaz needs third-party dependencies and an API key.
The data layout is:
  data/dog_uk_au.sqlite3   NEW independent database
  data/raw/betfair_uk/    Original authorised UK market streams
  data/raw/au_qld/        Original Queensland CSV files
  data/raw/au_topaz/      Original Topaz Australian performance CSVs
  data/reports/uk_au_collection_status.txt

Important QA limits:
- Race/runner market-to-GBGB linkage NOT implemented until real UK samples
  are validated (track/time variations, named runners, dead heats).
- Queensland CSV structural mapping only after a real sample is inspected.
- Never use completed-date counts from one provider to claim complete AU
  or UK racing coverage from every track.
- Dates in a partial month are filtered at import; raw Topaz monthly CSVs
  may include dates outside the requested period, by source design.
- Use only rights granted by each provider. No CAPTCHA/login bypass.
- Re-running always recomputes source-file SHA256; expensive for huge archives.
- Betfair UK source files can be numerous and require plenty of free disk space.
- HTTP 401/403 stops the affected source; HTTP 429 backs off.
- Program never modifies the original v1.6 GBGB SQLite database.
- The separate UK/AU SQLite database allows independent progress and reporting.

Do NOT start three years until the pilot shows actual downloaded/parsed data.
If no Betfair data is entitled or Topaz access is absent, the programme will
report SKIPPED; that is not successful three-year collection.

ADDITIONAL COVERAGE REPORTS
---------------------------
  data/reports/uk_au_monthly_coverage.csv  – month-by-month coverage gaps
  data/reports/au_topaz_by_state.csv       – seven Australian states individually
Zero monthly records means missing from LOCAL STORE, not necessarily no races.
