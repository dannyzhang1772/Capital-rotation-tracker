"""projectmodl.com backend: turns a set of holdings into the Trading tab's analytics.

Two ways in
  POST /api/analyze            holdings typed or pasted by the visitor
  POST /api/connect/start      register a SnapTrade user (read-only) and return the Connection Portal link
  POST /api/connect/portfolio  pull that user's accounts, positions and buy dates from SnapTrade, analyse, return
  POST /api/connect/disconnect delete the SnapTrade user and its brokerage connection

Nothing is stored here. A visitor's SnapTrade user id and secret are returned to their browser as one encrypted
token (Fernet, key from TOKEN_KEY); the browser sends it back with each request. Results go to that browser only.

Environment
  SNAPTRADE_CLIENT_ID, SNAPTRADE_CONSUMER_KEY   from the SnapTrade dashboard (never in code or git)
  TOKEN_KEY        any long random string; encrypts visitor tokens
  ADMIN_TOKEN      random string; guards /api/admin/*
  ALLOWED_ORIGINS  comma-separated origins allowed to call this service
  FRONTEND_URL     where the Connection Portal sends the visitor back
"""
import os, sys, json, uuid, base64, hashlib, datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from cryptography.fernet import Fernet, InvalidToken

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'pipeline'))
from portfolio_core import analyze, CA_EXCH   # noqa: E402

CLIENT_ID = os.environ.get('SNAPTRADE_CLIENT_ID', ''); CONSUMER_KEY = os.environ.get('SNAPTRADE_CONSUMER_KEY', '')
TOKEN_KEY = os.environ.get('TOKEN_KEY', ''); ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN', '')
ORIGINS = [o.strip() for o in os.environ.get('ALLOWED_ORIGINS', 'http://localhost:8765').split(',') if o.strip()]
FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://localhost:8765/trading.html')
MAX_POSITIONS = 80

app = FastAPI(title='projectmodl api', docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=['GET', 'POST', 'DELETE'], allow_headers=['Content-Type', 'X-Admin-Token'])

def fernet():
    if not TOKEN_KEY: raise HTTPException(503, 'TOKEN_KEY is not configured')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(TOKEN_KEY.encode()).digest()))
def seal(uid, secret): return fernet().encrypt(json.dumps({'u': uid, 's': secret}).encode()).decode()
def unseal(token):
    try: d = json.loads(fernet().decrypt(token.encode())); return d['u'], d['s']
    except (InvalidToken, KeyError, ValueError): raise HTTPException(401, 'connection token is not valid; connect again')

def snaptrade():
    if not (CLIENT_ID and CONSUMER_KEY): raise HTTPException(503, 'brokerage connections are not configured on this service yet')
    from snaptrade_client import SnapTrade, SnapTradeAuth
    return SnapTrade(auth=SnapTradeAuth.commercial_api_key(consumer_key=CONSUMER_KEY, client_id=CLIENT_ID))

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

def snaptrade_error(e):
    body = getattr(e, 'body', None); status = getattr(e, 'status', 502) or 502
    detail = body.get('detail') if isinstance(body, Mapping) else None
    raise HTTPException(502 if status >= 500 else status, f"SnapTrade: {detail or getattr(e, 'reason', None) or str(e)[:200]}")

# ---------------------------------------------------------------- SnapTrade -> holdings
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
    out = {'label': 'Connected portfolio', 'accounts': {}, 'positions': [], 'skipped': []}; ccy_votes = {}
    for a in accounts or []:
        if a.get('kind') not in (None, 'investment') or a.get('status') in ('closed', 'archived'): continue
        aid = a['id']; name = a.get('display_name') or a.get('raw_type') or 'Account'
        label = f"{name} {a.get('masked_account_number') or ''}".strip()
        nv = a.get('net_value') or {}
        out['accounts'][label] = {'type': name, 'total': num(nv.get('amount')), 'currency': nv.get('currency'), 'first_transaction': ((a.get('sync_status') or {}).get('transactions') or {}).get('first_transaction_date')}
        if nv.get('currency'): ccy_votes[nv['currency']] = ccy_votes.get(nv['currency'], 0) + (num(nv.get('amount')) or 0)
        try: pos = plain(st.account_information.get_all_account_positions(account_id=aid, user_id=uid, user_secret=secret).body).get('results', [])
        except Exception as e: snaptrade_error(e)
        # first purchase date per symbol from BUY activities (paged, newest first)
        first_buy = {}; offset = 0
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

# ---------------------------------------------------------------- routes
class Holdings(BaseModel):
    holdings: dict
class Token(BaseModel):
    token: str | None = None

@app.get('/api/health')
def health():
    return {'ok': True, 'snaptrade_configured': bool(CLIENT_ID and CONSUMER_KEY), 'time': dt.datetime.utcnow().isoformat() + 'Z'}

def run(h):
    if len(h.get('positions') or []) > MAX_POSITIONS: raise HTTPException(400, f'at most {MAX_POSITIONS} positions per request')
    try: return analyze(h)
    except ValueError as e: raise HTTPException(400, str(e))

@app.post('/api/analyze')
def api_analyze(body: Holdings):
    return {'portfolio': run(body.holdings)}

@app.post('/api/connect/start')
def connect_start(body: Token):
    st = snaptrade()
    if body.token: uid, secret = unseal(body.token)
    else:
        uid = 'pm-' + uuid.uuid4().hex
        try: secret = plain(st.authentication.register_snap_trade_user(user_id=uid).body)['userSecret']
        except Exception as e: snaptrade_error(e)
    try: resp = plain(st.authentication.login_snap_trade_user(user_id=uid, user_secret=secret, connection_type='read', custom_redirect=FRONTEND_URL + '?connected=1', connection_portal_version='v4').body)
    except Exception as e: snaptrade_error(e)
    url = resp.get('redirectURI') if isinstance(resp, dict) else resp
    if not url: raise HTTPException(502, 'SnapTrade did not return a Connection Portal link')
    return {'token': seal(uid, secret), 'portal_url': url}

@app.post('/api/connect/portfolio')
def connect_portfolio(body: Token):
    if not body.token: raise HTTPException(400, 'missing token')
    uid, secret = unseal(body.token); st = snaptrade()
    h = fetch_holdings(st, uid, secret)
    if not h['accounts']: return {'status': 'no_connection'}
    if not h['positions']: return {'status': 'no_positions', 'accounts': list(h['accounts']), 'skipped': h['skipped']}
    return {'status': 'ok', 'portfolio': run(h), 'accounts': {k: {'first_transaction': v.get('first_transaction')} for k, v in h['accounts'].items()}, 'skipped': h['skipped']}

@app.post('/api/connect/disconnect')
def connect_disconnect(body: Token):
    if not body.token: raise HTTPException(400, 'missing token')
    uid, _ = unseal(body.token); st = snaptrade()
    try: st.authentication.delete_snap_trade_user(user_id=uid)
    except Exception as e: snaptrade_error(e)
    return {'status': 'deleted'}

def admin(tok):
    if not ADMIN_TOKEN or tok != ADMIN_TOKEN: raise HTTPException(403, 'forbidden')
@app.get('/api/admin/users')
def admin_users(x_admin_token: str = Header(default='')):
    admin(x_admin_token); st = snaptrade()
    try: return {'users': plain(st.authentication.list_snap_trade_users().body)}
    except Exception as e: snaptrade_error(e)
@app.delete('/api/admin/users/{uid}')
def admin_delete(uid: str, x_admin_token: str = Header(default='')):
    admin(x_admin_token); st = snaptrade()
    try: st.authentication.delete_snap_trade_user(user_id=uid)
    except Exception as e: snaptrade_error(e)
    return {'status': 'deleted', 'user': uid}
