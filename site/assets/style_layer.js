/* Style layer: momentum vs defensive cohorts inside each sub-industry (window.CRT_STYLE from data/style.js).
   app.js calls CRTStyle.drawSector / drawHistory / bucketHtml / tableCols; everything degrades to nothing when style.js is absent. */
(function () {
  const ST = window.CRT_STYLE || null;
  const C = () => window.CRTCharts;
  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pct = (x, d = 1) => x == null || isNaN(x) ? '–' : `${x > 0 ? '+' : ''}${(x * 100).toFixed(d)}%`;
  const sig = (z, d = 1) => z == null || isNaN(z) ? '–' : `${z > 0 ? '+' : ''}${z.toFixed(d)}σ`;
  const cls = (x) => x == null ? 'muted' : x > 0 ? 'pos' : x < 0 ? 'neg' : '';
  const from = (dates, start) => { let i = 0; while (i < dates.length && dates[i] < start) i++; return i; };
  const rebase = (arr, i0) => { let j = i0; while (j < arr.length && arr[j] == null) j++; if (j >= arr.length) return arr.map(() => null); const b = arr[j]; return arr.map((v, i) => (i < i0 || v == null ? null : (v / b) * 100)); };
  const bucket = (id) => (ST && ST.buckets[id]) || null;

  function regimeWord(b) {
    if (!b || b.spread_3m == null) return '';
    const z = b.spread_z10y; const s3 = b.spread_3m;
    if (s3 > 0.05 && (z == null || z > 0.5)) return 'momentum leading, extended';
    if (s3 > 0.05) return 'momentum leading';
    if (s3 < -0.05 && (z == null || z < -0.5)) return 'defensives leading, extended';
    if (s3 < -0.05) return 'defensives leading';
    return 'balanced';
  }
  function tiltNow(b) { const t = b && b.hf_tilt; if (!t) return null; const ks = Object.keys(t).sort(); if (!ks.length) return null; const last = t[ks[ks.length - 1]]; const prev = ks.length > 1 ? t[ks[ks.length - 2]] : null; return { period: ks[ks.length - 1], tilt: last, chg: prev == null ? null : last - prev }; }

  /** Stats row + cohort lists for a bucket card on the sector page. */
  function bucketHtml(id) {
    const b = bucket(id); if (!b) return '';
    const hf = tiltNow(b); const names = (arr) => arr.map((t) => `<span class="f" title="${esc(scoreTitle(b, t))}">${esc(t)}</span>`).join('');
    return `<div class="style-row"><div class="style-head"><span class="l">Momentum vs defensive inside this bucket</span><span class="muted small">${esc(regimeWord(b))} · cohorts since ${esc(b.first.slice(0, 4))}</span></div>
      <div class="stats">
        <div class="stat"><div class="l">Spread 1m</div><div class="v ${cls(b.spread_1m)}">${pct(b.spread_1m)}</div></div>
        <div class="stat"><div class="l">Spread 3m</div><div class="v ${cls(b.spread_3m)}">${pct(b.spread_3m)}</div></div>
        <div class="stat"><div class="l">Spread 12m</div><div class="v ${cls(b.spread_12m)}">${pct(b.spread_12m)}</div></div>
        <div class="stat"><div class="l">Spread z (10y)</div><div class="v ${cls(b.spread_z10y)}">${sig(b.spread_z10y)}</div></div>
        <div class="stat"><div class="l">Mom. share of cap</div><div class="v">${b.mom_share == null ? '–' : (b.mom_share * 100).toFixed(0) + '%'}</div><div class="tier">avg ${b.mom_share_mean == null ? '–' : (b.mom_share_mean * 100).toFixed(0) + '%'}</div></div>
        <div class="stat"><div class="l">HF tilt to mom.</div><div class="v">${hf ? (hf.tilt * 100).toFixed(0) + '%' : '–'}</div><div class="tier">${hf ? (hf.chg == null ? '' : (hf.chg > 0 ? '+' : '') + (hf.chg * 100).toFixed(1) + 'pp q/q · ') + hf.period.slice(0, 7) : 'no 13F'}</div></div>
      </div>
      <div class="cohorts"><div><span class="tier">Momentum cohort</span><div class="factors">${names(b.momentum_names)}</div></div><div><span class="tier">Defensive cohort</span><div class="factors">${names(b.defensive_names)}</div></div>${b.core_names.length ? `<div><span class="tier">Core (neither)</span><div class="factors">${names(b.core_names)}</div></div>` : ''}</div></div>`;
  }
  function scoreTitle(b, t) { const s = b.scores && b.scores[t]; if (!s) return t; return `${t}: 12-1m momentum ${pct(s.mom, 0)}, vol ${(s.vol * 100).toFixed(0)}%, beta ${s.beta}, style score ${s.style > 0 ? '+' : ''}${s.style}`; }

  /** Sector page chart: spread lines for every sub-industry in the sector; with a selection, that bucket's momentum / defensive / spread. */
  function drawSector(sec, subs, selId, start, regimes) {
    const card = $('#styleCard'); if (!ST || !card) return;
    const have = subs.filter((b) => bucket(b.id)); if (!have.length) { card.style.display = 'none'; return; } card.style.display = '';
    const s0 = start < '1996-01-01' ? '1996-01-01' : start;
    if (selId && bucket(selId)) {
      const b = bucket(selId); const h = b.history; const i0 = from(h.dates, s0); const labels = h.dates.slice(i0);
      C().lines('styleChart', labels, [
        { label: `${b.name}: momentum cohort`, data: rebase(h.mom.slice(i0), 0) },
        { label: `${b.name}: defensive cohort`, data: rebase(h.def.slice(i0), 0) },
        { label: 'Spread (momentum minus defensive)', data: rebase(h.spread.slice(i0), 0), color: C().tok().ink, width: 2.5 },
      ], { fmt: (v) => v.toFixed(1), yTick: (v) => v, refLine: 100, regimes, log: true, legend: true });
      $('#styleNote').textContent = `${b.name}: cap-weighted top-third (momentum) and bottom-third (defensive) cohorts, re-sorted monthly, rebased to 100 at the start of the range (log scale). The black line is the spread: rising = money favouring the high-momentum, high-beta names in the bucket; falling = favouring the steady, low-vol names.`;
    } else {
      // union of monthly dates across buckets (they share the month-end calendar; take the longest)
      const longest = have.map((b) => bucket(b.id)).sort((a, b) => b.history.dates.length - a.history.dates.length)[0];
      const i0 = from(longest.history.dates, s0); const labels = longest.history.dates.slice(i0);
      const series = have.map((sb) => { const b = bucket(sb.id); const map = new Map(b.history.dates.map((d, i) => [d, b.history.spread[i]])); return { label: b.name, data: rebase(labels.map((d) => (map.has(d) ? map.get(d) : null)), 0), emphasis: true }; });
      C().lines('styleChart', labels, series, { fmt: (v) => v.toFixed(1), yTick: (v) => v, refLine: 100, regimes, log: true, legend: true });
      $('#styleNote').textContent = 'Each line is one sub-industry\'s momentum-minus-defensive spread: the cap-weighted top third of its members by style score divided by the bottom third, re-sorted monthly, rebased to 100 at the start of the range (log scale). Up = capital inside that bucket favouring its high-momentum names; down = favouring its defensive names. Pick a sub-industry above to see both cohorts.';
    }
    // sector summary line under the chart
    const ss = ST.sectors[sec.id]; const sum = $('#styleSum');
    if (sum) sum.innerHTML = ss ? `Sector roll-up (cap-weighted across ${ss.buckets.length} buckets): spread 3m <b class="${cls(ss.spread_3m)}">${pct(ss.spread_3m)}</b> · 12m <b class="${cls(ss.spread_12m)}">${pct(ss.spread_12m)}</b> · z vs 10y <b class="${cls(ss.spread_z10y)}">${sig(ss.spread_z10y)}</b> · market-wide 3m <b class="${cls(ST.market.spread_3m)}">${pct(ST.market.spread_3m)}</b>` : '';
  }

  /** History page: market-wide momentum vs defensive since 1996 plus the Fama-French backdrop, and a by-sector table. */
  function drawHistory(start, regimes) {
    if (!ST || !$('#styleMktChart')) return;
    const s0 = start <= '1999-01-01' ? '1996-01-01' : start; const m = ST.market.history; const i0 = from(m.dates, s0); const labels = m.dates.slice(i0);
    C().lines('styleMktChart', labels, [
      { label: 'Momentum cohort (top third of every bucket)', data: rebase(m.mom.slice(i0), 0) },
      { label: 'Defensive cohort (bottom third)', data: rebase(m.def.slice(i0), 0) },
      { label: 'Spread: momentum minus defensive', data: rebase(m.spread.slice(i0), 0), color: C().tok().ink, width: 2.5 },
    ], { fmt: (v) => v.toFixed(1), yTick: (v) => v, refLine: 100, regimes, log: true, legend: true });
    if (ST.ff && ST.ff.dates) {
      const f = ST.ff; const j0 = from(f.dates, s0); const fl = f.dates.slice(j0);
      C().lines('styleFFChart', fl, [
        { label: 'Momentum factor (winners − losers)', data: rebase(f.MOM.slice(j0), 0) },
        { label: 'Quality (robust − weak profitability)', data: rebase(f.RMW.slice(j0), 0) },
        { label: 'Value (value − growth)', data: rebase(f.HML.slice(j0), 0) },
        { label: 'Size (small − big)', data: rebase(f.SMB.slice(j0), 0), emphasis: false },
      ], { fmt: (v) => v.toFixed(1), yTick: (v) => v, refLine: 100, regimes, log: true, legend: true });
    }
    const k = $('#styleKpis'); const mk = ST.market; const hf = tiltNow(mk);
    if (k) k.innerHTML = [
      ['Market style, 3m', pct(mk.spread_3m), `momentum minus defensive · 12m ${pct(mk.spread_12m)}`],
      ['vs 10-year trend', sig(mk.spread_z10y), 'z of the log spread against its trailing ten years'],
      ['Fama-French momentum', ST.ff && ST.ff.mom_3m != null ? pct(ST.ff.mom_3m) : '–', ST.ff ? `3m · 12m ${pct(ST.ff.mom_12m)} · through ${ST.ff.asof}` : ''],
      ['Fama-French quality', ST.ff && ST.ff.rmw_3m != null ? pct(ST.ff.rmw_3m) : '–', ST.ff ? `3m · 12m ${pct(ST.ff.rmw_12m)}` : ''],
      ['HF tilt to momentum', hf ? `${(hf.tilt * 100).toFixed(0)}%` : '–', hf ? `of 13F value in momentum cohorts${hf.chg == null ? '' : ` · ${hf.chg > 0 ? '+' : ''}${(hf.chg * 100).toFixed(1)}pp q/q`} · ${hf.period.slice(0, 7)}` : ''],
    ].map(([l, v, d]) => `<div class="kpi"><div class="label">${esc(l)}</div><div class="value">${esc(v)}</div><div class="delta">${esc(d)}</div></div>`).join('');
    const tbl = $('#styleTable');
    if (tbl) {
      const rows = Object.values(ST.buckets).sort((a, b) => (b.spread_3m || 0) - (a.spread_3m || 0));
      tbl.innerHTML = `<table class="data sticky-head"><thead><tr><th class="l">Sector</th><th class="l">Sub-industry</th><th>Spread 3m</th><th>Spread 12m</th><th>z (10y)</th><th>Mom. cap share</th><th>HF tilt</th><th class="l">Momentum cohort</th><th class="l">Defensive cohort</th></tr></thead><tbody>${rows.map((b) => { const t = tiltNow(b); return `<tr class="row" data-href="sector.html?s=${b.sector}#${b.id}"><td class="l sector">${esc(b.sector_name)}</td><td class="l"><b>${esc(b.name)}</b></td><td class="num ${cls(b.spread_3m)}">${pct(b.spread_3m)}</td><td class="num ${cls(b.spread_12m)}">${pct(b.spread_12m)}</td><td class="num ${cls(b.spread_z10y)}">${sig(b.spread_z10y)}</td><td class="num">${b.mom_share == null ? '–' : (b.mom_share * 100).toFixed(0) + '%'}</td><td class="num">${t ? (t.tilt * 100).toFixed(0) + '%' : '–'}</td><td class="l small">${esc(b.momentum_names.slice(0, 6).join(' '))}</td><td class="l small">${esc(b.defensive_names.slice(0, 6).join(' '))}</td></tr>`; }).join('')}</tbody></table>`;
      tbl.querySelectorAll('tr.row').forEach((tr) => tr.addEventListener('click', () => (location.href = tr.dataset.href)));
    }
  }

  /** Tracker table: extra columns and a KPI. */
  function tableCols() { return ST ? [{ k: 'style_3m', l: 'Mom−Def 3m' }, { k: 'style_z', l: 'Mom−Def z' }] : []; }
  function tableVal(id, k) { const b = bucket(id); if (!b) return null; return k === 'style_3m' ? b.spread_3m : k === 'style_z' ? b.spread_z10y : null; }
  function tableCell(id, k) { const b = bucket(id); if (!b) return '<td class="num"><span class="muted">–</span></td>'; const v = tableVal(id, k); const title = `momentum: ${b.momentum_names.slice(0, 5).join(' ')} · defensive: ${b.defensive_names.slice(0, 5).join(' ')} · ${regimeWord(b)}`; return `<td class="num ${cls(v)}" title="${esc(title)}">${k === 'style_3m' ? pct(v) : sig(v)}</td>`; }
  function kpi() { if (!ST) return null; const mk = ST.market; return ['Momentum vs defensive, 3m', pct(mk.spread_3m), `market-wide spread · ${sig(mk.spread_z10y)} vs 10y · ${mk.spread_3m > 0.03 ? 'momentum names leading' : mk.spread_3m < -0.03 ? 'defensive names leading' : 'balanced'}`]; }

  window.CRTStyle = { ready: !!ST, bucketHtml, drawSector, drawHistory, tableCols, tableVal, tableCell, kpi };
})();
