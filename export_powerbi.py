"""Export the warehouse as a star schema for the Power BI dashboard, and the numbers it must reproduce.

    python build.py && python export_powerbi.py          # writes powerbi/data/*.csv and powerbi/expected.csv

The CSVs are the only input of powerbi/ (no direct DuckDB connection, so the .pbix opens on any Windows machine).
Before writing, the script recomputes every dashboard number from the exported tables with the same logic as the
DAX measures in powerbi/README.md and compares it with the SQL marts. A mismatch fails the export.
"""
import argparse, os, sys
import duckdb
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'powerbi')

TABLES = {
    'dim_date': """
        WITH d AS (SELECT CAST(range AS DATE) AS date
                   FROM range((SELECT make_date(min(year), 1, 1) FROM fact_vat_line),
                              (SELECT make_date(max(year) + 1, 1, 1) FROM fact_vat_line), INTERVAL 1 DAY))
        SELECT date, year(date) AS year, month(date) AS month, strftime(date, '%b') AS month_name,
               (month(date) + 3) % 12 AS farm_month_order,                       -- Sep = 0 ... Aug = 11
               CASE WHEN month(date) >= 9 THEN year(date) ELSE year(date) - 1 END AS farm_year_start
        FROM d ORDER BY date""",
    'dim_counterparty': """
        SELECT counterparty_id, channel, channel_reason, n_invoices, active_months,
               counterparty_id IN (SELECT counterparty_id FROM cfg_buyer) AS is_export_buyer
        FROM dim_counterparty""",
    'fact_vat_line': """
        SELECT make_date(year, month, 1) AS date, year, month, direction, counterparty AS counterparty_id, reference,
               amount, vat, CASE WHEN direction = 'sale' THEN amount ELSE -amount END AS signed_amount,
               dq_flag IS NULL AS is_valid, dq_flag
        FROM fact_vat_line""",
    'fact_shipment': """
        SELECT note_no, ship_date, season, buyer_id, variety, grower_class, is_own, net_kg, own_kg, source
        FROM fact_shipment""",
    'dq_issue': 'SELECT issue_code, table_name, record_key, value_before, value_after, action FROM dq_issue',
    'mart_settlement_lag': 'SELECT * FROM mart_settlement_lag',
}


def expected(con):
    """The numbers the dashboard must show, straight from the SQL marts."""
    rows = []
    c = con.sql('SELECT * FROM mart_concentration').df().iloc[0]
    for k in ['largest_customer_share', 'top2_share', 'top5_share', 'hhi']:
        rows.append(('Q1 concentration', k, '', c[k]))
    for r in con.sql('SELECT * FROM mart_channel_share').df().itertuples():
        rows.append(('Q1 channel share', 'share', r.channel, r.share))
    for r in con.sql('SELECT * FROM mart_price_per_kg_season').df().itertuples():
        rows.append(('Q2 price per kg', 'ils_per_kg', str(r.season), r.ils_per_kg))
        if pd.notna(r.change_vs_prev):
            rows.append(('Q2 price per kg', 'change_vs_prev', str(r.season), r.change_vs_prev))
    for r in con.sql('SELECT * FROM mart_cash_curve').df().itertuples():
        rows.append(('Q3 cash curve', 'cumulative_ils', str(r.farm_order), r.cumulative_ils))
    for r in con.sql('SELECT * FROM mart_channel_stability').df().itertuples():
        rows.append(('Q4 stability', 'cv', r.channel, r.cv))
    for r in con.sql('SELECT * FROM dq_summary').df().itertuples():
        rows.append(('Data quality', 'rows_affected', r.issue_code, r.rows_affected))
    return pd.DataFrame(rows, columns=['page', 'metric', 'key', 'value'])


def recompute(t, from_year):
    """Same logic as the DAX measures, on the exported tables only."""
    v, cp, s = t['fact_vat_line'], t['dim_counterparty'], t['fact_shipment']
    rows = []
    sales = v[(v.direction == 'sale') & v.is_valid & (v.year >= from_year)]                       # [Sales ILS]
    by_cp = sales.groupby('counterparty_id').amount.sum().sort_values(ascending=False)
    share = by_cp / by_cp.sum()                                                                    # [Share of sales]
    rows += [('Q1 concentration', 'largest_customer_share', '', share.iloc[0]),
             ('Q1 concentration', 'top2_share', '', share.iloc[:2].sum()),
             ('Q1 concentration', 'top5_share', '', share.iloc[:5].sum()),
             ('Q1 concentration', 'hhi', '', (share ** 2).sum())]
    ch = sales.merge(cp, on='counterparty_id').groupby('channel').amount.sum()
    rows += [('Q1 channel share', 'share', k, x) for k, x in (ch / ch.sum()).items()]

    export_ils = (v[(v.direction == 'sale') & v.is_valid & (v.vat == 0)                             # [Settled export ILS]
                    & v.counterparty_id.isin(cp[cp.is_export_buyer].counterparty_id)]
                  .groupby('year').amount.sum())
    own = s[s.own_kg > 0].groupby('season').agg(kg=('own_kg', 'sum'), containers=('own_kg', 'size'))
    seasons = [y for y in own.index if own.containers[y] >= 30 and y >= from_year and y in export_ils.index]
    per_kg = {y: export_ils[y] / own.kg[y] for y in seasons}                                       # [ILS per kg]
    for i, y in enumerate(seasons):
        rows.append(('Q2 price per kg', 'ils_per_kg', str(y), per_kg[y]))
        if i:
            rows.append(('Q2 price per kg', 'change_vs_prev', str(y), per_kg[y] / per_kg[seasons[i - 1]] - 1))

    recent = v[v.year >= from_year]
    n_years = recent.year.nunique()
    m = recent[recent.is_valid].groupby('month').signed_amount.sum() / n_years                    # [Avg net cash ILS]
    m.index = (m.index + 3) % 12
    rows += [('Q3 cash curve', 'cumulative_ils', str(k), x) for k, x in m.sort_index().cumsum().items()]

    yearly = sales.merge(cp, on='counterparty_id').groupby(['channel', 'year']).amount.sum()        # [Channel CV]
    rows += [('Q4 stability', 'cv', k, g.std() / g.mean()) for k, g in yearly.groupby(level=0)]
    rows += [('Data quality', 'rows_affected', k, n) for k, n in t['dq_issue'].issue_code.value_counts().items()]
    return pd.DataFrame(rows, columns=['page', 'metric', 'key', 'value'])


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=os.path.join(ROOT, 'db', 'farm.duckdb'))
    ap.add_argument('--out', default=OUT)
    a = ap.parse_args()
    con = duckdb.connect(a.db, read_only=True)
    from_year = int(con.execute("SELECT value FROM cfg_param WHERE key = 'from_year'").fetchone()[0])
    t = {name: con.sql(q).df() for name, q in TABLES.items()}

    exp, got = expected(con), recompute(t, from_year)
    cmp = exp.merge(got, on=['page', 'metric', 'key'], how='outer', suffixes=('_sql', '_model'))
    bad = cmp[~((cmp.value_sql - cmp.value_model).abs() <= 1e-6 * cmp.value_sql.abs().clip(lower=1))]
    if len(bad):
        print('model does not reproduce the marts:\n' + bad.to_string(index=False))
        sys.exit(1)

    os.makedirs(os.path.join(a.out, 'data'), exist_ok=True)
    for name, df in t.items():
        df.to_csv(os.path.join(a.out, 'data', f'{name}.csv'), index=False)
        print(f'  {name:22s} {len(df):6d} rows')
    with pd.ExcelWriter(os.path.join(a.out, 'data', 'farm_model.xlsx')) as xw:      # one file for the Power BI web upload
        for name, df in t.items():
            df.to_excel(xw, sheet_name=name, index=False)
    exp.to_csv(os.path.join(a.out, 'expected.csv'), index=False)
    print(f'{len(exp)} expected values written; all reproduced from the exported tables')
