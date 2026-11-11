"""Automated event discovery and blind rating with the Claude API.

For each sub-industry: pull the last week's headlines from Google News RSS, ask Claude to extract dated catalysts
that moved the group (not single-stock noise) and rate each one with the same rubric and worked examples used for
the hand ratings. Claude never sees price data, so the ratings stay blind. New events are written to
research/events_auto.json and their ratings to research/event_ratings.json with {"auto": true, "reviewed": false}.
Unreviewed auto events are quarantined by calibrate.py: half weight in stances, excluded from the fits and backtest.

Usage:  python auto_events.py [--dry-run] [--max-buckets N] [--days 7]
Needs ANTHROPIC_API_KEY (or another credential the SDK resolves). Exits 0 without doing anything if none is set.
"""
import os, sys, json, re, time, html, datetime as dt, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from common import load_taxonomy, all_sectors, ROOT

MODEL = 'claude-opus-5-5'
DAYS = 7
EVENTS_PATH = os.path.join(ROOT, 'research', 'events_auto.json')
LOG_PATH = os.path.join(ROOT, 'data', 'processed', 'auto_events_last_run.txt')
_log_lines = []
def log(msg):
    print(msg, flush=True); _log_lines.append(str(msg))
RATINGS_PATH = os.path.join(ROOT, 'research', 'event_ratings.json')
UA = 'Mozilla/5.0 (CapitalRotationTracker research bot)'
DRIVER_TYPES = ['policy', 'rates', 'macro', 'commodity', 'tech_cycle', 'earnings', 'geopolitical', 'regulatory', 'positioning', 'election', 'pandemic', 'ma']

SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['events'],
    'properties': {'events': {'type': 'array', 'maxItems': 3, 'items': {
        'type': 'object', 'additionalProperties': False,
        'required': ['date', 'title', 'summary', 'evidence', 'direction', 'magnitude', 'driver_type', 'source_title', 'source_url', 'e', 'p', 'b', 'c', 'why'],
        'properties': {
            'date': {'type': 'string', 'description': 'YYYY-MM-DD of the catalyst'},
            'title': {'type': 'string'}, 'summary': {'type': 'string', 'description': '2-4 sentences: what happened and which names moved'},
            'evidence': {'type': 'string', 'description': 'one checkable data point from the headlines'},
            'direction': {'type': 'string', 'enum': ['in', 'out', 'mixed']}, 'magnitude': {'type': 'string', 'enum': ['major', 'moderate', 'minor']},
            'driver_type': {'type': 'string', 'enum': DRIVER_TYPES},
            'source_title': {'type': 'string'}, 'source_url': {'type': 'string'},
            'e': {'type': 'number', 'minimum': -5, 'maximum': 5}, 'p': {'type': 'integer', 'minimum': 1, 'maximum': 3},
            'b': {'type': 'integer', 'minimum': 1, 'maximum': 3}, 'c': {'type': 'integer', 'minimum': 1, 'maximum': 3},
            'why': {'type': 'string', 'description': 'one short clause justifying the rating'},
        }}}}}

def load_json(p, default):
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else default

def fetch_news(query, days):
    q = urllib.parse.quote(f'{query} when:{days}d')
    url = f'https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        xml = urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        print('  news fetch failed:', e); return []
    items = []
    try:
        root = ET.fromstring(xml)
        for it in root.iter('item'):
            title = html.unescape(it.findtext('title') or '')
            link = it.findtext('link') or ''
            pub = it.findtext('pubDate') or ''
            src = it.find('source'); src = src.text if src is not None else ''
            desc = re.sub('<[^>]+>', ' ', html.unescape(it.findtext('description') or ''))
            try: d = dt.datetime.strptime(pub[:16], '%a, %d %b %Y').date().isoformat()
            except Exception: d = pub[:16]
            items.append({'date': d, 'title': title, 'source': src, 'url': link, 'snippet': desc[:300]})
    except ET.ParseError as e:
        print('  bad RSS:', e)
    return items[:40]

def examples(ratings, events_by_id, n_per_sector=3):
    out, seen = [], {}
    for eid, r in ratings.items():
        if eid.startswith('_') or r.get('auto'): continue
        e = events_by_id.get(eid)
        if not e: continue
        s = e.get('sector', '?')
        if seen.get(s, 0) >= n_per_sector: continue
        seen[s] = seen.get(s, 0) + 1
        out.append(f"- [{e['sector']} / {', '.join(e.get('subindustries') or [])}] {e['date']} {e['title']} -> e={r['e']} p={r['p']} b={r['b']} c={r['c']} ({r.get('why', '')})")
    return '\n'.join(out)

def system_prompt(rubric, ex):
    return f"""You are the research analyst for a sector-rotation tracker. You extract dated catalysts from news headlines for one sub-industry at a time and rate how material each one is to that sub-industry's fundamentals, using a fixed rubric. You never see price data for the period after the event; rate on fundamentals only.

RUBRIC
- e: {rubric['e']}
- p: {rubric['p']}
- b: {rubric['b']}
- c: {rubric['c']}
- Formula: {rubric['formula']}
- Anchors: {rubric['anchors']}

WHAT COUNTS AS AN EVENT
- A dated catalyst (announcement, earnings print, ruling, policy, deal, data release, price shock) that affected the sub-industry as a group, or its largest member. Not opinion pieces, previews, analyst upgrades of one small stock, or generic market commentary.
- At most 3 per sub-industry per week; return an empty list when nothing qualifies. Fewer, better events beat more.
- The date is the day the news broke. Use only facts present in the headlines and snippets; do not invent figures. Put one checkable figure from the headlines in 'evidence' when there is one, otherwise describe qualitatively.
- 'source_url' and 'source_title' must come from the supplied headlines.
- Do not duplicate events already covered (a list is supplied).

WORKED EXAMPLES OF RATINGS (sector / sub-industries, date, title -> components, reason)
{ex}
"""

def user_prompt(sec, si, news, existing):
    lines = [f"SUB-INDUSTRY: {si['name']} (id {si['id']}) in sector {sec['name']} (id {sec['id']})",
             f"Members: {', '.join(si['basket'][:20])}", f"Known drivers: {'; '.join(si.get('drivers', []))}", '',
             'EVENTS ALREADY COVERED (last 45 days, do not repeat):']
    lines += [f"- {e['date']} {e['title']}" for e in existing] or ['- none']
    lines += ['', f'HEADLINES (last {DAYS} days):']
    lines += [f"- {n['date']} | {n['source']} | {n['title']} | {n['snippet'][:160]} | {n['url']}" for n in news]
    lines += ['', 'Return the qualifying catalysts as JSON matching the schema.']
    return '\n'.join(lines)

def similar(a, b):
    ta = set(re.findall(r'[a-z0-9]+', a.lower())); tb = set(re.findall(r'[a-z0-9]+', b.lower()))
    return len(ta & tb) / max(1, len(ta | tb))

def save_log():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, 'w', encoding='utf-8') as f:
        f.write(f'run {dt.datetime.now():%Y-%m-%d %H:%M} model {MODEL}\n')
        f.write('\n'.join(_log_lines) + '\n')

def main():
    args = sys.argv[1:]
    dry = '--dry-run' in args
    max_b = int(args[args.index('--max-buckets') + 1]) if '--max-buckets' in args else 10 ** 6
    global DAYS
    if '--days' in args: DAYS = int(args[args.index('--days') + 1])
    client = None
    if not dry:
        try:
            import anthropic
            client = anthropic.Anthropic()
        except Exception as e:
            log(f'Claude SDK/client unavailable, nothing done: {e}'); save_log(); return 0
        if not (os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC_AUTH_TOKEN')):
            log('No ANTHROPIC_API_KEY set; skipping automated event scoring.'); save_log(); return 0
    T = load_taxonomy()
    ratings = load_json(RATINGS_PATH, {})
    rubric = ratings.get('_rubric', {})
    all_events = []
    import glob
    for fp in sorted(glob.glob(os.path.join(ROOT, 'research', 'events_*.json'))):
        all_events += load_json(fp, {}).get('events', [])
    events_by_id = {e['id']: e for e in all_events}
    auto = load_json(EVENTS_PATH, {'events': []})
    today = dt.date.today(); cutoff = (today - dt.timedelta(days=45)).isoformat()
    sysp = system_prompt(rubric, examples(ratings, events_by_id))
    added = 0; calls = 0; usage_in = usage_out = 0
    for sec in all_sectors(T):
        for si in sec['subindustries']:
            if calls >= max_b: break
            q = f"{si['name'].split('(')[0].strip()} " + ' OR '.join(si['basket'][:4]) + ' stock'
            news = fetch_news(q, DAYS); time.sleep(1.0)
            existing = [e for e in all_events if si['id'] in (e.get('subindustries') or []) and e['date'] >= cutoff]
            log(f"{si['id']}: {len(news)} headlines, {len(existing)} recent known events")
            if not news: continue
            if dry:
                calls += 1; continue
            up = user_prompt(sec, si, news, existing)
            try:
                resp = client.beta.messages.create(
                    model=MODEL, max_tokens=4000,
                    betas=['server-side-fallback-2026-07-01'], fallbacks='default',
                    system=[{'type': 'text', 'text': sysp, 'cache_control': {'type': 'ephemeral'}}],
                    messages=[{'role': 'user', 'content': up}],
                    output_config={'format': {'type': 'json_schema', 'schema': SCHEMA}})
            except Exception as e:
                import anthropic
                if isinstance(e, anthropic.RateLimitError): print('  rate limited, sleeping 60s'); time.sleep(60); continue
                log(f'  API error: {type(e).__name__} {str(e)[:300]}'); continue
            calls += 1; time.sleep(0.5)
            usage_in += resp.usage.input_tokens; usage_out += resp.usage.output_tokens
            if resp.stop_reason == 'refusal':
                log('  model declined this batch'); continue
            text = next((b.text for b in resp.content if b.type == 'text'), None)
            if not text: continue
            try: data = json.loads(text)
            except json.JSONDecodeError: print('  bad JSON from model'); continue
            for k, ev in enumerate(data.get('events', [])):
                if not re.match(r'^\d{4}-\d{2}-\d{2}$', ev['date']): continue
                dup = any(similar(ev['title'], e['title']) > 0.5 and abs((dt.date.fromisoformat(ev['date']) - dt.date.fromisoformat(e['date'])).days) <= 5
                          for e in all_events if si['id'] in (e.get('subindustries') or []) and re.match(r'^\d{4}-\d{2}-\d{2}$', e['date']))
                if dup: print('  duplicate skipped:', ev['title'][:70]); continue
                eid = f"auto-{si['id']}-{ev['date']}-{k}"
                if eid in events_by_id: eid += f"-{int(time.time()) % 10000}"
                rec = {'id': eid, 'date': ev['date'], 'end_date': None, 'sector': sec['id'], 'subindustries': [si['id']], 'direction': ev['direction'], 'magnitude': ev['magnitude'],
                       'driver_type': ev['driver_type'], 'title': ev['title'], 'summary': ev['summary'], 'evidence': ev['evidence'],
                       'sources': [{'title': ev['source_title'], 'url': ev['source_url']}], 'auto': True, 'added': today.isoformat(), 'model': MODEL}
                auto['events'].append(rec); all_events.append(rec); events_by_id[eid] = rec
                ratings[eid] = {'e': ev['e'], 'p': int(ev['p']), 'b': int(ev['b']), 'c': int(ev['c']), 'why': ev['why'], 'auto': True, 'reviewed': False, 'model': MODEL, 'added': today.isoformat()}
                added += 1; log(f"  + {si['id']} {ev['date']} {ev['title'][:80]} -> e={ev['e']} p={ev['p']} b={ev['b']} c={ev['c']}")
    if not dry and added:
        json.dump(auto, open(EVENTS_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        json.dump(ratings, open(RATINGS_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    log(f"done: {calls} model calls, {added} events added, tokens in {usage_in} out {usage_out}" + (' (dry run)' if dry else ''))
    save_log(); return 0

if __name__ == '__main__':
    sys.exit(main())
