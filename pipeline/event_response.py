"""Measure what capital actually did after each researched event.

For every event in research/events_*.json and every sub-industry it tags (or the sector if none):
  pre_63   : bucket return relative to SPY over the 63 trading days before the event (anticipation)
  rel_N    : bucket total return minus SPY over N trading days after the event date (N = 21, 63, 126, 252, 504)
  sec_N    : bucket return minus its own sector over N days (isolates the sub-industry-specific move)
  flow_63  : ETF net flow over the 63 days after the event as a share of AUM at the event (if ETF data exists)
  overlap  : number of other major/moderate events in the same bucket within +/-126 days (attribution confidence)
Output: data/processed/event_response.csv
"""
import os, glob, json, math
import numpy as np, pandas as pd
from common import load_taxonomy, all_sectors, RAW, PROC, ROOT

T = load_taxonomy()
D = pd.read_parquet(os.path.join(PROC, 'daily_returns.parquet')).sort_index()
sector_of = {}
for sec in all_sectors(T):
    for si in sec['subindustries']: sector_of[si['id']] = sec['id']
theme_ids = {t['id'] for t in T['themes']}

events = []
for fp in sorted(glob.glob(os.path.join(ROOT, 'research', 'events_*.json'))):
    try: events += json.load(open(fp, encoding='utf-8')).get('events', [])
    except Exception as e: print('bad', fp, e)

flows = None
fp = os.path.join(RAW, 'etf_flows.csv')
if os.path.exists(fp):
    flows = pd.read_csv(fp, parse_dates=['date']).dropna(subset=['flow_usd']).sort_values('date')

etf_of = {}
for sec in all_sectors(T):
    etf_of['sector:' + sec['id']] = sec['sector_etf']
    for si in sec['subindustries']:
        if si.get('etf'): etf_of[si['id']] = si['etf']

WINDOWS = [21, 63, 126, 252, 504]
idx = D.index

def cum(series, i0, i1):
    s = series.iloc[i0:i1].dropna()
    if len(s) < max(3, int(0.6 * (i1 - i0))): return np.nan
    return float((1 + s).prod() - 1)

def pos_for(date):
    d = pd.Timestamp(date)
    p = idx.searchsorted(d)
    return int(p) if p < len(idx) else None

def flow_after(etf, date, days=63):
    if flows is None or not etf: return np.nan
    g = flows[flows.ticker == etf]
    if g.empty: return np.nan
    d = pd.Timestamp(date); end = d + pd.Timedelta(days=int(days * 7 / 5))
    w = g[(g.date > d) & (g.date <= end)]
    base = g[g.date <= d]
    if w.empty or base.empty or not base.iloc[-1]['aum_usd']: return np.nan
    return float(w['flow_usd'].sum() / base.iloc[-1]['aum_usd'])

rows = []
for e in events:
    targets = e.get('subindustries') or []
    sec = e.get('sector')
    keys = [(t, 'sub') for t in targets if t in D.columns] or ([(sec, 'sector')] if ('sector:' + str(sec)) in D.columns else [])
    for bid, kind in keys:
        col = bid if kind == 'sub' else 'sector:' + bid
        seccol = 'sector:' + (sector_of.get(bid, bid) if kind == 'sub' else bid)
        p = pos_for(e['date'])
        if p is None or p < 63: continue
        r = D[col]; spy = D['SPY']; secr = D[seccol] if seccol in D.columns else None
        rec = {'event_id': e['id'], 'date': e['date'], 'sector': sec, 'bucket': bid, 'level': kind, 'direction': e.get('direction'), 'magnitude': e.get('magnitude'), 'driver_type': e.get('driver_type'), 'title': e.get('title')}
        pre_b, pre_s = cum(r, p - 63, p), cum(spy, p - 63, p)
        rec['pre_63'] = pre_b - pre_s if not (math.isnan(pre_b) or math.isnan(pre_s)) else np.nan
        for n in WINDOWS:
            b, s = cum(r, p, p + n), cum(spy, p, p + n)
            rec[f'rel_{n}'] = b - s if not (math.isnan(b) or math.isnan(s)) else np.nan
            if secr is not None and kind == 'sub':
                sc = cum(secr, p, p + n); rec[f'sec_{n}'] = b - sc if not (math.isnan(b) or math.isnan(sc)) else np.nan
            else:
                rec[f'sec_{n}'] = rec[f'rel_{n}']
            rec[f'complete_{n}'] = bool(p + n <= len(idx))
        rec['flow_63'] = flow_after(etf_of.get(bid if kind == 'sub' else 'sector:' + bid), e['date'])
        rows.append(rec)

R = pd.DataFrame(rows)
# overlap: other major/moderate events for the same bucket within +/-126 trading days (~6 months)
R['date_ts'] = pd.to_datetime(R['date'])
ov = []
for i, row in R.iterrows():
    same = R[(R.bucket == row.bucket) & (R.event_id != row.event_id) & (R.magnitude.isin(['major', 'moderate']))]
    ov.append(int(((same.date_ts - row.date_ts).abs() <= pd.Timedelta(days=180)).sum()))
R['overlap'] = ov
R = R.drop(columns=['date_ts'])
R.to_csv(os.path.join(PROC, 'event_response.csv'), index=False)
print('event responses:', len(R), 'rows for', R.event_id.nunique(), 'events;', R.bucket.nunique(), 'buckets')
print(R[['rel_21', 'rel_63', 'rel_252', 'sec_63', 'sec_252']].describe().round(3))
print('direction check (median rel_63 by labelled direction):'); print(R.groupby('direction')[['rel_21', 'rel_63', 'rel_252', 'sec_252']].median().round(3))
