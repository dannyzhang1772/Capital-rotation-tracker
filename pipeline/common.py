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
