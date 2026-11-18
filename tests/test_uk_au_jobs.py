import bz2
import csv
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import patch

MODULE_FILE = Path(__file__).resolve().parents[1] / 'uk_au_jobs.py'
spec = importlib.util.spec_from_file_location('uk_au_jobs', MODULE_FILE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class JobsQA(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.patchers=[
            patch.object(m,'HERE',self.root),
            patch.object(m,'DATA',self.root/'data'),
            patch.object(m,'RAW',self.root/'data'/'raw'),
            patch.object(m,'DB',self.root/'data'/'dog_uk_au.sqlite3'),
            patch.object(m,'REPORTS',self.root/'data'/'reports'),
        ]
        for p in self.patchers:p.start()
        self.db=m.connection()
    def tearDown(self):
        self.db.close()
        for p in reversed(self.patchers):p.stop()
        self.tmp.cleanup()

    def test_month_windows_clip_3_year_interval(self):
        windows=list(m.date_windows(m.START,m.END))
        self.assertEqual(len(windows),37)
        self.assertEqual(windows[0],(dt.date(2023,11,18),dt.date(2023,11,30)))
        self.assertEqual(windows[-1],(dt.date(2026,11,1),dt.date(2026,11,17)))

    def test_bf_filter_GB_only_market_files(self):
        x=m.bf_filters(dt.date(2024,1,1),dt.date(2024,1,31))
        self.assertEqual(x['sport'],'Greyhound Racing')
        self.assertEqual(x['countriesCollection'],['GB'])
        self.assertEqual(x['fileTypeCollection'],['M'])
        self.assertEqual(x['marketTypesCollection'],['WIN','PLACE'])

    def bf_sample(self):
        return bz2.compress(b'\n'.join(json.dumps(x).encode() for x in [
            {'op':'mcm','mc':[{'id':'1.100','marketDefinition':{
                'countryCode':'GB','marketTime':'2024-06-01T16:01:00Z','name':'To Be Placed',
                'eventId':'121','marketType':'PLACE','status':'OPEN',
                'runners':[{'id':1,'name':'Dog One','status':'ACTIVE'},
                           {'id':2,'name':'Dog Two','status':'ACTIVE'}]}}]},
            {'op':'mcm','mc':[{'id':'1.100','rc':[{'id':1,'spn':3.4,'spf':3.8}, {'id':2,'spn':7.0}]}]},
            {'op':'mcm','mc':[{'id':'1.100','marketDefinition':{
                'status':'CLOSED', 'bspReconciled':True,
                'runners':[{'id':1,'name':'Dog One','status':'WINNER','bsp':3.5},
                           {'id':2,'name':'Dog Two','status':'LOSER'}]}}]}
        ])+b'\n')

    def test_betfair_bsp_actual_not_projected(self):
        d,r,b,inplay=m.parse_bf_file(self.bf_sample())
        self.assertEqual(d['1.100']['status'],'CLOSED')
        self.assertEqual(r[('1.100','1')]['status'],'WINNER')
        self.assertEqual(b[('1.100','1')],3.5)
        self.assertNotIn(('1.100','2'),b)

    def test_betfair_normalize_idempotent(self):
        path=self.root/'file.bz2';path.write_bytes(self.bf_sample())
        for _ in range(2):m.import_bf(self.db,'a',path)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM bf_markets').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM bf_runners').fetchone()[0],2)
        self.assertEqual(self.db.execute('SELECT bsp FROM bf_runners WHERE selection_id=2').fetchone()[0],None)

    def test_betfair_invalid_bzip_rejected(self):
        with self.assertRaises(ValueError):m.parse_bf_file(b'NOTBZ2')

    def test_betfair_unexpected_paths_refused(self):
        with self.assertRaises(ValueError):m.bf_get_file('https://example.com/secrets','token')

    def test_betfair_missing_key_skips_without_requests(self):
        with patch.object(m,'bf_post',side_effect=AssertionError('No HTTP expected')):
            self.assertEqual(m.collect_uk(self.db,m.START,m.END,''),'skipped')

    def test_betfair_download_and_resume_mocked(self):
        key='/data/xds/historic/BASIC/54321/1.100.bz2'
        def post(endpoint,token,payload=None):
            if endpoint=='GetMyData':return [{'sport':'Greyhound Racing','plan':'Basic Plan'}]
            return [key]
        with patch.object(m,'bf_post',side_effect=post),patch.object(m,'bf_get_file',return_value=self.bf_sample()) as mock_get:
            self.assertEqual(m.collect_uk(self.db,dt.date(2024,6,1),dt.date(2024,6,1),'session',limit_files=1,delay=0),'ok')
            self.assertEqual(m.collect_uk(self.db,dt.date(2024,6,1),dt.date(2024,6,1),'session',limit_files=1,delay=0),'ok')
            self.assertEqual(mock_get.call_count,1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM bf_runners').fetchone()[0],2)

    def test_qld_official_link_detection_and_date_filter(self):
        html='''<html><a href="/RacingFile.ashx?path=%2FSectional%2F20261116_QST_G.csv">CSV</a>
              <a href="/RacingFile.ashx?path=%2FSectional%2F20231120_TWN_G.csv">CSV</a>
              <a href="https://bad.example/steal.csv">CSV</a></html>'''
        parser=m.RQLinks(); parser.feed(html)
        self.assertEqual(len(parser.links),2)
        self.assertEqual(parser.links[0][1],'QST')

    def test_qld_csv_all_original_rows_preserved(self):
        document=b'Dog,Rug,50m\r\nA,1,3.2\r\nB,2,3.4\r\n'
        with patch.object(m,'discover_qld',return_value=[(dt.date(2026,11,16),'QST','https://www.racingqueensland.com.au/some.csv')]),\
             patch.object(m,'request_bytes',return_value=document) as mock_get:
            for _ in range(2):
                self.assertEqual(m.collect_qld(self.db,dt.date(2026,11,16),dt.date(2026,11,16),delay=0),'ok')
            self.assertEqual(mock_get.call_count,1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM au_qld_sectional_rows').fetchone()[0],3)

    def test_topaz_records_exact_missing_values(self):
        path=self.root/'test.csv'
        path.write_text('meetingDate,runId,raceId,dogName,distance,place,resultTime,resultMarginLengths,weightInKg,firstSplitTime,secondSplitTime,boxNumber\n'
                        '2024-06-01T00:00:00Z,10,30,Dog X,500,2,30.13,3.5L,29.1,5.18,,6\n'
                        '2024-06-01T00:00:00Z,11,30,Dog Y,500,1,,0.25L,27.5,,13.3,1\n'
                        '2024-07-01T00:00:00Z,12,31,Dog Z,500,1,30.0,,,,,1\n',encoding='utf-8')
        for _ in range(2):
            rows,times,invalid=m.import_topaz_csv(self.db,path,'VIC','VIC_2024-06',dt.date(2024,6,1),dt.date(2024,6,30))
            self.assertEqual((rows,times,invalid),(2,1,0))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM au_topaz_runs').fetchone()[0],2)
        r=self.db.execute('SELECT beaten_lengths,weight_kg,first_split_s,second_split_s FROM au_topaz_runs WHERE run_id="10"').fetchone()
        self.assertEqual(r,(3.5,29.1,5.18,None))
        self.assertIsNone(self.db.execute('SELECT finish_time_s FROM au_topaz_runs WHERE run_id="11"').fetchone()[0])

    def test_topaz_missing_key_skips(self):
        self.assertEqual(m.collect_topaz(self.db,m.START,m.END,''),'skipped')

    def test_topaz_authorised_monthly_download_and_resume(self):
        source = ('meetingDate,runId,raceId,dogName,distance,place,resultTime,weightInKg,firstSplitTime\n'
                  '2024-06-03T00:00:00Z,99,12,Fast Runner,500,1,29.93,30.1,5.23\n')
        class FakeFrame:
            empty=False
            def to_csv(self,path,index=False):
                Path(path).write_text(source,encoding='utf-8')
        class FakeAPI:
            calls=0
            def __init__(self,token):
                assert token=='issued-test-key'
            def get_bulk_runs_by_month(self,**kwargs):
                FakeAPI.calls+=1
                return FakeFrame()
        import sys
        mod=types.ModuleType('topaz')
        mod.TopazAPI=FakeAPI
        with patch.dict(sys.modules,{'topaz':mod}):
            for _ in range(2):
                result=m.collect_topaz(self.db,dt.date(2024,6,1),dt.date(2024,6,30),
                                      'issued-test-key',pilot=True,limit_files=1,delay=0)
                self.assertEqual(result,'ok')
        self.assertEqual(FakeAPI.calls,1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM au_topaz_runs').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT first_split_s FROM au_topaz_runs').fetchone()[0],5.23)

    def test_report_creates_without_data(self):
        m.status(self.db)
        report=m.REPORTS/'uk_au_collection_status.txt'
        self.assertTrue(report.exists())
        self.assertIn('DATE COVERAGE',report.read_text())

    def test_atomic_write_replaces_part_file(self):
        dest=self.root/'abc'/'sample.json'
        m.atomic_write(dest,b'hello')
        self.assertEqual(dest.read_bytes(),b'hello')
        self.assertFalse(dest.with_suffix('.json.part').exists())


if __name__=='__main__':unittest.main()
