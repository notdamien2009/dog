import contextlib
import csv
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gbgb
import gbgb_normalize as norm


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.conn = gbgb.open_db(self.root / 'gbgb.sqlite3')
        self.addCleanup(self.conn.close)

    @staticmethod
    def fixture():
        # Exact observed field NAMES and nesting from user-supplied GBGB audit.
        # Representative records constructed from a truncated preview; no live DB.
        return [{
            'meetingDate': '01/06/2024', 'meetingId': 411403, 'trackName': 'Towcester',
            'races': [
                {'raceTime': '18:01:00', 'raceDate': '01/06/2024', 'raceId': 1041545,
                 'raceNumber': '1', 'raceType': 'Flat', 'raceHandicap': False,
                 'raceClass': 'A3', 'raceDistance': 500.0, 'raceGoing': '0',
                 'traps': [
                    {'trapNumber': '5', 'dogId': 638670, 'dogName': 'Romeo Revere'},
                    {'trapNumber': '4', 'dogId': 629768, 'dogName': 'Fabulous Aurora',
                     'dogSire': 'Kilty Lad', 'dogDam': 'Drive On Betsy', 'dogBorn':'Nov-2021',
                     'dogOrigin': 'Br', 'dogColour':'bd','dogSex':'b',
                     'trainerName':'L G Tuffin','ownerName':'Mr D P Firmager',
                     'SP':'7/4F','resultPosition':1,'resultMarketPos':1,
                     'resultPriceNumerator':7,'resultPriceDenominator':4,
                     'resultBtnDistance':None, 'resultSectionalTime':'04.28',
                     'resultComment':'Mid,LdRnIn,FinWll', 'resultRunTime':'29.74',
                     'resultDogWeight':'25.9', 'resultAdjustedTime':'29.74'}]},
                {'raceTime': '18:17:00', 'raceDate': '01/06/2024', 'raceId': 1041546,
                 'raceNumber': '2', 'raceType':'Flat', 'raceClass':'D2',
                 'raceDistance': 270.0, 'raceGoing':'-20',
                 'traps': [
                    {'trapNumber':'2','dogId':626563,'dogName':'Makeit Arla',
                     'resultPosition':1,'resultSectionalTime':None,
                     'resultRunTime':'15.91','resultAdjustedTime':'15.91','resultDogWeight':'29.5'},
                    {'trapNumber':'5','dogId':614815,'dogName':'Makeit Buck',
                     'resultPosition':2,'resultBtnDistance':'3 1/2',
                     'resultSectionalTime':None,'resultRunTime':'16.20',
                     'resultAdjustedTime':'16.20','resultDogWeight':'31.2'}]}
            ]}]

    def test_real_audit_fields_map_correctly(self):
        o = self.fixture()
        result = gbgb.import_meeting(self.conn, '411403', '2024-06-01', o)
        self.assertEqual((result['races'], result['traps'], result['finished'],
                          result['timed'], result['sectionals']), (2, 4, 3, 3, 1))
        winner = self.conn.execute('''SELECT race_id,dog_name,finish_position,
                sectional_time_s,run_time_s,adjusted_time_s,dog_weight_kg,
                price_numerator,price_denominator FROM gbgb_runners
                WHERE dog_id='629768' ''').fetchone()
        self.assertEqual(winner, ('1041545','Fabulous Aurora',1,4.28,29.74,29.74,25.9,7,4))
        self.assertEqual(self.conn.execute('SELECT race_going_raw FROM gbgb_races WHERE race_id=?',
                                           ('1041546',)).fetchone()[0], '-20')
        self.assertEqual(self.conn.execute('SELECT beaten_distance_raw FROM gbgb_runners WHERE dog_id=?',
                                           ('614815',)).fetchone()[0], '3 1/2')
        self.assertEqual(self.conn.execute('SELECT finish_position FROM gbgb_runners WHERE dog_id=?',
                                           ('638670',)).fetchone()[0], None)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM gbgb_meetings').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM gbgb_runners').fetchone()[0], 4)

    def test_reimport_idempotent(self):
        o=self.fixture()
        gbgb.import_meeting(self.conn,'411403','2024-06-01',o)
        r=gbgb.import_meeting(self.conn,'411403','2024-06-01',o)
        self.assertEqual(r['state'],'unchanged')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM gbgb_races').fetchone()[0],2)

    def test_refresh_removes_old_runners_preserves_other_meeting(self):
        o=self.fixture()
        gbgb.import_meeting(self.conn,'411403','2024-06-01',o)
        other=[{'meetingId':987,'meetingDate':'02/06/2024','trackName':'Test',
                'races':[{'raceId':987321,'raceDate':'02/06/2024','traps':[{'trapNumber':'1'}]}]}]
        gbgb.import_meeting(self.conn,'987','2024-06-02',other)
        o[0]['races'][0]['traps'].pop()
        gbgb.import_meeting(self.conn,'411403','2024-06-01',o)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM gbgb_runners').fetchone()[0],4)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM gbgb_runners WHERE race_id=?',
                                           ('987321',)).fetchone()[0],1)

    def test_bad_second_import_keeps_old_rows_and_raw_stage(self):
        o=self.fixture()
        gbgb.import_meeting(self.conn,'411403','2024-06-01',o)
        o[0]['races'][0]['traps'][1]['trapNumber']='5'
        with self.assertRaisesRegex(gbgb.GBGBError,'duplicate trap'):
            gbgb.import_meeting(self.conn,'411403','2024-06-01',o)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbgb_runners').fetchone()[0],4)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbgb_meetings').fetchone()[0],1)

    def test_bad_race_id_mismatch_rejected(self):
        o=self.fixture();o[0]['meetingId']=777
        with self.assertRaisesRegex(norm.NormalizationError,'meetingId'):
            norm.normalize_meeting(self.conn, '411403',o)

    def test_cross_meeting_race_collision_rejected(self):
        o=self.fixture()
        gbgb.import_meeting(self.conn,'411403','2024-06-01',o)
        other=[{'meetingId':999, 'meetingDate':'01/06/2024','trackName':'Other',
                'races':[{'raceId':1041545,'raceDate':'01/06/2024','traps':[]}]}]
        with self.assertRaisesRegex(norm.NormalizationError,'already belongs'):
            norm.normalize_meeting(self.conn,'999',other)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbgb_races').fetchone()[0],2)

    def test_backfill_and_coverage_reports(self):
        o=self.fixture()
        gbgb.import_meeting(self.conn,'411403','2024-06-01',o)
        self.conn.execute('DELETE FROM gbgb_runners')
        self.conn.execute('DELETE FROM gbgb_races')
        self.conn.execute('DELETE FROM gbgb_meeting_imports')
        self.conn.commit()
        self.assertEqual(norm.backfill(self.conn,False),(1,1))
        self.assertEqual(norm.backfill(self.conn,False),(1,0))
        result=norm.coverage_report(self.conn,self.root/'reports')
        self.assertEqual(result['matched_meetings'],1)
        self.assertEqual(result['times'],3)
        self.assertEqual(result['sectionals'],1)
        text=(self.root/'reports'/'gbgb_normalized_quality.txt').read_text(encoding='utf-8')
        self.assertIn('First sectional times: 1',text)
        with (self.root/'reports'/'gbgb_track_coverage.csv').open(encoding='utf-8-sig') as f:
            rows=list(csv.DictReader(f))
        self.assertEqual(rows[0]['track'],'Towcester')

    def test_uploaded_audit_preview_real_field_mapping(self):
        # Truncated audit: only 2 races and 3 recorded finishers survive preview.
        # This validates FIELD NAMES, not entire-meeting or 3-year coverage.
        sample = json.loads((Path(__file__).resolve().parent / 'fixtures' /
                             'gbgb_real_structure_truncated.json').read_text(encoding='utf-8'))
        result = norm.normalize_meeting(self.conn,'411403',sample)
        self.assertEqual(result['races'],2)
        self.assertEqual(result['traps'],4)
        self.assertEqual(result['timed'],3)
        self.assertEqual(result['sectionals'],1)
        self.assertEqual(self.conn.execute(
            "SELECT run_time_s FROM gbgb_runners WHERE dog_name='Fabulous Aurora'").fetchone()[0],29.74)

    def test_bad_digest_rejected_before_mutation(self):
        with self.assertRaisesRegex(norm.NormalizationError,'digest'):
            norm.normalize_meeting(self.conn, '411403', self.fixture(), '0'*64)
        self.assertFalse(norm.table_exists(self.conn, 'gbgb_races'))

    def test_non_numeric_time_remains_null_and_warning(self):
        o=self.fixture()
        dog=o[0]['races'][0]['traps'][1]
        dog['resultRunTime']='N/R'
        result=gbgb.import_meeting(self.conn,'411403','2024-06-01',o)
        self.assertGreater(result['warnings'],0)
        self.assertIsNone(self.conn.execute("SELECT run_time_s FROM gbgb_runners WHERE dog_id='629768'").fetchone()[0])
        self.assertEqual(json.loads(self.conn.execute("SELECT source_json FROM gbgb_runners WHERE dog_id='629768'").fetchone()[0])['resultRunTime'],'N/R')

if __name__=='__main__':
    unittest.main()
