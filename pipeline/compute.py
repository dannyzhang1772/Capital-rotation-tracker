"""Core computation: bucket indices, market-cap weights, relative strength, factor betas, flows, 13F, heat scores.
Reads data/raw/* and research/*; writes site/data/*.js (window.CRT_* globals so the site works from file://).
"""
import os, json, glob, math, datetime as dt
import numpy as np, pandas as pd
from common import load_taxonomy, all_sectors, RAW, PROC, SITE_DATA, ROOT, clean_prices

T = load_taxonomy(); B = T['meta']['benchmarks']
SUB_START = pd.Timestamp(T['meta']['history_tiers']['subindustry_level'])
SEC_START = pd.Timestamp(T['meta']['history_tiers']['sector_level'])

# ---------------------------------------------------------------- load
P = pd.read_parquet(os.path.join(RAW, 'prices.parquet')).sort_index()
P = P.loc[P.index >= '1995-01-01']
P = clean_prices(P)
R = P.pct_change(fill_method=None)
R = R.where(R.abs() < 1.0)  # drop corrupt > +/-100% daily prints
SPY = P[B['market']]

# shares history -> daily panel, forward/back filled, scaled to Yahoo implied current shares when multi-class
sh_hist = pd.read_csv(os.path.join(RAW, 'shares_history.csv'), parse_dates=['date']) if os.path.exists(os.path.join(RAW, 'shares_history.csv')) else pd.DataFrame(columns=['ticker', 'date', 'shares', 'source'])
sh_cur = pd.read_csv(os.path.join(RAW, 'shares_current.csv')) if os.path.exists(os.path.join(RAW, 'shares_current.csv')) else pd.DataFrame(columns=['ticker', 'shares_implied'])
cur = sh_cur.set_index('ticker')['shares_implied'].to_dict()
SH_cols = {}
first_real = {}
for tk, g in sh_hist.groupby('ticker'):
    if tk not in P.columns: continue
    s = g.sort_values('date').set_index('date')['shares']
    s = s[~s.index.duplicated(keep='last')]
    s = s.reindex(P.index.union(s.index)).ffill().reindex(P.index)
    first_real[tk] = g['date'].min()
    s = s.bfill()  # before first observation: hold first known count (approximation)
    c = cur.get(tk)
    if c and c > 0 and s.iloc[-1] > 0:
        ratio = c / s.iloc[-1]
        if abs(ratio - 1) > 0.25: s = s * ratio  # multi-class or stale series: rescale to implied total
    SH_cols[tk] = s
for tk, c in cur.items():
    if tk in P.columns and tk not in SH_cols and c and c > 0:
        SH_cols[tk] = pd.Series(float(c), index=P.index)  # constant shares fallback
SH = pd.DataFrame(SH_cols, index=P.index)
MC = (P[SH.columns] * SH)  # market cap panel (USD)

# ---------------------------------------------------------------- helpers
def capw_index(members, start=None):
    """Chain-linked cap-weighted daily return index (uses prior-day caps) and equal-weighted index."""
    m = [x for x in members if x in R.columns]
    if not m: return None, None, None
    r = R[m]; w = MC[[x for x in m if x in MC.columns]].shift(1)
    w = w.reindex(columns=m)
    valid = r.notna() & w.notna() & (w > 0)
    w = w.where(valid); r = r.where(valid)
    wsum = w.sum(axis=1)
    cw = (w.mul(r).sum(axis=1) / wsum.replace(0, np.nan))
    ew = r.mean(axis=1)
    n = valid.sum(axis=1)
    cw = cw.where(n > 0); ew = ew.where(n > 0)
    return cw, ew, n

def to_index(ret, base_date):
    ret = ret.copy(); ret = ret[ret.index >= base_date]
    ret = ret.fillna(0.0)
    idx = (1 + ret).cumprod() * 100.0
    return idx

def z(series, window):
    mu = series.rolling(window, min_periods=int(window * 0.6)).mean(); sd = series.rolling(window, min_periods=int(window * 0.6)).std()
    return (series - mu) / sd

def pct_rank(series):
    s = series.dropna()
    if len(s) < 20: return None
    return float((s < s.iloc[-1]).mean())

def last(x):
    try:
        v = x.dropna().iloc[-1]; return None if (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else float(v)
    except Exception: return None

def ret_over(idx, days):
    s = idx.dropna()
    if len(s) <= days: return None
    return float(s.iloc[-1] / s.iloc[-1 - days] - 1)

def ytd(idx):
    s = idx.dropna()
    if s.empty: return None
    y = s.index[-1].year; prev = s[s.index < pd.Timestamp(y, 1, 1)]
    if prev.empty: return None
    return float(s.iloc[-1] / prev.iloc[-1] - 1)

# ---------------------------------------------------------------- factor spreads
def tot(t): return R[t] if t in R.columns else pd.Series(np.nan, index=R.index)
tnx = P['^TNX'] if '^TNX' in P.columns else pd.Series(np.nan, index=P.index)
FACT = pd.DataFrame({
    'growth': tot(B['growth']) - tot(B['value']),
    'size': tot(B['small_cap']) - tot(B['market']),
    'momentum': tot(B['momentum']) - tot(B['market']),
    'quality': tot(B['quality']) - tot(B['market']),
    'low_vol': tot(B['low_vol']) - tot(B['market']),
    'rates': tnx.diff() / 10.0,  # ^TNX is yield*10 -> daily change in percentage points
    'oil': tot(B['oil']),
    'dollar': tot(B['dollar']),
})

def factor_betas(ret, window=252):
    """OLS of bucket excess return vs SPY on factor spreads over the trailing window. Returns dict of betas (per 1% factor move)."""
    y = (ret - R[B['market']]).dropna()
    X = FACT.reindex(y.index)
    df = pd.concat([y.rename('y'), X], axis=1).dropna().tail(window)
    if len(df) < 120: return {}
    cols = [c for c in FACT.columns if df[c].abs().sum() > 0]
    A = np.column_stack([np.ones(len(df))] + [df[c].values for c in cols])
    beta, *_ = np.linalg.lstsq(A, df['y'].values, rcond=None)
    out = {}
    for c, b in zip(cols, beta[1:]):
        out[c] = round(float(b), 3)
    # rates beta is per 1pp daily change; scale to per 10bp for readability
    if 'rates' in out: out['rates'] = round(out['rates'] * 0.1, 4)
    return out

# ---------------------------------------------------------------- optional datasets
flows = None
fp = os.path.join(RAW, 'etf_flows.csv')
if os.path.exists(fp):
    flows = pd.read_csv(fp, parse_dates=['date'])
    flows = flows.dropna(subset=['flow_usd'])
f13 = None
fp = os.path.join(PROC, '13f_by_ticker.csv')
if os.path.exists(fp):
    f13 = pd.read_csv(fp, parse_dates=['period'])
    f13 = f13[f13.period >= '2013-06-30']  # the SEC's first (2013q2) data set is a tiny partial sample
    f13tot = pd.read_csv(os.path.join(PROC, '13f_totals.csv'), parse_dates=['period']) if os.path.exists(os.path.join(PROC, '13f_totals.csv')) else None
    if f13tot is not None: f13tot = f13tot[f13tot.period >= '2013-06-30']
macro = pd.read_csv(os.path.join(RAW, 'macro.csv'), index_col=0, parse_dates=True) if os.path.exists(os.path.join(RAW, 'macro.csv')) else None
if macro is not None: macro.index.name = 'date'

def etf_flow_block(etf):
    if flows is None or etf is None: return None
    g = flows[flows.ticker == etf].set_index('date').sort_index()
    if g.empty: return None
    m = g['flow_usd'].resample('ME').sum(); aum = g['aum_usd'].resample('ME').last()
    pct = (m / aum.shift(1)).replace([np.inf, -np.inf], np.nan)
    f3 = m.rolling(3).sum(); f3z = z(f3, 60)
    return {
        'etf': etf, 'first': str(g.index.min().date()), 'last': str(g.index.max().date()),
        'aum': last(aum), 'flow_1m': last(m), 'flow_3m': last(f3), 'flow_12m': last(m.rolling(12).sum()),
        'flow_3m_pct_aum': last((f3 / aum.shift(3))), 'flow_3m_z': last(f3z),
        'monthly': {'dates': [d.strftime('%Y-%m') for d in m.index], 'flow': [None if pd.isna(v) else round(float(v) / 1e6, 1) for v in m.values],
                    'aum': [None if pd.isna(v) else round(float(v) / 1e6, 1) for v in aum.values]},
    }

def f13_block(members):
    if f13 is None: return None
    g = f13[f13.ticker.isin(members)].groupby('period')[['total_value_usd', 'hf_value_usd', 'n_holders', 'hf_n_holders']].sum().sort_index()
    if g.empty: return None
    if f13tot is not None:
        tt = f13tot.set_index('period')['total_value_usd'].reindex(g.index)
        g['share_of_13f'] = g['total_value_usd'] / tt
        if 'hf_value_usd' in f13tot.columns:
            g['hf_share'] = g['hf_value_usd'] / f13tot.set_index('period')['hf_value_usd'].reindex(g.index)
    out = {'periods': [d.strftime('%Y-%m-%d') for d in g.index],
           'total_value': [round(float(v) / 1e9, 2) for v in g['total_value_usd']],
           'hf_value': [round(float(v) / 1e9, 2) for v in g['hf_value_usd']],
           'hf_holders': [int(v) for v in g['hf_n_holders']]}
    if 'share_of_13f' in g: out['share_of_13f'] = [None if pd.isna(v) else round(float(v) * 100, 3) for v in g['share_of_13f']]
    if 'hf_share' in g:
        out['hf_share'] = [None if pd.isna(v) else round(float(v) * 100, 3) for v in g['hf_share']]
        hs = g['hf_share'].dropna()
        if len(hs) >= 8:
            out['hf_share_z'] = float((hs.iloc[-1] - hs.tail(20).mean()) / (hs.tail(20).std() or np.nan)) if hs.tail(20).std() else None
            out['hf_share_chg_qoq'] = float(hs.iloc[-1] - hs.iloc[-2])
    return out

# ---------------------------------------------------------------- build buckets
SPY_IDX = to_index(R[B['market']], SEC_START)
month_ends = P.resample('ME').last().index
month_ends = month_ends[month_ends >= SEC_START]

bucket_caps = {}; bucket_cw = {}; bucket_ew = {}; bucket_n = {}; bucket_info = {}
for sec in all_sectors(T):
    for si in sec['subindustries']:
        cw, ew, n = capw_index(si['basket'])
        bid = si['id']
        bucket_cw[bid] = cw; bucket_ew[bid] = ew; bucket_n[bid] = n
        mem = [x for x in si['basket'] if x in MC.columns]
        bucket_caps[bid] = MC[mem].sum(axis=1, min_count=1) if mem else pd.Series(np.nan, index=P.index)
        bucket_info[bid] = (sec, si)

UNIV = pd.DataFrame(bucket_caps).sum(axis=1, min_count=1)
sector_caps = {sec['id']: pd.DataFrame({si['id']: bucket_caps[si['id']] for si in sec['subindustries']}).sum(axis=1, min_count=1) for sec in all_sectors(T)}

def series_for(ret_series, etf, start, etf_primary=False):
    """Choose the primary return series: ETF if etf_primary, else basket cap-weighted; ETF where basket missing."""
    if etf_primary and etf in R.columns:
        return R[etf], 'etf'
    if ret_series is not None and ret_series.notna().sum() > 250:
        return ret_series, 'basket'
    if etf and etf in R.columns:
        return R[etf], 'etf'
    return None, None

def heat(rs_z, w_z, flow_z, hf_z):
    parts = [(0.4, rs_z), (0.3, w_z), (0.2, flow_z), (0.1, hf_z)]
    parts = [(w, v) for w, v in parts if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not parts: return None
    tw = sum(w for w, _ in parts); zc = sum(w * max(-3, min(3, v)) for w, v in parts) / tw
    return round(float(math.tanh(zc / 1.5) * 100), 1)

def label(h):
    if h is None: return 'n/a'
    return 'Hot' if h > 40 else 'Warm' if h > 10 else 'Neutral' if h > -10 else 'Cool' if h > -40 else 'Cold'

def monthly(series, start=SEC_START, digits=4):
    s = series.reindex(P.index).ffill(limit=5).resample('ME').last().reindex(month_ends)
    return [None if pd.isna(v) else round(float(v), digits) for v in s.values]

DAILY = {'SPY': R[B['market']]}
snapshot = {'asof': str(P.index.max().date()), 'generated': dt.datetime.now().strftime('%Y-%m-%d %H:%M'), 'buckets': [], 'sectors': [], 'themes': []}
history = {'dates': [d.strftime('%Y-%m-%d') for d in month_ends], 'buckets': {}, 'sectors': {}, 'themes': {}, 'spy': monthly(SPY_IDX, digits=2)}
if macro is not None:
    history['macro'] = {k: monthly(macro[k], digits=2) for k in ['fed_funds', 'ust10y', 'real10y', 'vix', 'wti', 'hy_oas', 'mortgage30y'] if k in macro.columns}

for bid, (sec, si) in bucket_info.items():
    etf = si.get('etf'); ref = si.get('reference_etf')
    ret, src = series_for(bucket_cw[bid], etf, SUB_START, si.get('etf_primary', False))
    if ret is None: continue
    DAILY[bid] = ret
    if etf and etf in R.columns: DAILY['etf:' + bid] = R[etf]
    idx = to_index(ret, SUB_START)
    rs = (idx / SPY_IDX.reindex(idx.index)); rs = rs / rs.dropna().iloc[0] * 100
    lrs = np.log(rs)
    rs_z3 = z(lrs, 756); rs_z1 = z(lrs, 252)
    w = (bucket_caps[bid] / UNIV); w = w[w.index >= SUB_START]
    w_z = z(w, 2520)
    etf_idx = to_index(R[etf], SUB_START) if etf and etf in R.columns else None
    etf_rs = None
    if etf_idx is not None:
        etf_rs = etf_idx / SPY_IDX.reindex(etf_idx.index); etf_rs = etf_rs / etf_rs.dropna().iloc[0] * 100
    fl = etf_flow_block(etf) if etf else None  # a reference ETF is for charts only; its flows are not this bucket's
    f13b = f13_block(si['basket'])
    betas = factor_betas(ret)
    rsz = last(rs_z3); wz = last(w_z)
    flz = fl['flow_3m_z'] if fl else None
    hfz = f13b.get('hf_share_z') if f13b else None
    h = heat(rsz, wz, flz, hfz)
    rec = {
        'id': bid, 'sector': sec['id'], 'sector_name': sec['name'], 'name': si['name'], 'deep_dive': sec.get('deep_dive', False),
        'etf': etf, 'reference_etf': ref, 'series_source': src, 'n_members_now': int(last(bucket_n[bid]) or 0), 'basket': [x for x in si['basket'] if x in P.columns],
        'missing_tickers': [x for x in si['basket'] if x not in P.columns],
        'cap_usd_bn': round(last(bucket_caps[bid]) / 1e9, 1) if last(bucket_caps[bid]) else None,
        'weight': last(w), 'weight_z10y': wz, 'weight_pctile': pct_rank(w), 'weight_mean10y': last(w.rolling(2520, min_periods=1500).mean()),
        'weight_1y_ago': (lambda s: float(s.iloc[-253]) if len(s) > 253 else None)(w.dropna()),
        'rel_1m': ret_over(rs, 21), 'rel_3m': ret_over(rs, 63), 'rel_6m': ret_over(rs, 126), 'rel_12m': ret_over(rs, 252), 'rel_ytd': ytd(rs),
        'abs_1m': ret_over(idx, 21), 'abs_3m': ret_over(idx, 63), 'abs_12m': ret_over(idx, 252), 'abs_ytd': ytd(idx),
        'rs_z3y': rsz, 'rs_z1y': last(rs_z1), 'rs_dd_3y': (lambda s: float(s.iloc[-1] / s.tail(756).max() - 1) if len(s) > 100 else None)(rs.dropna()),
        'momentum_12_1': (lambda s: float(s.iloc[-22] / s.iloc[-253] - 1) if len(s) > 253 else None)(idx.dropna()),
        'factors_apriori': si.get('factors', {}), 'factors_empirical': betas, 'drivers': si.get('drivers', []),
        'flows': {k: v for k, v in fl.items() if k != 'monthly'} if fl else None,
        'f13': {k: v for k, v in f13b.items() if k not in ('periods', 'total_value', 'hf_value', 'hf_holders', 'share_of_13f', 'hf_share')} | {'last_period': f13b['periods'][-1], 'hf_value_bn': f13b['hf_value'][-1], 'total_value_bn': f13b['total_value'][-1], 'hf_share_pct': (f13b.get('hf_share') or [None])[-1], 'share_of_13f_pct': (f13b.get('share_of_13f') or [None])[-1]} if f13b else None,
        'heat': h, 'label': label(h), 'history_note': si.get('history_note'), 'etf_start': si.get('etf_start'),
        'shares_note': 'Share counts before late 2015 are held at the first observed count (Yahoo history limit); weights before then are approximate.',
    }
    snapshot['buckets'].append(rec)
    hb = {'w': monthly(w), 'rs': monthly(rs, digits=2), 'idx': monthly(idx, digits=2), 'rs_z': monthly(rs_z3, digits=2), 'n': monthly(bucket_n[bid], digits=0)}
    if etf_rs is not None: hb['etf_rs'] = monthly(etf_rs, digits=2)
    if fl: hb['flow'] = fl['monthly']
    if f13b: hb['f13'] = f13b
    history['buckets'][bid] = hb

# sectors
for sec in all_sectors(T):
    sid = sec['id']; etf = sec['sector_etf']; hist_etf = sec.get('sector_history_etf', etf)
    if hist_etf not in R.columns: hist_etf = etf
    r_etf = R[hist_etf] if hist_etf in R.columns else None
    if etf in R.columns and hist_etf != etf:  # splice: history ETF until main ETF starts
        r_main = R[etf]; first = r_main.first_valid_index()
        r_etf = pd.concat([R[hist_etf][R[hist_etf].index < first], r_main[r_main.index >= first]])
    cw = pd.DataFrame({si['id']: bucket_cw[si['id']] for si in sec['subindustries'] if bucket_cw[si['id']] is not None})
    caps = pd.DataFrame({si['id']: bucket_caps[si['id']].shift(1) for si in sec['subindustries']}).reindex(columns=cw.columns)
    basket_ret = (cw * caps).sum(axis=1, min_count=1) / caps.where(cw.notna()).sum(axis=1, min_count=1)
    idx_etf = to_index(r_etf, SEC_START) if r_etf is not None else None
    DAILY['sector:' + sid] = r_etf if r_etf is not None else basket_ret
    DAILY['sectorbasket:' + sid] = basket_ret
    idx_b = to_index(basket_ret, SUB_START)
    rs_e = (idx_etf / SPY_IDX.reindex(idx_etf.index)) if idx_etf is not None else None
    if rs_e is not None: rs_e = rs_e / rs_e.dropna().iloc[0] * 100
    rs_b = idx_b / SPY_IDX.reindex(idx_b.index); rs_b = rs_b / rs_b.dropna().iloc[0] * 100
    w = sector_caps[sid] / UNIV; w = w[w.index >= SUB_START]
    rs_use = rs_e if rs_e is not None else rs_b
    rsz = last(z(np.log(rs_use), 756)); wz = last(z(w, 2520))
    fl = etf_flow_block(etf)
    members = [x for si in sec['subindustries'] for x in si['basket']]
    f13b = f13_block(members)
    h = heat(rsz, wz, fl['flow_3m_z'] if fl else None, f13b.get('hf_share_z') if f13b else None)
    snapshot['sectors'].append({
        'id': sid, 'name': sec['name'], 'deep_dive': sec.get('deep_dive', False), 'etf': etf, 'history_etf': hist_etf,
        'subindustries': [si['id'] for si in sec['subindustries']], 'cap_usd_bn': round(last(sector_caps[sid]) / 1e9, 1) if last(sector_caps[sid]) else None,
        'weight': last(w), 'weight_z10y': wz, 'weight_pctile': pct_rank(w), 'weight_mean10y': last(w.rolling(2520, min_periods=1500).mean()),
        'rel_1m': ret_over(rs_use, 21), 'rel_3m': ret_over(rs_use, 63), 'rel_6m': ret_over(rs_use, 126), 'rel_12m': ret_over(rs_use, 252), 'rel_ytd': ytd(rs_use),
        'abs_ytd': ytd(idx_etf if idx_etf is not None else idx_b), 'abs_12m': ret_over(idx_etf if idx_etf is not None else idx_b, 252),
        'rs_z3y': rsz, 'factors_empirical': factor_betas(r_etf if r_etf is not None else basket_ret),
        'flows': {k: v for k, v in fl.items() if k != 'monthly'} if fl else None,
        'f13': {'last_period': f13b['periods'][-1], 'hf_value_bn': f13b['hf_value'][-1], 'total_value_bn': f13b['total_value'][-1], 'hf_share_pct': (f13b.get('hf_share') or [None])[-1], 'share_of_13f_pct': (f13b.get('share_of_13f') or [None])[-1], 'hf_share_z': f13b.get('hf_share_z'), 'hf_share_chg_qoq': f13b.get('hf_share_chg_qoq')} if f13b else None,
        'heat': h, 'label': label(h), 'history_note': sec.get('history_note'),
    })
    hs = {'w': monthly(w), 'rs_etf': monthly(rs_e, digits=2) if rs_e is not None else None, 'rs_basket': monthly(rs_b, digits=2),
          'idx_etf': monthly(idx_etf, digits=2) if idx_etf is not None else None, 'rs_z': monthly(z(np.log(rs_use), 756), digits=2)}
    if fl: hs['flow'] = fl['monthly']
    if f13b: hs['f13'] = f13b
    history['sectors'][sid] = hs

# themes (overlays)
for th in T['themes']:
    cw, ew, n = capw_index(th['basket'])
    start = pd.Timestamp(th.get('theme_start', SUB_START))
    ret, src = series_for(cw, th.get('etf'), start)
    if ret is None: continue
    idx = to_index(ret, max(start, SUB_START)); rs = idx / SPY_IDX.reindex(idx.index); rs = rs / rs.dropna().iloc[0] * 100
    mem = [x for x in th['basket'] if x in MC.columns]
    cap = MC[mem].sum(axis=1, min_count=1); w = cap / UNIV
    fl = etf_flow_block(th.get('etf'))
    rsz = last(z(np.log(rs), 756)); wz = last(z(w, 1260))
    h = heat(rsz, wz, fl['flow_3m_z'] if fl else None, None)
    snapshot['themes'].append({'id': th['id'], 'name': th['name'], 'etf': th.get('etf'), 'basket': mem, 'theme_start': th.get('theme_start'), 'note': th.get('note'),
                               'cap_usd_bn': round(last(cap) / 1e9, 1) if last(cap) else None, 'weight': last(w), 'rel_1m': ret_over(rs, 21), 'rel_3m': ret_over(rs, 63), 'rel_12m': ret_over(rs, 252), 'rel_ytd': ytd(rs),
                               'abs_ytd': ytd(idx), 'rs_z3y': rsz, 'weight_z5y': wz, 'factors_empirical': factor_betas(ret), 'flows': {k: v for k, v in fl.items() if k != 'monthly'} if fl else None, 'heat': h, 'label': label(h)})
    history['themes'][th['id']] = {'w': monthly(w), 'rs': monthly(rs, digits=2), 'idx': monthly(idx, digits=2)}

# universe totals and data-quality notes
snapshot['universe'] = {'cap_usd_tn': round(last(UNIV) / 1e12, 2) if last(UNIV) else None, 'n_tickers_priced': int(P.notna().iloc[-1].sum()),
                        'n_with_shares': int(SH.notna().iloc[-1].sum()), 'first_shares_obs_median': str(pd.Series(first_real).median().date()) if first_real else None,
                        'flows_available': flows is not None, 'f13_available': f13 is not None, 'tiers': T['meta']['history_tiers']}
history['universe_cap'] = monthly(UNIV / 1e12, digits=3)

pd.DataFrame(DAILY).to_parquet(os.path.join(PROC, 'daily_returns.parquet'))
# events / regimes / narratives / blurbs
events = []
for fp in sorted(glob.glob(os.path.join(ROOT, 'research', 'events_*.json'))):
    try: events += json.load(open(fp, encoding='utf-8')).get('events', [])
    except Exception as e: print('bad events file', fp, e)
events.sort(key=lambda e: e.get('date', ''))
regimes = []
fp = os.path.join(ROOT, 'research', 'regimes.json')
if os.path.exists(fp):
    try: regimes = json.load(open(fp, encoding='utf-8')).get('regimes', [])
    except Exception as e: print('bad regimes', e)
narr = {}
for fp in sorted(glob.glob(os.path.join(ROOT, 'research', '*.md'))):
    narr[os.path.splitext(os.path.basename(fp))[0]] = open(fp, encoding='utf-8').read()
blurbs = {}
fp = os.path.join(ROOT, 'research', 'blurbs.json')
if os.path.exists(fp): blurbs = json.load(open(fp, encoding='utf-8'))
heat_now = {b['id']: b['heat'] for b in snapshot['buckets']} | {t['id']: t['heat'] for t in snapshot['themes']}
stale = []
for k, v in blurbs.items():
    if not isinstance(v, dict) or 'heat_at_write' not in v: continue
    h = heat_now.get(k)
    v['heat_now'] = h
    v['stale'] = bool(h is not None and abs(h - v['heat_at_write']) > 30)
    if v['stale']: stale.append((k, v['heat_at_write'], h))
if stale: print('STALE catalyst notes (heat moved > 30 since written):', stale)

def dump(name, var, obj):
    with open(os.path.join(SITE_DATA, name), 'w', encoding='utf-8') as f:
        f.write(f"window.{var} = "); json.dump(obj, f, ensure_ascii=False, separators=(',', ':'), allow_nan=False, default=lambda o: None); f.write(';')

def clean(o):
    if isinstance(o, float): return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict): return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list): return [clean(v) for v in o]
    return o

dump('snapshot.js', 'CRT_SNAPSHOT', clean(snapshot))
dump('history.js', 'CRT_HISTORY', clean(history))
dump('events.js', 'CRT_EVENTS', {'events': events, 'regimes': regimes})
dump('narratives.js', 'CRT_NARRATIVES', narr)
dump('blurbs.js', 'CRT_BLURBS', blurbs)
dump('taxonomy.js', 'CRT_TAXONOMY', T)
print('snapshot buckets:', len(snapshot['buckets']), 'sectors:', len(snapshot['sectors']), 'themes:', len(snapshot['themes']), 'events:', len(events), 'asof', snapshot['asof'])
print('universe cap $tn:', snapshot['universe']['cap_usd_tn'], '| priced:', snapshot['universe']['n_tickers_priced'], '| with shares:', snapshot['universe']['n_with_shares'])
for b in sorted(snapshot['buckets'], key=lambda x: -(x['heat'] or -999))[:8]:
    print(f"  {b['label']:8s} {b['heat']:>6} {b['sector']:22s} {b['name'][:40]:40s} w={b['weight'] and round(b['weight']*100,2)}% rel12m={b['rel_12m'] and round(b['rel_12m']*100,1)}%")
