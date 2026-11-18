import contextlib, csv, io, json, sqlite3, tempfile, unittest, zipfile
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import collector as c

class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.conn=sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        c.initialise(self.conn)
    def test_betfair_links_real_published_pattern(self):
        page='''<a href="/data/assets/ANZ_Greyhounds_2023.zip">2023</a><a href="/data/assets/ANZ_Greyhounds_2024.zip">2024</a><a href="/data/assets/ANZ_Greyhounds_2026_10.csv">Oct</a><a href="/data/assets/ANZ_Thoroughbred_2024.zip">wrong</a>'''
        got=c.parse_betfair_links(page,'2023-11-18','2026-11-17')
        self.assertEqual(len(got),3)
        self.assertTrue(all('Greyhounds' in item[2] for item in got))
    def test_rq_links_encoded_query_and_date(self):
        page='''<ul><li>16/11/2026 Ladbrokes Q Straight Sectionals <a href="/RacingFile.ashx?path=%2FSectional%2F20261116_QST_G.csv">CSV</a></li>
        <li>16/11/2023 Albion Park <a href="/RacingFile.ashx?path=%2FSectional%2F20231116_AP_G.csv">CSV</a></li></ul>'''
        got=c.parse_rq_links(page,'2023-11-18','2026-11-17')
        self.assertEqual(len(got),1)
        self.assertEqual(got[0][4],'2026-11-16')
    def test_date_parser(self):
        self.assertEqual(c.to_iso('16/11/2026'),'2026-11-16')
        self.assertEqual(c.to_iso('2026-11-16 10:20:00'),'2026-11-16')
        self.assertIsNone(c.to_iso('unreported'))
    def test_betfair_import_market_results_and_date_filter(self):
        path=self.root/'anz.csv'
        path.write_text('LOCAL_MEETING_DATE,TRACK,RACE_NO,SELECTION_NAME,SELECTION_ID,WIN_MARKET_ID,TAB_NUMBER,WIN_BSP,WIN_RESULT,DISTANCE\n2026-10-11,GEELONG,4,RED FLASH,44,1000,3,5.5,WINNER,400\n2022-11-11,GEELONG,1,OLD DOG,20,2,1,3.5,LOSER,400\n',encoding='utf-8')
        with patch.object(c,'ROOT',self.root):
            result=c.import_betfair(self.conn,path,'2023-11-18','2026-11-17')
        self.assertEqual(result,1)
        self.assertEqual(self.conn.execute('SELECT dog,win_result FROM market_runners').fetchone(),('RED FLASH','WINNER'))
    def test_trapstats_import_from_archive(self):
        path=self.root/'test.zip'
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr('races.csv','race_id,date,track,country,distance,winning_time,grade\nr1,2026-10-16,Romford,GB,400,24.50,A1\n')
            archive.writestr('runners.csv','runner_id,race_id,dog,trap,finish_position,finish_time,sectional\nrn1,r1,FAST DOG,3,1,24.50,3.55\n')
        with patch.object(c,'ROOT',self.root):
            n=c.import_trapstats(self.conn,path,'2023-11-18','2026-11-17')
        self.assertEqual(n,2)
        self.assertEqual(self.conn.execute('SELECT winning_time_s FROM races').fetchone()[0],24.5)
        self.assertEqual(self.conn.execute('SELECT first_sectional_s FROM runners').fetchone()[0],3.55)
    def test_panda_import(self):
        path=self.root/'greyhound_sectionals_qld.csv'
        path.write_text('date,track,race,greyhound,box,split_50m_sec,position_50m,top_speed_kmh,finish_position\n2026-10-11,Q Straight,4,Dog A,5,3.83,1,68.3,1\n',encoding='utf-8')
        with patch.object(c,'ROOT',self.root):
            n=c.import_panda(self.conn,path,'2023-11-18','2026-11-17')
        self.assertEqual(n,1)
    def test_rq_preserves_mixed_raw_cells(self):
        path=self.root/'report.csv'
        path.write_text('Race 1,,,,\nDog,50m,100m\nDog A,3.83,7.20\n',encoding='utf-8')
        with patch.object(c,'ROOT',self.root):
            n=c.import_rq(self.conn,path)
        self.assertEqual(n,3)
        self.assertEqual(json.loads(self.conn.execute('SELECT cells_json FROM rq_csv_cells WHERE line_number=3').fetchone()[0])[0],'Dog A')
    def test_download_from_local_server_with_validation_and_resume(self):
        (self.root/'source.csv').write_text('col\n1\n',encoding='utf-8')
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(self_root),**kwargs)
            def log_message(self,*args,**kwargs):pass
        self_root=self.root
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        target=self.root/'data'/'download.csv'
        digest=c.fetch(f'http://127.0.0.1:{server.server_port}/source.csv',target,wait=0)
        self.assertEqual(len(digest),64)
        self.assertEqual(target.read_text(),'col\n1\n')
        self.assertEqual(c.fetch('http://127.0.0.1:1/offline',target,wait=0),digest)
    def test_main_closes_database_file_connection(self):
        # SQLite's `with connection` only controls transactions; Windows needs
        # an explicit close() before TempDirectory can remove the .sqlite3 file.
        root=self.root/'command_line';root.mkdir()
        opened=[]
        original_connect=sqlite3.connect
        class TrackedConnection(sqlite3.Connection):
            def close(self):
                opened.append(self)
                return super().close()
        def spy_connect(*args,**kwargs):
            return original_connect(*args,factory=TrackedConnection,**kwargs)
        def close_test_log_handlers(**settings):
            # main() normally keeps its log open until process exit. Here it
            # runs in-process, so close the temporary log before Windows cleanup.
            for handler in settings.get('handlers',[]):
                handler.close()
        with patch.object(c,'DB',root/'dog_race.sqlite3'), \
             patch.object(c,'RAW',root/'raw'), \
             patch.object(c,'LOG_DIR',root/'logs'), \
             patch.object(c.logging,'basicConfig',side_effect=close_test_log_handlers), \
             patch.object(c.sqlite3,'connect',side_effect=spy_connect):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(c.main(['status']),0)
        self.assertEqual(len(opened),1)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute('SELECT 1')

    def test_store_status_and_resume(self):
        path=self.root/'file.csv';path.write_text('dog\nA\n')
        with patch.object(c,'ROOT',self.root):
            c.register(self.conn,'test','item','http://test',path,'parsed',1)
        self.assertTrue(c.downloaded(self.conn,'test','item',path))
if __name__=='__main__':unittest.main()
