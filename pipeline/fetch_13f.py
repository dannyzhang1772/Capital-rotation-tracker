#!/usr/bin/env python
"""
fetch_13f.py - quarterly institutional ownership per taxonomy ticker from the
SEC "Form 13F Data Sets" (https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets).

Each SEC zip (e.g. 2025q3_form13f.zip) contains the 13F filings SUBMITTED during that
calendar quarter.  Nearly all of them report on the quarter that just ended, so the zip
for filing-quarter YYYYqN is used as the source for report period = end of the previous
quarter (2025q3 -> 2025-06-30).  Late filings and amendments for older periods that show
up in later zips are ignored (see caveats in the report).

Pipeline per zip
  1. download the zip to the bulk folder (outside OneDrive) with the SEC User-Agent
  2. SUBMISSION.tsv + COVERPAGE.tsv -> pick, per filer (CIK) and primary period, the latest
     13F-HR or RESTATEMENT amendment, plus any NEW HOLDINGS amendments filed on/after it
  3. match hedge funds on COVERPAGE.FILINGMANAGER_NAME (pipeline/hedge_fund_list.json)
  4. stream INFOTABLE.tsv in chunks: keep SSHPRNAMT_TYPE == 'SH', PUTCALL empty,
     accumulate per-filing totals (all securities) and per-(filing, CUSIP) rows for the
     taxonomy CUSIPs
  5. normalise VALUE to USD.  Values were reported in thousands of dollars until the
     form change effective 2023-01-03 (first dollar-denominated zip: 2023q1).  The unit is
     detected empirically from the implied AAPL/MSFT price per zip, and individual filings
     that are off by ~1000x from the rest of the zip are rescaled and logged.
  6. write per-zip aggregates to data/processed/13f_quarters/ and delete the zip
Finally all per-zip aggregates are assembled into
  data/processed/13f_by_ticker.csv   period, ticker, cusip, total_value_usd, total_shares,
                                     n_holders, hf_value_usd, hf_shares, hf_n_holders
  data/processed/13f_totals.csv      per-quarter totals across ALL securities
  data/processed/13f_hf_matches.csv  matched hedge-fund filers per quarter (audit)
  data/processed/13f_unmapped_tickers.csv

CUSIP -> ticker map: built from SEC fails-to-deliver files (several vintages 2018-2026),
saved to data/processed/cusip_ticker_map.csv (run with --build-map to rebuild).

Usage
  python pipeline/fetch_13f.py --build-map
  python pipeline/fetch_13f.py --quarters 2013q2 2026q3
  python pipeline/fetch_13f.py                 # everything from 2013q2 to the latest zip, incremental
  python pipeline/fetch_13f.py --assemble      # only rebuild the combined CSVs
  python pipeline/fetch_13f.py --log data/processed/13f_backfill.log
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import logging
import os
import re
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# --------------------------------------------------------------------------- paths
ROOT = Path(__file__).resolve().parents[1]
PIPE = ROOT / "pipeline"
DATA = ROOT / "data" / "processed"
QDIR = DATA / "13f_quarters"
BULK = Path(os.environ.get("CRT_BULK_DIR", r"C:\Users\danny\AppData\Local\Temp\crt_bulk"))
FTD_DIR = BULK / "ftd"

TAXONOMY = PIPE / "taxonomy.json"
HF_LIST = PIPE / "hedge_fund_list.json"
MAP_CSV = DATA / "cusip_ticker_map.csv"
BY_TICKER_CSV = DATA / "13f_by_ticker.csv"
TOTALS_CSV = DATA / "13f_totals.csv"
HF_MATCHES_CSV = DATA / "13f_hf_matches.csv"
UNMAPPED_CSV = DATA / "13f_unmapped_tickers.csv"

SEC_13F_BASES = ["https://www.sec.gov/files/structureddata/data/form-13f-data-sets/",
                 "https://www.sec.gov/files/datastandardsinnovation/data/form-13f-data-sets/"]
SEC_FTD_URL = "https://www.sec.gov/files/data/fails-deliver-data/cnsfails{v}.zip"
USER_AGENT = "CapitalRotationTracker/1.0 (research project)"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate", "Accept": "*/*"}
MIN_REQUEST_INTERVAL = 1.0  # seconds between SEC requests (well under 10 req/s)

FIRST_ZIP = "2013q2"          # first structured 13F data set published by the SEC
FIRST_DOLLAR_PERIOD = "2022-12-31"  # filings submitted after 2023-01-03 (2023q1 zip) report VALUE in dollars
FTD_VINTAGES = ["201801a", "202001a", "202201a", "202401a", "202501a", "202512a", "202606a"]
REF_CUSIPS = {"037833100": "AAPL", "594918104": "MSFT"}  # unit sanity references
CHUNK_ROWS = 750_000

# Manual CUSIP-map overrides (reviewed 2026-11-06 against the FTD vintages).
# MANUAL_ANCHOR: the taxonomy ticker's symbol has since been re-used by another security in the
#   newest FTD files, so the anchor is pinned to the company the taxonomy means.
# MANUAL_ACCEPT: older CUSIPs of the same company that the name/prefix heuristic rejected
#   (reincorporations and renames).
MANUAL_ANCHOR = {
    "GOLD": "067901108",   # Barrick Gold (now ticker B); 'GOLD' re-used by Gold.com Inc in 2026
    "PINC": "74051N102",   # Premier Inc (taken private 2026); 'PINC' re-used by a PGIM ETF
}
MANUAL_ACCEPT = {
    "APA": ["037411105"],   # Apache Corp -> APA Corp (2021 holding-company reorganisation)
    "BX": ["09253U108"],    # The Blackstone Group L.P. -> Blackstone Inc
    "CI": ["125509109"],    # Cigna Corp -> The Cigna Group (2018 Express Scripts merger)
    "GOLD": ["06849F108"],  # Barrick Gold -> Barrick Mining Corp (renamed 2025, now ticker B)
    "LH": ["50540R409"],    # Laboratory Corp of America Holdings -> Labcorp Holdings (2024)
    "NOV": ["637071101"],   # National Oilwell Varco -> NOV Inc
    "TRP": ["89353D107"],   # TransCanada -> TC Energy
    "TTEC": ["879939106"],  # TeleTech Holdings -> TTEC Holdings
}

log = logging.getLogger("fetch_13f")


# --------------------------------------------------------------------------- helpers
def setup_logging(logfile: str | None) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    log.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    if logfile:
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)


_last_request = [0.0]


def sec_get(url: str, stream: bool = False, head: bool = False) -> requests.Response:
    """GET/HEAD against sec.gov with throttling and backoff on 403 ('Request Rate Threshold
    Exceeded' is what Akamai returns both for real rate limiting and for undeclared bots)."""
    delays = [15, 30, 60, 120, 240, 300]
    for attempt in range(len(delays) + 1):
        wait = MIN_REQUEST_INTERVAL - (time.time() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.time()
        r = (requests.head if head else requests.get)(url, headers=HEADERS, timeout=900, stream=stream,
                                                       allow_redirects=True)
        if r.status_code in (403, 429, 503) and attempt < len(delays):
            log.warning("SEC returned %s for %s - backing off %ss (attempt %d)", r.status_code, url,
                        delays[attempt], attempt + 1)
            r.close()
            time.sleep(delays[attempt])
            continue
        return r
    return r  # pragma: no cover


def sec_exists(url: str) -> bool:
    """Existence check with a streamed GET (HEAD requests are refused by sec.gov's CDN);
    only the first bytes are read."""
    r = sec_get(url, stream=True)
    try:
        if r.status_code == 403:
            raise RuntimeError(f"SEC rejected request (HTTP 403) for {url} with User-Agent '{USER_AGENT}'")
        if r.status_code != 200:
            return False
        first = next(r.iter_content(4), b"")
        return first[:2] == b"PK"
    finally:
        r.close()


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        log.info("using cached %s", dest.name)
        return dest
    tmp = dest.with_suffix(".part")
    t0 = time.time()
    with sec_get(url, stream=True) as r:
        if r.status_code == 403:
            raise RuntimeError(f"SEC rejected download (HTTP 403) for {url} with User-Agent '{USER_AGENT}'")
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} for {url}")
        ctype = (r.headers.get("Content-Type") or "").lower()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    if not zipfile.is_zipfile(tmp):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{url} did not return a zip (content-type {ctype})")
    tmp.replace(dest)
    log.info("downloaded %s (%.1f MB in %.0fs)", dest.name, dest.stat().st_size / 1e6, time.time() - t0)
    return dest


def _month_end(y: int, m: int) -> dt.date:
    return dt.date(y + (m == 12), (m % 12) + 1, 1) - dt.timedelta(days=1)


def dataset_catalog() -> list[dict]:
    """All SEC 13F data sets in order, each with the single report period it is used for.

    2013q2 .. 2023q4: calendar-quarter zips (filings submitted in that quarter -> report
    period = end of the previous quarter).  From 2024 the SEC runs three-month windows ending
    Feb/May/Aug/Nov (01mar2024-31may2024_form13f.zip, ...), each starting in the month that
    contains the 45-day filing deadline, so the window starting in month M holds the filings
    for the quarter ending at the end of M-1 .. i.e. the quarter end inside the window's first
    month: 01mar..31may -> 03-31, 01jun..31aug -> 06-30, 01sep..30nov -> 09-30,
    01dec..28feb -> 12-31.  The one-off 01jan2024-29feb2024 stub holds the 2023-12-31 filings.
    Entries are generated up to today; existence on sec.gov is checked separately."""
    out = []
    y, q = 2013, 2
    while (y, q) <= (2023, 4):
        period = {1: f"{y - 1}-12-31", 2: f"{y}-03-31", 3: f"{y}-06-30", 4: f"{y}-09-30"}[q]
        out.append(dict(id=f"{y}q{q}", period=period, filename=f"{y}q{q}_form13f.zip"))
        q += 1
        if q == 5:
            y, q = y + 1, 1
    out.append(dict(id="01jan2024-29feb2024", period="2023-12-31", filename="01jan2024-29feb2024_form13f.zip"))
    start = dt.date(2024, 3, 1)
    today = dt.date.today()
    while start <= today:
        end_m = start.month + 2
        end_y = start.year + (end_m > 12)
        end_m = (end_m - 1) % 12 + 1
        end = _month_end(end_y, end_m)
        stem = f"{start:%d%b%Y}-{end:%d%b%Y}".lower()
        out.append(dict(id=stem, period=_month_end(start.year, start.month).isoformat(), filename=f"{stem}_form13f.zip"))
        start = dt.date(end_y + (end_m == 12), (end_m % 12) + 1, 1)
    return out


def dataset_urls(ds: dict) -> list[str]:
    return [b + ds["filename"] for b in SEC_13F_BASES]


def latest_available_dataset(catalog: list[dict]) -> dict:
    for ds in reversed(catalog[-8:]):
        for url in dataset_urls(ds):
            if sec_exists(url):
                ds["url"] = url
                return ds
    raise RuntimeError("could not find any 13F data set zip on sec.gov")


def taxonomy_tickers() -> list[str]:
    t = json.load(open(TAXONOMY, encoding="utf-8"))
    found: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "basket" and isinstance(v, list):
                    found.update(str(x).strip().upper() for x in v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(t)
    return sorted(found)


# --------------------------------------------------------------------------- CUSIP map
def ftd_symbol(ticker: str) -> str:
    """Taxonomy ticker -> symbol form used in the fails-to-deliver files (BRK-B -> BRKB)."""
    return re.sub(r"[^A-Z0-9]", "", ticker.upper())


def build_cusip_map(vintages: list[str] | None = None) -> pd.DataFrame:
    vintages = vintages or FTD_VINTAGES
    FTD_DIR.mkdir(parents=True, exist_ok=True)
    frames = []
    for v in vintages:
        dest = FTD_DIR / f"cnsfails{v}.zip"
        if not dest.exists():
            try:
                download(SEC_FTD_URL.format(v=v), dest)
            except Exception as e:  # older vintages are not at this path
                log.warning("FTD vintage %s unavailable: %s", v, e)
                continue
        with zipfile.ZipFile(dest) as z:
            for name in z.namelist():
                df = pd.read_csv(z.open(name), sep="|", dtype=str, encoding="latin-1", on_bad_lines="skip")
                df.columns = [c.strip().upper() for c in df.columns]
                df = df[df["CUSIP"].notna() & df["SYMBOL"].notna()].copy()
                df["CUSIP"] = df["CUSIP"].str.strip().str.upper()
                df["SYMBOL"] = df["SYMBOL"].str.strip().str.upper()
                df["DESCRIPTION"] = df["DESCRIPTION"].fillna("").str.strip()
                df["vintage"] = v
                frames.append(df[["CUSIP", "SYMBOL", "DESCRIPTION", "vintage"]].drop_duplicates(["CUSIP", "SYMBOL"]))
    if not frames:
        raise RuntimeError("no fails-to-deliver files could be read")
    ftd = pd.concat(frames, ignore_index=True)
    ftd = ftd[ftd["CUSIP"].str.len() == 9]
    g = (ftd.groupby(["SYMBOL", "CUSIP"])
            .agg(description=("DESCRIPTION", "last"), first_vintage=("vintage", "min"),
                 last_vintage=("vintage", "max"), n_vintages=("vintage", "nunique"))
            .reset_index())

    def first_word(desc: str) -> str:
        w = [x for x in re.findall(r"[A-Z0-9]+", desc.upper()) if x != "THE"]
        return w[0] if w else ""

    rows = []
    for tk in taxonomy_tickers():
        sym = ftd_symbol(tk)
        cands = g[g["SYMBOL"] == sym].sort_values(["last_vintage", "n_vintages"], ascending=[False, False])
        if cands.empty:
            rows.append(dict(ticker=tk, symbol_ftd=sym, cusip="", description="", first_vintage="",
                             last_vintage="", n_vintages=0, is_anchor=False, accepted=False,
                             reject_reason="symbol not found in FTD files"))
            continue
        extra = [c for c in MANUAL_ACCEPT.get(tk, []) if c not in set(cands["CUSIP"])]
        if extra:  # same company listed under another symbol in the FTD files (e.g. Barrick GOLD -> B)
            cands = pd.concat([cands, g[g["CUSIP"].isin(extra)].drop_duplicates("CUSIP")], ignore_index=True)
        if tk in MANUAL_ANCHOR and (cands["CUSIP"] == MANUAL_ANCHOR[tk]).any():
            cands = pd.concat([cands[cands["CUSIP"] == MANUAL_ANCHOR[tk]], cands[cands["CUSIP"] != MANUAL_ANCHOR[tk]]])
        anchor = cands.iloc[0]
        a_prefix, a_word = anchor["CUSIP"][:6], first_word(anchor["description"])
        for i, c in enumerate(cands.itertuples(index=False)):
            if i == 0:
                ok, why = True, ("manual anchor" if tk in MANUAL_ANCHOR else "")
            elif c.CUSIP in MANUAL_ACCEPT.get(tk, []):
                ok, why = True, "manual accept"
            elif c.CUSIP[:6] == a_prefix:
                ok, why = True, ""
            elif a_word and first_word(c.description) == a_word:
                ok, why = True, ""
            else:
                ok, why = False, "different issuer than the latest-vintage anchor (symbol reuse)"
            rows.append(dict(ticker=tk, symbol_ftd=sym, cusip=c.CUSIP, description=c.description,
                             first_vintage=c.first_vintage, last_vintage=c.last_vintage,
                             n_vintages=int(c.n_vintages), is_anchor=(i == 0), accepted=ok, reject_reason=why))
    m = pd.DataFrame(rows)
    DATA.mkdir(parents=True, exist_ok=True)
    m.to_csv(MAP_CSV, index=False)
    n_ok = m[m["accepted"]]["ticker"].nunique()
    log.info("CUSIP map: %d tickers, %d mapped, %d accepted CUSIPs, %d rejected (symbol reuse) -> %s",
             m["ticker"].nunique(), n_ok, int(m["accepted"].sum()), int((~m["accepted"] & (m["cusip"] != "")).sum()),
             MAP_CSV)
    return m


def load_cusip_map() -> pd.DataFrame:
    if not MAP_CSV.exists():
        return build_cusip_map()
    m = pd.read_csv(MAP_CSV, dtype=str, keep_default_na=False)
    m["accepted"] = m["accepted"].astype(str).str.lower() == "true"
    return m


# --------------------------------------------------------------------------- hedge funds
def load_hedge_funds() -> list[dict]:
    hf = json.load(open(HF_LIST, encoding="utf-8"))["funds"]
    for f in hf:
        f["_patterns"] = [p.lower() for p in f["patterns"]]
    return hf


def match_hedge_funds(cover: pd.DataFrame, funds: list[dict]) -> pd.DataFrame:
    """Case-insensitive match of each pattern as a whole word/phrase (so 'ra capital management'
    does not match 'Penserra Capital Management')."""
    names = cover["FILINGMANAGER_NAME"].fillna("").str.lower().str.replace(r"\s+", " ", regex=True)
    out = []
    for f in funds:
        mask = pd.Series(False, index=cover.index)
        for p in f["_patterns"]:
            rx = r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])"
            mask |= names.str.contains(rx, regex=True)
        for _, r in cover[mask].iterrows():
            out.append(dict(fund=f["name"], category=f["category"], include=bool(f.get("include", True)),
                            accession=r["ACCESSION_NUMBER"], filer_name=r["FILINGMANAGER_NAME"]))
    return pd.DataFrame(out, columns=["fund", "category", "include", "accession", "filer_name"])


# --------------------------------------------------------------------------- filings
def parse_sec_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, format="%d-%b-%Y", errors="coerce")


def select_filings(sub: pd.DataFrame, cover: pd.DataFrame, period: str) -> tuple[pd.DataFrame, dict]:
    """Keep, per CIK, the latest 13F-HR (or latest RESTATEMENT amendment) for `period`, plus any
    NEW HOLDINGS amendments filed on/after it.  Returns kept submissions and stats."""
    sub = sub.copy()
    sub["FILING_DATE_P"] = parse_sec_date(sub["FILING_DATE"])
    sub["PERIOD_P"] = parse_sec_date(sub["PERIODOFREPORT"]).dt.strftime("%Y-%m-%d")
    hr = sub[sub["SUBMISSIONTYPE"].isin(["13F-HR", "13F-HR/A"])]
    stats = dict(n_submissions=len(sub), n_hr_all_periods=len(hr))
    hr = hr[hr["PERIOD_P"] == period]
    stats["n_hr_primary_period"] = len(hr)
    stats["n_hr_other_periods"] = stats["n_hr_all_periods"] - len(hr)
    cov = cover.set_index("ACCESSION_NUMBER")
    hr = hr.join(cov[["ISAMENDMENT", "AMENDMENTTYPE"]], on="ACCESSION_NUMBER")
    is_amend = (hr["SUBMISSIONTYPE"] == "13F-HR/A") | (hr["ISAMENDMENT"].fillna("").str.upper() == "Y")
    atype = hr["AMENDMENTTYPE"].fillna("").str.upper().str.strip()
    hr["kind"] = np.where(~is_amend, "ORIG", np.where(atype == "NEW HOLDINGS", "NEW", "RESTATE"))
    hr = hr.sort_values(["CIK", "FILING_DATE_P", "ACCESSION_NUMBER"])
    keep = []
    n_dropped = 0
    for cik, grp in hr.groupby("CIK", sort=False):
        restate = grp[grp["kind"] == "RESTATE"]
        orig = grp[grp["kind"] == "ORIG"]
        base = restate.iloc[-1] if len(restate) else (orig.iloc[-1] if len(orig) else None)
        if base is not None:
            keep.append(base["ACCESSION_NUMBER"])
            new = grp[(grp["kind"] == "NEW") & (grp["FILING_DATE_P"] >= base["FILING_DATE_P"])]
        else:
            new = grp[grp["kind"] == "NEW"]
        keep.extend(new["ACCESSION_NUMBER"].tolist())
        n_dropped += len(grp) - (1 if base is not None else 0) - len(new)
    kept = hr[hr["ACCESSION_NUMBER"].isin(set(keep))]
    stats["n_kept"] = len(kept)
    stats["n_dropped_superseded"] = n_dropped
    stats["n_filers"] = int(kept["CIK"].nunique())
    return kept[["ACCESSION_NUMBER", "CIK", "FILING_DATE_P", "kind"]], stats


# --------------------------------------------------------------------------- one zip
def process_zip(ds: dict, cusips: set[str], funds: list[dict], keep_zip: bool = False) -> None:
    zq, period = ds["id"], ds["period"]
    zpath = BULK / ds["filename"]
    t0 = time.time()
    urls = [ds["url"]] if ds.get("url") else dataset_urls(ds)
    last_err = None
    for url in urls:
        try:
            download(url, zpath)
            break
        except RuntimeError as e:
            last_err = e
            if "403" in str(e):
                raise
    else:
        raise RuntimeError(f"no download location worked for {zq}: {last_err}")
    with zipfile.ZipFile(zpath) as z:
        names = {n.upper(): n for n in z.namelist()}
        sub = pd.read_csv(z.open(names["SUBMISSION.TSV"]), sep="\t", dtype=str, quoting=csv.QUOTE_NONE,
                          encoding="utf-8", encoding_errors="replace")
        cover = pd.read_csv(z.open(names["COVERPAGE.TSV"]), sep="\t", dtype=str, quoting=csv.QUOTE_NONE,
                            encoding="utf-8", encoding_errors="replace")
        kept, stats = select_filings(sub, cover, period)
        acc2cik = dict(zip(kept["ACCESSION_NUMBER"], kept["CIK"]))
        kept_set = set(acc2cik)
        log.info("%s period %s: %d submissions, %d 13F-HR(/A) for this period, kept %d filings from %d filers "
                 "(%d superseded dropped, %d filings for other periods ignored)", zq, period,
                 stats["n_submissions"], stats["n_hr_primary_period"], stats["n_kept"], stats["n_filers"],
                 stats["n_dropped_superseded"], stats["n_hr_other_periods"])

        hf = match_hedge_funds(cover[cover["ACCESSION_NUMBER"].isin(kept_set)], funds)
        hf["cik"] = hf["accession"].map(acc2cik)
        hf_acc = set(hf[hf["include"]]["accession"])

        # ---- stream INFOTABLE
        info_name = names["INFOTABLE.TSV"]
        with z.open(info_name) as fh:
            header = fh.readline().decode("utf-8", "replace").rstrip("\r\n").split("\t")
        col = {c.upper().replace("_", ""): c for c in header}
        c_acc, c_cusip, c_val, c_sh = col["ACCESSIONNUMBER"], col["CUSIP"], col["VALUE"], col["SSHPRNAMT"]
        c_type, c_pc = col["SSHPRNAMTTYPE"], col["PUTCALL"]
        usecols = [c_acc, c_cusip, c_val, c_sh, c_type, c_pc]

        per_acc_val: dict[str, float] = {}
        per_acc_rows: dict[str, int] = {}
        tax_parts = []
        n_rows = n_sh = n_kept_rows = 0
        reader = pd.read_csv(z.open(info_name), sep="\t", usecols=usecols, dtype=str, quoting=csv.QUOTE_NONE,
                             encoding="utf-8", encoding_errors="replace", chunksize=CHUNK_ROWS,
                             on_bad_lines="warn")
        for chunk in reader:
            n_rows += len(chunk)
            chunk = chunk[(chunk[c_type].str.upper() == "SH") & (chunk[c_pc].isna() | (chunk[c_pc].str.strip() == ""))
                          & (chunk[c_cusip] != "000000000")]  # placeholder row of an empty holdings report
            n_sh += len(chunk)
            chunk = chunk[chunk[c_acc].isin(kept_set)]
            n_kept_rows += len(chunk)
            if chunk.empty:
                continue
            val = pd.to_numeric(chunk[c_val], errors="coerce").fillna(0.0)
            sh = pd.to_numeric(chunk[c_sh], errors="coerce").fillna(0.0)
            df = pd.DataFrame({"acc": chunk[c_acc].values, "cusip": chunk[c_cusip].str.strip().str.upper().values,
                               "value": val.values, "shares": sh.values})
            tot = df.groupby("acc")["value"].agg(["sum", "size"])
            for acc, s, n in zip(tot.index, tot["sum"], tot["size"]):
                per_acc_val[acc] = per_acc_val.get(acc, 0.0) + float(s)
                per_acc_rows[acc] = per_acc_rows.get(acc, 0) + int(n)
            tdf = df[df["cusip"].isin(cusips)]
            if not tdf.empty:
                tax_parts.append(tdf.groupby(["acc", "cusip"], as_index=False)[["value", "shares"]].sum())
        reader.close()  # release the ZipExtFile handle so the zip can be deleted on Windows
        del reader
        log.info("%s INFOTABLE: %d rows, %d SH/no-option rows, %d in kept filings (%.0fs)", zq, n_rows, n_sh,
                 n_kept_rows, time.time() - t0)

    tax = (pd.concat(tax_parts, ignore_index=True).groupby(["acc", "cusip"], as_index=False)[["value", "shares"]].sum()
           if tax_parts else pd.DataFrame(columns=["acc", "cusip", "value", "shares"]))

    # ---- unit normalisation (thousands vs dollars), verified from implied reference prices
    # implied price per (filing, CUSIP) over ALL taxonomy CUSIPs; the per-CUSIP median across
    # filings is the consensus price, and a filing whose holdings are consistently ~1000x off
    # the consensus reported in the wrong unit (thousands vs dollars)
    ref = tax[(tax["shares"] > 0) & (tax["value"] > 0)].copy()
    ref["implied"] = ref["value"] / ref["shares"]
    med_by_cusip = ref.groupby("cusip")["implied"].median()
    n_by_cusip = ref.groupby("cusip")["implied"].size()
    med_by_cusip = med_by_cusip[n_by_cusip >= 5]  # need a consensus
    ref = ref[ref["cusip"].isin(med_by_cusip.index)]
    ref["ratio"] = ref["implied"] / ref["cusip"].map(med_by_cusip)
    filing_ratio = ref.groupby("acc")["ratio"].median()
    aapl_med = float(med_by_cusip.get("037833100", np.nan))
    expected_scale = 1000.0 if period < FIRST_DOLLAR_PERIOD else 1.0
    if np.isnan(aapl_med):
        scale = expected_scale
        unit_note = "no AAPL reference holdings; used expected unit"
    else:
        scale = 1000.0 if aapl_med < 10 else 1.0
        unit_note = "thousands" if scale == 1000 else "dollars"
        if scale != expected_scale:
            log.warning("%s: empirical unit (%s, median implied AAPL=%.4f) differs from expected rule", zq,
                        unit_note, aapl_med)
    acc_scale = pd.Series(scale, index=pd.Index(list(per_acc_val)))
    rescaled = []
    for acc, rr in filing_ratio.items():
        if rr < 1 / 300:
            acc_scale[acc] = scale * 1000.0
            rescaled.append((acc, rr, "x1000"))
        elif rr > 300:
            acc_scale[acc] = scale / 1000.0
            rescaled.append((acc, rr, "/1000"))
    log.info("%s unit: %s (median implied AAPL price %.4f raw units); %d filings rescaled (%d x1000, %d /1000)",
             zq, unit_note, aapl_med if not np.isnan(aapl_med) else -1, len(rescaled),
             sum(1 for r in rescaled if r[2] == "x1000"), sum(1 for r in rescaled if r[2] == "/1000"))

    # ---- aggregates
    tax["usd"] = tax["value"] * tax["acc"].map(acc_scale).fillna(scale)
    tax["cik"] = tax["acc"].map(acc2cik)
    tax["is_hf"] = tax["acc"].isin(hf_acc)
    g_all = tax.groupby("cusip").agg(total_value_usd=("usd", "sum"), total_shares=("shares", "sum"),
                                     n_holders=("cik", "nunique"))
    g_hf = tax[tax["is_hf"]].groupby("cusip").agg(hf_value_usd=("usd", "sum"), hf_shares=("shares", "sum"),
                                                  hf_n_holders=("cik", "nunique"))
    by_cusip = g_all.join(g_hf, how="left").fillna({"hf_value_usd": 0.0, "hf_shares": 0.0, "hf_n_holders": 0})
    by_cusip["hf_n_holders"] = by_cusip["hf_n_holders"].astype(int)
    by_cusip.insert(0, "zip_quarter", zq)
    by_cusip.insert(1, "period", period)

    acc_tot = pd.Series(per_acc_val)
    acc_usd = acc_tot * acc_scale.reindex(acc_tot.index).fillna(scale)
    ciks = pd.Series(acc2cik)
    hf_mask = acc_usd.index.isin(hf_acc)
    totals = dict(
        zip_quarter=zq, period=period,
        total_value_usd=float(acc_usd.sum()),
        n_filings=int(len(acc_usd)), n_filers=int(ciks.reindex(acc_usd.index).nunique()),
        n_positions=int(sum(per_acc_rows.values())),
        hf_value_usd=float(acc_usd[hf_mask].sum()),
        hf_n_filings=int(hf_mask.sum()), hf_n_filers=int(ciks.reindex(acc_usd.index[hf_mask]).nunique()),
        hf_names_matched_all=int(hf["accession"].nunique()),
        taxonomy_value_usd=float(by_cusip["total_value_usd"].sum()),
        unit_detected=unit_note, median_implied_aapl_raw=aapl_med, n_filings_rescaled=len(rescaled),
        infotable_rows=n_rows, infotable_rows_sh=n_sh, **{f"sel_{k}": v for k, v in stats.items()},
        processed_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    # per-fund value for the audit file
    hf["filing_value_usd"] = hf["accession"].map(acc_usd).round(0)
    hf["n_positions"] = hf["accession"].map(per_acc_rows)
    hf.insert(0, "period", period)
    hf.insert(0, "zip_quarter", zq)

    QDIR.mkdir(parents=True, exist_ok=True)
    by_cusip.reset_index().to_csv(QDIR / f"{zq}_by_cusip.csv", index=False)
    hf.to_csv(QDIR / f"{zq}_hf_matches.csv", index=False)
    pd.DataFrame(rescaled, columns=["accession", "ratio_to_median", "action"]).to_csv(
        QDIR / f"{zq}_rescaled_filings.csv", index=False)
    json.dump(totals, open(QDIR / f"{zq}_totals.json", "w"), indent=1)
    log.info("%s done: total 13F value $%.3fT from %d filers; taxonomy CUSIPs $%.3fT; HF filers %d ($%.1fB) "
             "[%.0fs]", zq, totals["total_value_usd"] / 1e12, totals["n_filers"], totals["taxonomy_value_usd"] / 1e12,
             totals["hf_n_filers"], totals["hf_value_usd"] / 1e9, time.time() - t0)
    if not keep_zip:
        remove_zip(zpath)


def remove_zip(zpath: Path) -> None:
    import gc
    for attempt in range(3):
        try:
            zpath.unlink(missing_ok=True)
            return
        except PermissionError:
            gc.collect()
            time.sleep(1)
    log.warning("could not delete %s (still locked); it will be removed at the end of the run", zpath.name)


def cleanup_bulk() -> None:
    for p in BULK.glob("*_form13f.zip"):
        remove_zip(p)
    for p in BULK.glob("*.part"):
        p.unlink(missing_ok=True)


# --------------------------------------------------------------------------- assemble
def assemble(cmap: pd.DataFrame) -> None:
    files = sorted(QDIR.glob("*_by_cusip.csv"))
    if not files:
        log.warning("nothing to assemble")
        return
    acc = cmap[cmap["accepted"] & (cmap["cusip"] != "")][["ticker", "cusip"]].drop_duplicates()
    parts = [pd.read_csv(f, dtype={"cusip": str}) for f in files]
    allq = pd.concat(parts, ignore_index=True)
    merged = allq.merge(acc, on="cusip", how="inner")
    agg = (merged.groupby(["period", "ticker"], as_index=False)
                 .agg(cusip=("cusip", lambda s: "|".join(sorted(set(s)))),
                      total_value_usd=("total_value_usd", "sum"), total_shares=("total_shares", "sum"),
                      n_holders=("n_holders", "sum"), hf_value_usd=("hf_value_usd", "sum"),
                      hf_shares=("hf_shares", "sum"), hf_n_holders=("hf_n_holders", "sum")))
    for c in ["total_value_usd", "total_shares", "hf_value_usd", "hf_shares"]:
        agg[c] = agg[c].round(0).astype("int64")
    agg = agg.sort_values(["period", "ticker"])
    agg.to_csv(BY_TICKER_CSV, index=False)

    tot = pd.DataFrame([json.load(open(f)) for f in sorted(QDIR.glob("*_totals.json"))]).sort_values("period")
    tot.to_csv(TOTALS_CSV, index=False)

    hfm = [pd.read_csv(f, dtype=str) for f in sorted(QDIR.glob("*_hf_matches.csv"))]
    if hfm:
        pd.concat(hfm, ignore_index=True).to_csv(HF_MATCHES_CSV, index=False)

    mapped = set(acc["ticker"])
    unm = cmap[~cmap["ticker"].isin(mapped)][["ticker", "symbol_ftd", "reject_reason"]].drop_duplicates("ticker")
    unm.to_csv(UNMAPPED_CSV, index=False)
    log.info("assembled %s (%d rows, %d periods, %d tickers), %s (%d quarters), %d unmapped tickers",
             BY_TICKER_CSV.name, len(agg), agg["period"].nunique(), agg["ticker"].nunique(), TOTALS_CSV.name,
             len(tot), len(unm))


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quarters", nargs="*", help="data sets to process: ids (2013q2, 01jun2026-31aug2026), "
                                                  "report periods (2026-06-30) or 'latest' (default: all)")
    ap.add_argument("--build-map", action="store_true", help="rebuild the CUSIP->ticker map from FTD files")
    ap.add_argument("--assemble", action="store_true", help="only rebuild combined CSVs from per-quarter files")
    ap.add_argument("--force", action="store_true", help="reprocess quarters even if aggregates exist")
    ap.add_argument("--keep-zip", action="store_true", help="do not delete downloaded zips")
    ap.add_argument("--log", help="log file (e.g. data/processed/13f_backfill.log)")
    args = ap.parse_args()
    setup_logging(args.log)
    DATA.mkdir(parents=True, exist_ok=True)
    BULK.mkdir(parents=True, exist_ok=True)

    if args.build_map:
        cmap = build_cusip_map()
    else:
        cmap = load_cusip_map()
    if args.assemble:
        assemble(cmap)
        return 0
    cusips = set(cmap[cmap["accepted"] & (cmap["cusip"] != "")]["cusip"]) | set(REF_CUSIPS)
    funds = load_hedge_funds()

    catalog = dataset_catalog()
    by_id = {d["id"]: d for d in catalog}
    by_period = {d["period"]: d for d in catalog}
    if args.quarters:
        wanted = []
        for q in args.quarters:
            q = q.lower()
            if q == "latest":
                wanted.append(latest_available_dataset(catalog))
            elif q in by_id:
                wanted.append(by_id[q])
            elif q in by_period:
                wanted.append(by_period[q])
            else:
                ap.error(f"unknown data set '{q}'; use an id like 2019q3 / 01jun2026-31aug2026, a period "
                         f"like 2026-06-30, or 'latest'")
    else:
        latest = latest_available_dataset(catalog)
        wanted = catalog[: catalog.index(latest) + 1]
    log.info("data sets to consider: %s", " ".join(f"{d['id']}({d['period']})" for d in wanted))
    failures = []
    for ds in wanted:
        zq = ds["id"]
        if not args.force and (QDIR / f"{zq}_totals.json").exists():
            log.info("%s already processed - skipping", zq)
            continue
        try:
            process_zip(ds, cusips, funds, keep_zip=args.keep_zip)
        except Exception as e:  # keep going; report at the end
            log.exception("%s FAILED: %s", zq, e)
            failures.append(zq)
            if "HTTP 403" in str(e):
                log.error("stopping: SEC is rejecting requests with the generic User-Agent")
                break
    assemble(cmap)
    if not args.keep_zip:
        cleanup_bulk()
    if failures:
        log.error("failed quarters: %s", " ".join(failures))
        return 1
    log.info("all done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
