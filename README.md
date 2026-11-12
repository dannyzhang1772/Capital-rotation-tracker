# projectmodl.com · Capital Rotation Tracker

A static website that tracks where public equity capital sits across 11 sectors and 62 sub-industries, compares it with 1999-to-date history, explains what drove each rotation, and says what would move capital next. Built for a student investment fund.

## Layout

```
pipeline/   Python scripts that fetch data and compute everything the site shows
  taxonomy.json       sectors, sub-industries, ticker baskets, ETFs, factor loadings, drivers (edit this)
  fetch_prices.py     Yahoo Finance daily prices for ~1,000 tickers (full back-fill with --full, incremental otherwise)
  fetch_shares.py     shares outstanding history (Yahoo; SEC XBRL optional via CRT_SEC_CONTACT)
  fetch_macro.py      FRED + Yahoo macro series (10y, Fed funds, VIX, oil, dollar)
  fetch_etf_flows.py  issuer shares-outstanding and NAV -> daily ETF creation/redemption flows
  fetch_13f.py        SEC Form 13F structured data -> institutional and hedge-fund ownership by ticker
  compute.py          bucket indices, weights, relative strength, factor betas, heat scores -> site/data/*.js
  style_cohorts.py    momentum vs defensive cohorts inside each sub-industry since 1996 (+ Fama-French backdrop) -> site/data/style.js
  event_response.py   measures what each sub-industry did (vs S&P and vs its sector) after every researched event
  calibrate.py        Beta Mode: blind materiality ratings (research/event_ratings.json) vs measured reactions, calibration fit, backtest, long/short stances -> site/data/beta.js
research/   events_*.json (dated, sourced rotation events), regimes.json, per-sector narratives (.md), blurbs.json (catalyst notes)
site/       the website (plain HTML/CSS/JS + Chart.js from CDN; data embedded as JS globals so it also opens from disk)
data/       raw and processed data (parquet/CSV); large raw files are git-ignored
.github/workflows/refresh.yml   scheduled refresh (weekdays after the US close) that commits new data
.github/workflows/pages.yml     publishes site/ to GitHub Pages on every push to main
```

## Run locally

```bash
pip install -r pipeline/requirements.txt
cd pipeline
python fetch_prices.py --full      # first time; later runs without --full are incremental
python fetch_shares.py --yahoo
python fetch_macro.py
python fetch_etf_flows.py
python fetch_13f.py                # slow on first run (downloads ~50 quarterly SEC zips)
python compute.py
python style_cohorts.py
python event_response.py
python calibrate.py
```

Then open `site/index.html` in a browser, or serve it: `python -m http.server 8765 --directory site`.

## Publish on GitHub Pages

1. Create an empty GitHub repository and push this folder to its `main` branch.
2. In the repository settings, under Pages, set the source to "GitHub Actions".
3. Under Actions, allow workflows to write to the repository (Settings > Actions > General > Workflow permissions > Read and write).
4. The `Publish site` workflow deploys on every push; `Refresh tracker data` runs each weekday evening and commits fresh data, which triggers a new deploy.

## Automated event scoring (weekly)

`pipeline/auto_events.py` pulls each sub-industry's headlines from Google News RSS, asks Claude (`claude-opus-5-5`) to extract dated catalysts and rate them blind with the same rubric and worked examples as the hand ratings, and writes them to `research/events_auto.json` plus `research/event_ratings.json` with `"auto": true, "reviewed": false`. The refresh workflow runs it on Mondays (and on a manual run) when the repository has an `ANTHROPIC_API_KEY` secret (Settings > Secrets and variables > Actions). Expect roughly 60 model calls per run. If the key expires or is revoked, the step logs an API error and the rest of the refresh continues; create a new key in the Anthropic console and update the secret.

Unreviewed auto events are quarantined: they show in the Beta tab's review queue, count at half weight in stances, and are excluded from the calibration fits and backtest. To accept one, set `"reviewed": true` on its entry in `research/event_ratings.json` (edit the components first if you disagree); to reject one, delete it from both files. Dry run locally without a key: `python auto_events.py --dry-run --max-buckets 3`.

## Optional: SEC share counts back to 2009

The SEC's fair-access policy requires a contact in the User-Agent header. Set `CRT_SEC_CONTACT="Your Name you@example.com"` (locally or as a GitHub Actions secret) and run `python fetch_shares.py --sec` to add XBRL share counts from 2009, which replaces the held-constant approximation before 2015.

## Editing

- Change baskets, ETFs or drivers in `pipeline/taxonomy.json`, then run `python pipeline/validate_taxonomy.py` and `python compute.py`.
- Catalyst notes live in `research/blurbs.json`; each note records the heat score at the time it was written and the pipeline flags it as stale when the score moves more than 30 points.
- Event annotations live in `research/events_*.json` and are merged automatically. Every event needs a rating in `research/event_ratings.json` (rubric components e/p/b/c, see the file header) or Beta Mode will skip it.

## Trading tab and portal

Signed out, `trading.html` shows a fictional sample book (`research/sample_portfolio.json` -> `python pipeline/portfolio.py --sample` -> `site/data/portfolio.js`). Signed in, it is a private portal: each invited user's brokerage link and last analysis are kept by the backend, and live quotes are laid on top every minute.

- `pipeline/portfolio_core.py`: the analytics (fund look-through exposure, returns decomposition, ratios, option-implied moves, scenarios) for any set of tickers.
- `backend/app.py`: FastAPI service. Every route needs a Supabase sign-in whose email is in `ALLOWED_EMAILS`. Routes: `/api/me`, `/api/me/portfolio`, `/api/me/refresh`, `/api/me/holdings`, `/api/me/quotes`, `/api/me/connect/start`, `/api/me/disconnect`, `DELETE /api/me`.
- `backend/supabase_schema.sql`: the one table (`pm_users`), row level security on with no policies, so only the backend can touch it. Brokerage secrets are encrypted with `TOKEN_KEY` before they are stored.

Setup (once)
1. Supabase: create a project; run `backend/supabase_schema.sql` in the SQL editor; under Authentication -> URL Configuration set the Site URL to the Trading page URL and add it to Redirect URLs.
2. Render: New -> Blueprint -> this repository (`render.yaml`). Fill in `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (the secret key), `ALLOWED_EMAILS`, `SNAPTRADE_CLIENT_ID`, `SNAPTRADE_CONSUMER_KEY`. Never put these in code or chat.
3. Put the three public values in `site/assets/config.js`: the Render service URL, the Supabase project URL and the Supabase publishable (anon) key.

Local development: `DEV_AUTH=1 TOKEN_KEY=dev ALLOWED_EMAILS=me@test.dev ALLOWED_ORIGINS=http://localhost:8765 uvicorn backend.app:app --port 8787`, then open `trading.html?api=http://localhost:8787&dev=me@test.dev` in the local preview. `DEV_AUTH` must never be set on the host.

Admin: `GET /api/admin/snaptrade-users` and `DELETE /api/admin/snaptrade-users/{id}` with the `X-Admin-Token` header (the free SnapTrade tier allows five connections).
