"""Beta Mode: materiality ratings vs measured capital response.

1. Score every (event, bucket) with the rubric in research/event_ratings.json (blind to price moves).
2. Fit a calibration: how much sector-relative move does history produce per rating point at 3 months (reaction)
   and once settled (12-24 months). Weighted: episodes 0.5, overlap-discounted, outliers clipped.
3. Residual = actual reaction - expected reaction. Verdicts: oversold / overbought / underbought / undersold / justified.
4. Backtest: do large residuals mean-revert over months 4-12? Hit rate and average drift by residual bucket.
5. Opportunity score per sub-industry from recency-weighted residuals of the last 18 months, cross-checked with heat.
Writes site/data/beta.js (window.CRT_BETA) and data/processed/event_scored.csv.
"""
import os, json, glob, math, datetime as dt
import numpy as np, pandas as pd
from common import load_taxonomy, all_sectors, PROC, SITE_DATA, ROOT

T = load_taxonomy()
P_W, B_W, C_W = {1: 0.6, 2: 0.8, 3: 1.0}, {1: 0.5, 2: 0.8, 3: 1.0}, {1: 0.5, 2: 0.8, 3: 1.0}
HALF_LIFE_DAYS = 180; RECENT_DAYS = 548

names = {}; sector_name = {}
for sec in all_sectors(T):
    sector_name[sec['id']] = sec['name']
    for si in sec['subindustries']: names[si['id']] = si['name']
for th in T['themes']: names[th['id']] = th['name']; sector_name[th['id']] = th['name']

ratings = json.load(open(os.path.join(ROOT, 'research', 'event_ratings.json'), encoding='utf-8'))
ratings = {k: v for k, v in ratings.items() if not k.startswith('_')}
events = {}
for fp in sorted(glob.glob(os.path.join(ROOT, 'research', 'events_*.json'))):
    for e in json.load(open(fp, encoding='utf-8')).get('events', []): events[e['id']] = e
R = pd.read_csv(os.path.join(PROC, 'event_response.csv'))
snap = json.loads(open(os.path.join(SITE_DATA, 'snapshot.js'), encoding='utf-8').read().split('=', 1)[1].rstrip(';'))
heat = {b['id']: b for b in snap['buckets']}; heat.update({t['id']: t for t in snap['themes']})
asof = pd.Timestamp(snap['asof'])

def score(ev_id, bucket):
    r = ratings.get(ev_id)
    if r is None: return None
    comp = {k: r[k] for k in ('e', 'p', 'b', 'c')}
    comp.update((r.get('per') or {}).get(bucket, {}))
    val = 100 * (comp['e'] / 5) * P_W[comp['p']] * B_W[comp['b']] * C_W[comp['c']]
    return round(val, 1), comp, bool(r.get('ep')), r.get('why', '')

rows = []
missing = set()
for _, x in R.iterrows():
    s = score(x.event_id, x.bucket)
    if s is None: missing.add(x.event_id); continue
    rating, comp, ep, why = s
    rows.append({**x.to_dict(), 'rating': rating, **{f'c_{k}': v for k, v in comp.items()}, 'episode': ep, 'why': why,
                 'weight': (0.5 if ep else 1.0) / (1 + 0.5 * x.overlap)})
D = pd.DataFrame(rows)
if missing: print('events without ratings:', len(missing), sorted(missing)[:10])

# ---------------------------------------------------------------- calibration fits (weighted, clipped)
def wfit(x, y, w, clip=(-0.8, 1.5)):
    m = ~(np.isnan(x) | np.isnan(y) | np.isnan(w))
    x, y, w = x[m], np.clip(y[m], *clip), w[m]
    if len(x) < 30: return None
    X = np.column_stack([np.ones(len(x)), x])
    W = np.sqrt(w)
    beta, *_ = np.linalg.lstsq(X * W[:, None], y * W, rcond=None)
    pred = X @ beta; resid = y - pred
    sd = float(np.sqrt(np.average(resid ** 2, weights=w)))
    r = float(np.corrcoef(x, y)[0, 1])
    return {'intercept': float(beta[0]), 'slope': float(beta[1]), 'resid_sd': sd, 'n': int(len(x)), 'corr': r}

fits = {}
for name, col, need in [('reaction_21', 'sec_21', 'complete_21'), ('reaction_63', 'sec_63', 'complete_63'), ('reaction_126', 'sec_126', 'complete_126'),
                         ('settled_252', 'sec_252', 'complete_252'), ('settled_504', 'sec_504', 'complete_504'),
                         ('market_63', 'rel_63', 'complete_63'), ('market_252', 'rel_252', 'complete_252')]:
    sub = D[D[need] == True]
    fits[name] = wfit(sub['rating'].values.astype(float), sub[col].values.astype(float), sub['weight'].values.astype(float))
# settled = average of 12m and 24m where both complete, else 12m
D['settled'] = D.apply(lambda r: np.nanmean([v for v, ok in [(r.sec_252, r.complete_252), (r.sec_504, r.complete_504)] if ok]) if (r.complete_252 or r.complete_504) else np.nan, axis=1)
fits['settled'] = wfit(D['rating'].values.astype(float), D['settled'].values.astype(float), D['weight'].values.astype(float))

# binned medians for the chart
bins = [-101, -60, -30, -10, 10, 30, 60, 101]
D['rbin'] = pd.cut(D['rating'], bins)
binned = []
for b, g in D.groupby('rbin', observed=True):
    binned.append({'bin': f'{int(b.left)} to {int(b.right)}', 'mid': float((b.left + b.right) / 2), 'n': int(len(g)),
                   'med_63': None if g['sec_63'].dropna().empty else float(g['sec_63'].median()),
                   'med_252': None if g['sec_252'].dropna().empty else float(g['sec_252'].median()),
                   'med_settled': None if g['settled'].dropna().empty else float(g['settled'].median())})

# ---------------------------------------------------------------- residuals and verdicts
f63, f21 = fits['reaction_63'], fits['reaction_21']
def expected(fit, rating): return fit['intercept'] + fit['slope'] * rating
D['exp_63'] = D['rating'].apply(lambda r: expected(f63, r))
D['exp_21'] = D['rating'].apply(lambda r: expected(f21, r))
D['resid_63'] = D['sec_63'] - D['exp_63']
D['resid_21'] = D['sec_21'] - D['exp_21']
# reaction used: 63d when complete, else 21d (recent events), scaled to 63d-equivalent sd units
D['z'] = np.where(D['complete_63'] & D['sec_63'].notna(), D['resid_63'] / f63['resid_sd'],
                  np.where(D['sec_21'].notna(), D['resid_21'] / f21['resid_sd'], np.nan))
D['window'] = np.where(D['complete_63'] & D['sec_63'].notna(), '3m', np.where(D['sec_21'].notna(), '1m', 'n/a'))
D['resid_used'] = np.where(D['window'] == '3m', D['resid_63'], D['resid_21'])

def verdict(r):
    if pd.isna(r.z): return 'n/a'
    if abs(r.z) < 0.75: return 'justified'
    if r.rating >= 10: return 'overbought' if r.z > 0 else 'underbought'
    if r.rating <= -10: return 'undersold' if r.z > 0 else 'oversold'
    return 'overbought (no fundamental driver)' if r.z > 0 else 'oversold (no fundamental driver)'
D['verdict'] = D.apply(verdict, axis=1)
D['confidence'] = D.apply(lambda r: 'high' if (r.overlap == 0 and not r.episode and r.window == '3m') else 'medium' if (r.overlap <= 1 and r.window == '3m') else 'low', axis=1)

# ---------------------------------------------------------------- backtest: do residuals mean-revert?
H = D[(D.complete_252 == True) & D['resid_63'].notna() & D['sec_252'].notna()].copy()
H['drift_4_12'] = (1 + H['sec_252']) / (1 + H['sec_63']) - 1
H['zq'] = pd.qcut(H['resid_63'] / f63['resid_sd'], 5, labels=['most oversold', 'oversold', 'middle', 'overbought', 'most overbought'])
bt_bins = [{'group': str(k), 'n': int(len(g)), 'mean_drift': float(g['drift_4_12'].mean()), 'median_drift': float(g['drift_4_12'].median()),
            'hit_rate': float((np.sign(g['drift_4_12']) == -np.sign(g['resid_63'])).mean())} for k, g in H.groupby('zq', observed=True)]
big = H[(H['resid_63'] / f63['resid_sd']).abs() > 1]
backtest = {'n': int(len(H)), 'corr_resid_drift': float(np.corrcoef(H['resid_63'], H['drift_4_12'])[0, 1]),
            'n_large': int(len(big)), 'hit_rate_large': float((np.sign(big['drift_4_12']) == -np.sign(big['resid_63'])).mean()) if len(big) else None,
            'mean_drift_oversold': float(big[big.resid_63 < 0]['drift_4_12'].mean()) if (big.resid_63 < 0).any() else None,
            'mean_drift_overbought': float(big[big.resid_63 > 0]['drift_4_12'].mean()) if (big.resid_63 > 0).any() else None,
            'by_quintile': bt_bins,
            'by_sector': [{'sector': s, 'n': int(len(g)), 'hit_rate': float((np.sign(g['drift_4_12']) == -np.sign(g['resid_63'])).mean()), 'corr': float(np.corrcoef(g['resid_63'], g['drift_4_12'])[0, 1]) if len(g) > 5 else None} for s, g in H.groupby('sector')],
            'note': 'Drift = sector-relative return from month 4 to month 12 after the event. Hit = drift moves against the 3-month residual.'}
# also: does the settled move line up with the rating at all (fundamental validity)
backtest['rating_vs_settled_corr'] = fits['settled']['corr'] if fits['settled'] else None
C = D[(~D.episode) & (D.complete_252 == True) & D['sec_252'].notna()]
strong = C[C['rating'].abs() >= 15]
backtest['fundamental_signal'] = {'n': int(len(strong)), 'hit_rate_12m': float((np.sign(strong['rating']) == np.sign(strong['sec_252'])).mean()),
    'mean_12m_positive': float(strong[strong.rating > 0]['sec_252'].mean()), 'mean_12m_negative': float(strong[strong.rating < 0]['sec_252'].mean()),
    'by_rating_bucket': [{'bucket': str(k), 'n': int(len(g)), 'mean_12m': float(g['sec_252'].mean()), 'median_12m': float(g['sec_252'].median())} for k, g in C.groupby(pd.cut(C['rating'], [-101, -40, -15, 15, 40, 101]), observed=True)],
    'note': 'Catalyst events only (episodes excluded). Hit = the sector-relative 12-month move has the same sign as the rating.'}
Hc = H[(~H.episode) & (H.overlap == 0)]
backtest['clean'] = {'n': int(len(Hc)), 'corr_resid_drift_4_12': float(np.corrcoef(Hc['resid_63'], Hc['drift_4_12'])[0, 1]),
    'corr_resid_drift_4_6': float(np.corrcoef(Hc['resid_63'], ((1 + Hc['sec_126']) / (1 + Hc['sec_63']) - 1).fillna(0))[0, 1]),
    'by_verdict': [{'verdict': k, 'n': int(len(g)), 'mean_drift_4_12': float(g['drift_4_12'].mean()), 'median_drift_4_12': float(g['drift_4_12'].median())} for k, g in Hc.groupby('verdict')],
    'note': 'Catalysts with no overlapping event. Positive correlation means over/under-reactions persisted rather than reverted.'}

# ---------------------------------------------------------------- opportunity score per bucket
D['date_ts'] = pd.to_datetime(D['date'])
recent = D[D['date_ts'] >= asof - pd.Timedelta(days=RECENT_DAYS)].copy()
recent['age'] = (asof - recent['date_ts']).dt.days
recent['rw'] = np.exp(-np.log(2) * recent['age'] / HALF_LIFE_DAYS) * recent['weight']
buckets = []
for bid, g in recent.groupby('bucket'):
    gg = g[g['resid_used'].notna()]
    fund = float((g['rating'] * g['rw']).sum() / g['rw'].sum())  # all recent events, including ones too fresh to have a reaction window
    if gg.empty:
        unexpl, zsum = 0.0, 0.0
    else:
        unexpl = float((gg['resid_used'] * gg['rw']).sum() / gg['rw'].sum())  # recency-weighted unexplained move (sector-relative)
        zsum = float((gg['z'] * gg['rw']).sum() / gg['rw'].sum())
    h = heat.get(bid, {})
    pct = h.get('weight_pctile'); hs = h.get('heat')
    # Stance follows the evidence: ratings predict 12-month sector-relative returns; residuals persist rather than revert.
    # Primary signal = recency-weighted fundamental score; residual says whether capital has already moved; heat says if it is crowded.
    stance, setup = 'Neutral', 'no strong recent fundamental signal'
    if fund >= 15:
        if zsum <= 0.3: stance, setup = 'Long', 'fundamentals improving, capital has not followed yet'
        elif hs is not None and hs >= 40: stance, setup = 'Long (crowded)', 'fundamentals improving but capital already concentrated; momentum, not value'
        else: stance, setup = 'Long (momentum)', 'fundamentals improving and capital following'
    elif fund <= -15:
        if zsum >= -0.3: stance, setup = 'Short', 'fundamentals deteriorating, capital has not left yet'
        elif hs is not None and hs <= -40: stance, setup = 'Short (washed out)', 'fundamentals deteriorating and capital already gone; late'
        else: stance, setup = 'Short (momentum)', 'fundamentals deteriorating and capital leaving'
    else:
        if zsum <= -1.0: setup = 'capital left without a fundamental cause (historically this has NOT reliably reverted)'
        elif zsum >= 1.0: setup = 'capital arrived without a fundamental cause (historically this has tended to persist)'
    conf = 'high' if (len(gg) >= 3 and (gg['confidence'] == 'high').mean() >= 0.5) else 'medium' if len(g) >= 2 else 'low'
    buckets.append({'id': bid, 'name': names.get(bid, bid), 'sector': g['sector'].iloc[0], 'sector_name': sector_name.get(g['sector'].iloc[0], g['sector'].iloc[0]),
                    'level': g['level'].iloc[0], 'n_events': int(len(g)), 'n_measured': int(len(gg)), 'unexplained': unexpl, 'z': zsum, 'fundamental': fund, 'stance': stance, 'setup': setup, 'confidence': conf,
                    'heat': hs, 'label': h.get('label'), 'weight_pctile': pct, 'rel_3m': h.get('rel_3m'), 'rel_12m': h.get('rel_12m'),
                    'events': [{'event_id': r.event_id, 'date': r.date, 'title': r.title, 'rating': r.rating, 'actual': (r.resid_used + (r.exp_63 if r.window == '3m' else r.exp_21)) if not pd.isna(r.resid_used) else None, 'expected': r.exp_63 if r.window == '3m' else r.exp_21, 'resid': r.resid_used, 'z': r.z, 'verdict': r.verdict, 'window': r.window, 'confidence': r.confidence} for r in g.sort_values('date', ascending=False).itertuples()]})
buckets.sort(key=lambda b: -b['fundamental'])

# ---------------------------------------------------------------- outputs
ev_out = []
for r in D.sort_values('date', ascending=False).itertuples():
    e = events.get(r.event_id, {})
    ev_out.append({'event_id': r.event_id, 'date': r.date, 'sector': r.sector, 'sector_name': sector_name.get(r.sector, r.sector), 'bucket': r.bucket, 'bucket_name': names.get(r.bucket, r.bucket), 'level': r.level,
                   'title': r.title, 'direction': r.direction, 'magnitude': r.magnitude, 'driver_type': r.driver_type, 'episode': bool(r.episode), 'why': r.why,
                   'rating': r.rating, 'components': {'e': r.c_e, 'p': r.c_p, 'b': r.c_b, 'c': r.c_c},
                   'pre_63': None if pd.isna(r.pre_63) else float(r.pre_63),
                   'sec_21': None if pd.isna(r.sec_21) else float(r.sec_21), 'sec_63': None if pd.isna(r.sec_63) else float(r.sec_63), 'sec_126': None if pd.isna(r.sec_126) else float(r.sec_126), 'sec_252': None if pd.isna(r.sec_252) else float(r.sec_252), 'sec_504': None if pd.isna(r.sec_504) else float(r.sec_504),
                   'rel_63': None if pd.isna(r.rel_63) else float(r.rel_63), 'rel_252': None if pd.isna(r.rel_252) else float(r.rel_252), 'flow_63': None if pd.isna(r.flow_63) else float(r.flow_63),
                   'settled': None if pd.isna(r.settled) else float(r.settled), 'expected_63': float(r.exp_63), 'expected_21': float(r.exp_21),
                   'resid': None if pd.isna(r.resid_used) else float(r.resid_used), 'z': None if pd.isna(r.z) else float(r.z), 'window': r.window, 'verdict': r.verdict, 'confidence': r.confidence, 'overlap': int(r.overlap),
                   'summary': e.get('summary'), 'evidence': e.get('evidence'), 'sources': (e.get('sources') or [])[:2]})

out = {'asof': snap['asof'], 'generated': dt.datetime.now().strftime('%Y-%m-%d %H:%M'), 'fits': fits, 'binned': binned, 'backtest': backtest,
       'buckets': buckets, 'events': ev_out, 'params': {'half_life_days': HALF_LIFE_DAYS, 'recent_days': RECENT_DAYS, 'z_threshold': 0.75, 'stance_threshold': 0.6},
       'rubric': json.load(open(os.path.join(ROOT, 'research', 'event_ratings.json'), encoding='utf-8'))['_rubric'],
       'n_rated': int(D.event_id.nunique()), 'n_unrated': len(missing)}
def clean(o):
    if isinstance(o, float): return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict): return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list): return [clean(v) for v in o]
    if isinstance(o, (np.floating,)): return clean(float(o))
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.bool_,)): return bool(o)
    return o
with open(os.path.join(SITE_DATA, 'beta.js'), 'w', encoding='utf-8') as f:
    f.write('window.CRT_BETA = '); json.dump(clean(out), f, ensure_ascii=False, separators=(',', ':')); f.write(';')
D.drop(columns=['rbin', 'date_ts']).to_csv(os.path.join(PROC, 'event_scored.csv'), index=False)

print(f"scored {len(D)} event-bucket rows ({D.event_id.nunique()} events)")
for k, v in fits.items():
    if v: print(f"  {k:13s} slope {v['slope']*100:6.3f}% per rating pt | intercept {v['intercept']*100:5.2f}% | corr {v['corr']:5.2f} | resid sd {v['resid_sd']*100:5.1f}% | n {v['n']}")
print('backtest:', {k: v for k, v in backtest.items() if k not in ('by_quintile', 'by_sector', 'note')})
for b in bt_bins: print('  ', b)
print('verdicts:', D['verdict'].value_counts().to_dict())
print('stances:', pd.Series([b['stance'] for b in buckets]).value_counts().to_dict())
for b in buckets[:6] + buckets[-6:]: print(f"  {b['stance']:24s} z={b['z']:+.2f} unexpl={b['unexplained']*100:+.1f}% fund={b['fundamental']:+.0f} n={b['n_events']} {b['name']}")
