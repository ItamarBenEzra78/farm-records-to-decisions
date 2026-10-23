"""Loader for the synthetic records: data/raw -> the staging contract (sql/00_staging.sql).
Types and lineage only. No cleaning here: every fix happens in SQL and is logged in dq_issue.
"""
import glob, hashlib, json, os
import pandas as pd

RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'raw')

# the cleaning dictionary: canonical variety -> roots to look for. Deliberately incomplete:
# misspellings and unseen varieties must be handled by the SQL (fuzzy match / unmatched log).
VARIETY_ROOTS = {
    'Regina': ['רגינה', 'regina'], 'Monique': ['מוניק', 'monique'], 'Larissa': ['לריסה', 'לריסא', 'larissa'],
    'Georgina': ['גורגיניה', 'גורגינה', "ג'ורגינה", "ג'ורג'ינה", 'georgina'], 'Tyson': ['טייסון', 'טיסון', 'tyson'],
    'Nicola': ['ניקולה', 'nicola'], 'Charlotte': ['שרלוט', 'charlotte'], 'Memphis': ['ממפיס', 'memphis'],
    'Angelina': ["אנג'לינה", 'אנגלינה', 'angelina'], 'Venezia': ['ונציה', 'venezia'], 'Sifra': ['סיפרה', 'sifra'],
}


def load(con):
    alias = pd.DataFrame([(v, a) for v, al in VARIETY_ROOTS.items() for a in al], columns=['variety', 'alias'])
    con.execute('INSERT INTO cfg_variety_alias SELECT * FROM alias')
    buyers = pd.read_csv(os.path.join(RAW, 'buyers.csv'), dtype=str)
    con.execute('INSERT INTO cfg_buyer SELECT counterparty_id, name_on_paper, label FROM buyers')
    books = pd.read_csv(os.path.join(RAW, 'workbooks', 'index.csv'), dtype=str).fillna('')
    books = books.replace({'': None})
    con.execute('INSERT INTO cfg_workbook SELECT book, role, buyer_id, default_grower FROM books')
    con.execute("INSERT INTO cfg_param VALUES ('own_grower_regex', 'אורן'), ('own_entity', '990000001'), ('from_year', '2022')")

    vat = pd.read_csv(os.path.join(RAW, 'vat_lines.csv'), dtype={'period': str, 'counterparty': str, 'reference': str, 'entity': str})
    vat['doc_date'] = pd.to_datetime(vat.doc_date)
    vat['record_type'] = None
    vat['source_file'] = 'vat_lines.csv'
    vat['row_num'] = range(len(vat))
    con.execute('''INSERT INTO stg_vat_line SELECT entity, period, doc_date, direction, record_type, counterparty,
                   reference, amount, vat, source_file, row_num FROM vat''')

    rows = []
    for i, f in enumerate(sorted(glob.glob(os.path.join(RAW, 'delivery_notes', '*.json')))):
        body = open(f, encoding='utf-8').read()
        j = json.loads(body)
        rows.append(dict(note_no=int(j['note']), note_date=pd.to_datetime(j['date']), buyer_raw=j['buyer'], grower_raw=j['grower'],
                         variety_raw=j['variety'], net_kg=float(j['net_kg']), port=None, container=None,
                         doc_hash=hashlib.sha1(body.encode()).hexdigest(), source_file=os.path.basename(f), row_num=i))
    notes = pd.DataFrame(rows)
    con.execute('INSERT INTO stg_note SELECT * FROM notes')

    wbs = []
    for f in sorted(glob.glob(os.path.join(RAW, 'workbooks', 'marketing_*.csv'))):
        d = pd.read_csv(f, dtype=str)
        d.columns = ['date_raw', 'note_no', 'grower_raw', 'variety_raw', 'weight_raw']
        d['book'], d['sheet'], d['row_num'] = os.path.basename(f), 'sheet1', range(2, len(d) + 2)
        wbs.append(d)
    wb = pd.concat(wbs, ignore_index=True)
    wb['note_no'] = pd.to_numeric(wb.note_no, errors='coerce').astype('Int64')
    wb['weight_raw'] = pd.to_numeric(wb.weight_raw, errors='coerce')
    wb['destination'] = None
    wb['is_subtotal_hint'] = wb.variety_raw.eq('סה"כ')
    con.execute('''INSERT INTO stg_workbook_row SELECT book, sheet, row_num, date_raw, note_no, grower_raw, variety_raw,
                   weight_raw, destination, is_subtotal_hint FROM wb''')
