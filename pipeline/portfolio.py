"""Trading tab analytics for one portfolio (research/private/portfolio_holdings.json, never committed).

Outputs
  site/data/portfolio_full.js  window.CRT_PORTFOLIO with dollar values   (gitignored: local preview only)
  site/data/portfolio.js       same object with every dollar amount, quantity and cost removed (publishable)

Pieces
  weights by position / instrument / account, look-through to sub-industries, sectors and themes
  returns decomposition per holding and for the whole book: market, sector, style (growth, size, momentum,
    quality, low-vol), FX, idiosyncratic; betas from trailing 252 days, contributions over 1m/3m/6m/12m/YTD
  key ratios (1y, 3y): return, vol, Sharpe, Sortino, max drawdown, beta, correlation, capture
  expected moves: ATM straddle implied move to the next monthly expiry, implied-vol 1m 1-sigma move,
    implied earnings move where an expiry straddles the print; realised-vol fallback for names without options;
    portfolio 1-sigma over 1 month from implied vols and the trailing correlation matrix; shock scenarios
"""
import os, json, math, datetime as dt
import numpy as np, pandas as pd
from common import load_taxonomy, all_sectors, RAW, SITE_DATA, ROOT

T = load_taxonomy()
HOLD = json.load(open(os.path.join(ROOT, 'research', 'private', 'portfolio_holdings.json'), encoding='utf-8'))
PP = pd.read_parquet(os.path.join(RAW, 'portfolio_prices.parquet')).sort_index()
P = pd.read_parquet(os.path.join(RAW, 'prices.parquet')).sort_index()
P = P.loc[P.index >= '2018-01-01']
PX = P.join(PP[[c for c in PP.columns if c not in P.columns]], how='outer').sort_index()
PX = PX.where(PX > 0)
R = PX.pct_change(fill_method=None); R = R.where(R.abs() < 1.0)
FX = R['CAD=X']                      # USDCAD daily change: positive = USD stronger = good for unhedged USD holdings in CAD terms
RF = (PX['^IRX'] / 100 / 252).reindex(R.index).ffill().fillna(0)   # 13-week bill, daily
usdcad = float(PX['CAD=X'].dropna().iloc[-1])
asof = str(R.index[-1].date())

SECTOR_ETF = {s['id']: s['sector_etf'] for s in all_sectors(T)}
SECTOR_NAME = {s['id']: s['name'] for s in all_sectors(T)}
SUB_NAME, SUB_SECTOR = {}, {}
WHERE = {}
for sec in all_sectors(T):
    for si in sec['subindustries']:
        SUB_NAME[si['id']] = si['name']; SUB_SECTOR[si['id']] = sec['id']
        for x in si['basket']: WHERE[x] = (sec['id'], si['id'])
THEMES = {th['id']: th for th in T['themes']}
THEME_NAME = {th['id']: th['name'] for th in T['themes']}

# ---------------------------------------------------------------- positions and weights
pos = pd.DataFrame(HOLD['positions'])
inst = HOLD['instruments']
pos['value_cad'] = np.where(pos.currency == 'USD', pos.value * usdcad, pos.value)
pos['cost'] = pos.value - pos.all_time_return
pos['cost_cad'] = np.where(pos.currency == 'USD', pos.cost * usdcad, pos.cost)
total = float(pos.value_cad.sum())
pos['weight'] = pos.value_cad / total
by_inst = pos.groupby('ticker').agg(value_cad=('value_cad', 'sum'), cost_cad=('cost_cad', 'sum'), qty=('qty', 'sum'), weight=('weight', 'sum'), name=('name', 'first'), yahoo=('yahoo', 'first'), currency=('currency', 'first')).reset_index()
by_inst['pnl_cad'] = by_inst.value_cad - by_inst.cost_cad; by_inst['pnl_pct'] = by_inst.pnl_cad / by_inst.cost_cad
acct = pos.groupby('account').agg(value_cad=('value_cad', 'sum')).reset_index(); acct['weight'] = acct.value_cad / total
W = by_inst.set_index('ticker')['weight']
# leverage: account totals are net of cash and margin debit, positions are gross
net_equity = float(sum(a['total_cad'] for a in HOLD['accounts'].values()))
acct['net_cad'] = [HOLD['accounts'][a]['total_cad'] for a in acct.account]
acct['cash_or_debit_cad'] = acct.net_cad - acct.value_cad      # positive = cash, negative = margin loan
leverage = total / net_equity
print(f"{len(by_inst)} instruments, {len(pos)} positions, {total:,.0f} CAD modelled; USDCAD {usdcad:.3f}; as of {asof}")

# ---------------------------------------------------------------- look-through exposures
def lookthrough(tk):
    i = inst[tk]
    if i.get('lookthrough'):
        lt = dict(i['lookthrough']); s = sum(lt.values())
        return {k: v for k, v in lt.items()}, max(0.0, 1 - s)   # (names, unassigned share)
    if i.get('subindustry') or i.get('custom_sub'): return {tk: 1.0}, 0.0
    return {}, 1.0

sub_exp, sec_exp, theme_exp, name_exp = {}, {}, {}, {}
diversified = 0.0
for tk, w in W.items():
    i = inst[tk]; names, un = lookthrough(tk)
    if un:
        if i.get('sector'): sec_exp[i['sector']] = sec_exp.get(i['sector'], 0) + w * un
        else: diversified += w * un
    for n, share in names.items():
        name_exp[n] = name_exp.get(n, 0) + w * share
        if n == tk and i.get('custom_sub'):
            sec_id, sub_id = i['sector'], 'custom:' + tk; SUB_NAME[sub_id] = i['custom_sub']; SUB_SECTOR[sub_id] = sec_id
        elif n == tk and i.get('subindustry'): sec_id, sub_id = i['sector'], i['subindustry']
        else: sec_id, sub_id = WHERE.get(n, (i.get('sector'), None))
        if sub_id: sub_exp[sub_id] = sub_exp.get(sub_id, 0) + w * share
        if sec_id: sec_exp[sec_id] = sec_exp.get(sec_id, 0) + w * share
        for th_id, th in THEMES.items():
            if n in th['basket'] or (n == tk and th_id in i.get('themes', [])): theme_exp[th_id] = theme_exp.get(th_id, 0) + w * share
space = sum(w for tk, w in W.items() if tk == 'ARKX')
sub_rows = sorted([{'id': k, 'name': SUB_NAME[k], 'sector': SUB_SECTOR[k], 'sector_name': SECTOR_NAME[SUB_SECTOR[k]], 'weight': float(v)} for k, v in sub_exp.items()], key=lambda r: -r['weight'])
sec_rows = sorted([{'id': k, 'name': SECTOR_NAME[k], 'weight': float(v)} for k, v in sec_exp.items()], key=lambda r: -r['weight'])
top_names = sorted([{'ticker': k, 'weight': float(v), 'sub': SUB_NAME.get(WHERE.get(k, (None, None))[1], '') if k not in inst else SUB_NAME.get(inst[k].get('subindustry'), '')} for k, v in name_exp.items()], key=lambda r: -r['weight'])[:25]

# ---------------------------------------------------------------- factor returns
def ex(a, b): return (R[a] - R[b])
FACTORS = {
    'mkt_us': R['SPY'], 'mkt_ca': R['XIU.TO'], 'fx': FX,
    'growth': ex('IWF', 'IWD'), 'size': ex('IWM', 'SPY'), 'momentum': ex('MTUM', 'SPY'), 'quality': ex('QUAL', 'SPY'), 'lowvol': ex('USMV', 'SPY'),
}
STYLES = ['growth', 'size', 'momentum', 'quality', 'lowvol']
THEME_ETF = {'ai': 'AIQ', 'hyperscalers': 'SKYY', 'reshoring_infrastructure': 'PAVE', 'cybersecurity': 'HACK', 'nuclear': 'URA'}
LOOK = 252

def local_returns(tk):
    """Daily return of the instrument in its own currency, and the CAD return the investor experiences."""
    i = inst[tk]; y = by_inst.set_index('ticker').loc[tk, 'yahoo']
    r = R[y]
    if by_inst.set_index('ticker').loc[tk, 'currency'] == 'USD' and not i.get('fx_hedged'):
        cad = (1 + r) * (1 + FX) - 1; fx_part = cad - r
    else:
        cad = r; fx_part = pd.Series(0.0, index=r.index)
    return r, cad, fx_part

def regress(y, X):
    df = pd.concat([y] + [x.rename(k) for k, x in X.items()], axis=1, sort=True).dropna()
    if len(df) < 120: return None, None, None
    A = np.column_stack([np.ones(len(df)), df.iloc[:, 1:].values]); b = df.iloc[:, 0].values
    coef, *_ = np.linalg.lstsq(A, b, rcond=None)
    resid = b - A @ coef
    r2 = 1 - resid.var() / b.var() if b.var() > 0 else 0
    return dict(zip(['alpha'] + list(X.keys()), coef)), pd.Series(resid, index=df.index), float(r2)

def window_start(key):
    last = R.index[-1]
    return {'1m': last - pd.Timedelta(days=30), '3m': last - pd.Timedelta(days=91), '6m': last - pd.Timedelta(days=182), '12m': last - pd.Timedelta(days=365), 'ytd': pd.Timestamp(last.year, 1, 1)}[key]
WINDOWS = ['1m', '3m', '6m', '12m', 'ytd']

decomp, betas_by, resid_by, cadret_by = {}, {}, {}, {}
for tk in W.index:
    i = inst[tk]; r, cad, fxp = local_returns(tk)
    mkt = 'mkt_ca' if i.get('home') == 'CA' else 'mkt_us'
    X = {mkt: FACTORS[mkt]}
    sec = i.get('sector')
    if sec and SECTOR_ETF.get(sec) in R.columns: X['sector'] = ex(SECTOR_ETF[sec], 'SPY')
    for s in STYLES: X[s] = FACTORS[s]
    if i.get('home') == 'CA' or (by_inst.set_index('ticker').loc[tk, 'currency'] == 'CAD' and not i.get('fx_hedged')): X['fx_beta'] = FX   # CAD-listed wrappers of US assets carry FX inside their price
    fit, resid, r2 = regress(r.iloc[-LOOK:], {k: v.iloc[-LOOK:] for k, v in X.items()})
    cadret_by[tk] = cad
    if fit is None:
        decomp[tk] = None; continue
    betas_by[tk] = {k: float(v) for k, v in fit.items()}; resid_by[tk] = resid
    out = {'betas': betas_by[tk], 'r2': r2, 'market_factor': mkt, 'windows': {}}
    for wk in WINDOWS:
        s0 = window_start(wk); idx = r.index[(r.index > s0)]
        rr = r.reindex(idx).dropna()
        if len(rr) < 5: out['windows'][wk] = None; continue
        tot_local = float(rr.sum()); contrib = {}
        for k, f in X.items():
            contrib[k] = float(fit[k] * f.reindex(rr.index).fillna(0).sum())
        contrib['fx'] = float(fxp.reindex(rr.index).fillna(0).sum())
        explained = sum(v for k, v in contrib.items() if k != 'fx')
        idio = tot_local - explained
        tot_cad = float(cad.reindex(rr.index).dropna().sum())
        out['windows'][wk] = {'ret_cad': tot_cad, 'ret_local': tot_local, 'market': contrib.get(mkt, 0.0), 'sector': contrib.get('sector', 0.0),
                              'style': {s: contrib.get(s, 0.0) for s in STYLES}, 'fx': contrib['fx'] + contrib.get('fx_beta', 0.0), 'idio': idio, 'days': int(len(rr))}
    decomp[tk] = out

# portfolio-level decomposition: weighted sum of holding pieces (current weights)
port_decomp = {}
for wk in WINDOWS:
    agg = {'ret_cad': 0, 'market': 0, 'sector': 0, 'fx': 0, 'idio': 0, 'style': {s: 0 for s in STYLES}, 'covered': 0}
    for tk, w in W.items():
        d = decomp.get(tk); dw = d['windows'].get(wk) if d else None
        if not dw: continue
        agg['covered'] += w
        for k in ['ret_cad', 'market', 'sector', 'fx', 'idio']: agg[k] += w * dw[k]
        for s in STYLES: agg['style'][s] += w * dw['style'][s]
    port_decomp[wk] = {k: (float(v) if not isinstance(v, dict) else {a: float(b) for a, b in v.items()}) for k, v in agg.items()}

# ---------------------------------------------------------------- portfolio daily series and ratios
CADR = pd.DataFrame({tk: cadret_by[tk] for tk in W.index})
def port_series(start):
    sub = CADR.loc[CADR.index >= start]
    have = sub.notna().all(axis=1); sub = sub[have]
    return (sub * W.reindex(sub.columns)).sum(axis=1) / W.reindex(sub.columns).sum(), sub
def ratios(pr, bench):
    if len(pr) < 60: return None
    rf = RF.reindex(pr.index).fillna(0); exr = pr - rf
    ann = float((1 + pr).prod() ** (252 / len(pr)) - 1); vol = float(pr.std() * math.sqrt(252))
    dn = pr[pr < 0].std() * math.sqrt(252)
    cum = (1 + pr).cumprod(); dd = cum / cum.cummax() - 1
    b = bench.reindex(pr.index)
    cov = pd.concat([pr, b], axis=1).dropna(); beta = float(cov.iloc[:, 0].cov(cov.iloc[:, 1]) / cov.iloc[:, 1].var()) if len(cov) > 30 else None
    up = cov[cov.iloc[:, 1] > 0]; down = cov[cov.iloc[:, 1] < 0]
    return {'days': int(len(pr)), 'start': str(pr.index[0].date()), 'ann_return': ann, 'ann_vol': vol, 'sharpe': float(exr.mean() / pr.std() * math.sqrt(252)) if pr.std() > 0 else None,
            'sortino': float(exr.mean() * 252 / dn) if dn and dn > 0 else None, 'max_drawdown': float(dd.min()), 'max_dd_date': str(dd.idxmin().date()),
            'beta_spy': beta, 'corr_spy': float(cov.iloc[:, 0].corr(cov.iloc[:, 1])) if len(cov) > 30 else None,
            'up_capture': float(up.iloc[:, 0].mean() / up.iloc[:, 1].mean()) if len(up) > 10 else None, 'down_capture': float(down.iloc[:, 0].mean() / down.iloc[:, 1].mean()) if len(down) > 10 else None,
            'best_day': float(pr.max()), 'worst_day': float(pr.min()), 'hit_rate': float((pr > 0).mean()), 'cum_return': float(cum.iloc[-1] - 1)}
spy_cad = (1 + R['SPY']) * (1 + FX) - 1
last = R.index[-1]
port_stats = {}
for key, start in [('1y', last - pd.Timedelta(days=365)), ('3y', last - pd.Timedelta(days=3 * 365)), ('common', pd.Timestamp('2018-01-01'))]:
    pr, sub = port_series(start)
    port_stats[key] = ratios(pr, R['SPY'])
    if port_stats[key]:
        port_stats[key]['vs_spy_cad'] = ratios(spy_cad.reindex(pr.index).dropna(), R['SPY'])
        port_stats[key]['vs_tsx'] = ratios(R['XIU.TO'].reindex(pr.index).dropna(), R['SPY'])
pr1, _ = port_series(last - pd.Timedelta(days=365))
cum1 = (1 + pr1).cumprod() * 100
spy1 = (1 + spy_cad.reindex(pr1.index).fillna(0)).cumprod() * 100; tsx1 = (1 + R['XIU.TO'].reindex(pr1.index).fillna(0)).cumprod() * 100
series = {'dates': [d.strftime('%Y-%m-%d') for d in cum1.index], 'portfolio': [round(float(v), 2) for v in cum1.values], 'spy_cad': [round(float(v), 2) for v in spy1.values], 'tsx': [round(float(v), 2) for v in tsx1.values]}
inst_stats = {}
for tk in W.index:
    s = cadret_by[tk].loc[cadret_by[tk].index >= last - pd.Timedelta(days=365)].dropna()
    inst_stats[tk] = ratios(s, R['SPY'])

# empirical theme betas (portfolio CAD return on theme ETF excess, trailing 252d, one theme at a time)
pr_fit, _ = port_series(last - pd.Timedelta(days=400))
theme_beta = {}
for th, etf in THEME_ETF.items():
    if etf not in R.columns: continue
    fit, _, r2 = regress(pr_fit.iloc[-LOOK:], {'mkt': R['SPY'].iloc[-LOOK:], 'theme': ex(etf, 'SPY').iloc[-LOOK:]})
    if fit: theme_beta[th] = {'beta': float(fit['theme']), 'etf': etf}
theme_rows = sorted([{'id': k, 'name': THEME_NAME[k], 'direct': float(theme_exp.get(k, 0.0)), 'beta': theme_beta.get(k, {}).get('beta'), 'etf': THEME_ETF.get(k)} for k in THEMES], key=lambda r: -r['direct'])
theme_rows.append({'id': 'space', 'name': 'Space & defence (ARKX)', 'direct': float(space), 'beta': None, 'etf': 'ARKX'})
theme_rows.sort(key=lambda r: -r['direct'])

# ---------------------------------------------------------------- expected moves from options
def implied_block(tk):
    import yfinance as yf
    y = by_inst.set_index('ticker').loc[tk, 'yahoo']; spot = float(PX[y].dropna().iloc[-1])
    out = {'spot': spot, 'has_options': False}
    try:
        t = yf.Ticker(y); exps = list(t.options)
    except Exception: exps = []
    try:
        cal = t.calendar; ed = cal.get('Earnings Date') if isinstance(cal, dict) else None
        out['earnings'] = str(ed[0]) if ed else None
    except Exception: out['earnings'] = None
    today = pd.Timestamp(asof)
    def straddle(exp):
        ch = t.option_chain(exp); c, p = ch.calls, ch.puts
        if c.empty or p.empty: return None
        mid = lambda o: (o.bid + o.ask) / 2 if o.bid > 0 and o.ask > 0 else o.lastPrice
        common = sorted(set(c.strike) & set(p.strike), key=lambda x: abs(x - spot))
        if common and abs(common[0] - spot) / spot <= 0.04:
            k = common[0]; cc = c[c.strike == k].iloc[0]; pp_ = p[p.strike == k].iloc[0]
            price = mid(cc) + mid(pp_); iv = np.nanmean([cc.impliedVolatility, pp_.impliedVolatility])
        else:
            # sparse chain: build the straddle from the single option nearest the money via put-call parity
            cc = c.iloc[(c.strike - spot).abs().argsort()[:1]].iloc[0]; pp_ = p.iloc[(p.strike - spot).abs().argsort()[:1]].iloc[0]
            if abs(cc.strike - spot) <= abs(pp_.strike - spot): k = cc.strike; price = 2 * mid(cc) - (spot - k); iv = cc.impliedVolatility
            else: k = pp_.strike; price = 2 * mid(pp_) + (spot - k); iv = pp_.impliedVolatility
        if not (price > 0) or not (iv > 0.01): return None
        days = (pd.Timestamp(exp) - today).days
        return {'expiry': exp, 'days': int(days), 'strike': float(k), 'straddle': float(price), 'iv': float(iv)}
    if exps:
        valid = [e for e in exps if (pd.Timestamp(e) - today).days >= 1]
        near = [e for e in valid if (pd.Timestamp(e) - today).days >= 20]
        try:
            m = straddle(near[0] if near else valid[-1])
            if m:
                out.update({'has_options': True, 'expiry': m['expiry'], 'days_to_expiry': m['days'], 'implied_move_to_expiry': m['straddle'] / spot, 'iv': m['iv'],
                            'sigma_1m': m['iv'] * math.sqrt(21 / 252)})
            # implied earnings move: variance in the expiry just after earnings minus the one just before, where both exist
            if out.get('earnings'):
                ed = pd.Timestamp(out['earnings'])
                before = [e for e in valid if pd.Timestamp(e) < ed]; after = [e for e in valid if pd.Timestamp(e) >= ed]
                if after and (pd.Timestamp(after[0]) - today).days <= 60:
                    a = straddle(after[0]); b = straddle(before[-1]) if before else None
                    if a:
                        if b and a['days'] > b['days']:
                            var_a = (a['iv'] ** 2) * a['days'] / 365; var_b = (b['iv'] ** 2) * b['days'] / 365
                            em = math.sqrt(max(var_a - var_b, 0)) if var_a > var_b else None
                        else:
                            em = a['straddle'] / spot * 0.8   # rough: most of a near-dated straddle is the event
                        out['earnings_move'] = em; out['earnings_expiry'] = a['expiry']
        except Exception as e:
            out['error'] = str(e)[:120]
    rv = float(cadret_by[tk].dropna().iloc[-63:].std() * math.sqrt(252))
    out['realised_vol_3m'] = rv
    if not out['has_options']: out['sigma_1m'] = rv * math.sqrt(21 / 252); out['basis'] = 'realised vol (no listed options)'
    else: out['basis'] = 'ATM options'
    return out
moves = {}
for tk in W.index:
    moves[tk] = implied_block(tk); print(f"  {tk:5s} {moves[tk]['basis']:32s} 1m sigma {moves[tk]['sigma_1m']*100:.1f}%  earnings {moves[tk].get('earnings')}  em {moves[tk].get('earnings_move')}")

# portfolio 1-sigma over one month: trailing-252d correlation of CAD returns x implied (or realised) 1m vols
C = CADR.iloc[-LOOK:].dropna(how='all'); corr = C.corr().fillna(0)
sig = pd.Series({tk: moves[tk]['sigma_1m'] for tk in W.index})
cov = corr.loc[W.index, W.index].values * np.outer(sig.values, sig.values)
w = W.values; port_sigma = float(math.sqrt(w @ cov @ w))
# contribution of each holding to the portfolio variance
mcr = cov @ w; contrib = w * mcr / (port_sigma ** 2) if port_sigma > 0 else w * 0
risk_contrib = sorted([{'ticker': tk, 'weight': float(W[tk]), 'sigma_1m': float(sig[tk]), 'share_of_variance': float(contrib[i])} for i, tk in enumerate(W.index)], key=lambda r: -r['share_of_variance'])

# scenarios: shock a factor, propagate through each holding's beta (FX exact for unhedged USD positions)
usd_unhedged = {tk: (by_inst.set_index('ticker').loc[tk, 'currency'] == 'USD' and not inst[tk].get('fx_hedged')) for tk in W.index}
SCEN = [
    ('S&P 500 −10%', 'mkt_us', -0.10), ('S&P 500 +10%', 'mkt_us', 0.10), ('TSX −10%', 'mkt_ca', -0.10), ('US Tech underperforms S&P by 15%', 'sector:technology', -0.15),
    ('Momentum unwind (MTUM −10% vs S&P)', 'momentum', -0.10), ('Growth beats value by 10%', 'growth', 0.10), ('Small caps rally +10% vs S&P', 'size', 0.10),
    ('USD −5% vs CAD', 'fx', -0.05), ('USD +5% vs CAD', 'fx', 0.05),
]
scen_rows = []
for name, fac, shock in SCEN:
    parts = []
    for tk, wt in W.items():
        b = betas_by.get(tk, {}); eff = 0.0
        if fac == 'fx':
            eff = (1.0 if usd_unhedged[tk] else b.get('fx_beta', 0.0)) * shock
        elif fac.startswith('sector:'):
            if inst[tk].get('sector') == fac.split(':')[1]: eff = b.get('sector', 0.0) * shock
        else:
            eff = b.get(fac, 0.0) * shock
        parts.append((tk, float(wt * eff)))
    tot = sum(p for _, p in parts); parts.sort(key=lambda x: abs(x[1]), reverse=True)
    scen_rows.append({'name': name, 'portfolio_pct': tot, 'equity_pct': tot * leverage, 'portfolio_cad': tot * total, 'drivers': [{'ticker': tk, 'pct': v} for tk, v in parts[:4] if abs(v) > 0.0005]})

# ---------------------------------------------------------------- write
def clean(o):
    if isinstance(o, float): return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict): return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, list): return [clean(v) for v in o]
    if isinstance(o, (np.floating,)): return clean(float(o))
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.bool_,)): return bool(o)
    return o
full = {
    'asof': asof, 'generated': dt.datetime.now().strftime('%Y-%m-%d %H:%M'), 'owner': HOLD.get('owner'), 'base_currency': 'CAD', 'usdcad': usdcad, 'total_cad': total,
    'net_equity_cad': net_equity, 'leverage': leverage, 'margin_debit_cad': float(-acct.cash_or_debit_cad[acct.cash_or_debit_cad < 0].sum()), 'cash_cad': float(acct.cash_or_debit_cad[acct.cash_or_debit_cad > 0].sum()),
    'accounts': [{'id': r.account, 'type': HOLD['accounts'][r.account]['type'], 'value_cad': float(r.value_cad), 'net_cad': float(r.net_cad), 'cash_or_debit_cad': float(r.cash_or_debit_cad), 'weight': float(r.weight), 'leverage': float(r.value_cad / r.net_cad) if r.net_cad else None} for r in acct.itertuples()],
    'managed_not_modelled': HOLD.get('managed_not_modelled', []),
    'positions': [{'ticker': r.ticker, 'name': r.name, 'account': r.account, 'account_type': HOLD['accounts'][r.account]['type'], 'currency': r.currency, 'qty': float(r.qty), 'price': float(r.price), 'value': float(r.value), 'value_cad': float(r.value_cad), 'cost_cad': float(r.cost_cad), 'pnl_cad': float(r.value_cad - r.cost_cad), 'pnl_pct': float((r.value_cad - r.cost_cad) / r.cost_cad) if r.cost_cad else None, 'weight': float(r.weight)} for r in pos.itertuples()],
    'instruments': [{'ticker': r.ticker, 'name': r.name, 'yahoo': r.yahoo, 'currency': r.currency, 'kind': inst[r.ticker]['kind'], 'fx_hedged': bool(inst[r.ticker].get('fx_hedged')), 'home': inst[r.ticker].get('home'),
                     'sector': inst[r.ticker].get('sector'), 'sector_name': SECTOR_NAME.get(inst[r.ticker].get('sector')), 'subindustry': inst[r.ticker].get('subindustry'), 'sub_name': SUB_NAME.get(inst[r.ticker].get('subindustry')), 'note': inst[r.ticker].get('note'),
                     'qty': float(r.qty), 'value_cad': float(r.value_cad), 'cost_cad': float(r.cost_cad), 'pnl_cad': float(r.pnl_cad), 'pnl_pct': float(r.pnl_pct) if r.cost_cad else None, 'weight': float(r.weight),
                     'lookthrough': inst[r.ticker].get('lookthrough'), 'decomp': decomp.get(r.ticker), 'stats_1y': inst_stats.get(r.ticker), 'moves': moves.get(r.ticker)} for r in by_inst.sort_values('weight', ascending=False).itertuples()],
    'exposure': {'subindustries': sub_rows, 'sectors': sec_rows, 'diversified_unclassified': float(diversified), 'themes': theme_rows, 'top_names': top_names},
    'decomp': port_decomp, 'stats': port_stats, 'series': series,
    'risk': {'sigma_1m': port_sigma, 'sigma_1m_cad': port_sigma * total, 'sigma_1m_on_equity': port_sigma * leverage, 'contrib': risk_contrib, 'scenarios': scen_rows},
    'method': {'lookback_days': LOOK, 'factors': 'market (SPY for US names, XIU for Canadian), sector ETF minus SPY, growth IWF−IWD, size IWM−SPY, momentum MTUM−SPY, quality QUAL−SPY, low-vol USMV−SPY, USDCAD; residual = idiosyncratic', 'weights': 'current weights held constant (daily rebalanced) for the portfolio series', 'options': 'Yahoo Finance delayed chains; ATM straddle at the first expiry at least 20 days out; implied earnings move from the variance difference between the expiries around the print'},
}
STRIP = {'qty', 'price', 'value', 'value_cad', 'cost_cad', 'pnl_cad', 'total_cad', 'sigma_1m_cad', 'portfolio_cad', 'managed_not_modelled', 'net_equity_cad', 'margin_debit_cad', 'cash_cad', 'net_cad', 'cash_or_debit_cad'}
def public(o):
    if isinstance(o, dict): return {k: public(v) for k, v in o.items() if k not in STRIP}
    if isinstance(o, list): return [public(v) for v in o]
    return o
with open(os.path.join(SITE_DATA, 'portfolio_full.js'), 'w', encoding='utf-8') as f:
    f.write('window.CRT_PORTFOLIO = '); json.dump(clean(full), f, ensure_ascii=False, separators=(',', ':')); f.write(';')
pub = public(clean(full)); pub['public'] = True; pub['owner'] = None
with open(os.path.join(SITE_DATA, 'portfolio.js'), 'w', encoding='utf-8') as f:
    f.write('window.CRT_PORTFOLIO = '); json.dump(pub, f, ensure_ascii=False, separators=(',', ':')); f.write(';')
print('gross', round(total), 'net equity', round(net_equity), 'leverage', round(leverage, 2))
print('portfolio 1m sigma', round(port_sigma * 100, 2), '% ; stats 1y', {k: round(v, 3) if isinstance(v, float) else v for k, v in (port_stats['1y'] or {}).items() if k in ('ann_return', 'ann_vol', 'sharpe', 'max_drawdown', 'beta_spy')})
print('decomp 3m', {k: (round(v * 100, 2) if isinstance(v, float) else v) for k, v in port_decomp['3m'].items() if k != 'style'})
