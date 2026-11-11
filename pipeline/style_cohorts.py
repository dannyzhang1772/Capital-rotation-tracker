"""Style layer: momentum/growth names vs defensive/quality names INSIDE each sub-industry.

Each month-end, every member of a bucket is scored on one axis:
    style = z(12-1 month momentum) - z(defensiveness),  defensiveness = -z(252d volatility) - z(252d beta to SPY)
The top third of the bucket by style is the "momentum" cohort, the bottom third the "defensive" cohort, the rest core.
Cohorts are held for the following month; cap-weighted daily returns are chain-linked into a momentum index, a
defensive index and their spread (momentum minus defensive). The same classification across the whole universe gives
a market-level momentum-vs-defensive regime series. Ken French's daily factors (momentum, profitability, value,
market) are added as the 30-year external backdrop.

Also: the momentum cohort's share of bucket market cap, and (2013+) the hedge-fund 13F tilt toward the momentum
cohort, which is the flow measure. Output: site/data/style.js (window.CRT_STYLE) and data/processed/style_cohorts.csv.
"""
import os, io, json, math, zipfile, urllib.request, datetime as dt
import numpy as np, pandas as pd
from common import load_taxonomy, all_sectors, RAW, PROC, SITE_DATA, clean_prices

T = load_taxonomy()
MIN_MEMBERS = 8
LOOKBACK = 252
START = pd.Timestamp('1995-01-01')

P = pd.read_parquet(os.path.join(RAW, 'prices.parquet')).sort_index()
P = clean_prices(P.loc[P.index >= START])
R = P.pct_change(fill_method=None); R = R.where(R.abs() < 1.0)
SPY = R['SPY']

# shares -> caps (same recipe as compute.py)
sh = pd.read_csv(os.path.join(RAW, 'shares_history.csv'), parse_dates=['date'])
cur = pd.read_csv(os.path.join(RAW, 'shares_current.csv')).set_index('ticker')['shares_implied'].to_dict() if os.path.exists(os.path.join(RAW, 'shares_current.csv')) else {}
cols = {}
for tk, g in sh.groupby('ticker'):
    if tk not in P.columns: continue
    s = g.sort_values('date').set_index('date')['shares']; s = s[~s.index.duplicated(keep='last')]
    s = s.reindex(P.index.union(s.index)).ffill().reindex(P.index).bfill()
    c = cur.get(tk)
    if c and c > 0 and s.iloc[-1] > 0 and abs(c / s.iloc[-1] - 1) > 0.25: s = s * (c / s.iloc[-1])
    cols[tk] = s
SH = pd.DataFrame(cols, index=P.index); MC = P[SH.columns] * SH

names, sector_of, sector_name = {}, {}, {}
buckets = {}
for sec in all_sectors(T):
    sector_name[sec['id']] = sec['name']
    for si in sec['subindustries']:
        names[si['id']] = si['name']; sector_of[si['id']] = sec['id']
        mem = [x for x in si['basket'] if x in R.columns and x in MC.columns]
        if len(mem) >= MIN_MEMBERS: buckets[si['id']] = mem
all_members = sorted({x for m in buckets.values() for x in m})

# month-end scoring dates
month_ends = R.resample('ME').last().index
month_ends = month_ends[(month_ends >= START + pd.Timedelta(days=LOOKBACK * 1.5))]
pos = {d: R.index.searchsorted(d, side='right') - 1 for d in month_ends}

def z(x):
    x = x.astype(float); s = x.std()
    return (x - x.mean()) / s if s and s > 0 else x * 0

def scores_at(p, members):
    i0 = p - LOOKBACK
    if i0 < 21: return None
    r = R[members].iloc[i0:p + 1]; px = P[members]
    mom = px.iloc[p - 21] / px.iloc[i0] - 1
    vol = r.std()
    sp = SPY.iloc[i0:p + 1]
    beta = r.apply(lambda c: c.cov(sp) / sp.var() if c.notna().sum() > 150 else np.nan)
    alive = px.iloc[p - 5:p + 1].notna().any()
    ok = mom.notna() & vol.notna() & beta.notna() & (r.notna().sum() > 150) & alive & (px.iloc[p] >= 1) & (vol * np.sqrt(252) < 1.2)
    if ok.sum() < MIN_MEMBERS: return None
    m, v, b = mom[ok], vol[ok], beta[ok]
    style = z(m) - (z(-v) + z(-b)) / 2
    return pd.DataFrame({'mom': m, 'vol': v, 'beta': b, 'style': style})

def classify(df):
    n = len(df); k = max(2, n // 3)
    srt = df['style'].sort_values(ascending=False)
    return list(srt.index[:k]), list(srt.index[-k:])

def cohort_series(members_fn, label):
    """members_fn(p) -> (mom_list, def_list) at month-end position p; returns daily cohort returns."""
    mom_r = pd.Series(np.nan, index=R.index); def_r = pd.Series(np.nan, index=R.index); mshare = {}
    hist = {}
    for k, d in enumerate(month_ends[:-1]):
        p = pos[d]; nxt = pos[month_ends[k + 1]]
        res = members_fn(p)
        if res is None: continue
        mom, dfn = res
        w = MC.iloc[p]
        for grp, sr in ((mom, mom_r), (dfn, def_r)):
            ww = w[grp].dropna(); ww = ww[ww > 0]
            if ww.empty: continue
            rr = R[ww.index].iloc[p + 1:nxt + 1]
            sr.iloc[p + 1:nxt + 1] = (rr * (ww / ww.sum())).sum(axis=1, min_count=1).values
        tot = w[mom + dfn].sum(); mshare[d] = float(w[mom].sum() / tot) if tot else np.nan
        hist[d] = (mom, dfn)
    return mom_r, def_r, pd.Series(mshare), hist

def to_index(ret):
    ret = ret.dropna()
    if ret.empty: return ret
    return (1 + ret).cumprod() * 100

def monthly(s, digits=3):
    if s is None or len(s) == 0: return []
    m = s.resample('ME').last()
    return [None if pd.isna(v) else round(float(v), digits) for v in m.values]

def zroll(s, win):
    mu = s.rolling(win, min_periods=int(win * 0.6)).mean(); sd = s.rolling(win, min_periods=int(win * 0.6)).std()
    return (s - mu) / sd

# 13F hedge-fund tilt toward the momentum cohort
f13 = None
fp = os.path.join(PROC, '13f_by_ticker.csv')
if os.path.exists(fp):
    f13 = pd.read_csv(fp, parse_dates=['period']); f13 = f13[f13.period >= '2013-06-30']
def hf_tilt(hist):
    if f13 is None: return None
    out = {}
    for per, g in f13.groupby('period'):
        d = max([k for k in hist if k <= per], default=None)
        if d is None: continue
        mom, dfn = hist[d]; v = g.set_index('ticker')['hf_value_usd']
        a, b = v.reindex(mom).sum(), v.reindex(dfn).sum()
        if a + b > 0: out[per.strftime('%Y-%m-%d')] = round(float(a / (a + b)), 4)
    return out

print(f"{len(buckets)} buckets with >= {MIN_MEMBERS} members; {len(all_members)} stocks; {len(month_ends)} month-ends from {month_ends[0].date()}")
out_b = {}; rows = []
for bid, mem in buckets.items():
    mr, dr, mshare, hist = cohort_series(lambda p, mem=mem: (lambda df: classify(df) if df is not None else None)(scores_at(p, mem)), bid)
    spread = (mr - dr)
    mi, di = to_index(mr), to_index(dr)
    si = to_index(spread)
    if si.empty: continue
    s63 = (1 + spread).rolling(63).apply(np.prod, raw=True) - 1
    zs = zroll(np.log(si), 2520)
    last_d = max(hist); mom_now, def_now = hist[last_d]
    df_now = scores_at(pos[last_d], mem)
    core_now = [x for x in df_now.index if x not in mom_now and x not in def_now] if df_now is not None else []
    rec = {
        'id': bid, 'name': names[bid], 'sector': sector_of[bid], 'sector_name': sector_name[sector_of[bid]], 'n_members': len(mem),
        'first': str(si.index[0].date()), 'asof': str(last_d.date()),
        'spread_1m': float(si.iloc[-1] / si.iloc[-22] - 1) if len(si) > 22 else None, 'spread_3m': float(si.iloc[-1] / si.iloc[-64] - 1) if len(si) > 64 else None,
        'spread_12m': float(si.iloc[-1] / si.iloc[-253] - 1) if len(si) > 253 else None, 'spread_z10y': None if pd.isna(zs.iloc[-1]) else float(zs.iloc[-1]),
        'spread_pctile': float((si < si.iloc[-1]).mean()), 'mom_share': None if mshare.empty else float(mshare.iloc[-1]), 'mom_share_mean': None if mshare.empty else float(mshare.mean()),
        'momentum_names': mom_now, 'defensive_names': def_now, 'core_names': core_now,
        'scores': {t: {'mom': round(float(df_now.loc[t, 'mom']), 3), 'vol': round(float(df_now.loc[t, 'vol'] * math.sqrt(252)), 3), 'beta': round(float(df_now.loc[t, 'beta']), 2), 'style': round(float(df_now.loc[t, 'style']), 2)} for t in df_now.index} if df_now is not None else {},
        'hf_tilt': hf_tilt(hist),
    }
    rec['history'] = {'dates': [d.strftime('%Y-%m-%d') for d in si.resample('ME').last().index], 'spread': monthly(si, 2), 'mom': monthly(mi.reindex(si.index), 2), 'def': monthly(di.reindex(si.index), 2),
                      'spread_z': monthly(zs, 2), 'mom_share': [None if pd.isna(v) else round(float(v), 3) for v in mshare.reindex(si.resample('ME').last().index, method='ffill').values]}
    out_b[bid] = rec
    for d, (mom, dfn) in hist.items(): rows.append({'date': d.date(), 'bucket': bid, 'momentum': ' '.join(mom), 'defensive': ' '.join(dfn)})
    fp = lambda v: '   n/a' if v is None else f"{v*100:+.1f}%"
    print(f"  {bid:32s} n={len(mem):2d} since {si.index[0].year} spread 3m {fp(rec['spread_3m'])} 12m {fp(rec['spread_12m'])} z {rec['spread_z10y'] and round(rec['spread_z10y'],2)} mom={mom_now[:4]} def={def_now[:4]}")

# market-level: classify across the whole universe
mr, dr, mshare, hist = cohort_series(lambda p: (lambda df: classify(df) if df is not None else None)(scores_at(p, all_members)), 'market')
si_m = to_index(mr - dr); zs_m = zroll(np.log(si_m), 2520)
market = {'first': str(si_m.index[0].date()), 'spread_3m': float(si_m.iloc[-1] / si_m.iloc[-64] - 1), 'spread_12m': float(si_m.iloc[-1] / si_m.iloc[-253] - 1), 'spread_z10y': float(zs_m.iloc[-1]),
          'history': {'dates': [d.strftime('%Y-%m-%d') for d in si_m.resample('ME').last().index], 'spread': monthly(si_m, 2), 'mom': monthly(to_index(mr).reindex(si_m.index), 2), 'def': monthly(to_index(dr).reindex(si_m.index), 2), 'spread_z': monthly(zs_m, 2)},
          'momentum_names': hist[max(hist)][0][:40], 'defensive_names': hist[max(hist)][1][:40], 'hf_tilt': hf_tilt(hist)}

# Fama-French daily factors (external 30-year backdrop)
ff = {}
try:
    def ffcsv(name):
        data = urllib.request.urlopen(urllib.request.Request('https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/' + name, headers={'User-Agent': 'Mozilla/5.0'}), timeout=60).read()
        zf = zipfile.ZipFile(io.BytesIO(data)); txt = zf.read(zf.namelist()[0]).decode('latin1')
        lines = [l for l in txt.splitlines() if l[:8].isdigit()]
        df = pd.read_csv(io.StringIO('\n'.join(lines)), header=None); df[0] = pd.to_datetime(df[0].astype(str)); return df.set_index(0)
    f5 = ffcsv('F-F_Research_Data_5_Factors_2x3_daily_CSV.zip'); f5.columns = ['Mkt-RF', 'SMB', 'HML', 'RMW', 'CMA', 'RF']
    fm = ffcsv('F-F_Momentum_Factor_daily_CSV.zip'); fm.columns = ['MOM']
    F = f5.join(fm, how='inner') / 100.0; F = F[F.index >= '1995-01-01']
    FI = (1 + F[['MOM', 'RMW', 'HML', 'SMB']]).cumprod() * 100
    ff = {'dates': [d.strftime('%Y-%m-%d') for d in FI.resample('ME').last().index], 'MOM': monthly(FI['MOM'], 2), 'RMW': monthly(FI['RMW'], 2), 'HML': monthly(FI['HML'], 2), 'SMB': monthly(FI['SMB'], 2),
          'asof': str(F.index[-1].date()), 'mom_3m': float((1 + F['MOM'].tail(63)).prod() - 1), 'mom_12m': float((1 + F['MOM'].tail(252)).prod() - 1), 'rmw_3m': float((1 + F['RMW'].tail(63)).prod() - 1), 'rmw_12m': float((1 + F['RMW'].tail(252)).prod() - 1), 'hml_12m': float((1 + F['HML'].tail(252)).prod() - 1),
          'note': 'Kenneth French data library: MOM = winners minus losers; RMW = robust minus weak profitability (quality); HML = value minus growth; SMB = small minus big. Daily, cumulative, rebased to 100 at 1995.'}
    print('FF factors through', ff['asof'])
except Exception as e:
    print('FF download failed:', e)

# sector roll-ups: average of member-bucket spread z and 3m spread, cap-weighted by bucket cap
sectors = {}
for sec in all_sectors(T):
    ids = [si['id'] for si in sec['subindustries'] if si['id'] in out_b]
    if not ids: continue
    w = np.array([MC[buckets[i]].iloc[-1].sum() for i in ids]); w = w / w.sum()
    sectors[sec['id']] = {'name': sec['name'], 'buckets': ids, 'spread_3m': float(sum(wi * (out_b[i]['spread_3m'] or 0) for wi, i in zip(w, ids))), 'spread_12m': float(sum(wi * (out_b[i]['spread_12m'] or 0) for wi, i in zip(w, ids))), 'spread_z10y': float(sum(wi * (out_b[i]['spread_z10y'] or 0) for wi, i in zip(w, ids)))}

def clean(o):
    if isinstance(o, float): return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict): return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, list): return [clean(v) for v in o]
    if isinstance(o, (np.floating,)): return clean(float(o))
    if isinstance(o, (np.integer,)): return int(o)
    return o
out = {'asof': str(R.index[-1].date()), 'generated': dt.datetime.now().strftime('%Y-%m-%d %H:%M'), 'method': {'lookback_days': LOOKBACK, 'min_members': MIN_MEMBERS, 'rebalance': 'monthly', 'cohort': 'top/bottom third by style = z(12-1m momentum) - mean(z(-vol), z(-beta))', 'weighting': 'cap-weighted within cohort'},
       'buckets': out_b, 'market': market, 'sectors': sectors, 'ff': ff}
with open(os.path.join(SITE_DATA, 'style.js'), 'w', encoding='utf-8') as f:
    f.write('window.CRT_STYLE = '); json.dump(clean(out), f, ensure_ascii=False, separators=(',', ':')); f.write(';')
pd.DataFrame(rows).to_csv(os.path.join(PROC, 'style_cohorts.csv'), index=False)
print('market spread since', market['first'], '3m', round(market['spread_3m'] * 100, 1), '12m', round(market['spread_12m'] * 100, 1), 'z', round(market['spread_z10y'], 2))
