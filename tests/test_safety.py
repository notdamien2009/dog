import sqlite3,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import collector as c

class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.conn=sqlite3.connect(':memory:');self.addCleanup(self.conn.close)
        c.initialise(self.conn)
    def test_reject_zip_without_csv(self):
        path=self.root/'empty.zip'
        with zipfile.ZipFile(path,'w') as z:z.writestr('readme.txt','no data')
        with patch.object(c,'ROOT',self.root):
            with self.assertRaises(c.DownloadError):c.import_betfair(self.conn,path,'2023-11-18','2026-11-17')
    def test_reject_broken_trapstats_zip(self):
        path=self.root/'broken.zip'
        with zipfile.ZipFile(path,'w') as z:z.writestr('races.csv','race_id,date\nr1,2026-10-10\n')
        with patch.object(c,'ROOT',self.root):
            with self.assertRaises(c.DownloadError):c.import_trapstats(self.conn,path,'2023-11-18','2026-11-17')
    def test_only_pdfs_if_no_csv_for_same_meeting(self):
        html='''<li>16/11/2026 Ladbrokes Q Straight <a href="/RacingFile.ashx?path=/Sectional/20261116_QST_G.pdf">PDF</a> <a href="/RacingFile.ashx?path=/Sectional/20261116_QST_G.csv">CSV</a></li>
<li>15/11/2026 Ladbrokes Q Straight <a href="/RacingFile.ashx?path=/Sectional/20261115_QST_G.pdf">PDF</a></li>'''
        got=c.parse_rq_links(html,'2026-11-01','2026-11-17')
        self.assertEqual(sorted(x[3] for x in got),['csv','pdf'])
    def test_two_pass_import_window_keys_can_coexist(self):
        path=self.root/'a.csv';path.write_text('date\n2023\n')
        with patch.object(c,'ROOT',self.root):
            c.register(self.conn,'betfair_anz','archive@2023-11-18:2026-11-17','url',path,'parsed',1)
            self.assertFalse(c.downloaded(self.conn,'betfair_anz','archive@2020-11-18:2023-11-17',path))
if __name__=='__main__':unittest.main()
