#!/usr/bin/env python3
"""Print coverage and write useful CSV reports. No network access required."""
import contextlib, csv, sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DB=ROOT/'data'/'dog_race.sqlite3'
REPORTS=ROOT/'data'/'reports'

def export_query(conn,name,query):
    cur=conn.execute(query)
    dest=REPORTS/name
    with dest.open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.writer(handle)
        writer.writerow([item[0] for item in cur.description])
        while True:
            part=cur.fetchmany(2000)
            if not part:break
            writer.writerows(part)
    print('  ',dest)


def main():
    if not DB.exists():
        print('No database yet. Run RUN_PILOT.bat first.');return
    REPORTS.mkdir(parents=True,exist_ok=True)
    with contextlib.closing(sqlite3.connect(DB)) as conn:
        queries={
           'source_downloads.csv': "SELECT source,item_key,status,bytes,rows_imported,error,local_path,url FROM downloads ORDER BY source,item_key",
           'schema_columns.csv': 'SELECT source,file_path,member,columns_json,row_count FROM source_schemas ORDER BY source,file_path',
           'market_coverage_by_month.csv': "SELECT source,substr(date,1,7) AS month,track,count(*) as runner_rows,sum(CASE WHEN UPPER(COALESCE(win_result,'')) IN ('WINNER','1','WIN') THEN 1 ELSE 0 END) AS winner_rows FROM market_runners WHERE date IS NOT NULL GROUP BY source,month,track ORDER BY month,track",
           'race_coverage_by_month.csv': "SELECT source,substr(date,1,7) AS month, country,track,count(*) AS races,sum(CASE WHEN winning_time_s IS NOT NULL THEN 1 ELSE 0 END) AS races_with_winning_time FROM races WHERE date IS NOT NULL GROUP BY source,month,country,track ORDER BY month,track",
           'qld_coverage_by_month.csv': "SELECT substr(date,1,7) AS month, track,count(*) AS runners,sum(CASE WHEN split_50m_s IS NOT NULL THEN 1 ELSE 0 END) AS with_50m_split,sum(CASE WHEN top_speed_kmh IS NOT NULL THEN 1 ELSE 0 END) AS with_top_speed FROM qld_sectional_summary GROUP BY month,track ORDER BY month,track",
           'trapstats_runner_quality.csv': "SELECT source,count(*) AS runner_rows,sum(CASE WHEN finish_time_s IS NOT NULL THEN 1 ELSE 0 END) AS finish_times,sum(CASE WHEN first_sectional_s IS NOT NULL THEN 1 ELSE 0 END) AS sectionals,sum(CASE WHEN finish_position IS NOT NULL THEN 1 ELSE 0 END) AS placings FROM runners GROUP BY source",
        }
        print('Writing DOG RACE audit reports...')
        for name,query in queries.items():export_query(conn,name,query)
    print('\nIMPORTANT: These are observed rows, not proof of complete national historical coverage.')
if __name__=='__main__':main()
