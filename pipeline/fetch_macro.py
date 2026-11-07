"""Macro context series. Tries FRED (fredgraph CSV, no key) with a short timeout; falls back to Yahoo Finance
for the series Yahoo carries (10y/2y/13w yields, VIX, WTI, dollar index). Output: data/raw/macro.csv
"""
import os, io, time, pandas as pd, requests
from common import RAW

FRED = {'DFF': 'fed_funds', 'DGS10': 'ust10y', 'DGS2': 'ust2y', 'DFII10': 'real10y', 'T10YIE': 'breakeven10y',
        'UNRATE': 'unemployment', 'VIXCLS': 'vix', 'DCOILWTICO': 'wti', 'DTWEXBGS': 'dollar_broad', 'BAMLH0A0HYM2': 'hy_oas',
        'MORTGAGE30US': 'mortgage30y', 'WALCL': 'fed_balance_sheet'}
YAHOO = {'^TNX': ('ust10y', 1.0), '^FVX': ('ust5y', 1.0), '^IRX': ('ust13w', 1.0), '^VIX': ('vix', 1.0), 'CL=F': ('wti', 1.0), 'DX-Y.NYB': ('dollar_dxy', 1.0)}
OUT = os.path.join(RAW, 'macro.csv')

def fred():
    out = {}
    for sid, name in FRED.items():
        try:
            r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
            r.raise_for_status(); df = pd.read_csv(io.StringIO(r.text)); df.columns = ['date', name]
            df['date'] = pd.to_datetime(df['date']); df[name] = pd.to_numeric(df[name], errors='coerce')
            out[name] = df.set_index('date')[name]; print('FRED ok', sid, flush=True)
        except Exception as e:
            print('FRED failed', sid, type(e).__name__, flush=True)
        time.sleep(0.3)
    return out

def yahoo():
    import yfinance as yf
    out = {}
    try:
        df = yf.download(list(YAHOO), start='1998-01-01', auto_adjust=False, progress=False, group_by='column')['Close']
        df.index = pd.to_datetime(df.index).tz_localize(None)
        for t, (name, k) in YAHOO.items():
            if t in df.columns: out[name] = df[t] * k
        print('Yahoo ok', list(out), flush=True)
    except Exception as e:
        print('Yahoo failed', e)
    return out

if __name__ == '__main__':
    series = fred()
    y = yahoo()
    for k, v in y.items():
        if k not in series: series[k] = v
    old = pd.read_csv(OUT, parse_dates=['date']).set_index('date') if os.path.exists(OUT) else None
    m = pd.DataFrame(series).sort_index()
    if old is not None: m = m.combine_first(old)
    m = m[m.index >= '1998-01-01']; m.index.name = 'date'; m.to_csv(OUT)
    print('macro saved', m.shape, 'columns', list(m.columns), 'last', m.index.max().date())
