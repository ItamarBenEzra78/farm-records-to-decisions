"""Generate a synthetic farm's records, messy in the same ways the real ones were.

Business shape (all names and numbers invented):
  a potato grower ("Oren Fields") that also runs a packing house for neighbouring growers;
  two export buyers settle monthly, 1-3 months after shipping, one lump sum per month;
  a local wholesaler, a contract buyer, packing-service clients, input suppliers.

Mess injected on purpose. Some of it the cleaning rules know about, some of it they do not
(misspellings, re-issued notes, weight disagreements), so the pipeline cannot score 100% by construction:
  M1  duplicate files of the same delivery note            M7  a weight column in tons for some rows, kg for others
  M2  the same shipment in master + buyer workbooks        M8  impossible dates
  M3  subtotal rows inside the workbooks                   M9  one corrupt VAT line (300,000,304 on 1,900)
  M4  ~50 spellings per variety, plus ~4% misspellings      M10 re-issued notes: same number, corrected weight
  M5  grower names in many spellings, shared containers    M11 workbook weight 1-3% off the note in ~3% of rows
  M6  one season with most delivery notes lost             M12 a few varieties the dictionary has never seen

Standard library only.   python generate_data.py [--seed 42]
Writes data/raw/* (what the analyst gets) and data/truth/* (used only by the tests).
"""
import argparse, csv, json, os, random
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
SEASONS = [2022, 2023, 2024, 2025]
LOST_SEASON = 2024
SEASON_PRICE = {2022: 1.70, 2023: 1.85, 2024: 2.40, 2025: 1.45}          # true net ILS/kg, own export
VAT_RATE = {2022: .17, 2023: .17, 2024: .17, 2025: .18}                  # Israel raised VAT to 18% in Jan 2025
VARIETIES = {  # canonical: (Hebrew, share)
    'Regina': ('רגינה', .17), 'Monique': ('מוניק', .14), 'Larissa': ('לריסה', .12), 'Georgina': ("ג'ורג'ינה", .10),
    'Tyson': ('טייסון', .08), 'Nicola': ('ניקולה', .08), 'Charlotte': ('שרלוט', .07), 'Memphis': ('ממפיס', .07),
    'Angelina': ("אנג'לינה", .06), 'Venezia': ('ונציה', .05), 'Sifra': ('סיפרה', .04),
    'Bellini': ('בליני', .01), 'Alouette': ('אלואט', .01),                  # M12: not in the cleaning dictionary
}
KNOWN = [v for v in VARIETIES if v not in ('Bellini', 'Alouette')]
HEB_ALT = {'Georgina': ['גורגיניה', 'גורגינה', "ג'ורגינה"], 'Tyson': ['טיסון'], 'Angelina': ['אנגלינה'], 'Larissa': ['לריסה', 'לריסא']}
OWN_SPELLINGS = ['שדות אורן', 'שדות אורן בע"מ', 'ש. אורן', 'אורן 3101', 'אורן 3214', 'שדות אורן גידולים', 'אורן']
OTHER_GROWERS = ['משק לבנה', 'קיבוץ הדר', 'מושב תמר', 'אגודת שקד', 'חוות רימון', 'משק גפן']
OWN_SHARE = 0.33
BUYERS = {'990000101': ('Hollandia Fresh', .7), '990000102': ('Nordic Produce', .3)}
LOCAL, CONTRACT = '990000201', '990000301'
SERVICE_CLIENTS = ['990000401', '990000402', '990000403', '990000404']
OWN_ENTITY = '990000001'


def typo(rnd, word):
    i = rnd.randrange(1, len(word) - 1)
    return rnd.choice([word[:i] + word[i + 1:], word[:i] + word[i] + word[i:], word[:i] + word[i + 1] + word[i] + word[i + 2:]])


def variety_text(rnd, v):
    if rnd.random() < .04 and v in KNOWN:                                   # M4b misspelling
        return typo(rnd, v)
    heb = rnd.choice(HEB_ALT.get(v, [VARIETIES[v][0]]))
    size = rnd.choice(['', ' 35-45', ' 45-55', ' 55+', ' 40-60', ' מיקס', ' 50-60'])
    f = rnd.random()
    if f < .35: return heb + size
    if f < .55: return v + size
    if f < .70: return 'תפוא ' + heb + size
    if f < .80: return v.lower()
    if f < .90: return heb + ' ' + str(rnd.choice([3101, 3214, 4402, 5120]))
    return heb + size + ' צוברים'


def write_csv(path, rows):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


def main(seed):
    rnd = random.Random(seed)
    raw, truth = os.path.join(HERE, 'data', 'raw'), os.path.join(HERE, 'data', 'truth')
    for d in [os.path.join(raw, 'delivery_notes'), os.path.join(raw, 'workbooks'), truth]:
        os.makedirs(d, exist_ok=True)

    notes, true_rows, own_kg_month = [], [], {}
    note_no = 3967
    for s in SEASONS:
        for _ in range(rnd.randint(620, 760)):
            d = date(s, 2, 1) + timedelta(days=rnd.randint(0, 140))
            own = rnd.random() < OWN_SHARE
            v = rnd.choices(list(VARIETIES), weights=[w for _, w in VARIETIES.values()])[0]
            kg = rnd.choice([24000, 25000, 25000, 26000, 27000])
            n_growers = 1
            if own:
                grower = rnd.choice(OWN_SPELLINGS)
                if rnd.random() < .04:                                       # M5 shared container
                    grower = f'{rnd.choice(OTHER_GROWERS)}, {grower}'; n_growers = 2
            else:
                grower = rnd.choice(OTHER_GROWERS)
            buyer = rnd.choices(list(BUYERS), weights=[w for _, w in BUYERS.values()])[0]
            rec = dict(note=note_no, date=d.isoformat(), buyer=BUYERS[buyer][0], grower=grower, variety=variety_text(rnd, v), net_kg=kg)
            reissued = rnd.random() < .01                                    # M10: corrected weight issued later
            if reissued:
                rec_v2 = dict(rec, net_kg=kg - rnd.choice([500, 1000, 1500]))
            final = rec_v2 if reissued else rec
            notes.append(dict(rec=rec, v2=rec_v2 if reissued else None))
            own_kg = final['net_kg'] / n_growers if own else 0
            true_rows.append(dict(note=note_no, date=d.isoformat(), buyer_id=buyer, variety_true=v, net_kg=final['net_kg'],
                                  own_kg=own_kg, is_known_variety=v in KNOWN))
            if own_kg:
                own_kg_month[(buyer, s, d.month)] = own_kg_month.get((buyer, s, d.month), 0) + own_kg
            note_no += 1

    # delivery-note files (M1 copies, M6 lost season, M10 re-issued v2 files)
    for n in notes:
        rec = n['rec']
        if rec['date'].startswith(str(LOST_SEASON)) and rnd.random() < .70:
            continue
        for copy in range(1 + (rnd.random() < .45) + (rnd.random() < .15)):
            name = f"note_{rec['note']}" + ('' if copy == 0 else f' ({copy})') + '.json'
            json.dump(rec, open(os.path.join(raw, 'delivery_notes', name), 'w'), ensure_ascii=False)
        if n['v2']:
            json.dump(n['v2'], open(os.path.join(raw, 'delivery_notes', f"note_{rec['note']}_v2.json"), 'w'), ensure_ascii=False)

    # marketing workbooks + an index describing them (M2, M3, M7, M8, M11)
    index = []
    for s in SEASONS:
        recs = [(n['v2'] or n['rec']) for n in notes if n['rec']['date'].startswith(str(s))]
        books = {f'marketing_{s}_master.csv': ('master', None, recs)}
        for bid, (bname, _) in BUYERS.items():
            books[f'marketing_{s}_{bname.split()[0].lower()}.csv'] = ('buyer', bid, [r for r in recs if r['buyer'] == bname and rnd.random() < .85])
        for fname, (role, bid, rows) in books.items():
            index.append(dict(book=fname, role=role, buyer_id=bid or '', default_grower=''))
            with open(os.path.join(raw, 'workbooks', fname), 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(['תאריך', 'ת. משלוח', 'מגדל', 'זן', 'סה"כ טון'])
                running = 0
                for i, r in enumerate(sorted(rows, key=lambda r: r['date'])):
                    kg = r['net_kg'] * (1 + rnd.choice([-1, 1]) * rnd.uniform(.01, .03)) if rnd.random() < .03 else r['net_kg']   # M11
                    weight = round(kg / 1000, 3) if rnd.random() < .6 else round(kg)                                            # M7
                    d = r['date'] if rnd.random() >= .003 else rnd.choice(['0254-03-13', '1987-09-30', '2052-04-01'])            # M8
                    w.writerow([d, r['note'], r['grower'], r['variety'], weight])
                    running += r['net_kg']
                    if i % 40 == 39:
                        w.writerow(['', '', '', 'סה"כ', running]); running = 0                                                  # M3
    write_csv(os.path.join(raw, 'workbooks', 'index.csv'), index)
    write_csv(os.path.join(raw, 'buyers.csv'), [dict(counterparty_id=k, name_on_paper=v[0], label=v[0]) for k, v in BUYERS.items()])

    # VAT lines
    vat, inv_no, settle = [], 100600, {}
    for (b, s, m), kg in own_kg_month.items():
        lag = rnd.choice([1, 2, 2, 3])
        mm, yy = (m - 1 + lag) % 12 + 1, s + (m - 1 + lag) // 12
        settle[(b, yy, mm)] = settle.get((b, yy, mm), 0) + kg * SEASON_PRICE[s] * rnd.uniform(.97, 1.03)
    line = lambda y, m, day, direction, cp, ref, a, rate: dict(entity=OWN_ENTITY, period=f'{y}{m:02d}', doc_date=date(y, m, day).isoformat(),
                                                               direction=direction, counterparty=cp, reference=ref, amount=round(a), vat=round(a * rate))
    for (b, y, m), amt in sorted(settle.items()):
        vat.append(line(y, m, 28, 'sale', b, inv_no, amt, 0)); inv_no += 1
    for y in SEASONS:
        r = VAT_RATE[y]
        for m in range(1, 13):
            for _ in range(rnd.randint(6, 14)):
                vat.append(line(y, m, rnd.randint(1, 28), 'sale', LOCAL, inv_no, rnd.randint(6000, 18000) * (1.6 if m in (4, 5, 6) else 1), 0)); inv_no += 1
            if m in (1, 5, 8):
                vat.append(line(y, m, 25, 'sale', CONTRACT, inv_no, rnd.randint(900_000, 1_300_000), 0)); inv_no += 1
            for c in SERVICE_CLIENTS:
                vat.append(line(y, m, 28, 'sale', c, inv_no, rnd.randint(60_000, 95_000), r)); inv_no += 1
            for _ in range(rnd.randint(25, 45)):
                vat.append(line(y, m, rnd.randint(1, 28), 'input', f'9900005{rnd.randint(10, 79)}', rnd.randint(1, 99999),
                                rnd.randint(3000, 60000) * (1.8 if m in (10, 11, 12, 1) else 1), r))
    vat.append(dict(entity=OWN_ENTITY, period='202305', doc_date='2023-05-14', direction='input', counterparty='990000533',
                    reference=48213, amount=1900, vat=300000304))                                                                  # M9
    write_csv(os.path.join(raw, 'vat_lines.csv'), vat)

    write_csv(os.path.join(truth, 'shipments_truth.csv'), true_rows)
    json.dump(dict(season_price=SEASON_PRICE, known_varieties=KNOWN, lost_season=LOST_SEASON, corrupt_vat=300000304,
                   own_entity=OWN_ENTITY), open(os.path.join(truth, 'truth.json'), 'w'), indent=1)
    print(f"{len(notes)} shipments, {len(os.listdir(os.path.join(raw, 'delivery_notes')))} note files, {len(vat)} VAT lines -> data/raw")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', type=int, default=42)
    main(ap.parse_args().seed)
