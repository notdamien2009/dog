#!/usr/bin/env python3
"""Non-destructive Windows launcher for existing DOG RACE collectors.

No schema changes, no database writes of its own, no deletion of legacy files.
Intended to live beside gbgb.py, collector.py, uk_au_jobs.py.
"""
from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
START = '2023-11-18'
END = '2026-11-17'
OLDER_START = '2020-11-18'
OLDER_END = '2023-11-17'

DATABASES = (
    ('UK GBGB', 'gbgb.sqlite3', ('gbgb_index', 'gbgb_meetings', 'gbgb_races', 'gbgb_runners')),
    ('AU/NZ public downloads', 'dog_race.sqlite3',
     ('downloads', 'market_runners', 'races', 'runners', 'rq_csv_cells')),
    ('UK Betfair / AU Topaz & QLD', 'dog_uk_au.sqlite3',
     ('source_files', 'bf_markets', 'bf_runners', 'au_topaz_runs', 'au_qld_sectional_rows')),
)


def db_status(path: Path, table_names: tuple[str, ...]) -> list[tuple[str, int | str]]:
    """Inspect an existing SQLite DB in read-only mode. Never create databases."""
    if not path.is_file():
        return [('Database', 'not created yet')]
    try:
        # Path.resolve().as_uri() supports Windows drive paths and spaces.
        uri = path.resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=3)) as db:
            existing = {x[0] for x in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            result: list[tuple[str, int | str]] = []
            for table in table_names:
                if table in existing:
                    # Table names come from hardcoded constants, not user input.
                    n = db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    result.append((table, n))
            return result or [('Tables', 'no expected tables found')]
    except sqlite3.Error as exc:
        return [('Database read error', str(exc))]


def show_status() -> None:
    print('\n' + '=' * 68)
    print('DOG RACE - LOCAL DATABASE STATUS (read only)')
    print('=' * 68)
    for title, filename, tables in DATABASES:
        print(f'\n{title}: data/{filename}')
        for key, value in db_status(DATA / filename, tables):
            print(f'  {key:<26} {value:>12,}' if isinstance(value, int)
                  else f'  {key:<26} {value}')
    print('\nNote: row counts cannot prove complete date/track coverage.')
    print('Raw results, partial rows and settled betting outcomes are distinct.')
    print('GitHub does not contain the local data directory.')


def credential_status() -> None:
    print('\nACCESS:')
    print('  UK Betfair: ' + ('session token configured (validity unverified)'
                                  if os.getenv('DOG_BETFAIR_SSOID') else 'not configured'))
    print('  AU Topaz:   ' + ('API key configured (validity unverified)'
                                  if os.getenv('DOG_TOPAZ_KEY') else 'not configured'))
    print('  GBGB:      uses its existing authorised-results collector')
    print('  QLD:       public published CSVs; partial coverage')
    print('  Secrets are never displayed.')


def run_one(label: str, argv: list[str]) -> bool:
    script = argv[0]
    if script.endswith('.py') and not (ROOT / script).is_file():
        print(f'ERROR: Missing {script}; check that this launcher is in DOG RACE root.')
        return False
    cmd = [sys.executable, '-u', *argv]
    print(f'\n--- {label} ---', flush=True)
    try:
        p = subprocess.run(cmd, cwd=ROOT, check=False)
    except KeyboardInterrupt:
        print('\nInterrupted; existing collector checkpoints remain on disk.')
        return False
    except OSError as exc:
        print(f'Cannot start job: {exc}')
        return False
    if p.returncode != 0:
        print(f'JOB FAILED/STOPPED: {label} (exit code {p.returncode})')
        return False
    print(f'Command exited: {label}. VERIFY COVERAGE before claiming completion.')
    return True


def call(label: str, script: str, *args: str) -> bool:
    return run_one(label, [script, *args])


def confirm_full() -> bool:
    print('This can run for a long time and make many permitted source requests.')
    print('Existing data is retained; do not run another collector in parallel.')
    return input('Type RUN to continue: ').strip() == 'RUN'


def run_option(option: str) -> None:
    if option == '1':
        show_status()
        credential_status()
    elif option == '2':
        run_one('Offline tests', ['-m', 'unittest', 'discover', '-s', 'tests', '-v'])
    elif option == '3':
        if call('GBGB pilot - 2024-06-01', 'gbgb.py', 'collect', '--start', '2024-06-01', '--end', '2024-06-01', '--delay', '3'):
            if call('GBGB pilot - 2026-11-16', 'gbgb.py', 'collect', '--start', '2026-11-16', '--end', '2026-11-16', '--delay', '3'):
                call('GBGB normalised report', 'gbgb_normalize.py', 'report')
    elif option == '4':
        if confirm_full() and call('GBGB 3 years', 'gbgb.py', 'collect', '--start', START, '--end', END, '--delay', '3'):
            call('GBGB final QA', 'gbgb_normalize.py', 'report')
    elif option == '5':
        if confirm_full():
            call('AU/NZ Betfair published archives', 'collector.py', 'collect', '--sources', 'betfair', '--start', START, '--end', END)
            print('Check the database status. Older published files may already be cached.')
    elif option in ('6', '7', 'A'):
        token = os.getenv('DOG_BETFAIR_SSOID')
        if not token:
            print('UK Betfair NOT STARTED: DOG_BETFAIR_SSOID is missing.')
            print('Get authorised historical datasets and set a session token in access.local.bat.')
            if option != 'A':
                return
        if option == '6':
            call('UK Betfair small pilot', 'uk_au_jobs.py', 'pilot', '--uk', '--start', START, '--end', END)
        elif option == '7':
            if confirm_full():
                call('UK Betfair historical 3 years', 'uk_au_jobs.py', 'collect', '--uk', '--start', START, '--end', END)
        else:
            if confirm_full():
                if token:
                    call('UK Betfair 3 years', 'uk_au_jobs.py', 'collect', '--uk', '--start', START, '--end', END)
                else:
                    print('UK Betfair skipped (no session token).')
                print('AU results job: QLD public CSVs, plus Topaz only when configured.')
                call('Australian performance', 'uk_au_jobs.py', 'collect', '--au', '--start', START, '--end', END)
    elif option in ('8', '9'):
        if not os.getenv('DOG_TOPAZ_KEY'):
            print('No Topaz key: this will ONLY collect QLD publicly listed sectional CSVs.')
            print('It will NOT obtain complete Australian national results.')
        if option == '8':
            call('Australian performance pilot', 'uk_au_jobs.py', 'pilot', '--au', '--start', START, '--end', END)
        elif confirm_full():
            call('Australian performance 3 years', 'uk_au_jobs.py', 'collect', '--au', '--start', START, '--end', END)
    elif option == 'B':
        if call('GBGB re-import cached meeting records', 'gbgb_normalize.py', 'backfill'):
            call('GBGB quality report', 'gbgb_normalize.py', 'report')
    elif option == 'C':
        call('GBGB API/database status', 'gbgb.py', 'status')
        call('GBGB normalised quality', 'gbgb_normalize.py', 'report')
        call('AU/NZ downloads status', 'collector.py', 'status')
        call('AU/NZ detailed audit files', 'audit.py')
        call('UK Betfair / AU results status', 'uk_au_jobs.py', 'status')
    elif option == 'D':
        if confirm_full():
            call('Older GBGB history', 'gbgb.py', 'collect', '--start', OLDER_START, '--end', OLDER_END, '--delay', '3')
    elif option == 'E':
        if confirm_full():
            call('Older AU/NZ published odds archive', 'collector.py', 'collect', '--sources', 'betfair', '--start', OLDER_START, '--end', OLDER_END)
    elif option == 'F':
        advanced_menu()
    else:
        print('Unknown menu option.')


def advanced_menu() -> None:
    """Preserve the specialist workflows previously offered by separate BAT files."""
    while True:
        print('\nADVANCED TOOLS (old individual BAT functions)')
        print(' 1  Audit GBGB cached JSON fields')
        print(' 2  Inspect one GBGB historical date')
        print(' 3  Inspect one GBGB meeting')
        print(' 4  Public downloads small pilot (AU/NZ, QLD, open UK sample)')
        print(' 5  All public download sources, 2023-2026')
        print(' 6  All public download sources, 2020-2023')
        print(' 7  Generate AU/NZ CSV audit reports')
        print(' 8  Install optional Topaz Python support')
        print(' 9  UK Betfair + Australian performance pilot')
        print(' 0  Back')
        try:
            choice = input('Choose advanced tool: ').strip().upper()
        except (KeyboardInterrupt, EOFError):
            return
        if choice == '0':
            return
        if choice == '1':
            call('GBGB cached field audit', 'gbgb_field_audit.py')
        elif choice == '2':
            call('One GBGB date inspection', 'gbgb.py', 'inspect', '--date', '2024-06-01')
        elif choice == '3':
            call('One GBGB meeting inspection', 'gbgb.py', 'inspect-meeting', '--meeting', '415753')
        elif choice == '4':
            call('Public sources pilot', 'collector.py', 'collect', '--start', '2026-10-01', '--end', END, '--pilot')
        elif choice == '5':
            if confirm_full():
                call('All public historical sources', 'collector.py', 'collect', '--start', START, '--end', END)
        elif choice == '6':
            if confirm_full():
                call('Older public historical sources', 'collector.py', 'collect', '--start', OLDER_START, '--end', OLDER_END)
        elif choice == '7':
            call('AU/NZ public archive audit', 'audit.py')
        elif choice == '8':
            print('Installs optional Python packages topaz_api and pandas; not a data download.')
            if input('Type INSTALL to proceed: ').strip() == 'INSTALL':
                run_one('Topaz support install', ['-m', 'pip', 'install', '--upgrade', 'topaz_api', 'pandas'])
        elif choice == '9':
            call('UK Betfair + AU results pilot', 'uk_au_jobs.py', 'pilot', '--start', START, '--end', END)
        else:
            print('Unknown advanced option.')


def menu() -> None:
    while True:
        print('\n' + '=' * 68)
        print('                  DOG RACE - COMMAND CENTRE')
        print('    UK and Australian data collection | Existing data preserved')
        print('=' * 68)
        print(' 1  View all local database counts and access readiness')
        print(' 2  Run offline tests')
        print(' 3  GBGB two-date pilot')
        print(' 4  GBGB full 3-year historical results')
        print(' 5  AU/NZ Betfair published market archives (3 years)')
        print(' 6  UK Betfair small pilot (token needed)')
        print(' 7  UK Betfair 3 years (token needed)')
        print(' 8  Australian official data pilot (QLD / optional Topaz)')
        print(' 9  Australian official data 3 years (QLD / optional Topaz)')
        print(' A  UK Betfair + AU performance (sequential, configured sources)')
        print(' B  Rebuild GBGB runner tables from downloaded JSON')
        print(' C  Detailed source status + quality reports')
        print(' D  Older GBGB history, 2020-2023')
        print(' E  Older AU/NZ Betfair archives, 2020-2023')
        print(' F  Advanced tools: audits, inspectors, all-source runs, Topaz setup')
        print(' 0  Exit')
        try:
            choice = input('\nSelect: ').strip().upper()
            if choice == '0':
                return
            run_option(choice)
            input('\nPress Enter to return to the menu...')
        except (KeyboardInterrupt, EOFError):
            print('\nExiting menu. Existing data untouched.')
            return


if __name__ == '__main__':
    if not (ROOT / 'gbgb.py').exists():
        print('This command centre must be copied into the DOG RACE project root.')
        sys.exit(2)
    menu()
