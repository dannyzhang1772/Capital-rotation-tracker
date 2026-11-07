import json, collections, sys
t = json.load(open('pipeline/taxonomy.json'))
seen = collections.defaultdict(list); n=0; etfs=set()
for s in t['sectors']+t['context_sectors']:
    etfs.add(s['sector_etf']); etfs.update(s.get('alt_etfs',[]))
    if s.get('sector_history_etf'): etfs.add(s['sector_history_etf'])
    for si in s['subindustries']:
        n+=1
        for k in ('etf','reference_etf'):
            if si.get(k): etfs.add(si[k])
        etfs.update(si.get('alt_etfs',[]))
        for tk in si['basket']:
            seen[tk].append(f"{s['id']}/{si['id']}")
for th in t['themes']:
    if th.get('etf'): etfs.add(th['etf'])
    etfs.update(th.get('alt_etfs',[]))
dups = {k:v for k,v in seen.items() if len(v)>1}
print("subindustries:", n, "| deep-dive:", sum(len(s['subindustries']) for s in t['sectors']), "| unique basket tickers:", len(seen), "| ETFs:", len(etfs))
print("DUPLICATES:", dups or "none")
sys.exit(1 if dups else 0)
