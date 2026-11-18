import csv
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import contextlib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gbgb
import gbgb_field_audit as audit

class GBGBFieldAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'gbgb.sqlite3'
        with contextlib.closing(gbgb.open_db(self.db)) as conn:
            index_item = {'meetingId': 123, 'raceId': 567, 'dogId': 1,
                          'dogName': 'TEST DOG', 'trapNumber': 4,
                          'trackName': 'Test Track', 'raceDistance': 500}
            gbgb.import_index(conn, '2024-06-01', 1,
                              {'items': [index_item], 'meta': {'pageCount': 1}})
            gbgb.import_meeting(conn, '123', '2024-06-01',
                                [{'meetingId': 123, 'meetingDate': '01/06/2024',
                                  'trackName': 'Test Track',
                                  'races': [{'raceId': 567, 'raceDate': '01/06/2024',
                                             'raceDistance': 500, 'traps': [
                                             {'trapNumber':'1', 'dogId':1, 'dogName': 'TEST DOG',
                                              'resultRunTime': '29.52', 'resultSectionalTime':'4.12'},
                                             {'trapNumber':'2', 'dogId':2, 'dogName': 'OTHER DOG',
                                              'resultRunTime': '29.78', 'resultSectionalTime':'4.19'}]}]}])
            conn.execute('INSERT INTO gbgb_date_coverage VALUES (?,?,?,?,?,?)',
                         ('2024-06-01', 1, 1, 1, 1, gbgb.now()))
            conn.commit()

    def test_schema_paths_and_candidate_report(self):
        result = audit.run_audit(self.db, self.root / 'out')
        self.assertEqual(result['meetings'], 1)
        self.assertEqual(result['index_rows'], 1)
        with (self.root / 'out' / 'gbgb_all_fields.csv').open(encoding='utf-8-sig', newline='') as fh:
            rows = list(csv.DictReader(fh))
        self.assertTrue(any(row['json_path'] == '$[].races[].traps[].resultRunTime'
                            and int(row['value_count']) == 2 for row in rows))
        self.assertTrue(any(row['json_path'] == '$[].races[].traps[].resultSectionalTime'
                            and int(row['value_count']) == 2 for row in rows))
        candidates = (self.root / 'out' / 'gbgb_timing_candidates.csv').read_text(encoding='utf-8-sig')
        self.assertIn('resultRunTime', candidates)
        self.assertIn('resultSectionalTime', candidates)
        sample = json.loads((self.root / 'out' / 'gbgb_meeting_structure_samples.json').read_text())
        self.assertEqual(sample['samples'][0]['meeting_id'], '123')
        self.assertIn('Index records (not necessarily races): 1',
                      (self.root / 'out' / 'gbgb_field_audit.txt').read_text())
        with contextlib.closing(audit.read_conn(self.db)) as readonly:
            with self.assertRaises(sqlite3.OperationalError):
                readonly.execute('DELETE FROM gbgb_index')
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM gbgb_index').fetchone()[0], 1)

    def test_missing_db_is_friendly_error(self):
        with self.assertRaises(FileNotFoundError):
            audit.run_audit(self.root / 'missing.sqlite3', self.root / 'out')

if __name__ == '__main__':
    unittest.main()
