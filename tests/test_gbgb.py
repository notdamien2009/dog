import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import gbgb

class GBGBTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.conn=gbgb.open_db(Path(self.tmp.name)/'test.sqlite3')
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)
    def sample_item(self):
        return {'meetingId':41420,'raceId':330,'dogId':55,'dogName':'Test Runner',
          'trackName':'Romford','raceDistance':400,'raceClass':'A4',
          'resultPosition':1,'trapNumber':4,'raceDate':'01/06/2024'}
    def sample_meeting(self, mid=41420, raceid=330):
        return [{'meetingId': mid, 'meetingDate': '01/06/2024', 'trackName': 'Romford',
          'races': [{'raceId': raceid, 'raceDate': '01/06/2024', 'raceTime': '18:01:00',
          'raceNumber': '1', 'raceDistance': 400, 'raceClass': 'A4', 'raceGoing': '0',
          'traps': [{'trapNumber': '4', 'dogId': 55, 'dogName': 'Test Runner',
                     'resultPosition': 1, 'resultRunTime': '24.16',
                     'resultAdjustedTime': '24.15', 'resultSectionalTime': '03.86'}]}]}]
    def test_index_url(self):
        self.assertIn('date=2024-06-01',gbgb.index_url('2024-06-01',1))
        self.assertIn('race_type=race',gbgb.index_url('2024-06-01',1))
        self.assertEqual(gbgb.meeting_url('123'),'https://api.gbgb.org.uk/api/results/meeting/123?meeting=123')
        with self.assertRaises(gbgb.GBGBError):gbgb.meeting_url('../123')
    def test_two_valid_index_shapes(self):
        it=[self.sample_item()]
        for sample in ({'items':it,'meta':{'pageCount':1}},{'data':{'items':it,'meta':{'pageCount':1}},'status':'success'}):
            got,meta=gbgb.extract_data(sample)
            self.assertEqual(got,it)
            self.assertEqual(meta['pageCount'],1)
        with self.assertRaises(gbgb.GBGBError):gbgb.extract_data({'unexpected':it})
    def test_import_preserves_payload_and_stages_only(self):
        item=self.sample_item()
        count,meta=gbgb.import_index(self.conn,'2024-06-01',1,{'items':[item], 'meta':{'pageCount':1}})
        self.assertEqual(count,1)
        row=self.conn.execute('SELECT dog_name,meeting_id,payload_json FROM gbgb_index').fetchone()
        self.assertEqual(row[:2],('Test Runner','41420'))
        self.assertEqual(json.loads(row[2])['trapNumber'],4)
        gbgb.import_meeting(self.conn,'41420','2024-06-01',self.sample_meeting())
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbgb_meetings').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbgb_runners').fetchone()[0],1)
    def test_collect_no_token_file_needed(self):
        def getter(url):
            if 'meeting/' in url: return self.sample_meeting()
            return {'items':[self.sample_item()],'meta':{'pageCount':1}}
        with patch.object(gbgb,'RAW_ROOT',Path(self.tmp.name)/'raw'), patch.object(gbgb,'MIN_DELAY',0):
            with contextlib.redirect_stdout(io.StringIO()):
                gbgb.collect(self.conn,'2024-06-01','2024-06-01',0,getter=getter)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbgb_meetings').fetchone()[0],1)
    def test_empty_date_pagecount_zero(self):
        def getter(url): return {'items':[],'meta':{'pageCount':0}}
        with patch.object(gbgb,'RAW_ROOT',Path(self.tmp.name)/'raw'), patch.object(gbgb,'MIN_DELAY',0):
            with contextlib.redirect_stdout(io.StringIO()):
                gbgb.collect(self.conn,'2024-12-25','2024-12-25',0,getter=getter)
        self.assertEqual(self.conn.execute('SELECT meetings_found FROM gbgb_date_coverage').fetchone()[0],0)
    def test_empty_middle_page_rejected(self):
        def getter(url): return {'items':[],'meta':{'pageCount':2}}
        with patch.object(gbgb,'RAW_ROOT',Path(self.tmp.name)/'raw'), patch.object(gbgb,'MIN_DELAY',0):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(gbgb.GBGBError):
                    gbgb.collect(self.conn,'2024-06-01','2024-06-01',0,getter=getter)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbgb_date_coverage').fetchone()[0],0)
    def test_one_request_inspection_not_stored(self):
        calls=[]
        def getter(url):
            calls.append(url)
            return {'items':[self.sample_item()],'meta':{'pageCount':1}}
        with contextlib.redirect_stdout(io.StringIO()):gbgb.inspect('2024-06-01',getter=getter)
        self.assertEqual(len(calls),1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbgb_index').fetchone()[0],0)
    def test_collect_two_pages_with_resume(self):
        calls=[]
        def getter(url):
            calls.append(url)
            if 'meeting/41420?' in url:return self.sample_meeting()
            if 'page=1' in url:return {'items':[self.sample_item()],'meta':{'pageCount':2}}
            return {'items':[],'meta':{'pageCount':2}}
        with patch.object(gbgb,'RAW_ROOT',Path(self.tmp.name)/'raw'):
            with patch.object(gbgb,'MIN_DELAY',0):
                with contextlib.redirect_stdout(io.StringIO()):
                    gbgb.collect(self.conn,'2024-06-01','2024-06-01',0,getter=getter)
                    gbgb.collect(self.conn,'2024-06-01','2024-06-01',0,getter=getter)
        self.assertEqual(len(calls),3)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbgb_meetings').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbgb_index').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT pages_found FROM gbgb_date_coverage').fetchone()[0],2)
    def test_separate_gbgb_db_works_while_original_collector_is_locked(self):
        # Simulate the AU job holding a write transaction in dog_race.sqlite3.
        # The GBGB connection must never attempt to initialise that shared DB.
        shared = Path(self.tmp.name) / 'dog_race.sqlite3'
        separated = Path(self.tmp.name) / 'gbgb.sqlite3'
        old = sqlite3.connect(shared, timeout=0.1)
        try:
            old.execute('CREATE TABLE original_records (x TEXT)')
            old.execute('BEGIN IMMEDIATE')
            old.execute('INSERT INTO original_records VALUES (?)', ('do not delete',))
            with patch.object(gbgb.collector, 'DB', shared), patch.object(gbgb, 'GBGB_DB', separated):
                # sqlite3.Connection.__exit__ commits/rolls back but DOES NOT close.
                # Windows will refuse to delete the temp SQLite file until close().
                with contextlib.closing(gbgb.open_db()) as new:
                    self.assertEqual(new.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
                    self.assertEqual(new.execute('SELECT count(*) FROM gbgb_requests').fetchone()[0], 0)
                with self.assertRaises(sqlite3.ProgrammingError):
                    new.execute('SELECT 1')  # prove the file handle was released
        finally:
            old.rollback()
            old.close()
        self.assertTrue(shared.exists())
        self.assertTrue(separated.exists())
        self.assertNotEqual(shared.resolve(), separated.resolve())

    def test_second_gbgb_job_is_rejected_without_touching_database(self):
        lockpath = Path(self.tmp.name) / 'gbgb-run.lock'
        with gbgb.exclusive_run_lock(lockpath):
            with self.assertRaisesRegex(gbgb.GBGBError, 'Another GBGB collection'):
                with gbgb.exclusive_run_lock(lockpath):
                    pass
        with gbgb.exclusive_run_lock(lockpath):
            pass  # lock released after the prior run completes

    def test_cached_page_from_interrupted_old_run_is_reused(self):
        raw = Path(self.tmp.name) / 'raw'
        cached = raw / 'days' / '2024-06-01' / 'page_001.json'
        gbgb.save_json(cached, {'items': [self.sample_item()], 'meta': {'pageCount': 1}})
        urls = []
        def getter(url):
            urls.append(url)
            if 'meeting/' not in url:
                raise AssertionError('cached index must not be downloaded again')
            return self.sample_meeting()
        with patch.object(gbgb, 'RAW_ROOT', raw), patch.object(gbgb, 'MIN_DELAY', 0):
            with contextlib.redirect_stdout(io.StringIO()):
                gbgb.collect(self.conn, '2024-06-01', '2024-06-01', 0, getter=getter)
        self.assertEqual(len(urls), 1)
        self.assertIn('meeting/', urls[0])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbgb_date_coverage').fetchone()[0], 1)

    def test_date_range_boundary(self):
        self.assertEqual(list(gbgb.daterange('2024-02-28','2024-03-01')),
                         ['2024-02-28','2024-02-29','2024-03-01'])

if __name__=='__main__': unittest.main()
