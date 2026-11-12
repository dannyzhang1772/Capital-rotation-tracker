"""Run the Trading tab analytics from the command line.

  python portfolio.py --sample   fictional book in research/sample_portfolio.json -> site/data/portfolio.js (published)
  python portfolio.py            private holdings in research/private/ (never committed) -> site/data/portfolio_full.js
                                 (gitignored, for inspection only; the live portal serves real books from the backend)
The analytics live in portfolio_core.py, which the hosted backend also uses.
"""
import os, sys, json
from common import ROOT, SITE_DATA
from portfolio_core import analyze

def write(name, obj):
    with open(os.path.join(SITE_DATA, name), 'w', encoding='utf-8') as f:
        f.write('window.CRT_PORTFOLIO = '); json.dump(obj, f, ensure_ascii=False, separators=(',', ':')); f.write(';')

if '--sample' in sys.argv:
    # the public page's fictional book: full detail is fine because nobody owns it
    holdings = json.load(open(os.path.join(ROOT, 'research', 'sample_portfolio.json'), encoding='utf-8'))
    # give the margin account a loan so the leverage maths has something to show: net value = 70% of its positions
    full = analyze(holdings, log=print)
    margin = next((a for a in full['accounts'] if a['id'] == 'Margin'), None)
    if margin:
        holdings['accounts']['Margin']['total'] = round(margin['value_base'] * 0.7, 2)
        for a in full['accounts']:
            if a['id'] != 'Margin': holdings['accounts'][a['id']]['total'] = round(a['value_base'], 2)
        full = analyze(holdings, log=print)
    full['label'] = 'Sample book'; full['sample'] = True
    write('portfolio.js', full); out = 'site/data/portfolio.js (fictional sample, published)'
else:
    H = json.load(open(os.path.join(ROOT, 'research', 'private', 'portfolio_holdings.json'), encoding='utf-8'))
    holdings = {
        'label': 'My portfolio', 'base_currency': H.get('base_currency', 'CAD'),
        'accounts': {k: {'type': v['type'], 'total': v.get('total_cad')} for k, v in H['accounts'].items()},
        'positions': [{'ticker': p['ticker'], 'yahoo': p.get('yahoo'), 'name': p.get('name'), 'account': p['account'], 'currency': p['currency'], 'qty': p['qty'], 'price': p.get('price'), 'value': p.get('value'),
                       'cost': (p['value'] - p['all_time_return']) if p.get('all_time_return') is not None else None, 'first_buy': p.get('first_buy')} for p in H['positions']],
        'instruments': {'UBER': {'yahoo': 'UBER', 'fx_hedged': True}},   # the Uber line is a CAD-hedged CDR: model it on the US shares
    }
    full = analyze(holdings, log=print)
    write('portfolio_full.js', full); out = 'site/data/portfolio_full.js (private, gitignored; not loaded by the site)'
print('wrote', out)
s1 = full['stats']['1y'] or {}
print('1m sigma', round(full['risk']['sigma_1m'] * 100, 2), '% | 1y return', round((s1.get('cum_return') or 0) * 100, 1), '% sharpe', round(s1.get('sharpe') or 0, 2), '| leverage', round(full['leverage'], 2))
print('sectors', [(r['name'], round(r['weight'], 3)) for r in full['exposure']['sectors']], 'unclassified', round(full['exposure']['diversified_unclassified'], 3))
print('themes', [(r['name'], round(r['direct'], 3)) for r in full['exposure']['themes'] if r['direct'] > 0])
print('subs', [(r['name'], round(r['weight'], 3)) for r in full['exposure']['subindustries'][:8]])
print('3m decomp', {k: round(v * 100, 2) for k, v in full['decomp']['3m'].items() if not isinstance(v, dict)})
print('moves', [(i['ticker'], i['moves']['basis'][:3], round(i['moves']['sigma_1m'] * 100, 1), i['moves'].get('earnings'), i['moves'].get('earnings_move') and round(i['moves']['earnings_move'] * 100, 1)) for i in full['instruments']])
