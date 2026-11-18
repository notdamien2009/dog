#!/usr/bin/env python3
"""Conservative one-time local cleanup for the verified DOG RACE GitHub layout.

Removes only known obsolete entry-point/document paths from the live project by
moving each file into an external, timestamped recovery backup. Never touches
racing data, Git internals, active private configuration, or unrecognised files.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SUPPORT = ROOT / '_cleanup_support'
REQUIRED = ('collector.py', 'gbgb.py', 'gbgb_normalize.py', 'uk_au_jobs.py')

# Reviewed against notdamien2009/dog main on 18 Nov 2026.
OLD_LAUNCHERS = (
    'CHECK_STATUS.bat',
    'CHECK_UK_AU_STATUS.bat',
    'GBGB_AUDIT_FIELDS.bat',
    'GBGB_BUILD_RACE_DATABASE.bat',
    'GBGB_CHECK_DATA_QUALITY.bat',
    'GBGB_CHECK_STATUS.bat',
    'INSPECT_GBGB_API.bat',
    'INSPECT_GBGB_MEETING.bat',
    'INSTALL_TOPAZ_SUPPORT.bat',
    'RUN_3_YEARS.bat',
    'RUN_GBGB_3_YEARS.bat',
    'RUN_GBGB_OLDER_YEARS.bat',
    'RUN_GBGB_PILOT.bat',
    'RUN_OLDER_YEARS.bat',
    'RUN_PILOT.bat',
    'RUN_TESTS.bat',
    'RUN_UK_AU_JOBS.bat',
    'RUN_UK_AU_PILOT.bat',
)
OLD_DOCUMENTS = (
    'GBGB_ACCESS_GUIDE.txt',
    'GBGB_ACCESS_REQUEST_EMAIL.txt',
    'README - START HERE.txt',
    'READ_ME_FIRST.txt',
    'README.txt',
    'README_UK_AU_JOBS.txt',
    'WHAT_CHANGED_v1_2.txt',
    'WHAT_CHANGED_v1_3.txt',
    'WHAT_CHANGED_v1_4.txt',
    'WHAT_CHANGED_v1_5.txt',
    'WHAT_CHANGED_v1_6.txt',
    'WHAT_CHANGED_v1_7.txt',
)
OLD_LEGACY_FILES = ('dog_race/collector.py',)
ARCHIVE = OLD_LAUNCHERS + OLD_DOCUMENTS + OLD_LEGACY_FILES

PROTECTED = (
    'data/', '.git/', '.gitignore', 'access.local.bat',
    'access.local.TEMPLATE.bat', 'SET_API_KEY.bat', 'config.json',
    'collector.py', 'gbgb.py', 'gbgb_normalize.py', 'gbgb_field_audit.py',
    'uk_au_jobs.py', 'audit.py', 'requirements.txt', 'tests/',
    'QA_AND_LIMITATIONS.txt',
)
# The existing launcher may have been locally installed from v2; upgrade only
# if it is exactly that reviewed release. Never overwrite custom changes.
CURRENT_HASHES = {
    'dog_race_manager.py': 'dd55f62405be8b5df6f1a3cd273849e5871726b267c46de16dfa7ebe0277c26f',
    'START_DOG_RACE.bat': 'd118409b92d56016bc11f279135d4b38798b38ba584928e2bbf07b95cf08769e',
}
OLDER_HASHES = {
    'dog_race_manager.py': '4f210579d80190211250164d3e5edfdc7eae6da7b354cf4f6704e20e876cb69c',
    'START_DOG_RACE.bat': 'd118409b92d56016bc11f279135d4b38798b38ba584928e2bbf07b95cf08769e',
}

NEW_README = """# DOG RACE — UK and Australian greyhound data

Private local research with source-controlled Python collectors. **Historical race
records and credentials stay on this PC**, never in GitHub.

## Start here

Double-click **START_DOG_RACE.bat** to open the single collector menu.
Option **1** shows database counts and access readiness. Option **2** runs tests.
Option **C** produces quality/coverage reports. **F** opens advanced utilities.
Use the appropriate pilot before starting any multi-year collection.

## Core collectors (do not delete)

- `gbgb.py` and `gbgb_normalize.py`: GBGB meetings, UK races and runners;
  local database `data/gbgb.sqlite3`.
- `collector.py`: public Betfair AU/NZ monthly archives, Queensland reports,
  and optional other published files; local database `data/dog_race.sqlite3`.
- `uk_au_jobs.py`: UK Betfair Historical Data and official AU performance;
  local database `data/dog_uk_au.sqlite3`.
- `gbgb_field_audit.py` and `audit.py`: diagnostics and source coverage.
- `tests/`: offline regression checks.

## Credentials and availability

- UK Betfair needs historical Greyhound Basic months acquired in **My Data**
  and an authorised session token set in `access.local.bat`.
- Topaz needs its own approved API key. Without one, Australian performance
  collection is limited to published Queensland sectional CSVs, **not**
  complete Australian race results.
- `access.local.bat`, `SET_API_KEY.bat` (if present), and `config.json`
  remain private/local. Never push them or the `data/` directory to GitHub.

## Data and model limitations

- Counts of raw rows, races and Betfair markets are not proof of complete
  track/date or runner-level performance coverage.
- GBGB first-sectional marker distances have not all been verified.
- Betfair ANZ processed files have market prices and winner/place results,
  **not** individual finishing times, weights or lengths beaten.
- UK Betfair Basic importer currently does not store a usable full pre-race
  price movement series. No betting strategy or speed figure is certified.
- Track-adjusted speed and finishing-strength models still require QA.

## Safe cleanup

`CLEAN_DOG_RACE.bat` moves reviewed old BAT files, old version documents and
one unused legacy collector into a **recovery backup outside this folder**.
It does not delete databases or unrecognised scripts. Keep its backup until
collection and offline tests have been verified on Windows.

GitHub: https://github.com/notdamien2009/dog
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_root(root: Path) -> None:
    missing = [n for n in REQUIRED if not (root / n).is_file()]
    if missing:
        raise RuntimeError('Wrong folder. Missing collector modules: ' + ', '.join(missing))
    if not (root / 'tests').is_dir():
        raise RuntimeError('Missing tests/; refusing to clean an incomplete project')
    if root.is_symlink():
        raise RuntimeError('Project root is a symlink; refusing to clean')


def support_plan(root: Path) -> list[tuple[str, str]]:
    actions = []
    for name in ('START_DOG_RACE.bat', 'dog_race_manager.py'):
        current = root / name
        proposed = root / '_cleanup_support' / name
        if current.is_symlink() or proposed.is_symlink():
            raise RuntimeError(f'Unexpected symlink: {name}')
        if not proposed.is_file() and not current.is_file():
            raise RuntimeError(f'Missing {name}: include the packaged _cleanup_support folder')
        if not proposed.is_file():
            # After one successful clean, bootstrap files are outside the project.
            # Do not assume an unknown local launcher implements retired features.
            if sha256(current) != CURRENT_HASHES[name]:
                raise RuntimeError(f'Cannot verify existing {name}; include the reviewed _cleanup_support files')
            actions.append((name, 'already installed; not modified'))
            continue
        if not current.exists():
            actions.append((name, 'install from included command centre'))
        elif sha256(current) == sha256(proposed):
            actions.append((name, 'already on current release'))
        elif sha256(current) == OLDER_HASHES[name]:
            actions.append((name, 'upgrade reviewed v2; old version backed up'))
        else:
            raise RuntimeError(
                f'{name} was locally modified or comes from an unreviewed version. '
                'No old launcher will be retired; please preserve it for review.'
            )
    return actions


def archive_names(root: Path) -> list[Path]:
    found = []
    for name in ARCHIVE:
        path = root / Path(name)
        if path.is_symlink():
            raise RuntimeError(f'Unexpected symlink; cannot clean: {name}')
        if path.is_file():
            found.append(path)
        elif path.exists():
            raise RuntimeError(f'Expected a file; refusing to modify: {name}')
    return found


def print_plan(root: Path, files: list[Path], commands: list[tuple[str, str]]) -> None:
    print('=' * 68)
    print('DOG RACE — SAFE CLEANUP PREVIEW')
    print('=' * 68)
    print('Project:', root)
    print('Reviewed obsolete files:', len(files))
    for path in files:
        print('  MOVE TO BACKUP:', path.relative_to(root))
    readme = root / 'README.md'
    if readme.exists() and readme.read_text(encoding='utf-8-sig').strip() == '# dog':
        print('  UPDATE README.md (old placeholder backed up)')
    elif not readme.exists():
        print('  CREATE README.md')
    else:
        print('  KEEP README.md (locally edited)')
    for name, action in commands:
        print('  COMMAND CENTRE:', name, '-', action)
    print('\nPROTECTED; NEVER MOVED:')
    print('  data/ (all downloaded archives/SQLite databases/WAL/locks), .git/')
    print('  active credentials and config, collectors, tests, QA, unrecognised files')
    print('  SET_API_KEY.bat is intentionally NOT removed; may hold a private key.')
    print('\nOld files move to a timestamped sibling backup on your Desktop.')
    print('No remote GitHub changes are made.')


def apply(root: Path, items: list[Path], actions: list[tuple[str, str]]) -> Path:
    base = root.parent / (root.name + ' - CLEANUP BACKUPS')
    base.mkdir(parents=True, exist_ok=True)
    tag = dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = base / tag
    backup.mkdir(parents=True, exist_ok=False)
    entries: list[dict[str, str]] = []

    def move_to_backup(path: Path) -> None:
        relative = path.relative_to(root)
        dest = backup / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            raise RuntimeError(f'Backup target already exists: {dest}')
        checksum = sha256(path)
        shutil.move(str(path), str(dest))
        entries.append({'path': relative.as_posix(), 'sha256': checksum, 'backup': str(dest)})

    # Ensure the replacement command centre exists before removing any BAT.
    for name, action in actions:
        current, proposed = root / name, SUPPORT / name
        if 'upgrade' in action:
            move_to_backup(current)
            shutil.copy2(proposed, current)
        elif action.startswith('install'):
            shutil.copy2(proposed, current)

    for path in items:
        move_to_backup(path)
    legacy_dir = root / 'dog_race'
    if legacy_dir.is_dir() and not any(legacy_dir.iterdir()):
        legacy_dir.rmdir()

    readme = root / 'README.md'
    if not readme.exists():
        readme.write_text(NEW_README, encoding='utf-8', newline='\n')
    elif readme.is_file() and readme.read_text(encoding='utf-8-sig').strip() == '# dog':
        move_to_backup(readme)
        readme.write_text(NEW_README, encoding='utf-8', newline='\n')

    # Supplied bootstrap files are transient; only known files are moved.
    for name in ('START_DOG_RACE.bat', 'dog_race_manager.py'):
        source = SUPPORT / name
        if source.is_file():
            move_to_backup(source)
    if SUPPORT.is_dir() and not any(SUPPORT.iterdir()):
        SUPPORT.rmdir()

    report = {
        'created_local': dt.datetime.now().isoformat(),
        'original_project': str(root),
        'backup_dir': str(backup),
        'files_archived': entries,
        'files_preserved': list(PROTECTED),
        'github_push_performed': False,
        'permanent_deletion_performed': False,
    }
    (backup / 'MANIFEST.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    (backup / 'RESTORE_INSTRUCTIONS.txt').write_text(
        'Close all DOG RACE processes before restoration.\n'
        'Every file listed in MANIFEST.json is stored under its original relative path.\n'
        'Copy selected files back to the DOG RACE folder if an old feature is needed.\n'
        'Do not overwrite newer working code without comparing the content.\n',
        encoding='utf-8',
    )
    return backup


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Safe DOG RACE known-file cleanup')
    parser.add_argument('--preview', action='store_true', help='Show files without modifying anything')
    args = parser.parse_args(argv)
    try:
        ensure_root(ROOT)
        commands = support_plan(ROOT)
        files = archive_names(ROOT)
        print_plan(ROOT, files, commands)
        if args.preview:
            return 0
        readme = ROOT / 'README.md'
        readme_needs_work = not readme.exists() or (readme.is_file() and readme.read_text(encoding='utf-8-sig').strip() == '# dog')
        support_present = any((SUPPORT / n).is_file() for n in ('START_DOG_RACE.bat', 'dog_race_manager.py'))
        changes_pending = bool(files or readme_needs_work or support_present or
                               any(a.startswith(('install', 'upgrade')) for _,a in commands))
        if not changes_pending:
            print('\nAlready clean. No changes or backup needed.')
            return 0
        print('\nClose all running DOG RACE collectors and SQLite database viewers first.')
        if input('Type CLEAN to archive these obsolete files: ').strip() != 'CLEAN':
            print('Cancelled — nothing changed.')
            return 0
        backup = apply(ROOT, files, commands)
        print('\nCLEANUP COMPLETE: Obsolete files removed from the live project.')
        print('Recovery backup:', backup)
        print('No databases, credentials, downloads or git history changed.')
        print('NEXT: Run START_DOG_RACE.bat and use option 2 for offline tests.')
        print('Then inspect git status before committing reviewed cleanup on GitHub.')
        return 0
    except (OSError, ValueError, RuntimeError, UnicodeError) as exc:
        print('\nCLEANUP STOPPED:', exc)
        print('No further cleanup will be attempted; keep any recovery backup.')
        return 2


if __name__ == '__main__':
    sys.exit(main())
