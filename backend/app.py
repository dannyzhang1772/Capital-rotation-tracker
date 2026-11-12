"""projectmodl.com backend: the signed-in Trading portal.

Every route except /api/health needs a signed-in user (Supabase access token in the Authorization header) whose
email is on the invite list. Per user the service keeps one row: the brokerage connection (SnapTrade user id and
secret, the secret encrypted with TOKEN_KEY), the last normalised holdings and the last analysis, so the portal
opens instantly and prices can be refreshed live on top of it.

  GET    /api/me                 who am I, is a brokerage linked, how old is the saved analysis
  GET    /api/me/portfolio       the saved analysis
  POST   /api/me/refresh         re-pull positions from the brokerage (or re-run typed holdings), analyse, save
  POST   /api/me/holdings        save typed holdings and analyse them
  GET    /api/me/quotes          live prices on top of the saved analysis: value now, today's change
  POST   /api/me/connect/start   register the SnapTrade user if needed and return the read-only Connection Portal link
  POST   /api/me/disconnect      delete the SnapTrade user and its brokerage connection
  DELETE /api/me                 delete everything held for this user, including the sign-in account

Environment (set on the host, never in code or git)
  SUPABASE_URL, SUPABASE_SERVICE_KEY   project URL and the secret (service-role) key
  SNAPTRADE_CLIENT_ID, SNAPTRADE_CONSUMER_KEY
  TOKEN_KEY        long random string; encrypts brokerage secrets at rest. Changing it orphans stored connections.
  ALLOWED_EMAILS   comma-separated invite list; nobody else can use the service
  ADMIN_TOKEN      guards /api/admin/*
  ALLOWED_ORIGINS  comma-separated origins allowed to call this service
  FRONTEND_URL     where the Connection Portal sends the visitor back
  DEV_AUTH=1       local development only: accept "Bearer dev:<email>" and keep rows in a local JSON file
"""
import os, sys, json, uuid, time, base64, hashlib, threading, datetime as dt
from collections.abc import Mapping
from decimal import Decimal
import requests
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from cryptography.fernet import Fernet, InvalidToken

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))
from portfolio_core import analyze, get_prices, CA_EXCH   # noqa: E402

E = os.environ.get
SUPABASE_URL = E('SUPABASE_URL', '').rstrip('/'); SUPABASE_KEY = E('SUPABASE_SERVICE_KEY', '')
CLIENT_ID = E('SNAPTRADE_CLIENT_ID', ''); CONSUMER_KEY = E('SNAPTRADE_CONSUMER_KEY', '')
TOKEN_KEY = E('TOKEN_KEY', ''); ADMIN_TOKEN = E('ADMIN_TOKEN', '')
ALLOWED_EMAILS = {e.strip().lower() for e in E('ALLOWED_EMAILS', '').split(',') if e.strip()}
ORIGINS = [o.strip() for o in E('ALLOWED_ORIGINS', 'http://localhost:8765').split(',') if o.strip()]
FRONTEND_URL = E('FRONTEND_URL', 'http://localhost:8765/trading.html')
DEV_AUTH = E('DEV_AUTH', '') == '1'
MAX_POSITIONS = 80; STALE_HOURS = 8

app = FastAPI(title='projectmodl api', docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=['GET', 'POST', 'DELETE'], allow_headers=['Content-Type', 'Authorization', 'X-Admin-Token'])

# ---------------------------------------------------------------- small helpers
def fernet():
    if not TOKEN_KEY: raise HTTPException(503, 'TOKEN_KEY is not configured')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(TOKEN_KEY.encode()).digest()))
def enc(s): return fernet().encrypt(s.encode()).decode()
def dec(s):
    try: return fernet().decrypt(s.encode()).decode()
    except InvalidToken: raise HTTPException(409, 'the stored brokerage connection can no longer be read; remove it and connect again')
def now_iso(): return dt.datetime.now(dt.timezone.utc).isoformat()
def plain(o):
    """SDK response bodies are frozen dict/tuple subclasses; turn them into ordinary JSON-like values."""
    if isinstance(o, Mapping): return {str(k): plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [plain(v) for v in o]
    if isinstance(o, Decimal): return float(o)
    if isinstance(o, (dt.date, dt.datetime)): return o.isoformat()
    if isinstance(o, (str, int, float, bool)) or o is None: return o
    return str(o)
def num(x):
    try: return None if x in (None, '') else float(x)
    except (TypeError, ValueError): return None

# ---------------------------------------------------------------- per-user store
class SupabaseStore:
    def __init__(self):
        self.base = SUPABASE_URL + '/rest/v1/pm_users'
        self.h = {'apikey': SUPABASE_KEY, 'Authorization': 'Bearer ' + SUPABASE_KEY, 'Content-Type': 'application/json'}
    def get(self, uid):
        r = requests.get(self.base, params={'user_id': 'eq.' + uid, 'select': '*'}, headers=self.h, timeout=20)
        if r.status_code >= 400: raise HTTPException(502, f'store read failed ({r.status_code}): {r.text[:200]}')
        rows = r.json(); return rows[0] if rows else None
    def upsert(self, uid, **fields):
        body = {'user_id': uid, 'updated_at': now_iso(), **fields}
        r = requests.post(self.base, params={'on_conflict': 'user_id'}, headers={**self.h, 'Prefer': 'resolution=merge-duplicates,return=minimal'}, data=json.dumps(body), timeout=30)
        if r.status_code >= 400: raise HTTPException(502, f'store write failed ({r.status_code}): {r.text[:200]}')
    def delete(self, uid):
        r = requests.delete(self.base, params={'user_id': 'eq.' + uid}, headers=self.h, timeout=20)
        if r.status_code >= 400: raise HTTPException(502, f'store delete failed ({r.status_code})')
class LocalStore:
    """Development only: one JSON file."""
    path = os.path.join(ROOT, 'data', 'processed', 'dev_store.json'); lock = threading.Lock()
    def _load(self): return json.load(open(self.path, encoding='utf-8')) if os.path.exists(self.path) else {}
    def _save(self, d):
        os.makedirs(os.path.dirname(self.path), exist_ok=True); json.dump(d, open(self.path, 'w', encoding='utf-8'))
    def get(self, uid):
        with self.lock: return self._load().get(uid)
    def upsert(self, uid, **fields):
        with self.lock: d = self._load(); row = d.get(uid, {'user_id': uid}); row.update(fields); row['updated_at'] = now_iso(); d[uid] = row; self._save(d)
    def delete(self, uid):
        with self.lock: d = self._load(); d.pop(uid, None); self._save(d)
def store():
    if SUPABASE_URL and SUPABASE_KEY: return SupabaseStore()
    if DEV_AUTH: return LocalStore()
    raise HTTPException(503, 'the account store is not configured on this service yet')

# ---------------------------------------------------------------- who is calling
_auth_cache = {}
def current_user(authorization: str = Header(default='')):
    tok = authorization[7:].strip() if authorization.lower().startswith('bearer ') else ''
    if not tok: raise HTTPException(401, 'sign in first')
    if DEV_AUTH and tok.startswith('dev:'):
        email = tok[4:].lower(); user = {'id': 'dev-' + hashlib.sha1(email.encode()).hexdigest()[:16], 'email': email}
    else:
        hit = _auth_cache.get(tok)
        if hit and hit[0] > time.time(): user = hit[1]
        else:
            if not (SUPABASE_URL and SUPABASE_KEY): raise HTTPException(503, 'sign-in is not configured on this service yet')
            r = requests.get(SUPABASE_URL + '/auth/v1/user', headers={'apikey': SUPABASE_KEY, 'Authorization': 'Bearer ' + tok}, timeout=15)
            if r.status_code != 200: raise HTTPException(401, 'your session has expired; sign in again')
            u = r.json(); user = {'id': u['id'], 'email': (u.get('email') or '').lower()}
            _auth_cache[tok] = (time.time() + 60, user)
            if len(_auth_cache) > 500: _auth_cache.clear()
    if user['email'] not in ALLOWED_EMAILS: raise HTTPException(403, f"{user['email']} is not on the beta list yet")
    return user

# ---------------------------------------------------------------- SnapTrade -> holdings
def snaptrade():
    if not (CLIENT_ID and CONSUMER_KEY): raise HTTPException(503, 'brokerage connections are not configured on this service yet')
    from snaptrade_client import SnapTrade, SnapTradeAuth
    return SnapTrade(auth=SnapTradeAuth.commercial_api_key(consumer_key=CONSUMER_KEY, client_id=CLIENT_ID))
def snaptrade_error(e):
    body = getattr(e, 'body', None); status = getattr(e, 'status', 502) or 502
    detail = body.get('detail') if isinstance(body, Mapping) else None
    raise HTTPException(502 if status >= 500 else status, f"SnapTrade: {detail or getattr(e, 'reason', None) or str(e)[:200]}")

SKIP_KINDS = {'cash'}; UNPRICED_KINDS = {'option', 'future', 'future_option', 'cfd', 'crypto', 'tokenized_asset', 'bond', 'other'}
def yahoo_symbol(inst):
    sym = (inst.get('symbol') or inst.get('raw_symbol') or '').upper(); raw = (inst.get('raw_symbol') or sym).upper()
    if '.' in sym and sym.rsplit('.', 1)[1] in ('TO', 'V', 'NE', 'CN'): return sym
    suf = CA_EXCH.get((inst.get('exchange') or '').upper())
    return raw.replace('.', '-') + suf if suf else raw.replace('.', '-')

def fetch_holdings(st, uid, secret):
    try: accounts = plain(st.account_information.list_all_user_accounts(user_id=uid, user_secret=secret).body)
    except Exception as e: snaptrade_error(e)
    accounts = accounts.get('results', accounts) if isinstance(accounts, dict) else accounts
    out = {'label': 'My portfolio', 'accounts': {}, 'positions': [], 'skipped': []}; ccy_votes = {}
    for a in accounts or []:
        if a.get('kind') not in (None, 'investment') or a.get('status') in ('closed', 'archived'): continue
        aid = a['id']; name = a.get('display_name') or a.get('raw_type') or 'Account'
        label = f"{name} {a.get('masked_account_number') or ''}".strip()
        nv = a.get('net_value') or {}
        out['accounts'][label] = {'type': name, 'total': num(nv.get('amount')), 'currency': nv.get('currency'), 'first_transaction': ((a.get('sync_status') or {}).get('transactions') or {}).get('first_transaction_date')}
        if nv.get('currency'): ccy_votes[nv['currency']] = ccy_votes.get(nv['currency'], 0) + (num(nv.get('amount')) or 0)
        try: pos = plain(st.account_information.get_all_account_positions(account_id=aid, user_id=uid, user_secret=secret).body).get('results', [])
        except Exception as e: snaptrade_error(e)
        first_buy = {}; offset = 0      # first purchase date per symbol from BUY activities (paged, newest first)
        while offset < 5000:
            try: page = plain(st.account_information.get_account_activities(account_id=aid, type='BUY', offset=offset, limit=1000, user_id=uid, user_secret=secret).body)
            except Exception: break
            data = page.get('data', []) if isinstance(page, dict) else []
            for act in data:
                s = (act.get('symbol') or {}); key = (s.get('raw_symbol') or s.get('symbol') or '').upper(); d = (act.get('trade_date') or '')[:10]
                if key and d and (key not in first_buy or d < first_buy[key]): first_buy[key] = d
            total = ((page.get('pagination') or {}).get('total') or 0) if isinstance(page, dict) else 0
            offset += 1000
            if not data or offset >= total: break
        for p in pos:
            inst = p.get('instrument') or {}; kind = inst.get('kind'); units = num(p.get('units'))
            if kind in SKIP_KINDS or not units: continue
            raw = (inst.get('raw_symbol') or inst.get('symbol') or '').upper()
            if kind in UNPRICED_KINDS or not raw:
                out['skipped'].append({'ticker': raw or kind, 'name': inst.get('description'), 'account': label, 'kind': kind}); continue
            lots = [l.get('original_purchase_date') for l in (p.get('tax_lots') or []) if l.get('original_purchase_date')]
            fb = min([d[:10] for d in lots] + ([first_buy[raw]] if raw in first_buy else []), default=None)
            out['positions'].append({'ticker': raw, 'yahoo': yahoo_symbol(inst), 'name': inst.get('description'), 'account': label, 'currency': p.get('currency') or inst.get('currency'),
                                     'qty': units, 'price': num(p.get('price')), 'avg_cost': num(p.get('cost_basis')), 'exchange': inst.get('exchange'), 'first_buy': fb})
    out['base_currency'] = max(ccy_votes, key=ccy_votes.get) if ccy_votes else ('CAD' if any(p['currency'] == 'CAD' for p in out['positions']) else 'USD')
    for a in out['accounts'].values():       # a net value in another currency cannot be compared with base-currency positions
        if a.get('currency') and a['currency'] != out['base_currency']: a['total'] = None
    return out

# ---------------------------------------------------------------- analysis and live quotes
def run(h):
    if len(h.get('positions') or []) > MAX_POSITIONS: raise HTTPException(400, f'at most {MAX_POSITIONS} positions')
    try: return analyze(h)
    except ValueError as e: raise HTTPException(400, str(e))

def age_hours(iso):
    try: return (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(iso)).total_seconds() / 3600
    except Exception: return None

_quote_cache = {}
def live_quotes(tickers):
    """Latest and previous close for each Yahoo ticker (about 15 minutes delayed during the session); cached 45 s."""
    key = tuple(sorted(tickers)); hit = _quote_cache.get(key)
    if hit and hit[0] > time.time(): return hit[1]
    import yfinance as yf, pandas as pd
    out = {}
    try:
        df = yf.download(list(key), period='5d', interval='1d', auto_adjust=False, progress=False, threads=True, group_by='column')
        c = df['Close'] if 'Close' in df.columns.get_level_values(0) else df
        if isinstance(c, pd.Series): c = c.to_frame(key[0])
        for t in key:
            if t in c.columns:
                s = c[t].dropna()
                if len(s) >= 2: out[t] = {'price': float(s.iloc[-1]), 'prev': float(s.iloc[-2]), 'date': str(s.index[-1])[:10]}
    except Exception:
        pass
    _quote_cache[key] = (time.time() + 45, out)
    if len(_quote_cache) > 200: _quote_cache.clear()
    return out

def live_view(pf):
    """Re-price the saved analysis: value now and today's change per holding and for the book."""
    insts = {i['ticker']: i for i in pf['instruments']}; base = pf.get('base_currency', 'CAD')
    q = live_quotes(sorted({i['yahoo'] for i in pf['instruments']} | {'CAD=X'}))
    fx = q.get('CAD=X'); fx_then = pf.get('usdcad')
    rows = []; tot_now = tot_prev = 0.0
    for tk, i in insts.items():
        spot = (i.get('moves') or {}).get('spot'); qq = q.get(i['yahoo']); v0 = i.get('value_base')
        if not (spot and qq and v0):
            if v0: tot_now += v0; tot_prev += v0
            rows.append({'ticker': tk, 'value_base': v0, 'day_pct': None}); continue
        foreign = (i['currency'] != base) and not i.get('fx_hedged')
        sign = 1 if (i['currency'] == 'USD' and base == 'CAD') else -1
        fx_level = ((fx['price'] / fx_then) ** sign) if (foreign and fx and fx_then) else 1.0
        fx_day = ((fx['price'] / fx['prev']) ** sign) if (foreign and fx) else 1.0
        v_now = v0 * (qq['price'] / spot) * fx_level
        day = (qq['price'] / qq['prev']) * fx_day - 1
        v_prev = v_now / (1 + day)
        tot_now += v_now; tot_prev += v_prev
        rows.append({'ticker': tk, 'price': qq['price'], 'prev_close': qq['prev'], 'day_pct': day, 'value_base': v_now, 'day_change_base': v_now - v_prev, 'quote_date': qq['date']})
    debit = pf.get('margin_debit_base') or 0.0; cash = pf.get('cash_base') or 0.0
    return {'asof': now_iso(), 'base_currency': base, 'total_base': tot_now, 'day_change_base': tot_now - tot_prev, 'day_pct': (tot_now / tot_prev - 1) if tot_prev else None,
            'net_equity_base': tot_now - debit + cash, 'usdcad': fx['price'] if fx else None, 'holdings': sorted(rows, key=lambda r: -(r.get('value_base') or 0)),
            'note': 'Yahoo Finance quotes, about 15 minutes delayed; quantities are as of the last brokerage sync'}

# ---------------------------------------------------------------- routes
class HoldingsIn(BaseModel):
    holdings: dict

@app.get('/api/health')
def health():
    return {'ok': True, 'store': bool(SUPABASE_URL and SUPABASE_KEY) or DEV_AUTH, 'snaptrade_configured': bool(CLIENT_ID and CONSUMER_KEY), 'invited': len(ALLOWED_EMAILS), 'time': now_iso()}

def summary(user, row):
    row = row or {}
    return {'email': user['email'], 'connected': bool(row.get('snaptrade_user_id')), 'has_analysis': bool(row.get('analysis')), 'analysis_at': row.get('analysis_at'), 'source': row.get('source'),
            'stale': (age_hours(row['analysis_at']) or 0) > STALE_HOURS if row.get('analysis_at') else False, 'snaptrade_available': bool(CLIENT_ID and CONSUMER_KEY)}

@app.get('/api/me')
def me(user=Depends(current_user)):
    return summary(user, store().get(user['id']))

@app.get('/api/me/portfolio')
def my_portfolio(user=Depends(current_user)):
    row = store().get(user['id'])
    if not row or not row.get('analysis'): return {'status': 'empty', **summary(user, row)}
    return {'status': 'ok', 'portfolio': row['analysis'], **summary(user, row)}

@app.post('/api/me/holdings')
def my_holdings(body: HoldingsIn, user=Depends(current_user)):
    h = dict(body.holdings); h['label'] = 'My portfolio'
    pf = run(h); s = store()
    s.upsert(user['id'], email=user['email'], holdings=h, analysis=pf, analysis_at=now_iso(), source='typed')
    return {'status': 'ok', 'portfolio': pf, **summary(user, s.get(user['id']))}

@app.post('/api/me/refresh')
def my_refresh(user=Depends(current_user)):
    s = store(); row = s.get(user['id']) or {}
    skipped = []
    if row.get('snaptrade_user_id'):
        h = fetch_holdings(snaptrade(), row['snaptrade_user_id'], dec(row['snaptrade_secret_enc'])); skipped = h.pop('skipped', [])
        if not h['accounts']: return {'status': 'no_connection', **summary(user, row)}
        if not h['positions']: return {'status': 'no_positions', 'skipped': skipped, **summary(user, row)}
        source = 'brokerage'
    elif row.get('holdings'): h = row['holdings']; source = row.get('source') or 'typed'
    else: return {'status': 'empty', **summary(user, row)}
    pf = run(h)
    s.upsert(user['id'], email=user['email'], holdings=h, analysis=pf, analysis_at=now_iso(), source=source)
    return {'status': 'ok', 'portfolio': pf, 'skipped': skipped, **summary(user, s.get(user['id']))}

@app.get('/api/me/quotes')
def my_quotes(user=Depends(current_user)):
    row = store().get(user['id'])
    if not row or not row.get('analysis'): return {'status': 'empty'}
    return {'status': 'ok', 'live': live_view(row['analysis'])}

@app.post('/api/me/connect/start')
def connect_start(user=Depends(current_user)):
    st = snaptrade(); s = store(); row = s.get(user['id']) or {}
    if row.get('snaptrade_user_id'): uid, secret = row['snaptrade_user_id'], dec(row['snaptrade_secret_enc'])
    else:
        uid = 'pm-' + uuid.uuid4().hex
        try: secret = plain(st.authentication.register_snap_trade_user(user_id=uid).body)['userSecret']
        except Exception as e: snaptrade_error(e)
        s.upsert(user['id'], email=user['email'], snaptrade_user_id=uid, snaptrade_secret_enc=enc(secret))
    try: resp = plain(st.authentication.login_snap_trade_user(user_id=uid, user_secret=secret, connection_type='read', custom_redirect=FRONTEND_URL + '?connected=1', connection_portal_version='v4').body)
    except Exception as e: snaptrade_error(e)
    url = resp.get('redirectURI') if isinstance(resp, dict) else resp
    if not url: raise HTTPException(502, 'SnapTrade did not return a Connection Portal link')
    return {'portal_url': url}

def drop_snaptrade(row):
    if row and row.get('snaptrade_user_id') and CLIENT_ID and CONSUMER_KEY:
        try: snaptrade().authentication.delete_snap_trade_user(user_id=row['snaptrade_user_id'])
        except Exception as e: snaptrade_error(e)

@app.post('/api/me/disconnect')
def disconnect(user=Depends(current_user)):
    s = store(); row = s.get(user['id'])
    drop_snaptrade(row)
    s.upsert(user['id'], snaptrade_user_id=None, snaptrade_secret_enc=None, holdings=None, analysis=None, analysis_at=None, source=None)
    return {'status': 'disconnected', **summary(user, s.get(user['id']))}

@app.delete('/api/me')
def delete_me(user=Depends(current_user)):
    s = store(); row = s.get(user['id'])
    drop_snaptrade(row); s.delete(user['id'])
    if SUPABASE_URL and SUPABASE_KEY and not user['id'].startswith('dev-'):
        requests.delete(SUPABASE_URL + '/auth/v1/admin/users/' + user['id'], headers={'apikey': SUPABASE_KEY, 'Authorization': 'Bearer ' + SUPABASE_KEY}, timeout=20)
    return {'status': 'deleted'}

def admin(tok):
    if not ADMIN_TOKEN or tok != ADMIN_TOKEN: raise HTTPException(403, 'forbidden')
@app.get('/api/admin/snaptrade-users')
def admin_users(x_admin_token: str = Header(default='')):
    admin(x_admin_token)
    try: return {'users': plain(snaptrade().authentication.list_snap_trade_users().body)}
    except HTTPException: raise
    except Exception as e: snaptrade_error(e)
@app.delete('/api/admin/snaptrade-users/{uid}')
def admin_delete(uid: str, x_admin_token: str = Header(default='')):
    admin(x_admin_token)
    try: snaptrade().authentication.delete_snap_trade_user(user_id=uid)
    except HTTPException: raise
    except Exception as e: snaptrade_error(e)
    return {'status': 'deleted', 'user': uid}
