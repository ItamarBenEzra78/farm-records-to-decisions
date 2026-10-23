"""Build the farm database: staging -> (loader) -> core -> marts -> SQL tests.

    python build.py                                  # synthetic data, db/farm.duckdb
    python build.py --loader /path/load_real.py --db /path/real.duckdb   # same SQL on another source

A loader is any .py file with a `load(con)` function that fills the stg_* and cfg_* tables
defined in sql/00_staging.sql. Everything after that is shared SQL.
"""
import argparse, glob, importlib.util, os, sys, time
import duckdb

ROOT = os.path.dirname(os.path.abspath(__file__))
SQL = os.path.join(ROOT, 'sql')


def run_file(con, path):
    con.execute(open(path, encoding='utf-8').read())


def load_module(path):
    spec = importlib.util.spec_from_file_location('loader', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build(db, loader):
    if os.path.exists(db):
        os.remove(db)
    os.makedirs(os.path.dirname(db), exist_ok=True)
    con = duckdb.connect(db)
    t0 = time.time()
    run_file(con, os.path.join(SQL, '00_staging.sql'))
    load_module(loader).load(con)
    run_file(con, os.path.join(SQL, '10_core.sql'))
    run_file(con, os.path.join(SQL, '20_marts.sql'))
    failed = []
    for t in sorted(glob.glob(os.path.join(SQL, 'tests', '*.sql'))):
        n = len(con.execute(open(t, encoding='utf-8').read()).fetchall())
        print(f"  {'PASS' if n == 0 else 'FAIL'}  {os.path.basename(t)}" + (f'  ({n} rows)' if n else ''))
        if n:
            failed.append(t)
    print(f'built {db} in {time.time() - t0:.1f}s')
    return con, failed


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--loader', default=os.path.join(ROOT, 'load_synthetic.py'))
    ap.add_argument('--db', default=os.path.join(ROOT, 'db', 'farm.duckdb'))
    a = ap.parse_args()
    con, failed = build(a.db, a.loader)
    for v in ['dq_summary', 'mart_channel_share', 'mart_concentration', 'mart_price_per_kg_season', 'mart_settlement_lag', 'mart_channel_stability', 'mart_packing_fee_per_container']:
        print(f'\n== {v}')
        print(con.sql(f'SELECT * FROM {v}').df().to_string(index=False))
    sys.exit(1 if failed else 0)
