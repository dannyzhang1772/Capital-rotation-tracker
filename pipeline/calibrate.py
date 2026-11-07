"""Beta Mode v2: materiality ratings vs measured capital response.

Improvements over v1 (all documented on the page):
 1. Residuals are normalised by each sub-industry's own volatility (trailing 252d sector-relative vol, scaled to 63d).
 2. Calibration is piecewise: separate slopes for positive and negative ratings and for sector groups
    (rate-sensitive / growth / cyclical / defensive), shrunk toward the pooled fit when a cell is thin.
 3. Overlapping events in the same bucket (within 90 days) split the measured move in proportion to |rating|.
 4. Episodes (year-long flow descriptions) are excluded from stances and from the backtest.
 5. Stance rules are chosen by the data: the forward return (months 4-12) of each (rating sign x early-reaction class)
    cell is measured on clean catalysts and only cells that historically paid become Long/Short rules.
 6. "Has capital moved?" combines price residual, ETF flow z, 13F hedge-fund share change and the pre-event move.
 7. Evidence gate: a stance needs >= 2 recent catalysts, or 1 catalyst plus a confirming tracker signal; otherwise "Watch".
 8. Hindsight-free subsample: events dated after 2026-06-30 were rated without knowing the outcome; their stats are reported separately.
Writes site/data/beta.js (window.CRT_BETA) and data/processed/event_scored.csv.
"""
import os, json, glob, math, datetime as dt
import numpy as np, pandas as pd
from common import load_taxonomy, all_sectors, PROC, SITE_DATA, ROOT

T = load_taxonomy()
P_W, B_W, C_W = {1: 0.6, 2: 0.8, 3: 1.0}, {1: 0.5, 2: 0.8, 3: 1.0}, {1: 0.5, 2: 0.8, 3: 1.0}
HALF_LIFE_DAYS, RECENT_DAYS, BLIND_FROM, CLUSTER_DAYS, SHRINK_K = 180, 548, '2026-07-01', 90, 40
GROUP = {'real_estate': 'rate-sensitive', 'utilities': 'rate-sensitive', 'financials': 'rate-sensitive', 'technology': 'growth', 'healthcare': 'growth',
         'communication_media': 'growth', 'industrials': 'cyclical', 'energy': 'cyclical', 'materials': 'cyclical', 'consumer_discretionary': 'cyclical',
         'consumer_staples': 'defensive'}
GROUP_OVERRIDE = {'telecom': 'rate-sensitive', 'net_lease_specialty_reits': 'rate-sensitive', 'waste': 'defensive', 'regulated_utilities': 'rate-sensitive', 'managed_care': 'defensive', 'pharma': 'defensive'}

names, sector_name, sector_of = {}, {}, {}
for sec in all_sectors(T):
    sector_name[sec['id']] = sec['name']
    for si in sec['subindustries']: names[si['id']] = si['name']; sector_of[si['id']] = sec['id']
for th in T['themes']: names[th['id']] = th['name']; sector_name[th['id']] = th['name']

ratings = {k: v for k, v in json.load(open(os.path.join(ROOT, 'research', 'event_ratings.json'), encoding='utf-8')).items() if not k.startswith('_')}
rubric = json.load(open(os.path.join(ROOT, 'research', 'event_ratings.json'), encoding='utf-8'))['_rubric']
events = {}
for fp in sorted(glob.glob(os.path.join(ROOT, 'research', 'events_*.json'))):
    for e in json.load(open(fp, encoding='utf-8')).get('events', []): events[e['id']] = e
R = pd.read_csv(os.path.join(PROC, 'event_response.csv'))
snap = json.loads(open(os.path.join(SITE_DATA, 'snapshot.js'), encoding='utf-8').read().split('=', 1)[1].rstrip(';'))
heat = {b['id']: b for b in snap['buckets']}; heat.update({t['id']: t for t in snap['themes']}); heat.update({'sector:' + s['id']: s for s in snap['sectors']})
asof = pd.Timestamp(snap['asof'])

def score(ev_id, bucket):
    r = ratings.get(ev_id)
    if r is None: return None
    comp = {k: r[k] for k in ('e', 'p', 'b', 'c')}; comp.update((r.get('per') or {}).get(bucket, {}))
    return round(100 * (comp['e'] / 5) * P_W[comp['p']] * B_W[comp['b']] * C_W[comp['c']], 1), comp, bool(r.get('ep')), r.get('why', '')

rows, missing = [], set()
for _, x in R.iterrows():
    s = score(x.event_id, x.bucket)
    if s is None: missing.add(x.event_id); continue
    rating, comp, ep, why = s
    rows.append({**x.to_dict(), 'rating': rating, **{f'c_{k}': v for k, v in comp.items()}, 'episode': ep, 'why': why, 'weight': (0.5 if ep else 1.0) / (1 + 0.5 * x.overlap)})
D = pd.DataFrame(rows); D['date_ts'] = pd.to_datetime(D['date'])
D['group'] = D.apply(lambda r: GROUP_OVERRIDE.get(r.bucket, GROUP.get(sector_of.get(r.bucket, r.sector), 'cyclical')), axis=1)
D['blind'] = D['date_ts'] >= pd.Timestamp(BLIND_FROM)
if missing: print('events without ratings:', len(missing), sorted(missing)[:10])

# ---------------------------------------------------------------- 3. overlap attribution: split the move by |rating| inside 90-day clusters
D = D.sort_values(['bucket', 'date_ts']).reset_index(drop=True)
D['cluster'] = 0; cid = 0; last_b, last_d = None, None
for i, r in D.iterrows():
    if r.bucket != last_b or (r.date_ts - last_d).days > CLUSTER_DAYS: cid += 1
    D.at[i, 'cluster'] = cid; last_b, last_d = r.bucket, r.date_ts
csum = D.groupby('cluster')['rating'].apply(lambda s: s.abs().sum()).rename('cl_abs'); D = D.join(csum, on='cluster')
D['attr'] = np.where(D['cl_abs'] > 0, D['rating'].abs() / D['cl_abs'], 1.0 / D.groupby('cluster')['rating'].transform('size'))
for n in (21, 63, 126, 252, 504):
    D[f'sec_{n}_attr'] = D[f'sec_{n}'] * D['attr']

# ---------------------------------------------------------------- 1. volatility-normalised reactions (units of the bucket's own 63d vol)
D['vol63'] = D['vol63'].replace(0, np.nan)
for n, k in ((21, math.sqrt(21 / 63)), (63, 1.0), (126, math.sqrt(2)), (252, 2.0)):
    D[f'nv_{n}'] = D[f'sec_{n}_attr'] / (D['vol63'] * k)
D['nv_pre'] = D['pre_63'] / D['vol63']

# ---------------------------------------------------------------- 2. piecewise calibration with shrinkage
def wfit(x, y, w, clip=(-4, 6)):
    m = ~(np.isnan(x) | np.isnan(y) | np.isnan(w)); x, y, w = x[m], np.clip(y[m], *clip), w[m]
    if len(x) < 25: return None
    X = np.column_stack([np.ones(len(x)), x]); W = np.sqrt(w)
    beta, *_ = np.linalg.lstsq(X * W[:, None], y * W, rcond=None); resid = y - X @ beta
    return {'intercept': float(beta[0]), 'slope': float(beta[1]), 'resid_sd': float(np.sqrt(np.average(resid ** 2, weights=w))), 'n': int(len(x)), 'corr': float(np.corrcoef(x, y)[0, 1]) if len(x) > 2 else None}

CAT = D[~D.episode]
pooled = {n: wfit(CAT['rating'].values.astype(float), CAT[f'nv_{n}'].values.astype(float), CAT['weight'].values.astype(float)) for n in (21, 63, 126, 252) if (CAT[f'complete_{n}'] == True).any()}
cells = {}
for (sign, grp), g in CAT.groupby([np.where(CAT['rating'] >= 0, 'pos', 'neg'), CAT['group']]):
    for n in (21, 63):
        raw = wfit(g['rating'].values.astype(float), g[f'nv_{n}'].values.astype(float), g['weight'].values.astype(float))
        pool = pooled[n]
        if raw is None: cells[(sign, grp, n)] = {**pool, 'n_cell': 0, 'shrunk': True}; continue
        k = SHRINK_K; lam = raw['n'] / (raw['n'] + k)
        cells[(sign, grp, n)] = {'intercept': lam * raw['intercept'] + (1 - lam) * pool['intercept'], 'slope': lam * raw['slope'] + (1 - lam) * pool['slope'],
                                 'resid_sd': lam * raw['resid_sd'] + (1 - lam) * pool['resid_sd'], 'n': raw['n'], 'n_cell': raw['n'], 'corr': raw['corr'], 'shrunk': lam < 0.6}
def fit_for(r, n): return cells.get(('pos' if r.rating >= 0 else 'neg', r.group, n), pooled[n])
D['exp_nv_63'] = D.apply(lambda r: fit_for(r, 63)['intercept'] + fit_for(r, 63)['slope'] * r.rating, axis=1)
D['exp_nv_21'] = D.apply(lambda r: fit_for(r, 21)['intercept'] + fit_for(r, 21)['slope'] * r.rating, axis=1)
D['resid_nv_63'] = D['nv_63'] - D['exp_nv_63']; D['resid_nv_21'] = D['nv_21'] - D['exp_nv_21']
sd63 = float(D.loc[CAT.index, 'resid_nv_63'].std()); sd21 = float(D.loc[CAT.index, 'resid_nv_21'].std())
D['window'] = np.where((D['complete_63'] == True) & D['nv_63'].notna(), '3m', np.where(D['nv_21'].notna(), '1m', 'n/a'))
D['z'] = np.where(D['window'] == '3m', D['resid_nv_63'] / sd63, np.where(D['window'] == '1m', D['resid_nv_21'] / sd21, np.nan))
D['exp_ret'] = np.where(D['window'] == '3m', D['exp_nv_63'] * D['vol63'], D['exp_nv_21'] * D['vol63'] * math.sqrt(21 / 63))
D['act_ret'] = np.where(D['window'] == '3m', D['sec_63_attr'], D['sec_21_attr'])

def verdict(r):
    if pd.isna(r.z): return 'n/a'
    if abs(r.z) < 0.75: return 'justified'
    if r.rating >= 10: return 'overbought' if r.z > 0 else 'underbought'
    if r.rating <= -10: return 'undersold' if r.z > 0 else 'oversold'
    return 'overbought (no fundamental driver)' if r.z > 0 else 'oversold (no fundamental driver)'
D['verdict'] = D.apply(verdict, axis=1)
D['confidence'] = D.apply(lambda r: 'high' if (r.overlap == 0 and not r.episode and r.window == '3m') else 'medium' if (r.overlap <= 1 and r.window == '3m') else 'low', axis=1)

# ---------------------------------------------------------------- 6. capital-moved gauge (positive = capital arrived beyond what fundamentals explain)
CAT = D[~D.episode]
flow_sd_all = float(CAT['flow_z'].std()) if CAT['flow_z'].notna().sum() > 50 else 1.0
hf_sd = float(CAT['hf_chg_pp'].std()) if CAT['hf_chg_pp'].notna().sum() > 50 else 1.0
pre_sd = float(CAT['nv_pre'].std()) if CAT['nv_pre'].notna().sum() > 50 else 1.0
def gauge(r):
    parts = [(0.5, r.z), (0.2, r.flow_z / flow_sd_all if not pd.isna(r.flow_z) else np.nan), (0.2, r.hf_chg_pp / hf_sd if not pd.isna(r.hf_chg_pp) else np.nan), (0.1, r.nv_pre / pre_sd if not pd.isna(r.nv_pre) else np.nan)]
    parts = [(w, v) for w, v in parts if not pd.isna(v)]
    if not parts: return np.nan
    return float(sum(w * max(-3, min(3, v)) for w, v in parts) / sum(w for w, _ in parts))
D['capital_moved'] = D.apply(gauge, axis=1)
CAT = D[~D.episode]

# ---------------------------------------------------------------- 5. data-chosen stance rules (clean catalysts, forward drift months 4-12)
H = CAT[(CAT['complete_252'] == True) & CAT['resid_nv_63'].notna() & CAT['sec_252'].notna()].copy()
H['drift_4_12'] = (1 + H['sec_252']) / (1 + H['sec_63']) - 1
H['z63'] = H['resid_nv_63'] / sd63
H['rsign'] = np.where(H['rating'] >= 15, 'positive', np.where(H['rating'] <= -15, 'negative', 'neutral'))
H['rclass'] = np.where(H['z63'] <= -0.75, 'under-reacted', np.where(H['z63'] >= 0.75, 'over-reacted', 'in line'))
rules = []
for (rs, rc), g in H.groupby(['rsign', 'rclass']):
    rules.append({'rating_sign': rs, 'reaction': rc, 'n': int(len(g)), 'mean_drift': float(g['drift_4_12'].mean()), 'median_drift': float(g['drift_4_12'].median()),
                  'hit_rate': float((g['drift_4_12'] > 0).mean()), 'mean_12m_total': float(g['sec_252'].mean())})
for r in rules:
    r['decision'] = 'Long' if (r['n'] >= 15 and r['mean_drift'] >= 0.03 and r['hit_rate'] >= 0.55) else 'Short' if (r['n'] >= 15 and r['mean_drift'] <= -0.03 and r['hit_rate'] <= 0.45) else 'no edge'
rule_map = {(r['rating_sign'], r['reaction']): r for r in rules}
bt_quint = []
for k, g in H.groupby(pd.qcut(H['z63'], 5, labels=['most under-reacted', 'under', 'middle', 'over', 'most over-reacted']), observed=True):
    bt_quint.append({'group': str(k), 'n': int(len(g)), 'mean_drift': float(g['drift_4_12'].mean()), 'median_drift': float(g['drift_4_12'].median()), 'hit_rate_reversal': float((np.sign(g['drift_4_12']) == -np.sign(g['z63'])).mean())})
strong = H[H['rating'].abs() >= 15]
backtest = {
    'n': int(len(H)), 'corr_resid_drift_4_12': float(np.corrcoef(H['z63'], H['drift_4_12'])[0, 1]),
    'corr_resid_drift_4_6': float(np.corrcoef(H['z63'], ((1 + H['sec_126']) / (1 + H['sec_63']) - 1).fillna(0))[0, 1]),
    'fundamental_signal': {'n': int(len(strong)), 'hit_rate_12m': float((np.sign(strong['rating']) == np.sign(strong['sec_252'])).mean()),
                           'mean_12m_positive': float(strong[strong.rating > 0]['sec_252'].mean()), 'mean_12m_negative': float(strong[strong.rating < 0]['sec_252'].mean()),
                           'by_rating_bucket': [{'bucket': str(k), 'n': int(len(g)), 'mean_12m': float(g['sec_252'].mean()), 'median_12m': float(g['sec_252'].median())} for k, g in H.groupby(pd.cut(H['rating'], [-101, -40, -15, 15, 40, 101]), observed=True)]},
    'by_quintile': bt_quint, 'rules': rules,
    'by_group': [{'group': grp, 'n': int(len(g)), 'corr_resid_drift': float(np.corrcoef(g['z63'], g['drift_4_12'])[0, 1]) if len(g) > 10 else None, 'hit_rate_12m': float((np.sign(g[g.rating.abs() >= 15]['rating']) == np.sign(g[g.rating.abs() >= 15]['sec_252'])).mean()) if (g.rating.abs() >= 15).sum() > 10 else None} for grp, g in H.groupby('group')],
    'note': 'Clean catalysts only (episodes excluded, overlapping moves split by rating). Drift = sector-relative return from month 4 to month 12 after the event.'}
BL = CAT[CAT['blind']]; HS = CAT[~CAT['blind']]
def quick(g, n):
    g = g[(g[f'complete_{n}'] == True) & g[f'nv_{n}'].notna() & (g['rating'].abs() >= 15)]
    if len(g) < 5: return {'n': int(len(g))}
    return {'n': int(len(g)), 'corr_rating_vs_move': float(np.corrcoef(g['rating'], g[f'nv_{n}'])[0, 1]), 'hit_rate': float((np.sign(g['rating']) == np.sign(g[f'nv_{n}'])).mean())}
backtest['hindsight'] = {'blind_from': BLIND_FROM, 'blind_21': quick(BL, 21), 'blind_63': quick(BL, 63), 'hindsight_21': quick(HS, 21), 'hindsight_63': quick(HS, 63),
                         'note': 'Events dated on or after the blind date were rated without knowing what the stock did; earlier events were rated by an analyst who knew the history. Compare the two to gauge hindsight inflation.'}

# ---------------------------------------------------------------- 7. stances per bucket with the evidence gate
recent = CAT[CAT['date_ts'] >= asof - pd.Timedelta(days=RECENT_DAYS)].copy()
recent['age'] = (asof - recent['date_ts']).dt.days
recent['rw'] = np.exp(-np.log(2) * recent['age'] / HALF_LIFE_DAYS) * recent['weight']
def confirm(h, fund):
    sig = []
    if h.get('rs_z3y') is not None and np.sign(h['rs_z3y']) == np.sign(fund) and abs(h['rs_z3y']) >= 0.5: sig.append(f"relative strength z {h['rs_z3y']:+.1f}")
    fl = (h.get('flows') or {}).get('flow_3m_z')
    if fl is not None and np.sign(fl) == np.sign(fund) and abs(fl) >= 0.5: sig.append(f"ETF flow z {fl:+.1f}")
    hf = (h.get('f13') or {}).get('hf_share_chg_qoq')
    if hf is not None and np.sign(hf) == np.sign(fund) and abs(hf) >= 0.05: sig.append(f"HF share {hf:+.2f}pp q/q")
    return sig
buckets = []
for bid, g in recent.groupby('bucket'):
    gg = g[g['z'].notna()]
    fund = float((g['rating'] * g['rw']).sum() / g['rw'].sum())
    zsum = float((gg['z'] * gg['rw']).sum() / gg['rw'].sum()) if not gg.empty else 0.0
    cm = g[g['capital_moved'].notna()]; moved = float((cm['capital_moved'] * cm['rw']).sum() / cm['rw'].sum()) if not cm.empty else 0.0
    unexpl = float((gg['act_ret'] - gg['exp_ret']).mul(gg['rw']).sum() / gg['rw'].sum()) if not gg.empty else 0.0
    h = heat.get(bid, {}); hs = h.get('heat'); pct = h.get('weight_pctile')
    rs = 'positive' if fund >= 15 else 'negative' if fund <= -15 else 'neutral'
    rc = 'under-reacted' if moved <= -0.75 else 'over-reacted' if moved >= 0.75 else 'in line'
    rule = rule_map.get((rs, rc), {})
    conf_sig = confirm(h, fund) if rs != 'neutral' else []
    n_cat = int(len(g)); gate_ok = n_cat >= 2 or (n_cat == 1 and len(conf_sig) > 0)
    decision = rule.get('decision', 'no edge')
    if rs == 'neutral': stance, setup = 'Neutral', 'no strong fundamental signal in the last 18 months'
    elif not gate_ok: stance, setup = 'Watch', f"one recent catalyst and no confirming tracker signal yet (fundamentals {rs}, capital {rc})"
    elif decision == 'Long': stance, setup = 'Long', f"fundamentals {rs}, capital {rc}: historically this cell drifted {rule['mean_drift']*100:+.1f}% vs sector over months 4-12 (n={rule['n']}, hit {rule['hit_rate']*100:.0f}%)"
    elif decision == 'Short': stance, setup = 'Short', f"fundamentals {rs}, capital {rc}: historically this cell drifted {rule['mean_drift']*100:+.1f}% vs sector over months 4-12 (n={rule['n']}, hit {rule['hit_rate']*100:.0f}%)"
    else: stance, setup = ('Lean long' if rs == 'positive' else 'Lean short'), f"fundamentals {rs}, capital {rc}: the backtest shows no reliable edge for this combination (n={rule.get('n', 0)}, mean drift {rule.get('mean_drift', 0)*100:+.1f}%), so this is a fundamentals view, not a tested setup"
    if stance in ('Long', 'Lean long') and hs is not None and hs >= 60: setup += '; note the bucket is already Hot on the tracker (crowding risk)'
    if stance in ('Short', 'Lean short') and hs is not None and hs <= -60: setup += '; note the bucket is already Cold on the tracker (late)'
    buckets.append({'id': bid, 'name': names.get(bid, bid), 'sector': g['sector'].iloc[0], 'sector_name': sector_name.get(g['sector'].iloc[0], g['sector'].iloc[0]), 'level': g['level'].iloc[0], 'group': g['group'].iloc[0],
                    'n_events': n_cat, 'n_measured': int(len(gg)), 'fundamental': fund, 'z': zsum, 'capital_moved': moved, 'unexplained': unexpl, 'rating_sign': rs, 'reaction_class': rc,
                    'stance': stance, 'setup': setup, 'rule': {k: rule.get(k) for k in ('n', 'mean_drift', 'hit_rate', 'decision')} if rule else None, 'confirming': conf_sig, 'gate_ok': gate_ok,
                    'confidence': 'high' if (n_cat >= 3 and (gg['confidence'] == 'high').mean() >= 0.5) else 'medium' if n_cat >= 2 else 'low',
                    'heat': hs, 'label': h.get('label'), 'weight_pctile': pct, 'rel_3m': h.get('rel_3m'), 'rel_12m': h.get('rel_12m'),
                    'events': [{'event_id': r.event_id, 'date': r.date, 'title': r.title, 'rating': r.rating, 'actual': None if pd.isna(r.act_ret) else float(r.act_ret), 'expected': None if pd.isna(r.exp_ret) else float(r.exp_ret), 'z': None if pd.isna(r.z) else float(r.z), 'flow_z': None if pd.isna(r.flow_z) else float(r.flow_z), 'hf_chg_pp': None if pd.isna(r.hf_chg_pp) else float(r.hf_chg_pp), 'pre_63': None if pd.isna(r.pre_63) else float(r.pre_63), 'capital_moved': None if pd.isna(r.capital_moved) else float(r.capital_moved), 'verdict': r.verdict, 'window': r.window, 'confidence': r.confidence, 'attr': float(r.attr)} for r in g.sort_values('date', ascending=False).itertuples()]})
order = {'Long': 0, 'Lean long': 1, 'Watch': 2, 'Neutral': 3, 'Lean short': 4, 'Short': 5}
buckets.sort(key=lambda b: (order.get(b['stance'], 3), -b['fundamental']))

# ---------------------------------------------------------------- outputs
def f(v): return None if (v is None or (isinstance(v, float) and math.isnan(v))) else float(v)
ev_out = []
for r in D.sort_values('date', ascending=False).itertuples():
    e = events.get(r.event_id, {})
    ev_out.append({'event_id': r.event_id, 'date': r.date, 'sector': r.sector, 'sector_name': sector_name.get(r.sector, r.sector), 'bucket': r.bucket, 'bucket_name': names.get(r.bucket, r.bucket), 'level': r.level, 'group': r.group,
                   'title': r.title, 'direction': r.direction, 'magnitude': r.magnitude, 'driver_type': r.driver_type, 'episode': bool(r.episode), 'blind': bool(r.blind), 'why': r.why,
                   'rating': r.rating, 'components': {'e': r.c_e, 'p': r.c_p, 'b': r.c_b, 'c': r.c_c}, 'attr': float(r.attr), 'overlap': int(r.overlap),
                   'pre_63': f(r.pre_63), 'sec_21': f(r.sec_21), 'sec_63': f(r.sec_63), 'sec_126': f(r.sec_126), 'sec_252': f(r.sec_252), 'sec_504': f(r.sec_504), 'rel_63': f(r.rel_63), 'rel_252': f(r.rel_252),
                   'flow_63': f(r.flow_63), 'flow_z': f(r.flow_z), 'hf_chg_pp': f(r.hf_chg_pp), 'vol63': f(r.vol63),
                   'expected': f(r.exp_ret), 'actual': f(r.act_ret), 'z': f(r.z), 'capital_moved': f(r.capital_moved), 'window': r.window, 'verdict': r.verdict, 'confidence': r.confidence,
                   'summary': e.get('summary'), 'evidence': e.get('evidence'), 'sources': (e.get('sources') or [])[:2]})
cell_out = [{'sign': k[0], 'group': k[1], 'window': k[2], **v} for k, v in cells.items()]
out = {'asof': snap['asof'], 'generated': dt.datetime.now().strftime('%Y-%m-%d %H:%M'), 'pooled': {str(k): v for k, v in pooled.items()}, 'cells': cell_out, 'resid_sd': {'63': sd63, '21': sd21},
       'backtest': backtest, 'buckets': buckets, 'events': ev_out, 'rubric': rubric, 'n_rated': int(D.event_id.nunique()), 'n_unrated': len(missing),
       'params': {'half_life_days': HALF_LIFE_DAYS, 'recent_days': RECENT_DAYS, 'z_threshold': 0.75, 'cluster_days': CLUSTER_DAYS, 'shrink_k': SHRINK_K, 'blind_from': BLIND_FROM, 'gauge_weights': {'price_residual': 0.5, 'etf_flow': 0.2, 'hf_13f_change': 0.2, 'pre_event_move': 0.1}}}
def clean(o):
    if isinstance(o, float): return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict): return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, list): return [clean(v) for v in o]
    if isinstance(o, np.floating): return clean(float(o))
    if isinstance(o, np.integer): return int(o)
    if isinstance(o, np.bool_): return bool(o)
    return o
with open(os.path.join(SITE_DATA, 'beta.js'), 'w', encoding='utf-8') as fh:
    fh.write('window.CRT_BETA = '); json.dump(clean(out), fh, ensure_ascii=False, separators=(',', ':')); fh.write(';')
D.drop(columns=['date_ts']).to_csv(os.path.join(PROC, 'event_scored.csv'), index=False)

print(f"scored {len(D)} rows ({D.event_id.nunique()} events); pooled 63d: slope {pooled[63]['slope']:.4f} vol-units/pt corr {pooled[63]['corr']:.2f} n {pooled[63]['n']}")
print('rules (rating sign x reaction -> drift 4-12):')
for r in sorted(rules, key=lambda x: -x['mean_drift']): print(f"   {r['rating_sign']:9s} {r['reaction']:14s} n={r['n']:3d} mean {r['mean_drift']*100:+6.1f}% med {r['median_drift']*100:+6.1f}% hit {r['hit_rate']*100:3.0f}% -> {r['decision']}")
print('backtest:', {k: round(v, 3) for k, v in backtest.items() if isinstance(v, float)}, '| fundamental:', {k: round(v, 3) for k, v in backtest['fundamental_signal'].items() if isinstance(v, float)})
print('hindsight:', backtest['hindsight']['blind_21'], backtest['hindsight']['blind_63'], '| vs', backtest['hindsight']['hindsight_63'])
print('stances:', pd.Series([b['stance'] for b in buckets]).value_counts().to_dict())
for b in [x for x in buckets if x['stance'] in ('Long', 'Short', 'Lean long', 'Lean short')][:14]: print(f"  {b['stance']:10s} fund={b['fundamental']:+.0f} moved={b['capital_moved']:+.2f} n={b['n_events']} {b['name']} | {b['setup'][:80]}")
