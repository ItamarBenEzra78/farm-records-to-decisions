"""The generator knows the truth; the database never sees it. These tests compare the two.

    python generate_data.py && pytest -q        (the fixture builds db/farm.duckdb)
Thresholds are deliberately not 100%: misspellings, re-issued notes and unseen varieties
are built into the data, and the point is to measure how close the pipeline gets.
"""
import json, os, subprocess, sys
import duckdb, pandas as pd, pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'db', 'farm.duckdb')


@pytest.fixture(scope='session')
def con():
    if not os.path.exists(os.path.join(ROOT, 'data', 'raw')):
        subprocess.run([sys.executable, os.path.join(ROOT, 'generate_data.py')], check=True)
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'build.py')], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr          # build.py fails if any sql/tests check fails
    c = duckdb.connect(DB, read_only=True)
    c.register('truth', pd.read_csv(os.path.join(ROOT, 'data', 'truth', 'shipments_truth.csv'), dtype={'buyer_id': str}))
    yield c
    c.close()


@pytest.fixture(scope='session')
def T():
    return json.load(open(os.path.join(ROOT, 'data', 'truth', 'truth.json')))


def test_shipments_recovered(con):
    missing, extra = con.execute('''SELECT (SELECT count(*) FROM truth WHERE note NOT IN (SELECT note_no FROM fact_shipment)),
                                           (SELECT count(*) FROM fact_shipment WHERE note_no NOT IN (SELECT note FROM truth))''').fetchone()
    n = con.execute('SELECT count(*) FROM truth').fetchone()[0]
    assert extra == 0 and missing / n < 0.002


def test_kg_per_season_within_half_percent(con):
    worst = con.execute('''WITH t AS (SELECT year(CAST(date AS DATE)) s, sum(net_kg) kg FROM truth GROUP BY 1),
                                f AS (SELECT season s, sum(net_kg) kg FROM fact_shipment GROUP BY 1)
                           SELECT max(abs(f.kg / t.kg - 1)) FROM t JOIN f USING (s)''').fetchone()[0]
    assert worst < 0.005


def test_reissued_notes_keep_the_corrected_weight(con):
    wrong = con.execute('''SELECT count(*) FROM fact_shipment f JOIN truth t ON f.note_no = t.note
                           WHERE f.source = 'delivery_note' AND f.net_kg <> t.net_kg''').fetchone()[0]
    assert wrong == 0


def test_variety_accuracy_on_known_varieties(con):
    acc = con.execute('''SELECT avg(CASE WHEN f.variety = t.variety_true THEN 1 ELSE 0 END)
                         FROM fact_shipment f JOIN truth t ON f.note_no = t.note WHERE t.is_known_variety''').fetchone()[0]
    assert acc >= 0.97, acc


def test_unseen_varieties_are_not_guessed(con):
    guessed = con.execute('''SELECT count(*) FROM fact_shipment f JOIN truth t ON f.note_no = t.note
                             WHERE NOT t.is_known_variety AND f.variety IS NOT NULL''').fetchone()[0]
    assert guessed == 0


def test_own_kg_matches_truth(con):
    ratio = con.execute('SELECT (SELECT sum(own_kg) FROM fact_shipment) / (SELECT sum(own_kg) FROM truth)').fetchone()[0]
    assert abs(ratio - 1) < 0.005


def test_corrupt_vat_line_flagged(con, T):
    assert con.execute('SELECT count(*) FROM fact_vat_line WHERE dq_flag IS NOT NULL AND vat = ?', [T['corrupt_vat']]).fetchone()[0] == 1


def test_price_per_kg_within_2pct(con, T):
    got = dict(con.execute('SELECT season, ils_per_kg FROM mart_price_per_kg_season').fetchall())
    for season, p in T['season_price'].items():
        assert abs(got[int(season)] / p - 1) < 0.02, (season, got[int(season)], p)
