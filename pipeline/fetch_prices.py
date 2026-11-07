"""Download daily adjusted close + volume for the whole universe from Yahoo Finance.
Full back-fill from 1998-12-01 on first run; incremental (last 10 days) afterwards.
Output: data/raw/prices.parquet (wide, columns=tickers) and data/raw/volume.parquet.
"""
import sys, time, os, datetime as dt
import pandas as pd, numpy as np, yfinance as yf
from common import load_taxonomy, universe, RAW

START = '1998-12-01'
PRICES = os.path.join(RAW, 'prices.parquet')
VOLUME = os.path.join(RAW, 'volume.parquet')
LOG = os.path.join(RAW, 'fetch_prices.log')

def log(msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f: f.write(line + '\n')

def download(tickers, start, batch=80):
    closes, vols, failed = [], [], []
    for i in range(0, len(tickers), batch):
        chunk = tickers[i:i+batch]
        for attempt in range(3):
            try:
                df = yf.download(chunk, start=start, auto_adjust=True, progress=False,
                                 threads=True, group_by='column', actions=False)
                break
            except Exception as e:
                log(f"batch {i} attempt {attempt} failed: {e}"); time.sleep(5 * (attempt + 1)); df = None
        if df is None or df.empty:
            failed.extend(chunk); continue
        c = df['Close'] if 'Close' in df.columns.get_level_values(0) else df
        v = df['Volume'] if 'Volume' in df.columns.get_level_values(0) else None
        if isinstance(c, pd.Series): c = c.to_frame(chunk[0])
        got = [t for t in chunk if t in c.columns and c[t].notna().any()]
        failed.extend([t for t in chunk if t not in got])
        closes.append(c[got]); 
        if v is not None: vols.append(v[got])
        log(f"batch {i//batch+1}/{(len(tickers)+batch-1)//batch}: {len(got)}/{len(chunk)} ok")
        time.sleep(1.0)
    C = pd.concat(closes, axis=1) if closes else pd.DataFrame()
    V = pd.concat(vols, axis=1) if vols else pd.DataFrame()
    return C, V, failed

def main(full=False):
    t = load_taxonomy(); tickers = universe(t)
    log(f"universe: {len(tickers)} tickers")
    if os.path.exists(PRICES) and not full:
        old = pd.read_parquet(PRICES); oldv = pd.read_parquet(VOLUME)
        start = (old.index.max() - pd.Timedelta(days=10)).strftime('%Y-%m-%d')
        new_tickers = [x for x in tickers if x not in old.columns]
        C, V, failed = download(tickers, start)
        if new_tickers:
            log(f"new tickers (full history): {new_tickers}")
            C2, V2, f2 = download(new_tickers, START); failed += f2
            C = C.drop(columns=[c for c in C2.columns if c in C.columns]).join(C2, how='outer')
            V = V.drop(columns=[c for c in V2.columns if c in V.columns]).join(V2, how='outer')
        # new values override old where present; old data is kept for tickers whose refresh failed
        C = C.combine_first(old).sort_index()
        V = V.combine_first(oldv).sort_index()
    else:
        C, V, failed = download(tickers, START)
    C.index = pd.to_datetime(C.index).tz_localize(None); V.index = pd.to_datetime(V.index).tz_localize(None)
    C = C.sort_index(); V = V.sort_index()
    C.to_parquet(PRICES); V.to_parquet(VOLUME)
    pd.Series(sorted(set(failed))).to_csv(os.path.join(RAW, 'prices_failed.csv'), index=False, header=['ticker'])
    log(f"saved {C.shape[0]} days x {C.shape[1]} tickers; last date {C.index.max().date()}; failed: {len(set(failed))}")

if __name__ == '__main__':
    main(full='--full' in sys.argv)
