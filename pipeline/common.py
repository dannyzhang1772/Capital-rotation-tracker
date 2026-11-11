"""Shared helpers for the Capital Rotation Tracker pipeline."""
import json, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAX_PATH = os.path.join(ROOT, 'pipeline', 'taxonomy.json')
RAW = os.path.join(ROOT, 'data', 'raw')
PROC = os.path.join(ROOT, 'data', 'processed')
SITE_DATA = os.path.join(ROOT, 'site', 'data')
for d in (RAW, PROC, SITE_DATA):
    os.makedirs(d, exist_ok=True)

def load_taxonomy():
    with open(TAX_PATH, encoding='utf-8') as f:
        return json.load(f)

def all_sectors(t):
    return t['sectors'] + t['context_sectors']

def etf_tickers(t):
    s = set()
    for sec in all_sectors(t):
        s.add(sec['sector_etf']); s.update(sec.get('alt_etfs', []))
        if sec.get('sector_history_etf'): s.add(sec['sector_history_etf'])
        for si in sec['subindustries']:
            for k in ('etf', 'reference_etf'):
                if si.get(k): s.add(si[k])
            s.update(si.get('alt_etfs', []))
    for th in t['themes']:
        if th.get('etf'): s.add(th['etf'])
        s.update(th.get('alt_etfs', []))
    return sorted(s)

def basket_tickers(t):
    s = set()
    for sec in all_sectors(t):
        for si in sec['subindustries']:
            s.update(si['basket'])
    for th in t['themes']:
        s.update(th['basket'])
    return sorted(s)

def benchmark_tickers(t):
    return sorted(set(t['meta']['benchmarks'].values()))

def universe(t):
    return sorted(set(etf_tickers(t)) | set(basket_tickers(t)) | set(benchmark_tickers(t)))


def clean_prices(P, min_days=60, log=print):
    """Shared price hygiene: drop near-empty series, blank single-day spikes that fully revert (bad prints),
    and drop series that are corrupt end to end (many >50% days plus a >10x swing inside five days)."""
    import numpy as np
    P = P.where(P > 0)
    thin = [c for c in P.columns if P[c].notna().sum() < min_days]
    if thin: P = P.drop(columns=thin)
    R = P.pct_change(fill_method=None)
    big = (R.abs() > 0.5).sum()
    swing = (P.rolling(5).max() / P.rolling(5).min()).max()
    corrupt = [c for c in P.columns if big[c] >= 10 and swing[c] > 10]
    if corrupt: P = P.drop(columns=corrupt)
    # spike that reverts next day: P_t/P_{t-1} outside [0.5, 2] while P_{t+1}/P_{t-1} inside [0.67, 1.5]
    prev = P.shift(1); nxt = P.shift(-1)
    spike = ((P / prev > 2) | (P / prev < 0.5)) & ((nxt / prev >= 0.67) & (nxt / prev <= 1.5))
    n_spike = int(spike.sum().sum())
    P = P.mask(spike)
    log(f"clean_prices: dropped {len(thin)} thin series {thin[:12]}{'...' if len(thin) > 12 else ''}; dropped corrupt {corrupt}; blanked {n_spike} reverting spikes")
    return P
