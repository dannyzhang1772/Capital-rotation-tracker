"""Shares outstanding for every basket ticker.

Sources:
  --yahoo  (default) Yahoo Finance shares history (Ticker.get_shares_full, typically from late 2015)
           plus current implied shares (market_cap / price from fast_info, which handles multi-class names).
  --sec    SEC XBRL frames API (dei:EntityCommonStockSharesOutstanding, 2009+). The SEC requires a
           User-Agent containing a contact e-mail; set env var CRT_SEC_CONTACT="Name email@domain" to enable.

Outputs: data/raw/shares_history.csv (ticker, date, shares, source)
         data/raw/shares_current.csv  (ticker, shares_implied, market_cap, price, shares_reported, asof)
"""
import sys, os, time, json, datetime as dt, concurrent.futures as cf
import pandas as pd, requests
from common import load_taxonomy, basket_tickers, RAW

HIST = os.path.join(RAW, 'shares_history.csv'); CUR = os.path.join(RAW, 'shares_current.csv')
CIKMAP = os.path.join(RAW, 'sec_company_tickers.json')
SEC_CONTACT = os.environ.get('CRT_SEC_CONTACT')
UA = {'User-Agent': f'CapitalRotationTracker/1.0 {SEC_CONTACT}' if SEC_CONTACT else 'CapitalRotationTracker/1.0 (research project)',
      'Accept-Encoding': 'gzip, deflate'}

# ---------------- Yahoo ----------------
def yahoo_history(tickers):
    import yfinance as yf
    def one(t):
        for attempt in range(2):
            try:
                s = yf.Ticker(t).get_shares_full(start='2000-01-01')
                if s is None or len(s) == 0: return (t, None)
                s = s[~s.index.duplicated(keep='last')]
                s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
                return (t, s)
            except Exception:
                time.sleep(1.5)
        return (t, None)
    rows = []; got = 0
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        for i, (t, s) in enumerate(ex.map(one, tickers)):
            if s is not None:
                got += 1
                for d, v in s.items():
                    if v and v > 0: rows.append((t, d, float(v), 'yahoo'))
            if i % 100 == 0: print(f"  yahoo history {i}/{len(tickers)} ({got} ok)", flush=True)
    df = pd.DataFrame(rows, columns=['ticker', 'date', 'shares', 'source'])
    if os.path.exists(HIST):  # keep any SEC rows already present
        old = pd.read_csv(HIST, parse_dates=['date'])
        old = old[old.source != 'yahoo'] if 'source' in old.columns else old.iloc[0:0]
        df = pd.concat([old, df])
    df = df.sort_values(['ticker', 'date']).drop_duplicates(['ticker', 'date', 'source'], keep='last')
    df.to_csv(HIST, index=False)
    print(f"saved {len(df)} rows for {df.ticker.nunique()} tickers -> {HIST}")

def yahoo_current(tickers):
    import yfinance as yf
    def one(t):
        for attempt in range(2):
            try:
                fi = yf.Ticker(t).fast_info
                mc = fi.get('market_cap'); px = fi.get('last_price'); sh = fi.get('shares')
                if mc and px: return (t, mc / px, mc, px, sh)
                if sh and px: return (t, sh, sh * px, px, sh)
                return (t, None, None, px, sh)
            except Exception:
                time.sleep(1.5)
        return (t, None, None, None, None)
    out = []
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        for i, r in enumerate(ex.map(one, tickers)):
            out.append(r)
            if i % 100 == 0: print(f"  yahoo current {i}/{len(tickers)}", flush=True)
    df = pd.DataFrame(out, columns=['ticker', 'shares_implied', 'market_cap', 'price', 'shares_reported'])
    df['asof'] = dt.date.today().isoformat(); df.to_csv(CUR, index=False)
    print(f"saved current shares for {df.shares_implied.notna().sum()}/{len(df)} tickers -> {CUR}")

# ---------------- SEC (optional) ----------------
def sec_cik_map(tickers):
    if not os.path.exists(CIKMAP):
        r = requests.get('https://www.sec.gov/files/company_tickers.json', headers=UA, timeout=60); r.raise_for_status()
        open(CIKMAP, 'w').write(r.text)
    d = json.load(open(CIKMAP)); m = {v['ticker'].upper(): int(v['cik_str']) for v in d.values()}
    return {t: m[t.replace('.', '-').upper()] for t in tickers if t.replace('.', '-').upper() in m}

def sec_frames(tickers):
    if not SEC_CONTACT:
        print('SEC fetch skipped: set CRT_SEC_CONTACT="Name email@domain" (SEC fair-access policy requires a contact).'); return
    cmap = sec_cik_map(tickers); cik2t = {}
    for t, c in cmap.items(): cik2t.setdefault(c, t)
    print(f"SEC CIK map: {len(cmap)}/{len(tickers)} tickers mapped", flush=True)
    rows = []
    for y in range(2009, dt.date.today().year + 1):
        for q in (1, 2, 3, 4):
            url = f"https://data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/CY{y}Q{q}I.json"
            for attempt in range(3):
                try:
                    r = requests.get(url, headers=UA, timeout=60)
                    if r.status_code == 404: break
                    r.raise_for_status()
                    for f in r.json().get('data', []):
                        t = cik2t.get(f['cik'])
                        if t: rows.append((t, f['end'], f['val'], 'sec'))
                    break
                except Exception as e:
                    print('retry', url, e); time.sleep(2)
            time.sleep(0.15)
    df = pd.DataFrame(rows, columns=['ticker', 'date', 'shares', 'source']); df['date'] = pd.to_datetime(df['date'])
    if os.path.exists(HIST):
        old = pd.read_csv(HIST, parse_dates=['date']); df = pd.concat([old[old.source != 'sec'], df])
    df = df[df.shares > 0].sort_values(['ticker', 'date']).drop_duplicates(['ticker', 'date', 'source'], keep='last')
    df.to_csv(HIST, index=False); print(f"saved {len(df)} rows -> {HIST}")

if __name__ == '__main__':
    t = load_taxonomy(); tickers = basket_tickers(t)
    args = sys.argv[1:] or ['--yahoo']
    if '--yahoo' in args: yahoo_history(tickers); yahoo_current(tickers)
    if '--sec' in args: sec_frames(tickers)
