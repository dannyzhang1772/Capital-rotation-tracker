"""Local run of the Trading tab analytics for the private holdings file.

Reads   research/private/portfolio_holdings.json   (never committed)
Writes  site/data/portfolio_full.js   window.CRT_PORTFOLIO with dollar values (gitignored: local preview only)
        site/data/portfolio.js        the same with dollar amounts, quantities and costs removed (publishable)
The analytics live in portfolio_core.py, which the hosted backend also uses.
"""
import os, json
from common import ROOT, SITE_DATA
from portfolio_core import analyze, public_view

src = os.path.join(ROOT, 'research', 'private', 'portfolio_holdings.json')
H = json.load(open(src, encoding='utf-8'))
holdings = {
    'label': 'Beta portfolio', 'base_currency': H.get('base_currency', 'CAD'),
    'accounts': {k: {'type': v['type'], 'total': v.get('total_cad')} for k, v in H['accounts'].items()},
    'positions': [{'ticker': p['ticker'], 'yahoo': p.get('yahoo'), 'name': p.get('name'), 'account': p['account'], 'currency': p['currency'], 'qty': p['qty'], 'price': p.get('price'), 'value': p.get('value'),
                   'cost': (p['value'] - p['all_time_return']) if p.get('all_time_return') is not None else None, 'first_buy': p.get('first_buy')} for p in H['positions']],
    # the Uber line is a CAD-hedged CDR: model it on the US shares
    'instruments': {'UBER': {'yahoo': 'UBER', 'fx_hedged': True}},
}
full = analyze(holdings, log=print)
for name, obj in (('portfolio_full.js', full), ('portfolio.js', public_view(full))):
    with open(os.path.join(SITE_DATA, name), 'w', encoding='utf-8') as f:
        f.write('window.CRT_PORTFOLIO = '); json.dump(obj, f, ensure_ascii=False, separators=(',', ':')); f.write(';')
s1 = full['stats']['1y'] or {}
print('1m sigma', round(full['risk']['sigma_1m'] * 100, 2), '% | 1y return', round((s1.get('cum_return') or 0) * 100, 1), '% sharpe', round(s1.get('sharpe') or 0, 2), '| leverage', round(full['leverage'], 2))
print('sectors', [(r['name'], round(r['weight'], 3)) for r in full['exposure']['sectors']], 'unclassified', round(full['exposure']['diversified_unclassified'], 3))
print('themes', [(r['name'], round(r['direct'], 3)) for r in full['exposure']['themes'] if r['direct'] > 0])
print('subs', [(r['name'], round(r['weight'], 3)) for r in full['exposure']['subindustries'][:8]])
print('3m decomp', {k: round(v * 100, 2) for k, v in full['decomp']['3m'].items() if not isinstance(v, dict)})
print('moves', [(i['ticker'], i['moves']['basis'][:3], round(i['moves']['sigma_1m'] * 100, 1), i['moves'].get('earnings'), i['moves'].get('earnings_move') and round(i['moves']['earnings_move'] * 100, 1)) for i in full['instruments']])
