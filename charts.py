"""Draw the four README charts straight from the marts in db/farm.duckdb, and write reports/summary.json."""
import json, os
import duckdb
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.abspath(__file__))
FIG = os.path.join(ROOT, 'reports', 'figures')
os.makedirs(FIG, exist_ok=True)
con = duckdb.connect(os.path.join(ROOT, 'db', 'farm.duckdb'), read_only=True)
q = lambda sql: con.sql(sql).df()
INK, ACCENT, MUTED = '#1d2a3a', '#c8553d', '#8a9bb0'
plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False, 'axes.edgecolor': MUTED,
                     'axes.titleweight': 'bold', 'axes.titlesize': 12, 'axes.titlelocation': 'left'})

share = q('SELECT channel, share FROM mart_channel_share ORDER BY share')
conc = q('SELECT * FROM mart_concentration').iloc[0]
price = q('SELECT * FROM mart_price_per_kg_season ORDER BY season')
lag = q('SELECT avg(lag_months) AS lag FROM mart_settlement_lag').lag[0]
cash = q('SELECT * FROM mart_cash_curve ORDER BY farm_order')
stab = q('SELECT * FROM mart_channel_stability')
byyear = q('''SELECT c.channel, v.year, sum(v.amount) AS ils FROM fact_vat_line v JOIN dim_counterparty c ON v.counterparty = c.counterparty_id
              WHERE v.direction = 'sale' AND v.dq_flag IS NULL GROUP BY ALL''').pivot(index='year', columns='channel', values='ils')
fees = q('SELECT ils_per_container FROM mart_packing_fee_per_container').ils_per_container
fee = fees.mean()
dq = q('SELECT * FROM dq_summary')

last, prev = price.iloc[-1], price.iloc[-2]
S = dict(export_share=round(float(share.set_index('channel').share.get('Export (monthly settlement)', 0)), 3),
         largest_customer_share=round(float(conc.largest_customer_share), 3), top5_share=round(float(conc.top5_share), 3),
         hhi=round(float(conc.hhi), 3),
         ils_per_kg=dict(zip(price.season.astype(str), price.ils_per_kg.round(2))),
         last_change=round(float(last.change_vs_prev), 3), last_season=int(last.season),
         settlement_lag_months=round(float(lag), 1),
         cash_trough_ils=round(float(-cash.cumulative_ils.min())), cash_trough_month=int(cash.loc[cash.cumulative_ils.idxmin(), 'month']),
         cv=dict(zip(stab.channel, stab.cv.round(3))), packing_fee_per_container=round(float(fee)), packing_fee_min=round(float(fees.min())), packing_fee_max=round(float(fees.max())),
         dq_rows=int(dq.rows_affected.sum()), dq_issue_types=len(dq))
json.dump(S, open(os.path.join(ROOT, 'reports', 'summary.json'), 'w'), indent=1, default=float)

# Q1
fig, ax = plt.subplots(figsize=(7.5, 3.3))
ax.barh(share.channel, share.share, color=[ACCENT if 'Export' in c else MUTED for c in share.channel])
for i, v in enumerate(share.share):
    ax.text(v + .005, i, f'{v:.0%}', va='center')
ax.set_xlim(0, share.share.max() * 1.18); ax.xaxis.set_visible(False)
ax.set_title(f'Export is {S["export_share"]:.0%} of sales; one buyer alone is {S["largest_customer_share"]:.0%}')
fig.tight_layout(); fig.savefig(os.path.join(FIG, 'q1_channels.png'), dpi=160); plt.close(fig)

# Q2
fig, ax = plt.subplots(figsize=(7.5, 3.3))
worst = price.ils_per_kg.idxmin()
ax.bar(price.season.astype(str), price.ils_per_kg, width=.55, color=[ACCENT if i == worst else INK for i in price.index])
for x, v in zip(price.season.astype(str), price.ils_per_kg):
    ax.text(x, v + .03, f'₪{v:.2f}', ha='center')
ax.set_ylabel('Net ₪ per kg, own export'); ax.set_ylim(0, price.ils_per_kg.max() * 1.2)
ax.set_title(f'Net price per kg fell {-S["last_change"]:.0%} from {S["last_season"] - 1} to {S["last_season"]}')
fig.tight_layout(); fig.savefig(os.path.join(FIG, 'q2_price_per_kg.png'), dpi=160); plt.close(fig)

# Q3
fig, ax = plt.subplots(figsize=(7.5, 3.3))
lbl = ['Sep', 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug']
y = cash.cumulative_ils.values / 1e6
ax.fill_between(range(12), y, 0, where=y < 0, color=ACCENT, alpha=.25, step='mid')
ax.plot(range(12), y, color=INK, lw=2, drawstyle='steps-mid'); ax.axhline(0, color=MUTED, lw=.8)
ax.set_xticks(range(12)); ax.set_xticklabels(lbl); ax.set_ylabel('Cumulative ₪M (VAT-visible flows)')
ax.set_title(f'At least ₪{S["cash_trough_ils"] / 1e6:.1f}M in the red before buyers pay')
fig.tight_layout(); fig.savefig(os.path.join(FIG, 'q3_cash_curve.png'), dpi=160); plt.close(fig)

# Q4
fig, ax = plt.subplots(figsize=(7.5, 3.3))
style = {'Export (monthly settlement)': (ACCENT, '-', 1.8), 'Packing services': (INK, '-', 2.6),
         'Contract buyer': (MUTED, '--', 1.4), 'Local wholesale': (MUTED, ':', 1.6)}
ends = byyear.iloc[-1].sort_values() / 1e6
label_y, last = {}, -1e9
for c, v in ends.items():                       # spread end labels so they never overlap
    last = max(v, last + 0.85); label_y[c] = last
for c in byyear.columns:
    col, ls, lw = style.get(c, (MUTED, '-', 1.2))
    ax.plot(byyear.index, byyear[c] / 1e6, marker='o', ls=ls, lw=lw, color=col)
    ax.annotate(f'{c.split(" (")[0]}  (CV {S["cv"].get(c, 0):.0%})', (byyear.index[-1], byyear[c].iloc[-1] / 1e6),
                xytext=(byyear.index[-1] + .15, label_y[c]), textcoords='data', va='center', fontsize=8.5, color=col)
ax.set_xticks(byyear.index); ax.set_ylabel('Revenue, ₪M'); ax.set_xlim(byyear.index[0] - .2, byyear.index[-1] + 1.6)
ax.set_title('Packing services: the steadiest line in the business', pad=10)
fig.tight_layout(); fig.savefig(os.path.join(FIG, 'q4_stability.png'), dpi=160); plt.close(fig)
print(json.dumps(S, indent=1, default=float))
