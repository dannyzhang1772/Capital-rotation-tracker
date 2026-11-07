#!/usr/bin/env python
"""
fetch_etf_flows.py
==================

Builds the longest available DAILY history of NAV, shares outstanding and
AUM for every ETF referenced in pipeline/taxonomy.json, computes daily net
creation/redemption flow, and writes

    data/raw/etf_flows.csv            long format:
                                      date, ticker, nav, shares_outstanding,
                                      aum_usd, flow_usd, source
    data/raw/etf_flows_coverage.csv   one row per ETF:
                                      ticker, issuer, source_url_or_method,
                                      first_date, last_date, n_rows, status, notes

flow_usd = (shares_t - shares_{t-1}) * nav_t   (consecutive available NAV dates)

Data sources (all free issuer downloads; discovered 2026-11-06)
--------------------------------------------------------------
SPDR / State Street  https://www.ssga.com/library-content/products/fund-data/etfs/us/navhist-us-en-<ticker>.xlsx
                     columns Date, NAV, Shares Outstanding, Total Net Assets
iShares              product screener JSON -> portfolioId, then
                     https://www.blackrock.com/varnish-api/blk-one01-product-data/product-data/api/v1/get-fund-document
                       ?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=us-ishares&locale=en_US
                       &portfolioId=<id>&component=fundDownload&userType=individual
                     (SpreadsheetML workbook; the "Historical" sheet has As Of, NAV per Share,
                     Shares Outstanding).  AUM is derived = NAV * shares.
VanEck               https://www.vaneck.com/us/en/investments/<slug>/downloads/fundhistoprices/
                     (xlsx with Date, NAV, ..., AUM).  Shares derived = AUM / NAV.
Invesco              https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/<ticker>/navs?idType=ticker&productType=ETF
                     (JSON with effectiveDate, netAssetValue, totalNetAssets; history starts 2016-09-29).
                     Shares derived = totalNetAssets / NAV.
First Trust          https://www.ftportfolios.com/Retail/Etf/EtfPriceHistory.aspx?Ticker=<ticker>
                     ASP.NET "Download" postback with a date range -> xlsx with Date, Market Price,
                     Net Asset Value, Bid/Ask Midpoint, Volume, Net Assets.  Shares derived = Net Assets / NAV.
Vanguard, Global X, Pacer, ALPS, Amplify: no downloadable daily NAV + shares/TNA history was found
                     on the issuer sites (see coverage notes).  Nothing is fabricated for them.

Caching
-------
Issuer downloads are cached in data/raw/etf_issuer/<TICKER>.<ext>.  For issuers whose raw
download is very large (iShares ~3 MB SpreadsheetML, Invesco ~2 MB JSON) only the extracted
historical table is cached as <TICKER>.csv so the OneDrive folder stays small.  A cached file
younger than --max-age-hours (default 20) is reused without any network call, so re-runs are
fast and idempotent; --refresh forces re-download.

Usage
-----
    python pipeline/fetch_etf_flows.py                # incremental run
    python pipeline/fetch_etf_flows.py --refresh      # re-download everything
    python pipeline/fetch_etf_flows.py --only XLK XBI # subset (outputs still rebuilt from cache for the rest)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
import requests

# --------------------------------------------------------------------------------------
# Paths / constants
# --------------------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = ROOT / "pipeline" / "taxonomy.json"
RAW_DIR = ROOT / "data" / "raw"
CACHE_DIR = RAW_DIR / "etf_issuer"
OUT_FLOWS = RAW_DIR / "etf_flows.csv"
OUT_COVERAGE = RAW_DIR / "etf_flows_coverage.csv"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
REQUEST_DELAY_S = 1.5          # polite delay between network requests
TIMEOUT_S = 90

SKIP_TICKERS = {"UUP", "USO", "GLD"}   # not equity ETFs (and not in the taxonomy keys anyway)

# ticker -> issuer.  RSPD/RSPS are Invesco (ex-Guggenheim equal-weight), not SPDR.
ISSUER_TICKERS = {
    "SSGA":        "XLI XLV XLK XLY XLP XLE XLRE XLF XLU XLB XLC XBI XOP XHB XRT XAR XES KBE KRE KIE XME XPH",
    "iShares":     "ITA IYT IHE IBB IHI IHF SOXX IGV IYZ IYR IYW IYJ IYH IYE REM REZ ICLN IAI ITB IWM IWF IWD MTUM QUAL USMV",
    "Vanguard":    "VTI VIS VHT VGT VCR VDC VDE VNQ",
    "VanEck":      "OIH SMH GDX CRAK EVX PPH NLR",
    "Invesco":     "PKB PEJ PBJ PBS PBW TAN RSP RSPD RSPS",
    "Global X":    "URA AIQ BOTZ PAVE BUG FINX",
    "First Trust": "FDN GRID SKYY CARZ CIBR",
    "ALPS":        "AMLP",
    "Pacer":       "SRVR INDS",
    "Amplify":     "HACK IPAY",
}
TICKER_ISSUER = {t: iss for iss, ts in ISSUER_TICKERS.items() for t in ts.split()}

SESSION = requests.Session()
SESSION.headers.update(BASE_HEADERS)

_last_request_ts = 0.0


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# --------------------------------------------------------------------------------------
# Generic helpers
# --------------------------------------------------------------------------------------
def _throttle() -> None:
    global _last_request_ts
    wait = REQUEST_DELAY_S - (time.time() - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.time()


def http_get(url: str, headers: dict | None = None, allow_redirects: bool = True) -> requests.Response:
    _throttle()
    r = SESSION.get(url, headers=headers or {}, timeout=TIMEOUT_S, allow_redirects=allow_redirects)
    r.raise_for_status()
    return r


def curl_get(url: str, headers: dict | None = None, retries: int = 3) -> bytes:
    """GET through the system curl binary (plain HTTP client, same browser-like UA).

    dng-api.invesco.com answers HTTP 406 to the Python requests/urllib clients but serves
    curl normally, so Invesco is fetched this way.  Retries with back-off on empty/non-200."""
    hdrs = dict(BASE_HEADERS)
    hdrs.update(headers or {})
    code = b""
    for attempt in range(1, retries + 1):
        _throttle()
        args = ["curl", "-s", "-L", "--max-time", str(TIMEOUT_S), "-A", USER_AGENT, "-w", "\n%{http_code}"]
        for k, v in hdrs.items():
            if k.lower() != "user-agent":
                args += ["-H", f"{k}: {v}"]
        args.append(url)
        out = subprocess.run(args, capture_output=True, timeout=TIMEOUT_S + 30)
        body, _, code = out.stdout.rpartition(b"\n")
        if out.returncode == 0 and code.strip() == b"200" and body.strip():
            return body
        log(f"    curl attempt {attempt} failed (rc={out.returncode}, http={code.strip()!r}, "
            f"{len(body)} bytes); backing off")
        time.sleep(5 * attempt)
    raise RuntimeError(f"curl failed after {retries} attempts (last http={code.strip()!r})")


def is_fresh(path: Path, max_age_hours: float, refresh: bool) -> bool:
    if refresh or not path.exists() or path.stat().st_size == 0:
        return False
    age_h = (time.time() - path.stat().st_mtime) / 3600.0
    return age_h < max_age_hours


def to_float(x) -> float:
    if x is None:
        return np.nan
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "").replace("$", "")
    if s in {"", "-", "--", "N/A", "n/a", "NA"}:
        return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


def xlsx_rows(path: Path) -> list[tuple]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return list(wb.worksheets[0].iter_rows(values_only=True))
    finally:
        wb.close()


def find_header_row(rows: list[tuple], first_cell: str = "Date") -> int:
    for i, row in enumerate(rows):
        if row and isinstance(row[0], str) and row[0].strip().lower() == first_cell.lower():
            return i
    raise ValueError(f"header row starting with {first_cell!r} not found")


def load_taxonomy_tickers() -> list[str]:
    tax = json.loads(TAXONOMY.read_text(encoding="utf-8"))
    found: set[str] = set()

    def grab(d: dict) -> None:
        for k in ("sector_etf", "sector_history_etf", "etf", "reference_etf"):
            if d.get(k):
                found.add(d[k])
        for e in d.get("alt_etfs") or []:
            found.add(e)

    for sec in tax.get("sectors", []) + tax.get("context_sectors", []):
        grab(sec)
        for si in sec.get("subindustries", []):
            grab(si)
    for th in tax.get("themes", []):
        grab(th)
        for si in th.get("subindustries", []):
            grab(si)
    return sorted(t for t in found if t not in SKIP_TICKERS)


# --------------------------------------------------------------------------------------
# Issuer fetchers.  Each returns (DataFrame[date, nav, shares_outstanding, aum_usd], source, notes)
# --------------------------------------------------------------------------------------
class NotAvailable(Exception):
    """Issuer does not publish a usable daily history download."""


# ---- SPDR / State Street ---------------------------------------------------------------
def fetch_ssga(ticker: str, max_age: float, refresh: bool):
    url = f"https://www.ssga.com/library-content/products/fund-data/etfs/us/navhist-us-en-{ticker.lower()}.xlsx"
    path = CACHE_DIR / f"{ticker}.xlsx"
    if not is_fresh(path, max_age, refresh):
        log(f"  {ticker}: downloading SSGA navhist")
        r = http_get(url)
        if "spreadsheet" not in r.headers.get("content-type", "") and not r.content.startswith(b"PK"):
            raise RuntimeError(f"unexpected content-type {r.headers.get('content-type')}")
        path.write_bytes(r.content)
    rows = xlsx_rows(path)
    h = find_header_row(rows)
    hdr = [str(c).strip() if c else "" for c in rows[h]]
    ci = {name: hdr.index(name) for name in ("Date", "NAV", "Shares Outstanding", "Total Net Assets")}
    recs = []
    for row in rows[h + 1:]:
        if not row or not row[0]:
            continue
        try:
            d = pd.to_datetime(str(row[ci["Date"]]).strip(), format="%d-%b-%Y")
        except (ValueError, TypeError):
            continue
        recs.append((d, to_float(row[ci["NAV"]]), to_float(row[ci["Shares Outstanding"]]),
                     to_float(row[ci["Total Net Assets"]])))
    df = pd.DataFrame(recs, columns=["date", "nav", "shares_outstanding", "aum_usd"])
    notes = "issuer file: NAV, shares outstanding and total net assets reported directly"
    # SSGA's file has rare one-day glitches (e.g. 2014-10-07) where the shares column disagrees
    # with the reported TNA/NAV; repair those rows from TNA so they do not create fake flows.
    ratio = df["nav"] * df["shares_outstanding"] / df["aum_usd"]
    bad = ratio.notna() & ((ratio - 1).abs() > 0.05)
    if bad.any():
        df.loc[bad, "shares_outstanding"] = df.loc[bad, "aum_usd"] / df.loc[bad, "nav"]
        days = ", ".join(sorted(d.strftime("%Y-%m-%d") for d in df.loc[bad, "date"]))
        notes += f"; {int(bad.sum())} row(s) with shares inconsistent with TNA/NAV (>5%) replaced by TNA/NAV: {days}"
    return df, url, notes


# ---- iShares ---------------------------------------------------------------------------
ISHARES_SCREENER = ("https://www.ishares.com/us/product-screener/product-screener-v3.1.jsn"
                    "?dcrPath=/templatedata/config/product-screener-v3/data/en/us-ishares/"
                    "ishares-product-screener-backend-config&siteEntryPassthrough=true")
ISHARES_DOC = ("https://www.blackrock.com/varnish-api/blk-one01-product-data/product-data/api/v1/get-fund-document"
               "?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=us-ishares&locale=en_US"
               "&portfolioId={pid}&component=fundDownload&userType=individual")
_ishares_ids: dict[str, dict] | None = None


def ishares_ids(max_age: float, refresh: bool) -> dict[str, dict]:
    global _ishares_ids
    if _ishares_ids is not None:
        return _ishares_ids
    path = CACHE_DIR / "_ishares_ids.json"
    if not is_fresh(path, max_age * 7, refresh):   # product list changes rarely
        log("  iShares: downloading product screener")
        r = http_get(ISHARES_SCREENER, headers={"Accept": "application/json, text/plain, */*"})
        data = r.json()
        ids = {}
        for v in data.values():
            t = v.get("localExchangeTicker")
            if t and v.get("portfolioId"):
                ids[t] = {"portfolioId": v["portfolioId"], "productPageUrl": v.get("productPageUrl"),
                          "inceptionDate": (v.get("inceptionDate") or {}).get("d")}
        path.write_text(json.dumps(ids, indent=0), encoding="utf-8")
    _ishares_ids = json.loads(path.read_text(encoding="utf-8"))
    return _ishares_ids


def _parse_spreadsheetml_sheet(xml: str, sheet_name: str) -> list[list[str]]:
    i = xml.find(f'ss:Name="{sheet_name}"')
    if i < 0:
        raise ValueError(f"sheet {sheet_name!r} not found")
    j = xml.find("</ss:Worksheet>", i)
    out = []
    for row in re.findall(r"<ss:Row[^>]*>(.*?)</ss:Row>", xml[i:j], flags=re.S):
        out.append([c for _t, c in re.findall(r'<ss:Data ss:Type="(\w+)">([^<]*)</ss:Data>', row)])
    return out


def fetch_ishares(ticker: str, max_age: float, refresh: bool):
    ids = ishares_ids(max_age, refresh)
    if ticker not in ids:
        raise RuntimeError("ticker not in iShares product screener")
    pid = ids[ticker]["portfolioId"]
    url = ISHARES_DOC.format(pid=pid)
    path = CACHE_DIR / f"{ticker}.csv"
    if not is_fresh(path, max_age, refresh):
        log(f"  {ticker}: downloading iShares fund document (portfolioId {pid})")
        r = http_get(url, headers={"Accept": "application/json, text/plain, */*",
                                   "Referer": "https://www.ishares.com/"})
        xml = r.content.decode("utf-8", "ignore")
        if "<ss:Workbook" not in xml[:2000]:
            raise RuntimeError("response is not a SpreadsheetML workbook (got HTML?)")
        rows = _parse_spreadsheetml_sheet(xml, "Historical")
        hdr = rows[0]
        ci = {name: hdr.index(name) for name in ("As Of", "NAV per Share", "Shares Outstanding")}
        recs = []
        for row in rows[1:]:
            if len(row) <= max(ci.values()):
                continue
            try:
                d = pd.to_datetime(row[ci["As Of"]].strip(), format="%b %d, %Y")
            except (ValueError, TypeError):
                continue
            recs.append((d.strftime("%Y-%m-%d"), to_float(row[ci["NAV per Share"]]),
                         to_float(row[ci["Shares Outstanding"]])))
        pd.DataFrame(recs, columns=["date", "nav", "shares_outstanding"]).to_csv(path, index=False)
    df = pd.read_csv(path, parse_dates=["date"])
    df["aum_usd"] = df["nav"] * df["shares_outstanding"]
    return df, url, ("issuer 'Historical' sheet (NAV per share, shares outstanding); "
                     "AUM derived = NAV * shares; cache holds the extracted sheet only")


# ---- VanEck ----------------------------------------------------------------------------
def fetch_vaneck(ticker: str, max_age: float, refresh: bool):
    slug_path = CACHE_DIR / "_vaneck_slugs.json"
    slugs = json.loads(slug_path.read_text(encoding="utf-8")) if slug_path.exists() else {}
    if ticker not in slugs:
        log(f"  {ticker}: resolving VanEck product page")
        r = http_get(f"https://www.vaneck.com/us/en/etf/equity/{ticker.lower()}/")
        final = r.url.split("?")[0].rstrip("/")
        if "/investments/" not in final:
            raise RuntimeError(f"could not resolve product page (landed on {final})")
        slugs[ticker] = final
        slug_path.write_text(json.dumps(slugs, indent=0), encoding="utf-8")
    url = slugs[ticker] + "/downloads/fundhistoprices/"
    path = CACHE_DIR / f"{ticker}.xlsx"
    if not is_fresh(path, max_age, refresh):
        log(f"  {ticker}: downloading VanEck fund history prices")
        r = http_get(url)
        if not r.content.startswith(b"PK"):
            raise RuntimeError(f"unexpected content-type {r.headers.get('content-type')}")
        path.write_bytes(r.content)
    rows = xlsx_rows(path)
    h = find_header_row(rows)
    hdr = [str(c).strip() if c else "" for c in rows[h]]
    ci = {name: hdr.index(name) for name in ("Date", "NAV", "AUM")}
    recs = []
    for row in rows[h + 1:]:
        if not row or not row[0]:
            continue
        try:
            d = pd.to_datetime(str(row[ci["Date"]]).strip(), format="%m/%d/%Y")
        except (ValueError, TypeError):
            continue
        nav, aum = to_float(row[ci["NAV"]]), to_float(row[ci["AUM"]])
        shares = aum / nav if (nav and nav > 0 and aum == aum) else np.nan
        recs.append((d, nav, shares, aum))
    df = pd.DataFrame(recs, columns=["date", "nav", "shares_outstanding", "aum_usd"])
    return df, url, "issuer file reports NAV and AUM; shares outstanding derived = AUM / NAV"


# ---- Invesco ---------------------------------------------------------------------------
def fetch_invesco(ticker: str, max_age: float, refresh: bool):
    url = (f"https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/{ticker}/navs"
           f"?idType=ticker&productType=ETF")
    path = CACHE_DIR / f"{ticker}.csv"
    if not is_fresh(path, max_age, refresh):
        log(f"  {ticker}: downloading Invesco NAV history")
        raw = curl_get(url, headers={"Accept": "application/json",
                                     "Origin": "https://www.invesco.com",
                                     "Referer": "https://www.invesco.com/"})
        data = json.loads(raw)
        if isinstance(data, list) and not data:
            raise NotAvailable("Invesco API returns an empty NAV history for this ticker "
                               "(fund no longer listed / liquidated?); nothing fetched")
        if not isinstance(data, list):
            raise RuntimeError("unexpected JSON payload")
        recs = [(d["effectiveDate"], to_float(d.get("netAssetValue")), to_float(d.get("totalNetAssets")))
                for d in data if d.get("effectiveDate")]
        pd.DataFrame(recs, columns=["date", "nav", "aum_usd"]).to_csv(path, index=False)
    df = pd.read_csv(path, parse_dates=["date"])
    df["shares_outstanding"] = np.where(df["nav"] > 0, df["aum_usd"] / df["nav"], np.nan)
    return df, url, ("issuer API reports NAV and total net assets (history begins 2016-09-29 for all funds); "
                     "shares outstanding derived = TNA / NAV; cache holds the extracted table only")


# ---- First Trust -----------------------------------------------------------------------
def fetch_firsttrust(ticker: str, max_age: float, refresh: bool):
    page_url = f"https://www.ftportfolios.com/Retail/Etf/EtfPriceHistory.aspx?Ticker={ticker}"
    path = CACHE_DIR / f"{ticker}.xlsx"
    if not is_fresh(path, max_age, refresh):
        log(f"  {ticker}: downloading First Trust price history")
        r = http_get(page_url)
        html = r.text
        hidden = dict(re.findall(r'<input type="hidden" name="([^"]+)"[^>]*value="([^"]*)"', html))
        if "__VIEWSTATE" not in hidden:
            raise RuntimeError("could not find ASP.NET form state on price-history page")
        i = html.find("ddlPriceHistoryYearSelection")
        j = html.find("</select>", i) if i >= 0 else -1
        years = [int(y) for y in re.findall(r"<option[^>]*>(\d{4})</option>", html[i:j])] if i >= 0 else []
        start_year = min(years) if years else 1999
        form = dict(hidden)
        form.update({
            "ctl00$ContentPlaceHolder1$PriceHistory$txtStartDate": f"1/1/{start_year}",
            "ctl00$ContentPlaceHolder1$PriceHistory$txtEndDate": datetime.now().strftime("%m/%d/%Y").lstrip("0").replace("/0", "/"),
            "ctl00$ContentPlaceHolder1$PriceHistory$btnDownload": "Download",
        })
        _throttle()
        r2 = SESSION.post(page_url, data=form, timeout=TIMEOUT_S,
                          headers={"Referer": page_url, "Content-Type": "application/x-www-form-urlencoded"})
        r2.raise_for_status()
        if not r2.content.startswith(b"PK"):
            raise RuntimeError("download postback did not return an xlsx")
        path.write_bytes(r2.content)
    rows = xlsx_rows(path)
    h = find_header_row(rows)
    hdr = [str(c).strip() if c else "" for c in rows[h]]
    ci = {name: hdr.index(name) for name in ("Date", "Net Asset Value", "Net Assets")}
    recs = []
    for row in rows[h + 1:]:
        if not row or not row[0]:
            continue
        try:
            d = pd.to_datetime(str(row[ci["Date"]]).strip(), format="%m/%d/%Y")
        except (ValueError, TypeError):
            continue
        nav, aum = to_float(row[ci["Net Asset Value"]]), to_float(row[ci["Net Assets"]])
        if not (aum and aum > 0):
            aum = np.nan                      # pre-launch placeholder rows carry 0 net assets
        shares = aum / nav if (nav and nav > 0 and aum == aum) else np.nan
        recs.append((d, nav, shares, aum))
    df = pd.DataFrame(recs, columns=["date", "nav", "shares_outstanding", "aum_usd"])
    return df, page_url + " (Download postback, full date range)", \
        "issuer file reports NAV and net assets; shares outstanding derived = net assets / NAV"


# ---- Issuers with no usable download -----------------------------------------------------
NOT_AVAILABLE_NOTES = {
    "Vanguard": ("investor.vanguard.com publishes current NAV/price and a web-component price chart only; "
                 "no downloadable daily shares-outstanding or net-assets history was found"),
    "Global X": ("globalxetfs.com product pages expose holdings and premium/discount CSVs only; "
                 "no daily NAV/shares-outstanding history download was found"),
    "Pacer": ("paceretfs.com product pages show current NAV/shares outstanding and a holdings download only; "
              "no historical NAV/shares file was found"),
    "ALPS": ("alpsfunds.com charts come from secure.alpsinc.com/MarketingAPI which answers 401 "
             "without a site token; no public historical NAV/shares download"),
    "Amplify": ("amplifyetfs.com serves fund time series to its own JavaScript via a Firestore backend; "
                "no issuer download file exists and the backend was deliberately not queried"),
}

FETCHERS = {
    "SSGA": fetch_ssga,
    "iShares": fetch_ishares,
    "VanEck": fetch_vaneck,
    "Invesco": fetch_invesco,
    "First Trust": fetch_firsttrust,
}


# --------------------------------------------------------------------------------------
# Post-processing
# --------------------------------------------------------------------------------------
SOURCE_TAGS = {            # compact per-row source tag; the full URL/method is in the coverage file
    "SSGA": "ssga_navhist_xlsx",
    "iShares": "ishares_fund_document_historical",
    "VanEck": "vaneck_fundhistoprices_xlsx",
    "Invesco": "invesco_dng_api_navs_json",
    "First Trust": "firsttrust_pricehistory_xlsx",
}


def clean_and_flow(df: pd.DataFrame, ticker: str, source: str) -> tuple[pd.DataFrame, list[str]]:
    """Clean one ETF's history and compute daily flow.

    Share splits (shares jump >25% while NAV moves inversely so AUM is unchanged) are not
    creations/redemptions: flow is set to 0 on those days and the dates are returned so the
    coverage file can document them."""
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"].dt.day_of_week < 5]                     # US ETFs have no weekend NAVs
    df = df.dropna(subset=["nav", "shares_outstanding"])
    df = df[(df["nav"] > 0) & (df["shares_outstanding"] > 0)]
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    if "aum_usd" not in df or df["aum_usd"].isna().all():
        df["aum_usd"] = df["nav"] * df["shares_outstanding"]
    df["aum_usd"] = df["aum_usd"].fillna(df["nav"] * df["shares_outstanding"])
    df["flow_usd"] = df["shares_outstanding"].diff() * df["nav"]
    rs = df["shares_outstanding"] / df["shares_outstanding"].shift(1)
    rn = df["nav"] / df["nav"].shift(1)
    split = (np.abs(np.log(rs)) > np.log(1.25)) & (np.abs(np.log(rs * rn)) < np.log(1.05))
    split = split.fillna(False)
    df.loc[split, "flow_usd"] = 0.0
    splits = [f"{d:%Y-%m-%d} (x{r:.3g})" for d, r in zip(df.loc[split, "date"], rs[split])]
    df["ticker"] = ticker
    df["source"] = source
    return df[["date", "ticker", "nav", "shares_outstanding", "aum_usd", "flow_usd", "source"]], splits


def run(tickers: list[str], max_age: float, refresh: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    frames, coverage = [], []
    for t in tickers:
        issuer = TICKER_ISSUER.get(t, "unknown")
        fetcher = FETCHERS.get(issuer)
        row = {"ticker": t, "issuer": issuer, "source_url_or_method": "", "first_date": "",
               "last_date": "", "n_rows": 0, "status": "", "notes": ""}
        if fetcher is None:
            row["status"] = "not_available"
            row["notes"] = NOT_AVAILABLE_NOTES.get(issuer, "issuer not mapped")
            row["source_url_or_method"] = "none"
            coverage.append(row)
            log(f"  {t}: {issuer} -> not available")
            continue
        try:
            df, source, notes = fetcher(t, max_age, refresh)
            raw_first = pd.to_datetime(df["date"]).min()
            out, splits = clean_and_flow(df, t, SOURCE_TAGS[issuer])
            row["source_url_or_method"] = source
            if out.empty:
                row["status"] = "empty"
                row["notes"] = notes + "; no rows with both NAV and shares"
            else:
                row["first_date"] = out["date"].min().strftime("%Y-%m-%d")
                row["last_date"] = out["date"].max().strftime("%Y-%m-%d")
                row["n_rows"] = int(len(out))
                row["status"] = "ok"
                extra = ""
                if pd.notna(raw_first) and raw_first < out["date"].min() - pd.Timedelta(days=10):
                    extra += f"; NAV-only rows exist back to {raw_first:%Y-%m-%d} (no shares/TNA before {row['first_date']})"
                if splits:
                    extra += "; share split(s) detected, flow set to 0 on: " + ", ".join(splits)
                row["notes"] = notes + extra
                frames.append(out)
            log(f"  {t}: {row['status']} {row['first_date']} -> {row['last_date']} ({row['n_rows']} rows)")
        except NotAvailable as exc:
            row["status"] = "not_available"
            row["notes"] = str(exc)
            log(f"  {t}: not available ({exc})")
        except Exception as exc:  # noqa: BLE001
            row["status"] = "error"
            row["notes"] = f"{type(exc).__name__}: {exc}"[:300]
            log(f"  {t}: ERROR {row['notes']}")
        coverage.append(row)

    flows = (pd.concat(frames, ignore_index=True) if frames
             else pd.DataFrame(columns=["date", "ticker", "nav", "shares_outstanding", "aum_usd", "flow_usd", "source"]))
    flows = flows.sort_values(["ticker", "date"]).reset_index(drop=True)
    cov = pd.DataFrame(coverage)
    return flows, cov


def verify(flows: pd.DataFrame) -> None:
    print("\n=== Verification ===")
    for t in ("XBI", "XLK"):
        sub = flows[flows["ticker"] == t]
        if sub.empty:
            print(f"{t}: no rows")
            continue
        print(f"\n{t}: {sub['date'].min():%Y-%m-%d} -> {sub['date'].max():%Y-%m-%d}, {len(sub)} rows")
        with pd.option_context("display.width", 160, "display.max_columns", 10, "display.float_format", "{:,.2f}".format):
            print(sub.head(3).drop(columns=["source"]).to_string(index=False))
            print(sub.tail(3).drop(columns=["source"]).to_string(index=False))
    xlk = flows[(flows["ticker"] == "XLK") & (flows["date"].dt.year == 2020)]
    if not xlk.empty:
        cum = xlk["flow_usd"].sum()
        print(f"\nXLK 2020: AUM {xlk['aum_usd'].iloc[0]/1e9:,.1f} bn -> {xlk['aum_usd'].iloc[-1]/1e9:,.1f} bn, "
              f"shares {xlk['shares_outstanding'].iloc[0]/1e6:,.1f} m -> {xlk['shares_outstanding'].iloc[-1]/1e6:,.1f} m, "
              f"cumulative net flow ${cum/1e9:,.2f} bn ({'positive' if cum > 0 else 'NEGATIVE'})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="ignore cache freshness and re-download everything")
    ap.add_argument("--max-age-hours", type=float, default=20.0, help="reuse cached issuer files younger than this")
    ap.add_argument("--only", nargs="*", help="restrict fetching to these tickers (others are rebuilt from cache if present)")
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args(argv)

    tickers = load_taxonomy_tickers()
    log(f"{len(tickers)} ETFs from taxonomy; cache dir {CACHE_DIR}")
    if args.only:
        wanted = {t.upper() for t in args.only}
        # tickers not in --only are rebuilt from cache only (never fetched): use an infinite max-age for them
        flows_a, cov_a = run([t for t in tickers if t in wanted], args.max_age_hours, args.refresh)
        flows_b, cov_b = run([t for t in tickers if t not in wanted], float("inf"), False)
        flows = pd.concat([flows_a, flows_b], ignore_index=True).sort_values(["ticker", "date"])
        cov = pd.concat([cov_a, cov_b], ignore_index=True)
    else:
        flows, cov = run(tickers, args.max_age_hours, args.refresh)

    cov = cov.set_index("ticker").loc[tickers].reset_index()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = flows.copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out["nav"] = out["nav"].round(6)
    out["shares_outstanding"] = out["shares_outstanding"].round(0)
    out["aum_usd"] = out["aum_usd"].round(2)
    out["flow_usd"] = out["flow_usd"].round(2)
    out.to_csv(OUT_FLOWS, index=False)
    cov.to_csv(OUT_COVERAGE, index=False)
    log(f"wrote {OUT_FLOWS} ({len(out):,} rows) and {OUT_COVERAGE} ({len(cov)} ETFs)")

    ok = cov[cov["status"] == "ok"]
    fd = pd.to_datetime(ok["first_date"], errors="coerce")
    log(f"coverage: ok={len(ok)}, not_available={(cov['status']=='not_available').sum()}, "
        f"error={(cov['status']=='error').sum()}, empty={(cov['status']=='empty').sum()} | "
        f"history before 2010: {(fd < '2010-01-01').sum()}, before 2015: {(fd < '2015-01-01').sum()}, "
        f"2015 or later only: {(fd >= '2015-01-01').sum()}")
    if not args.no_verify:
        verify(flows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
