"""Portfolio analytics shared by the local pipeline (portfolio.py) and the hosted backend (backend/app.py).

analyze(holdings) -> dict   everything the Trading tab shows, including dollar values
public_view(result) -> dict the same with every dollar amount, quantity and cost removed

holdings = {
  'label': str, 'base_currency': 'CAD' | 'USD', 'asof': 'YYYY-MM-DD' (optional),
  'accounts': {account_id: {'type': str, 'total': net value in base currency or None}},
  'positions': [{'ticker', 'yahoo' (optional), 'name' (optional), 'account', 'currency', 'qty',
                 'price' (optional), 'value' (optional), 'cost' (total, optional), 'avg_cost' (optional),
                 'exchange' (optional), 'first_buy' (optional 'YYYY-MM-DD')}],
  'instruments': {ticker: overrides}   (optional; merged over pipeline/instrument_overrides.json)
}
Prices, fund look-through and option chains come from Yahoo Finance and are cached in memory.
"""
import os, json, math, time, datetime as dt, concurrent.futures as cf
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
TAX = json.load(open(os.path.join(HERE, 'taxonomy.json'), encoding='utf-8'))
_ov_path = os.path.join(HERE, 'instrument_overrides.json')
OVERRIDES = json.load(open(_ov_path, encoding='utf-8')) if os.path.exists(_ov_path) else {}

ALL_SECTORS = TAX['sectors'] + TAX.get('context_sectors', [])
SECTOR_ETF = {s['id']: s['sector_etf'] for s in ALL_SECTORS}
SECTOR_NAME = {s['id']: s['name'] for s in ALL_SECTORS}
SUB_NAME, SUB_SECTOR, WHERE = {}, {}, {}
for _sec in ALL_SECTORS:
    for _si in _sec['subindustries']:
        SUB_NAME[_si['id']] = _si['name']; SUB_SECTOR[_si['id']] = _sec['id']
        for _x in _si['basket']: WHERE[_x] = (_sec['id'], _si['id'])
THEMES = {th['id']: th for th in TAX['themes']}
THEME_NAME = {th['id']: th['name'] for th in TAX['themes']}
THEME_ETF = {'ai': 'AIQ', 'hyperscalers': 'SKYY', 'reshoring_infrastructure': 'PAVE', 'cybersecurity': 'HACK', 'nuclear': 'URA'}
YAHOO_SECTOR = {'technology': 'technology', 'healthcare': 'healthcare', 'industrials': 'industrials', 'consumer cyclical': 'consumer_discretionary', 'consumer_cyclical': 'consumer_discretionary',
                'consumer defensive': 'consumer_staples', 'consumer_defensive': 'consumer_staples', 'energy': 'energy', 'real estate': 'real_estate', 'realestate': 'real_estate',
                'financial services': 'financials', 'financial_services': 'financials', 'utilities': 'utilities', 'basic materials': 'materials', 'basic_materials': 'materials',
                'communication services': 'communication_media', 'communication_services': 'communication_media'}
STYLES = ['growth', 'size', 'momentum', 'quality', 'lowvol']
BASE_TICKERS = sorted(set(['SPY', 'XIU.TO', 'CAD=X', '^IRX', 'IWF', 'IWD', 'IWM', 'MTUM', 'QUAL', 'USMV'] + list(SECTOR_ETF.values()) + list(THEME_ETF.values())))
LOOK = 252
HISTORY_DAYS = int(365 * 3.3)
CA_EXCH = {'TOR': '.TO', 'TSX': '.TO', 'XTSE': '.TO', 'VAN': '.V', 'TSXV': '.V', 'XTSX': '.V', 'NEO': '.NE', 'NEOE': '.NE', 'XATS': '.NE', 'CNQ': '.CN', 'CSE': '.CN', 'XCNQ': '.CN'}

# ---------------------------------------------------------------- caches
_PX = {}          # ticker -> (fetched_at, Series of adjusted closes)
_INFO = {}        # yahoo ticker -> (fetched_at, dict)
PX_TTL = 6 * 3600; INFO_TTL = 24 * 3600

def _download(tickers):
    import yfinance as yf
    start = (dt.date.today() - dt.timedelta(days=HISTORY_DAYS)).isoformat()
    out = {}
    for i in range(0, len(tickers), 60):
        chunk = tickers[i:i + 60]
        try:
            df = yf.download(chunk, start=start, auto_adjust=True, progress=False, threads=True, group_by='column')
        except Exception:
            continue
        if df is None or df.empty: continue
        c = df['Close'] if 'Close' in df.columns.get_level_values(0) else df
        if isinstance(c, pd.Series): c = c.to_frame(chunk[0])
        c.index = pd.to_datetime(c.index).tz_localize(None)
        for t in chunk:
            if t in c.columns and c[t].notna().sum() > 0: out[t] = c[t].dropna()
    return out

def get_prices(tickers):
    now = time.time(); need = [t for t in dict.fromkeys(tickers) if t not in _PX or now - _PX[t][0] > PX_TTL]
    if need:
        got = _download(need)
        for t in need: _PX[t] = (now, got.get(t, pd.Series(dtype=float)))
    df = pd.DataFrame({t: _PX[t][1] for t in dict.fromkeys(tickers) if len(_PX[t][1])}).sort_index()
    return df.where(df > 0)

_PXL = {}         # ticker -> (fetched_at, Series) daily closes since 2015, for replaying past episodes
def get_prices_long(tickers):
    import yfinance as yf
    now = time.time(); need = [t for t in dict.fromkeys(tickers) if t not in _PXL or now - _PXL[t][0] > 24 * 3600]
    if need:
        got = {}
        try:
            df = yf.download(need, start='2015-01-01', auto_adjust=True, progress=False, threads=True, group_by='column')
            c = df['Close'] if 'Close' in df.columns.get_level_values(0) else df
            if isinstance(c, pd.Series): c = c.to_frame(need[0])
            c.index = pd.to_datetime(c.index).tz_localize(None)
            got = {t: c[t].dropna() for t in need if t in c.columns and c[t].notna().sum() > 0}
        except Exception:
            pass
        for t in need: _PXL[t] = (now, got.get(t, pd.Series(dtype=float)))
    df = pd.DataFrame({t: _PXL[t][1] for t in dict.fromkeys(tickers) if len(_PXL[t][1])}).sort_index()
    return df.where(df > 0)

# Past regime shifts replayed on today's holdings: (group, name, close-of start, close-of end, what happened)
EPISODES = [
    ('Rates', 'Rates rise fast: the 2022 hiking cycle', '2022-01-03', '2022-10-12', 'The Fed took its policy rate from zero to above 3% in nine months. Long-duration growth and anything valued on distant profits fell hardest; energy and value held up.'),
    ('Rates', 'Long yields spike: summer to autumn 2023', '2023-07-31', '2023-10-27', 'The 10-year Treasury yield climbed toward 5% on heavy issuance and "higher for longer". Rate-sensitive sectors (utilities, real estate, small caps) led the fall.'),
    ('Rates', 'Rate cuts get priced in: the late-2023 pivot', '2023-10-27', '2023-12-29', 'The Fed signalled cuts and yields fell by about a point. Almost everything rallied; small caps, banks, real estate and unprofitable growth rallied most.'),
    ('Politics', 'Democrats win Congress: November 2026 midterms', '2026-11-03', None, 'Democrats took both the House and the Senate, leaving divided government under a Republican president. In the first sessions AI leaders sold off, then the broad market rallied; clean energy, health care and consumer staples rose and private-prison operators fell. The window runs from the election-day close for up to 30 days, so the numbers keep moving until then.'),
    ('Politics', 'Democratic sweep: Georgia runoffs, January 2021', '2021-01-04', '2021-01-20', 'Democrats took the Senate and with it unified control. Markets priced more fiscal stimulus and climate spending: cyclicals, small caps and clean energy rose, bond yields rose, mega-cap tech lagged.'),
    ('Politics', 'Republican sweep: November 2024 election', '2024-11-05', '2024-12-05', 'Markets priced tax cuts, deregulation and tariffs: banks, small caps and domestic cyclicals rose; clean energy, managed care and tariff-exposed importers fell.'),
    ('Policy and law', 'Tariff shock: April 2025', '2025-04-02', '2025-04-08', 'Sweeping US import tariffs were announced. Global equities fell hard in four sessions; importers, autos, semis and anything with Asian supply chains led down.'),
    ('Policy and law', 'A major industrial-policy law passes: Inflation Reduction Act, 2022', '2022-07-27', '2022-08-16', 'From the surprise Senate deal to the signing. Clean energy, utilities, and domestic manufacturing and construction names re-rated; drug pricing provisions weighed on pharma.'),
    ('Shocks', 'Banking stress: March 2023', '2023-03-08', '2023-03-17', 'Silicon Valley Bank failed. Banks and other financials sold off, bond yields collapsed, and money hid in mega-cap tech and gold.'),
    ('Shocks', 'AI spending scare: January 2025', '2025-01-24', '2025-01-27', 'A cheap Chinese model (DeepSeek) cast doubt on the AI build-out. Semiconductors, data-centre power and electrical-equipment names fell sharply in one session.'),
    ('Shocks', 'War and oil shock: February to March 2022', '2022-02-23', '2022-03-08', 'Russia invaded Ukraine and oil spiked. Energy, defence and commodity producers rose; consumer, European-exposed and travel names fell.'),
    ('Shocks', 'Pandemic crash: February to March 2020', '2020-02-19', '2020-03-23', 'The fastest bear market on record. Everything fell; travel, energy, banks and real estate fell most, staples and big tech least.'),
]

def yahoo_info(y):
    """Instrument facts from Yahoo: type, name, sector/industry, and for funds the top holdings and sector weights."""
    now = time.time()
    if y in _INFO and now - _INFO[y][0] < INFO_TTL: return _INFO[y][1]
    import yfinance as yf
    out = {}
    try:
        t = yf.Ticker(y); i = t.info or {}
        out = {'quote_type': i.get('quoteType'), 'name': i.get('longName') or i.get('shortName'), 'sector': i.get('sector'), 'industry': i.get('industry'), 'currency': i.get('currency'), 'exchange': i.get('exchange')}
        if out['quote_type'] in ('ETF', 'MUTUALFUND'):
            try:
                fd = t.funds_data; th = fd.top_holdings; sw = fd.sector_weightings or {}
                out['top_holdings'] = {str(sym): float(row['Holding Percent']) for sym, row in th.iterrows()} if th is not None and len(th) else {}
                out['sector_weights'] = {YAHOO_SECTOR[k]: float(v) for k, v in sw.items() if k in YAHOO_SECTOR and v and v > 0}
            except Exception:
                pass
    except Exception:
        out = {}
    _INFO[y] = (now, out)
    return out

def to_yahoo(p):
    if p.get('yahoo'): return p['yahoo']
    sym = str(p['ticker']).strip().upper()
    if '.' in sym and sym.rsplit('.', 1)[1] in ('TO', 'V', 'NE', 'CN'): return sym
    suf = CA_EXCH.get(str(p.get('exchange') or '').upper())
    if suf: return sym.replace('.', '-') + suf
    if p.get('currency') == 'CAD': return sym.replace('.', '-') + '.TO'
    return sym.replace('.', '-')

def resolve_instrument(tk, y, pos_currency, overrides):
    """Merge: explicit overrides > tracker taxonomy > Yahoo. Returns the instrument record used everywhere below."""
    ov = dict(OVERRIDES.get(tk, {})); ov.update(overrides.get(tk, {}))
    base = tk.split('.')[0]
    info = yahoo_info(y)
    qt = (info.get('quote_type') or '').upper()
    inst = {'kind': 'etf' if qt == 'ETF' else 'fund' if qt == 'MUTUALFUND' else 'stock', 'name': info.get('name'), 'fx_hedged': False, 'sector': None, 'subindustry': None}
    exch = (info.get('exchange') or '').upper()
    listed_ca = y.endswith(('.TO', '.V', '.NE', '.CN')) or exch in CA_EXCH
    if inst['kind'] == 'stock':
        inst['home'] = 'CA' if listed_ca else 'US'
        if base in WHERE: inst['sector'], inst['subindustry'] = WHERE[base]
        else:
            inst['sector'] = YAHOO_SECTOR.get((info.get('sector') or '').lower())
            if info.get('industry'): inst['custom_sub'] = info['industry']
    else:
        inst['home'] = None            # decided empirically (which market explains it better)
        sw = info.get('sector_weights') or {}
        if sw:
            inst['sector_weights'] = sw
            top = max(sw.items(), key=lambda kv: kv[1])
            if top[1] >= 0.6: inst['sector'] = top[0]
        if info.get('top_holdings'): inst['lookthrough'] = {k.split('.')[0] if k.endswith(('.TO', '.V')) is False else k: v for k, v in info['top_holdings'].items()}
        nm = (info.get('name') or '').lower()
        if 'hedged' in nm and 'unhedged' not in nm: inst['fx_hedged'] = True
    inst.update(ov)
    if not inst.get('name'): inst['name'] = tk
    return inst

# ---------------------------------------------------------------- helpers
def _regress(y, X):
    df = pd.concat([y] + [x.rename(k) for k, x in X.items()], axis=1, sort=True).dropna()
    if len(df) < 120: return None, None, None
    A = np.column_stack([np.ones(len(df)), df.iloc[:, 1:].values]); b = df.iloc[:, 0].values
    coef, *_ = np.linalg.lstsq(A, b, rcond=None)
    resid = b - A @ coef
    r2 = 1 - resid.var() / b.var() if b.var() > 0 else 0
    return dict(zip(['alpha'] + list(X.keys()), coef)), pd.Series(resid, index=df.index), float(r2)

def _clean(o):
    if isinstance(o, float): return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict): return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_clean(v) for v in o]
    if isinstance(o, np.floating): return _clean(float(o))
    if isinstance(o, np.integer): return int(o)
    if isinstance(o, np.bool_): return bool(o)
    if isinstance(o, (pd.Timestamp, dt.date, dt.datetime)): return str(o)[:10]
    return o

STRIP = {'qty', 'price', 'value', 'value_base', 'cost_base', 'pnl_base', 'total_base', 'sigma_1m_base', 'portfolio_base', 'net_equity_base', 'margin_debit_base', 'cash_base', 'net_base', 'cash_or_debit_base', 'unmodelled_base'}
def public_view(full):
    def walk(o):
        if isinstance(o, dict): return {k: walk(v) for k, v in o.items() if k not in STRIP}
        if isinstance(o, list): return [walk(v) for v in o]
        return o
    pub = walk(full); pub['public'] = True; pub['label'] = 'Beta portfolio'
    return pub

# ---------------------------------------------------------------- main
def analyze(holdings, with_options=True, log=lambda *a: None):
    base_ccy = (holdings.get('base_currency') or 'CAD').upper()
    overrides = holdings.get('instruments') or {}
    raw = [dict(p) for p in holdings['positions'] if p.get('ticker') and float(p.get('qty') or 0) != 0]
    if not raw: raise ValueError('no positions with a ticker and a quantity')
    for p in raw:
        p['ticker'] = str(p['ticker']).strip().upper(); p['currency'] = (p.get('currency') or base_ccy).upper(); p['account'] = p.get('account') or 'Account'
        ov = dict(OVERRIDES.get(p['ticker'], {})); ov.update(overrides.get(p['ticker'], {}))
        p['yahoo'] = ov.get('yahoo') or to_yahoo(p)
    ylist = sorted({p['yahoo'] for p in raw})
    PX = get_prices(BASE_TICKERS + ylist)
    # CDRs and other lines Yahoo has no history for: fall back to the plain US symbol
    for p in raw:
        y = p['yahoo']
        if (y not in PX.columns or PX[y].notna().sum() < 60) and '.' in y:
            alt = y.split('.')[0]
            PX2 = get_prices([alt])
            if alt in PX2.columns and PX2[alt].notna().sum() >= 60:
                PX = PX.join(PX2[[alt]], how='outer') if alt not in PX.columns else PX
                p['yahoo'] = alt; p['_alt'] = True
    R = PX.pct_change(fill_method=None); R = R.where(R.abs() < 1.0)
    usdcad_ret = R['CAD=X']; usdcad = float(PX['CAD=X'].dropna().iloc[-1])
    def fx_to_base(cur):   # (level multiplier, daily return series of holding 1 unit of `cur` measured in base)
        if cur == base_ccy: return 1.0, None
        if cur == 'USD' and base_ccy == 'CAD': return usdcad, usdcad_ret
        if cur == 'CAD' and base_ccy == 'USD': return 1 / usdcad, (1 / (1 + usdcad_ret) - 1)
        return 1.0, None
    RF = (PX['^IRX'] / 100 / 252).reindex(R.index).ffill().fillna(0)
    asof = str(R.index[-1].date()); last = R.index[-1]

    # ---- positions, modelled vs not
    rows, unmodelled = [], []
    for p in raw:
        y = p['yahoo']; has = y in PX.columns and PX[y].notna().sum() >= 60
        px_last = float(PX[y].dropna().iloc[-1]) if y in PX.columns and PX[y].notna().any() else None
        qty = float(p['qty']); price = float(p['price']) if p.get('price') not in (None, '') else px_last
        value = float(p['value']) if p.get('value') not in (None, '') else (qty * price if price is not None else None)
        cost = float(p['cost']) if p.get('cost') not in (None, '') else (qty * float(p['avg_cost']) if p.get('avg_cost') not in (None, '') else None)
        mult, _ = fx_to_base(p['currency'])
        rec = {'ticker': p['ticker'], 'yahoo': y, 'name': p.get('name'), 'account': p['account'], 'currency': p['currency'], 'qty': qty, 'price': price, 'value': value,
               'value_base': None if value is None else value * mult, 'cost_base': None if cost is None else cost * mult, 'first_buy': p.get('first_buy'), 'alt': bool(p.get('_alt'))}
        (rows if (has and value) else unmodelled).append(rec)
    if not rows: raise ValueError('none of the positions could be priced')
    pos = pd.DataFrame(rows)
    total = float(pos.value_base.sum()); pos['weight'] = pos.value_base / total
    inst = {}
    for tk, g in pos.groupby('ticker'):
        inst[tk] = resolve_instrument(tk, g.yahoo.iloc[0], g.currency.iloc[0], overrides)
        if g['alt'].iloc[0]: inst[tk]['fx_hedged'] = True; inst[tk].setdefault('note', 'no history for the listed line; modelled on the US-listed shares, treated as currency-hedged (CDR)')
    pos['name'] = [r.name if isinstance(r.name, str) and r.name else inst[r.ticker]['name'] for r in pos.itertuples()]
    by = pos.groupby('ticker').agg(value_base=('value_base', 'sum'), qty=('qty', 'sum'), weight=('weight', 'sum'), name=('name', 'first'), yahoo=('yahoo', 'first'), currency=('currency', 'first'), first_buy=('first_buy', 'min')).reset_index()
    cost_by = pos.groupby('ticker')['cost_base'].apply(lambda s: s.sum() if s.notna().all() else np.nan)
    by['cost_base'] = by.ticker.map(cost_by); by['pnl_base'] = by.value_base - by.cost_base; by['pnl_pct'] = by.pnl_base / by.cost_base
    by = by.set_index('ticker'); W = by['weight']
    accounts_in = holdings.get('accounts') or {}
    acct = pos.groupby('account').agg(value_base=('value_base', 'sum')).reset_index(); acct['weight'] = acct.value_base / total
    acct['net_base'] = [(accounts_in.get(a) or {}).get('total') for a in acct.account]
    acct['net_base'] = acct.net_base.astype(float).fillna(acct.value_base)
    acct['cash_or_debit_base'] = acct.net_base - acct.value_base
    net_equity = float(acct.net_base.sum()); leverage = total / net_equity if net_equity > 0 else 1.0
    log(f"{len(by)} instruments, {len(pos)} positions, {total:,.0f} {base_ccy} modelled, leverage {leverage:.2f}")

    # ---- returns per instrument (own currency, and as the investor experiences it in base currency)
    FACT = {'mkt_us': R['SPY'], 'mkt_ca': R['XIU.TO'], 'growth': R['IWF'] - R['IWD'], 'size': R['IWM'] - R['SPY'], 'momentum': R['MTUM'] - R['SPY'], 'quality': R['QUAL'] - R['SPY'], 'lowvol': R['USMV'] - R['SPY']}
    local, basert, fxpart = {}, {}, {}
    for tk in W.index:
        r = R[by.loc[tk, 'yahoo']]; _, fxr = fx_to_base(by.loc[tk, 'currency'])
        if fxr is not None and not inst[tk].get('fx_hedged'):
            b = (1 + r) * (1 + fxr) - 1; fxpart[tk] = b - r
        else:
            b = r; fxpart[tk] = pd.Series(0.0, index=r.index)
        local[tk] = r; basert[tk] = b

    # ---- exposures (look-through)
    sub_exp, sec_exp, theme_exp, name_exp = {}, {}, {}, {}; diversified = 0.0; sub_names = dict(SUB_NAME); sub_sector = dict(SUB_SECTOR)
    for tk, w in W.items():
        i = inst[tk]
        if i['kind'] == 'stock' or i.get('subindustry') or i.get('custom_sub'):
            if i.get('subindustry'): sid = i['subindustry']
            elif i.get('custom_sub'): sid = 'custom:' + i['custom_sub']; sub_names[sid] = i['custom_sub']; sub_sector[sid] = i.get('sector')
            else: sid = None
            if sid: sub_exp[sid] = sub_exp.get(sid, 0) + w
            if i.get('sector'): sec_exp[i['sector']] = sec_exp.get(i['sector'], 0) + w
            else: diversified += w
            name_exp[tk] = name_exp.get(tk, 0) + w
            for th_id, th in THEMES.items():
                if tk in th['basket'] or th_id in i.get('themes', []): theme_exp[th_id] = theme_exp.get(th_id, 0) + w
            continue
        sw = i.get('sector_weights') or ({i['sector']: 1.0} if i.get('sector') else {})
        tot_sw = sum(sw.values())
        if tot_sw > 0:
            for s, v in sw.items(): sec_exp[s] = sec_exp.get(s, 0) + w * v / max(tot_sw, 1.0)
            diversified += w * max(0.0, 1 - tot_sw)
        else: diversified += w
        for n, share in (i.get('lookthrough') or {}).items():
            n = n.replace('.TO', '')
            name_exp[n] = name_exp.get(n, 0) + w * share
            if n in WHERE:
                sub_exp[WHERE[n][1]] = sub_exp.get(WHERE[n][1], 0) + w * share
            for th_id, th in THEMES.items():
                if n in th['basket']: theme_exp[th_id] = theme_exp.get(th_id, 0) + w * share
        for th_id in i.get('themes', []): theme_exp[th_id] = theme_exp.get(th_id, 0) + w
    sub_rows = sorted([{'id': k, 'name': sub_names[k], 'sector': sub_sector.get(k), 'sector_name': SECTOR_NAME.get(sub_sector.get(k), ''), 'weight': float(v)} for k, v in sub_exp.items()], key=lambda r: -r['weight'])
    sec_rows = sorted([{'id': k, 'name': SECTOR_NAME.get(k, k), 'weight': float(v)} for k, v in sec_exp.items()], key=lambda r: -r['weight'])
    top_names = sorted([{'ticker': k, 'weight': float(v), 'sub': SUB_NAME.get(WHERE.get(k, (None, None))[1], '')} for k, v in name_exp.items()], key=lambda r: -r['weight'])[:25]

    # ---- returns decomposition
    def window_start(key):
        return {'1m': last - pd.Timedelta(days=30), '3m': last - pd.Timedelta(days=91), '6m': last - pd.Timedelta(days=182), '12m': last - pd.Timedelta(days=365), 'ytd': pd.Timestamp(last.year, 1, 1)}[key]
    WINDOWS = ['1m', '3m', '6m', '12m', 'ytd']
    decomp, betas_by = {}, {}
    for tk in W.index:
        i = inst[tk]; r = local[tk]
        if i.get('home') in ('CA', 'US'): mkt = 'mkt_ca' if i['home'] == 'CA' else 'mkt_us'
        else:   # funds: whichever market explains more of the last year
            best = None
            for m in ('mkt_us', 'mkt_ca'):
                _, _, r2m = _regress(r.iloc[-LOOK:], {m: FACT[m].iloc[-LOOK:]})
                if r2m is not None and (best is None or r2m > best[1]): best = (m, r2m)
            mkt = best[0] if best else 'mkt_us'
            i['home'] = 'CA' if mkt == 'mkt_ca' else 'US'
        X = {mkt: FACT[mkt]}
        if i.get('sector') and SECTOR_ETF.get(i['sector']) in R.columns: X['sector'] = R[SECTOR_ETF[i['sector']]] - R['SPY']
        for s in STYLES: X[s] = FACT[s]
        if by.loc[tk, 'currency'] == 'CAD' and not i.get('fx_hedged'): X['fx_beta'] = usdcad_ret    # CAD-listed lines carry currency inside their price
        fit, resid, r2 = _regress(r.iloc[-LOOK:], {k: v.iloc[-LOOK:] for k, v in X.items()})
        if fit is None: decomp[tk] = None; continue
        betas_by[tk] = {k: float(v) for k, v in fit.items()}
        out = {'betas': betas_by[tk], 'r2': r2, 'market_factor': mkt, 'windows': {}}
        for wk in WINDOWS:
            rr = r[r.index > window_start(wk)].dropna()
            if len(rr) < 5: out['windows'][wk] = None; continue
            contrib = {k: float(fit[k] * f.reindex(rr.index).fillna(0).sum()) for k, f in X.items()}
            fx_exact = float(fxpart[tk].reindex(rr.index).fillna(0).sum())
            tot_local = float(rr.sum())
            out['windows'][wk] = {'ret_cad': float(basert[tk].reindex(rr.index).dropna().sum()), 'ret_local': tot_local, 'market': contrib.get(mkt, 0.0), 'sector': contrib.get('sector', 0.0),
                                  'style': {s: contrib.get(s, 0.0) for s in STYLES}, 'fx': fx_exact + contrib.get('fx_beta', 0.0), 'idio': tot_local - sum(contrib.values()), 'days': int(len(rr))}
        decomp[tk] = out
    port_decomp = {}
    for wk in WINDOWS:
        agg = {'ret_cad': 0.0, 'market': 0.0, 'sector': 0.0, 'fx': 0.0, 'idio': 0.0, 'style': {s: 0.0 for s in STYLES}, 'covered': 0.0}
        for tk, w in W.items():
            dw = decomp[tk]['windows'].get(wk) if decomp.get(tk) else None
            if not dw: continue
            agg['covered'] += float(w)
            for k in ['ret_cad', 'market', 'sector', 'fx', 'idio']: agg[k] += float(w * dw[k])
            for s in STYLES: agg['style'][s] += float(w * dw['style'][s])
        port_decomp[wk] = agg

    # ---- book series and ratios
    BR = pd.DataFrame(basert)
    def port_series(start):
        sub = BR.loc[BR.index >= start]; sub = sub[sub.notna().all(axis=1)]
        ww = W.reindex(sub.columns)
        return (sub * ww).sum(axis=1) / ww.sum()
    def ratios(pr, bench):
        if pr is None or len(pr) < 60: return None
        rf = RF.reindex(pr.index).fillna(0); exr = pr - rf
        ann = float((1 + pr).prod() ** (252 / len(pr)) - 1); vol = float(pr.std() * math.sqrt(252)); dn = pr[pr < 0].std() * math.sqrt(252)
        cum = (1 + pr).cumprod(); dd = cum / cum.cummax() - 1
        cov = pd.concat([pr, bench.reindex(pr.index)], axis=1).dropna()
        beta = float(cov.iloc[:, 0].cov(cov.iloc[:, 1]) / cov.iloc[:, 1].var()) if len(cov) > 30 else None
        up = cov[cov.iloc[:, 1] > 0]; down = cov[cov.iloc[:, 1] < 0]
        return {'days': int(len(pr)), 'start': str(pr.index[0].date()), 'ann_return': ann, 'ann_vol': vol, 'sharpe': float(exr.mean() / pr.std() * math.sqrt(252)) if pr.std() > 0 else None,
                'sortino': float(exr.mean() * 252 / dn) if dn and dn > 0 else None, 'max_drawdown': float(dd.min()), 'max_dd_date': str(dd.idxmin().date()), 'beta_spy': beta,
                'corr_spy': float(cov.iloc[:, 0].corr(cov.iloc[:, 1])) if len(cov) > 30 else None, 'up_capture': float(up.iloc[:, 0].mean() / up.iloc[:, 1].mean()) if len(up) > 10 else None,
                'down_capture': float(down.iloc[:, 0].mean() / down.iloc[:, 1].mean()) if len(down) > 10 else None, 'best_day': float(pr.max()), 'worst_day': float(pr.min()), 'hit_rate': float((pr > 0).mean()), 'cum_return': float(cum.iloc[-1] - 1)}
    _, us_fx = fx_to_base('USD'); _, ca_fx = fx_to_base('CAD')
    spy_base = R['SPY'] if us_fx is None else (1 + R['SPY']) * (1 + us_fx) - 1
    tsx_base = R['XIU.TO'] if ca_fx is None else (1 + R['XIU.TO']) * (1 + ca_fx) - 1
    stats = {}
    for key, start in [('1y', last - pd.Timedelta(days=365)), ('common', R.index[0])]:
        pr = port_series(start); st = ratios(pr, R['SPY'])
        if st:
            st['vs_spy_cad'] = ratios(spy_base.reindex(pr.index).dropna(), R['SPY']); st['vs_tsx'] = ratios(tsx_base.reindex(pr.index).dropna(), R['SPY'])
        stats[key] = st
    pr1 = port_series(last - pd.Timedelta(days=365))
    if len(pr1) < 20: pr1 = port_series(R.index[0])
    cum1 = (1 + pr1).cumprod() * 100
    series = {'dates': [d.strftime('%Y-%m-%d') for d in cum1.index], 'portfolio': [round(float(v), 2) for v in cum1.values],
              'spy_cad': [round(float(v), 2) for v in ((1 + spy_base.reindex(pr1.index).fillna(0)).cumprod() * 100).values], 'tsx': [round(float(v), 2) for v in ((1 + tsx_base.reindex(pr1.index).fillna(0)).cumprod() * 100).values]}
    inst_stats = {tk: ratios(basert[tk][basert[tk].index >= last - pd.Timedelta(days=365)].dropna(), R['SPY']) for tk in W.index}

    pr_fit = port_series(last - pd.Timedelta(days=400)); theme_beta = {}
    for th, etf in THEME_ETF.items():
        if etf not in R.columns or len(pr_fit) < 120: continue
        fit, _, _ = _regress(pr_fit.iloc[-LOOK:], {'mkt': R['SPY'].iloc[-LOOK:], 'theme': (R[etf] - R['SPY']).iloc[-LOOK:]})
        if fit: theme_beta[th] = float(fit['theme'])
    theme_rows = sorted([{'id': k, 'name': THEME_NAME[k], 'direct': float(theme_exp.get(k, 0.0)), 'beta': theme_beta.get(k), 'etf': THEME_ETF.get(k)} for k in THEMES], key=lambda r: -r['direct'])
    for k, v in theme_exp.items():
        if k not in THEMES: theme_rows.append({'id': k, 'name': k.replace('_', ' ').title(), 'direct': float(v), 'beta': None, 'etf': None})
    theme_rows.sort(key=lambda r: -r['direct'])

    # ---- expected moves from options
    def implied_block(tk):
        import yfinance as yf
        y = by.loc[tk, 'yahoo']; spot = float(PX[y].dropna().iloc[-1]); out = {'spot': spot, 'has_options': False, 'earnings': None}
        rv = float(basert[tk].dropna().iloc[-63:].std() * math.sqrt(252)); out['realised_vol_3m'] = rv
        if with_options:
            try:
                t = yf.Ticker(y); exps = list(t.options)
            except Exception: t = None; exps = []
            if t is not None and inst[tk]['kind'] == 'stock':
                try:
                    cal = t.calendar; ed = cal.get('Earnings Date') if isinstance(cal, dict) else None
                    out['earnings'] = str(ed[0]) if ed else None
                except Exception: pass
            today = pd.Timestamp(asof)
            def straddle(exp):
                ch = t.option_chain(exp); c, p = ch.calls, ch.puts
                if c.empty or p.empty: return None
                mid = lambda o: (o.bid + o.ask) / 2 if o.bid > 0 and o.ask > 0 else o.lastPrice
                common = sorted(set(c.strike) & set(p.strike), key=lambda x: abs(x - spot))
                if common and abs(common[0] - spot) / spot <= 0.04:
                    k = common[0]; cc = c[c.strike == k].iloc[0]; pp_ = p[p.strike == k].iloc[0]
                    price = mid(cc) + mid(pp_); iv = np.nanmean([cc.impliedVolatility, pp_.impliedVolatility])
                else:   # sparse chain: build the straddle from the option nearest the money via put-call parity
                    cc = c.iloc[(c.strike - spot).abs().argsort()[:1]].iloc[0]; pp_ = p.iloc[(p.strike - spot).abs().argsort()[:1]].iloc[0]
                    if abs(cc.strike - spot) <= abs(pp_.strike - spot): k = cc.strike; price = 2 * mid(cc) - (spot - k); iv = cc.impliedVolatility
                    else: k = pp_.strike; price = 2 * mid(pp_) + (spot - k); iv = pp_.impliedVolatility
                if not (price > 0) or not (iv > 0.01): return None
                return {'expiry': exp, 'days': int((pd.Timestamp(exp) - today).days), 'straddle': float(price), 'iv': float(iv)}
            if exps:
                valid = [e for e in exps if (pd.Timestamp(e) - today).days >= 1]; near = [e for e in valid if (pd.Timestamp(e) - today).days >= 20]
                try:
                    m = straddle(near[0] if near else valid[-1]) if valid else None
                    if m: out.update({'has_options': True, 'expiry': m['expiry'], 'days_to_expiry': m['days'], 'implied_move_to_expiry': m['straddle'] / spot, 'iv': m['iv'], 'sigma_1m': m['iv'] * math.sqrt(21 / 252)})
                    if m and out.get('earnings'):
                        ed = pd.Timestamp(out['earnings']); before = [e for e in valid if pd.Timestamp(e) < ed]; after = [e for e in valid if pd.Timestamp(e) >= ed]
                        if ed >= today and after and (pd.Timestamp(after[0]) - today).days <= 60:
                            a = straddle(after[0]); b = straddle(before[-1]) if before else None
                            if a and b and a['days'] > b['days']:
                                va = a['iv'] ** 2 * a['days'] / 365; vb = b['iv'] ** 2 * b['days'] / 365
                                if va > vb: out['earnings_move'] = math.sqrt(va - vb); out['earnings_expiry'] = a['expiry']
                            elif a: out['earnings_move'] = a['straddle'] / spot * 0.8; out['earnings_expiry'] = a['expiry']
                except Exception as e:
                    out['error'] = str(e)[:120]
        if not out['has_options']: out['sigma_1m'] = rv * math.sqrt(21 / 252); out['basis'] = 'realised vol (no listed options)'
        else: out['basis'] = 'ATM options'
        return out
    with cf.ThreadPoolExecutor(max_workers=4) as ex: moves = dict(zip(W.index, ex.map(implied_block, W.index)))

    C = BR.iloc[-LOOK:].dropna(how='all'); corr = C.corr().fillna(0)
    sig = pd.Series({tk: moves[tk]['sigma_1m'] for tk in W.index}).fillna(0)
    cov = corr.loc[W.index, W.index].values * np.outer(sig.values, sig.values); w = W.values
    port_sigma = float(math.sqrt(max(w @ cov @ w, 0)))
    contrib = (w * (cov @ w) / port_sigma ** 2) if port_sigma > 0 else w * 0
    risk_contrib = sorted([{'ticker': tk, 'weight': float(W[tk]), 'sigma_1m': float(sig[tk]), 'share_of_variance': float(contrib[i])} for i, tk in enumerate(W.index)], key=lambda r: -r['share_of_variance'])

    # ---- scenarios: past regime shifts replayed on today's holdings
    # A holding that traded through the episode uses its own return; one that did not exist yet uses a stand-in
    # (its sector ETF, a fund's sector mix, or its home market), marked as a proxy.
    sector_etfs = sorted(set(SECTOR_ETF.values()))
    PL = get_prices_long(sorted(set(by.yahoo) | set(sector_etfs) | {'SPY', 'XIU.TO', 'CAD=X', '^TNX'}))
    def ep_ret(col, d0, d1):
        if col not in PL.columns: return None
        ser = PL[col].dropna()
        if ser.empty or ser.index[0] > d0: return None
        p0 = ser[ser.index <= d0]; p1 = ser[ser.index <= d1]
        return float(p1.iloc[-1] / p0.iloc[-1] - 1) if len(p0) and len(p1) else None
    def proxy_ret(tk, d0, d1):
        i = inst[tk]; mkt = ep_ret('XIU.TO' if i.get('home') == 'CA' else 'SPY', d0, d1)
        if i['kind'] != 'stock' and i.get('sector_weights'):
            tot = 0.0; wsum = 0.0
            for sct, wt in i['sector_weights'].items():
                r = ep_ret(SECTOR_ETF.get(sct), d0, d1)
                if r is None: r = mkt
                if r is not None: tot += wt * r; wsum += wt
            if wsum > 0: return tot / wsum
        r = ep_ret(SECTOR_ETF.get(i.get('sector')), d0, d1) if i.get('sector') else None
        return r if r is not None else mkt
    scen_rows = []
    for group, name, s0, s1, what in EPISODES:
        d0 = pd.Timestamp(s0); ongoing = False
        if s1: d1 = pd.Timestamp(s1)
        else:   # an episode still unfolding: from its start to the latest close, capped at 30 days
            d1 = min(PL.index[-1], d0 + pd.Timedelta(days=30)); ongoing = d1 < d0 + pd.Timedelta(days=30); s1 = str(d1.date())
        if d1 <= d0: continue
        fx_usd = ep_ret('CAD=X', d0, d1) or 0.0
        parts = []; proxy_w = 0.0; missing_w = 0.0
        for tk, wt in W.items():
            r = ep_ret(by.loc[tk, 'yahoo'], d0, d1); is_proxy = False
            if r is None: r = proxy_ret(tk, d0, d1); is_proxy = True
            if r is None: missing_w += float(wt); continue
            cur = by.loc[tk, 'currency']
            if cur != base_ccy and not inst[tk].get('fx_hedged'):
                fxr = fx_usd if (cur == 'USD' and base_ccy == 'CAD') else ((1 / (1 + fx_usd) - 1) if (cur == 'CAD' and base_ccy == 'USD') else 0.0)
                r = (1 + r) * (1 + fxr) - 1
            if is_proxy: proxy_w += float(wt)
            parts.append((tk, float(wt * r), float(r), is_proxy))
        tot = sum(p[1] for p in parts); parts.sort(key=lambda x: abs(x[1]), reverse=True)
        tnx0 = PL['^TNX'].dropna() if '^TNX' in PL.columns else pd.Series(dtype=float)
        y_bp = None
        if len(tnx0[tnx0.index <= d0]) and len(tnx0[tnx0.index <= d1]): y_bp = float((tnx0[tnx0.index <= d1].iloc[-1] - tnx0[tnx0.index <= d0].iloc[-1]) * 100)
        spx = ep_ret('SPY', d0, d1)
        scen_rows.append({'group': group, 'name': name, 'start': s0, 'end': s1, 'ongoing': ongoing, 'what': what, 'spx': spx, 'spx_base': None if spx is None else ((1 + spx) * (1 + fx_usd) - 1 if base_ccy == 'CAD' else spx), 'tsx': ep_ret('XIU.TO', d0, d1), 'y10_bp': y_bp,
                          'portfolio_pct': tot, 'equity_pct': tot * leverage, 'portfolio_base': tot * total, 'proxy_weight': proxy_w, 'missing_weight': missing_w,
                          'drivers': [{'ticker': tk, 'pct': v, 'own_return': r, 'proxy': px} for tk, v, r, px in parts[:4] if abs(v) > 0.0005]})

    today_d = dt.date.fromisoformat(asof)
    def held(d):
        try: return (today_d - dt.date.fromisoformat(str(d)[:10])).days
        except Exception: return None
    acc_type = lambda a: (accounts_in.get(a) or {}).get('type') or a
    full = {
        'asof': asof, 'generated': dt.datetime.now().strftime('%Y-%m-%d %H:%M'), 'label': holdings.get('label') or 'Portfolio', 'base_currency': base_ccy, 'usdcad': usdcad, 'total_base': total,
        'net_equity_base': net_equity, 'leverage': leverage, 'margin_debit_base': float(-acct.cash_or_debit_base[acct.cash_or_debit_base < 0].sum()), 'cash_base': float(acct.cash_or_debit_base[acct.cash_or_debit_base > 0].sum()),
        'accounts': [{'id': r.account, 'type': acc_type(r.account), 'value_base': float(r.value_base), 'net_base': float(r.net_base), 'cash_or_debit_base': float(r.cash_or_debit_base), 'weight': float(r.weight), 'leverage': float(r.value_base / r.net_base) if r.net_base else None} for r in acct.itertuples()],
        'unmodelled': [{'ticker': u['ticker'], 'name': u.get('name'), 'account': acc_type(u['account']), 'unmodelled_base': u.get('value_base')} for u in unmodelled],
        'positions': [{'ticker': r.ticker, 'name': r.name, 'account': r.account, 'account_type': acc_type(r.account), 'currency': r.currency, 'qty': float(r.qty), 'price': r.price, 'value': r.value, 'value_base': float(r.value_base),
                       'cost_base': r.cost_base, 'pnl_base': None if r.cost_base is None or pd.isna(r.cost_base) else float(r.value_base - r.cost_base), 'pnl_pct': None if not r.cost_base or pd.isna(r.cost_base) else float((r.value_base - r.cost_base) / r.cost_base),
                       'weight': float(r.weight), 'first_buy': r.first_buy, 'held_days': held(r.first_buy) if r.first_buy else None} for r in pos.itertuples()],
        'instruments': [{'ticker': tk, 'name': r['name'], 'yahoo': r.yahoo, 'currency': r.currency, 'kind': inst[tk]['kind'], 'fx_hedged': bool(inst[tk].get('fx_hedged')), 'home': inst[tk].get('home'),
                         'sector': inst[tk].get('sector'), 'sector_name': SECTOR_NAME.get(inst[tk].get('sector')), 'subindustry': inst[tk].get('subindustry'), 'sub_name': SUB_NAME.get(inst[tk].get('subindustry')) or inst[tk].get('custom_sub'), 'note': inst[tk].get('note'),
                         'qty': float(r.qty), 'value_base': float(r.value_base), 'cost_base': r.cost_base, 'pnl_base': r.pnl_base, 'pnl_pct': r.pnl_pct, 'weight': float(r.weight), 'first_buy': r.first_buy, 'held_days': held(r.first_buy) if r.first_buy else None,
                         'lookthrough': inst[tk].get('lookthrough'), 'decomp': decomp.get(tk), 'stats_1y': inst_stats.get(tk), 'moves': moves.get(tk)} for tk, r in by.sort_values('weight', ascending=False).iterrows()],
        'exposure': {'subindustries': sub_rows, 'sectors': sec_rows, 'diversified_unclassified': float(diversified), 'themes': theme_rows, 'top_names': top_names},
        'decomp': port_decomp, 'stats': stats, 'series': series,
        'risk': {'sigma_1m': port_sigma, 'sigma_1m_base': port_sigma * total, 'sigma_1m_on_equity': port_sigma * leverage, 'contrib': risk_contrib, 'scenarios': scen_rows},
        'method': {'lookback_days': LOOK, 'factors': 'market (S&P 500 for US names, TSX 60 for Canadian; funds use whichever fits better), sector ETF minus S&P 500, growth minus value, small minus large, momentum, quality, low-vol ETF spreads, USDCAD; the residual is idiosyncratic',
                   'weights': "today's weights held constant (rebalanced daily) for the book series", 'scenarios': "each scenario is a real past episode replayed on today's holdings: every holding's actual return over those dates, weighted by today's weights; holdings that did not trade then use their sector ETF (or a fund's sector mix) as a stand-in",
                   'options': 'Yahoo Finance delayed chains; ATM straddle at the first expiry at least 20 days out; implied earnings move from the variance difference between the expiries around the print',
                   'lookthrough': "funds are split by Yahoo's sector weights and top-ten holdings; sub-industry and theme exposure through funds therefore counts only their ten largest names"},
    }
    return _clean(full)
