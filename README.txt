DOG RACE — UK + AU historical collector

IMPORTANT:
This build uses the documented Greyhound Racing API rather than scraping a site's HTML.
You need an API key from the provider before running the collector.

1. Install Python 3.11+ on Windows.
2. Run RUN_TESTS.bat.
3. Run SET_API_KEY.bat and paste your API key.
4. Run RUN_3_YEARS.bat.
5. Run CHECK_STATUS.bat at any time.

Default range:
2023-11-18 through 2026-11-17
Countries:
GB, AU

The collector:
- saves every raw JSON response;
- imports races and runners into SQLite;
- resumes already-successful date/page requests;
- records failed requests;
- prints progress in the CMD window.

Do not start a bulk run until your API plan/terms permit the historical volume you intend to collect.
