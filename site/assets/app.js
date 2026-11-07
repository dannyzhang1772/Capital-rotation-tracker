/* Page logic for the Capital Rotation Tracker. Data arrives as window.CRT_* globals (site/data/*.js) so the site works from file:// and GitHub Pages alike. */
(function () {
  const S = window.CRT_SNAPSHOT || { buckets: [], sectors: [], themes: [], universe: {} };
  const H = window.CRT_HISTORY || { dates: [], buckets: {}, sectors: {}, themes: {} };
  const E = window.CRT_EVENTS || { events: [], regimes: [] };
  const N = window.CRT_NARRATIVES || {};
  const BL = window.CRT_BLURBS || {};
  const TAX = window.CRT_TAXONOMY || { sectors: [], context_sectors: [], themes: [] };
  const C = window.CRTCharts;
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pct = (x, d = 1, sign = true) => x == null || isNaN(x) ? '–' : `${sign && x > 0 ? '+' : ''}${(x * 100).toFixed(d)}%`;
  const wpct = (x, d = 2) => x == null || isNaN(x) ? '–' : `${(x * 100).toFixed(d)}%`;
  const sig = (z, d = 1) => z == null || isNaN(z) ? '–' : `${z > 0 ? '+' : ''}${z.toFixed(d)}σ`;
  const num = (x, d = 0) => x == null || isNaN(x) ? '–' : x.toLocaleString(undefined, { maximumFractionDigits: d });
  const bn = (x) => x == null ? '–' : x >= 1000 ? `$${(x / 1000).toFixed(2)}tn` : `$${x.toFixed(0)}bn`;
  const ord = (n) => { const v = n % 100; return n + (v >= 11 && v <= 13 ? 'th' : ['th', 'st', 'nd', 'rd'][Math.min(n % 10, 4) < 4 ? n % 10 : 0] || 'th'); };
  const cls = (x) => x == null ? 'muted' : x > 0 ? 'pos' : x < 0 ? 'neg' : '';
  const sectorById = Object.fromEntries(S.sectors.map((s) => [s.id, s]));
  const bucketById = Object.fromEntries(S.buckets.map((b) => [b.id, b]));
  const allTaxSectors = (TAX.sectors || []).concat(TAX.context_sectors || []);
  const taxSub = {}; allTaxSectors.forEach((s) => s.subindustries.forEach((si) => (taxSub[si.id] = si)));
  const params = new URLSearchParams(location.search);

  // ---------- theme ----------
  function initTheme() {
    let saved = null; try { saved = localStorage.getItem('crt-theme'); } catch (e) {}
    if (saved) document.documentElement.setAttribute('data-theme', saved);
    const btn = $('#themeBtn'); if (!btn) return;
    btn.addEventListener('click', () => {
      const cur = document.documentElement.getAttribute('data-theme') || 'dark';
      const next = cur === 'dark' ? 'light' : 'dark'; document.documentElement.setAttribute('data-theme', next);
      try { localStorage.setItem('crt-theme', next); } catch (e) {}
      render();
    });
  }
  function initNav() {
    const page = document.body.dataset.page; $$('header.top nav a').forEach((a) => { if (a.dataset.page === page) a.classList.add('active'); });
    const asof = $('#asof'); if (asof) asof.textContent = `Data as of ${S.asof || '–'} · generated ${S.generated || '–'}`;
    const sel = $('#sectorNav'); if (sel) { sel.innerHTML = '<option value="">Sector pages…</option>' + S.sectors.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join(''); sel.addEventListener('change', () => { if (sel.value) location.href = `sector.html?s=${sel.value}`; }); }
  }

  // ---------- shared pieces ----------
  function heatPill(label, heat) { const l = label || 'n/a'; return `<span class="pill ${esc(l)}" title="heat score ${heat == null ? '–' : heat}">${esc(l)}${heat == null ? '' : ' ' + Math.round(heat)}</span>`; }
  function wbar(w, mean) { // weight vs 10y mean bar: mean at 50%, scale 0..2x mean
    if (w == null || !mean) return '';
    const r = Math.max(0, Math.min(2, w / mean)); const left = r >= 1 ? 50 : 50 * r; const width = Math.abs(r - 1) * 50; const t = C.tok();
    return `<span class="bar" title="${wpct(w)} now vs ${wpct(mean)} 10-year average"><i style="left:${left}%;width:${width}%;background:${r >= 1 ? t.pos : t.neg}"></i></span>`;
  }
  function factorTags(b) {
    const emp = b.factors_empirical || {}; const ap = b.factors_apriori || {};
    const names = { growth: 'growth', size: 'small-cap', momentum: 'momentum', quality: 'quality', low_vol: 'low-vol', rates: 'rates', oil: 'oil', dollar: 'dollar' };
    const keys = Object.keys(emp).length ? Object.keys(names).filter((k) => emp[k] != null) : Object.keys(ap);
    return `<div class="factors">${keys.map((k) => {
      const v = Object.keys(emp).length ? emp[k] : ap[k]; const label = Object.keys(emp).length ? (k === 'rates' ? `${names[k]} ${v > 0 ? '+' : ''}${(v * 100).toFixed(2)}%/10bp` : `${names[k]} β ${v > 0 ? '+' : ''}${v.toFixed(2)}`) : `${names[k] || k} ${v > 0 ? '+' : ''}${v}`;
      return `<span class="f">${esc(label)}</span>`; }).join('')}</div>`;
  }
  function blurbHtml(id) {
    const b = BL[id]; if (!b) return '<div class="blurb muted">Catalyst note not written yet.</div>';
    return `<div class="blurb"><p><b>Stance:</b> ${esc(b.stance)}</p><p><b>Capital flows in if:</b> ${esc(b.inflow_if)}</p><p><b>Capital flows out if:</b> ${esc(b.outflow_if)}</p>${b.watch && b.watch.length ? `<p><b>Watch:</b> ${b.watch.map(esc).join(' · ')}</p>` : ''}<p class="tier">Written ${esc(b.updated || '')}${b.stale ? ' · flagged stale by the pipeline' : ''}</p></div>`;
  }
  function eventsList(evts, opts = {}) {
    if (!evts.length) return '<p class="muted">No events recorded for this selection yet.</p>';
    return `<ul class="events">${evts.map((e) => `<li><div class="d">${esc(e.date)}${e.end_date ? `<br>→ ${esc(e.end_date)}` : ''}</div><div><div class="t">${esc(e.title)}</div><div class="s">${esc(e.summary)}</div>${e.evidence ? `<div class="e">Evidence: ${esc(e.evidence)}</div>` : ''}<div class="tags"><span class="tag ${esc(e.direction)}">${e.direction === 'in' ? 'capital in' : e.direction === 'out' ? 'capital out' : 'mixed'}</span><span class="tag">${esc(e.magnitude)}</span><span class="tag">${esc(e.driver_type)}</span>${(e.subindustries || []).map((s) => `<span class="tag">${esc(taxSub[s] ? taxSub[s].name : s)}</span>`).join('')}${opts.showSector ? `<span class="tag">${esc(sectorById[e.sector] ? sectorById[e.sector].name : e.sector)}</span>` : ''}${(e.sources || []).slice(0, 2).map((s) => s.url ? `<a class="tag" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || 'source')}</a>` : '').join('')}</div></div></li>`).join('')}</ul>`;
  }
  function sliceFrom(labels, start) { let i = 0; while (i < labels.length && labels[i] < start) i++; return i; }
  function rangeStart(key) {
    const last = H.dates[H.dates.length - 1] || '2026-01-01'; const y = +last.slice(0, 4);
    return { all: '1999-01-01', sub: '2006-01-01', y10: `${y - 10}-01-01`, y5: `${y - 5}-01-01`, y2: `${y - 2}-01-01` }[key] || '1999-01-01';
  }
  function rebase(arr, i0) { // index series rebased to 100 at i0 (first non-null at/after i0)
    let j = i0; while (j < arr.length && arr[j] == null) j++; if (j >= arr.length) return arr.map(() => null); const b = arr[j];
    return arr.map((v, i) => (i < i0 || v == null ? null : (v / b) * 100));
  }

  // ---------- dashboard ----------
  function renderDashboard() {
    const deep = S.buckets.filter((b) => b.deep_dive);
    const hot = deep.filter((b) => b.label === 'Hot').length, cold = deep.filter((b) => b.label === 'Cold').length;
    const top = [...deep].sort((a, b) => (b.rel_3m || -9) - (a.rel_3m || -9))[0]; const bot = [...deep].sort((a, b) => (a.rel_3m || 9) - (b.rel_3m || 9))[0];
    $('#kpis').innerHTML = [
      ['Universe market cap', bn((S.universe.cap_usd_tn || 0) * 1000), `${num(S.universe.n_tickers_priced)} tickers priced · ${S.buckets.length} buckets`],
      ['Hot sub-industries', `${hot}`, `of ${deep.length} deep-dive buckets · ${cold} cold`],
      ['Best 3-month rotation', top ? top.name : '–', top ? `${pct(top.rel_3m)} vs S&P 500` : ''],
      ['Worst 3-month rotation', bot ? bot.name : '–', bot ? `${pct(bot.rel_3m)} vs S&P 500` : ''],
      ['Data layers live', `${['prices', S.universe.flows_available ? 'ETF flows' : null, S.universe.f13_available ? '13F' : null].filter(Boolean).length}`, `${S.universe.flows_available ? 'ETF flows ✓' : 'ETF flows pending'} · ${S.universe.f13_available ? '13F ✓' : '13F pending'}`],
    ].map(([l, v, d]) => `<div class="kpi"><div class="label">${esc(l)}</div><div class="value ${String(v).length > 12 ? 'text' : ''}">${esc(v)}</div><div class="delta">${esc(d)}</div></div>`).join('');
    $('#heatstrip').innerHTML = S.sectors.map((s) => `<a href="sector.html?s=${s.id}"><div class="n">${esc(s.name)}</div><div class="m"><span>${heatPill(s.label, s.heat)}</span><span class="${cls(s.rel_3m)}">${pct(s.rel_3m)} 3m</span></div><div class="m"><span>wt ${wpct(s.weight, 1)}</span><span class="muted">10y avg ${wpct(s.weight_mean10y, 1)}</span></div></a>`).join('');

    const sel = $('#fSector'); sel.innerHTML = '<option value="">All sectors</option>' + S.sectors.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
    let sortKey = 'heat', sortAsc = false;
    const cols = [
      { k: 'sector_name', l: 'Sector', left: true }, { k: 'name', l: 'Sub-industry', left: true }, { k: 'heat', l: 'Heat' },
      { k: 'weight', l: 'Weight' }, { k: 'weight_z10y', l: 'vs 10y avg' }, { k: 'weight_pctile', l: 'Wt pctile' },
      { k: 'rel_1m', l: 'Rel 1m' }, { k: 'rel_3m', l: 'Rel 3m' }, { k: 'rel_12m', l: 'Rel 12m' }, { k: 'abs_ytd', l: 'YTD' },
      { k: 'flow_3m_pct_aum', l: 'ETF flow 3m' }, { k: 'hf_chg', l: 'HF share Δ' }, { k: 'next', l: 'Next catalyst', left: true },
    ];
    const val = (b, k) => k === 'flow_3m_pct_aum' ? (b.flows ? b.flows.flow_3m_pct_aum : null) : k === 'hf_chg' ? (b.f13 ? b.f13.hf_share_chg_qoq : null) : k === 'next' ? (BL[b.id] ? BL[b.id].inflow_if : '') : b[k];
    function draw() {
      const q = ($('#fSearch').value || '').toLowerCase(); const sec = sel.value; const onlyDeep = $('#fDeep').checked; const lab = $('#fLabel').value;
      let rows = S.buckets.filter((b) => (!sec || b.sector === sec) && (!onlyDeep || b.deep_dive) && (!lab || b.label === lab) && (!q || `${b.name} ${b.sector_name} ${(b.basket || []).join(' ')}`.toLowerCase().includes(q)));
      rows.sort((a, b) => { const va = val(a, sortKey), vb = val(b, sortKey); if (va == null && vb == null) return 0; if (va == null) return 1; if (vb == null) return -1; if (typeof va === 'string') return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va); return sortAsc ? va - vb : vb - va; });
      $('#tblHead').innerHTML = cols.map((c) => `<th class="${c.left ? 'l' : ''} ${sortKey === c.k ? 'sorted' + (sortAsc ? ' asc' : '') : ''}" data-k="${c.k}">${esc(c.l)}</th>`).join('');
      $('#tblBody').innerHTML = rows.map((b) => `<tr class="row" data-href="sector.html?s=${b.sector}#${b.id}">
        <td class="l sector">${esc(b.sector_name)}</td><td class="l"><b>${esc(b.name)}</b>${b.etf ? ` <span class="muted">${esc(b.etf)}</span>` : ''}</td>
        <td>${heatPill(b.label, b.heat)}</td><td class="num">${wpct(b.weight)}</td><td class="num"><span class="bar-cell">${wbar(b.weight, b.weight_mean10y)}<span class="${cls(b.weight_z10y)}">${sig(b.weight_z10y)}</span></span></td>
        <td class="num">${b.weight_pctile == null ? '–' : Math.round(b.weight_pctile * 100) + '%'}</td>
        <td class="num ${cls(b.rel_1m)}">${pct(b.rel_1m)}</td><td class="num ${cls(b.rel_3m)}">${pct(b.rel_3m)}</td><td class="num ${cls(b.rel_12m)}">${pct(b.rel_12m)}</td><td class="num ${cls(b.abs_ytd)}">${pct(b.abs_ytd)}</td>
        <td class="num ${cls(val(b, 'flow_3m_pct_aum'))}">${b.flows ? pct(b.flows.flow_3m_pct_aum) : '<span class="muted">–</span>'}</td>
        <td class="num ${cls(val(b, 'hf_chg'))}">${b.f13 && b.f13.hf_share_chg_qoq != null ? (b.f13.hf_share_chg_qoq > 0 ? '+' : '') + b.f13.hf_share_chg_qoq.toFixed(2) + 'pp' : '<span class="muted">–</span>'}</td>
        <td class="l next" title="${esc(val(b, 'next') || '')}">${esc((val(b, 'next') || '').length > 110 ? (val(b, 'next') || '').slice(0, 108) + '…' : (val(b, 'next') || ''))}</td></tr>`).join('');
      $$('#tblHead th').forEach((th) => th.addEventListener('click', () => { const k = th.dataset.k; if (sortKey === k) sortAsc = !sortAsc; else { sortKey = k; sortAsc = ['sector_name', 'name', 'next'].includes(k); } draw(); }));
      $$('#tblBody tr.row').forEach((tr) => tr.addEventListener('click', () => (location.href = tr.dataset.href)));
      $('#rowCount').textContent = `${rows.length} buckets`;
      // heat bar chart (top 12 / bottom 12 of the filtered set)
      const byHeat = rows.filter((b) => b.heat != null).sort((a, b) => b.heat - a.heat); const show = byHeat.length > 24 ? byHeat.slice(0, 12).concat(byHeat.slice(-12)) : byHeat;
      const box = $('#heatChart').parentElement; box.style.height = `${Math.max(220, show.length * 22 + 40)}px`;
      C.divergingBars('heatChart', show.map((b) => b.name), show.map((b) => b.heat), { fmt: (v) => `heat ${v}`, yTick: (v) => v });
    }
    ['#fSearch', '#fDeep', '#fLabel'].forEach((s) => $(s).addEventListener('input', draw)); sel.addEventListener('change', draw);
    draw();
    // themes
    $('#themes').innerHTML = S.themes.map((t) => `<div class="card"><div class="head" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b>${esc(t.name)}</b> ${heatPill(t.label, t.heat)} <span class="muted small">${t.etf ? esc(t.etf) + ' · ' : ''}since ${esc(t.theme_start || '')}</span></div>
      <div class="stats" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(90px,1fr));gap:6px;margin-top:8px"><div><div class="tier">weight</div><b>${wpct(t.weight, 1)}</b></div><div><div class="tier">rel 3m</div><b class="${cls(t.rel_3m)}">${pct(t.rel_3m)}</b></div><div><div class="tier">rel 12m</div><b class="${cls(t.rel_12m)}">${pct(t.rel_12m)}</b></div><div><div class="tier">YTD</div><b class="${cls(t.abs_ytd)}">${pct(t.abs_ytd)}</b></div></div>
      <div class="small muted" style="margin-top:6px">${esc((t.basket || []).slice(0, 12).join(', '))}${t.basket && t.basket.length > 12 ? '…' : ''}</div>${factorTags(t)}</div>`).join('');
  }

  // ---------- sector page ----------
  function renderSector() {
    const sid = params.get('s') || 'industrials'; const sec = sectorById[sid]; if (!sec) { $('#title').textContent = 'Unknown sector'; return; }
    const subs = sec.subindustries.map((id) => bucketById[id]).filter(Boolean);
    document.title = `${sec.name} · Capital Rotation Tracker`; $('#title').textContent = sec.name;
    $('#sub').innerHTML = `${sec.deep_dive ? 'Deep-dive sector' : 'Context sector'} · sector ETF ${esc(sec.etf)}${sec.history_etf && sec.history_etf !== sec.etf ? ` (history spliced from ${esc(sec.history_etf)})` : ''} · ${subs.length} sub-industries${sec.history_note ? ` · ${esc(sec.history_note)}` : ''}`;
    $('#kpis').innerHTML = [
      ['Heat', `${sec.label}`, `score ${sec.heat == null ? '–' : Math.round(sec.heat)}`],
      ['Weight in universe', wpct(sec.weight, 1), `10y avg ${wpct(sec.weight_mean10y, 1)} · ${sig(sec.weight_z10y)} · ${sec.weight_pctile == null ? '' : ord(Math.round(sec.weight_pctile * 100)) + ' pctile since 2006'}`],
      ['Relative to S&P 500', pct(sec.rel_3m), `3m · 12m ${pct(sec.rel_12m)} · YTD ${pct(sec.rel_ytd)}`],
      ['Absolute', pct(sec.abs_ytd), `YTD · 12m ${pct(sec.abs_12m)}`],
      ['ETF flows (3m)', sec.flows ? pct(sec.flows.flow_3m_pct_aum) : 'pending', sec.flows ? `of AUM · ${sig(sec.flows.flow_3m_z)} vs 5y` : 'issuer flow data loading'],
      ['Hedge fund share', sec.f13 && sec.f13.hf_share_pct != null ? `${sec.f13.hf_share_pct.toFixed(2)}%` : 'pending', sec.f13 ? ['of HF 13F value', sec.f13.hf_share_chg_qoq == null ? null : (sec.f13.hf_share_chg_qoq > 0 ? '+' : '') + sec.f13.hf_share_chg_qoq.toFixed(2) + 'pp q/q', sec.f13.last_period || null].filter(Boolean).join(' · ') : 'SEC 13F data loading'],
    ].map(([l, v, d]) => `<div class="kpi"><div class="label">${esc(l)}</div><div class="value">${esc(v)}</div><div class="delta">${esc(d)}</div></div>`).join('');

    // narrative
    const md = N[sid]; $('#narrative').innerHTML = md ? (window.marked ? marked.parse(md) : `<pre>${esc(md)}</pre>`) : '<p class="muted">Narrative pending: the research agents are still writing this sector history.</p>';

    // controls
    const subSel = $('#subSel'); subSel.innerHTML = '<option value="">All sub-industries (sector view)</option>' + subs.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
    const hash = location.hash.replace('#', ''); if (hash && bucketById[hash]) subSel.value = hash;
    let range = 'all';
    $$('#rangeSeg button').forEach((btn) => btn.addEventListener('click', () => { $$('#rangeSeg button').forEach((b) => b.classList.remove('on')); btn.classList.add('on'); range = btn.dataset.r; drawCharts(); }));
    subSel.addEventListener('change', () => { drawCharts(); drawEvents(); const b = subSel.value && bucketById[subSel.value]; if (b) { const el = document.getElementById(b.id); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); } });
    const secEvents = E.events.filter((e) => e.sector === sid);
    const regimes = E.regimes || [];

    function drawCharts() {
      const start = rangeStart(range); const i0 = sliceFrom(H.dates, start); const labels = H.dates.slice(i0); const cut = (a) => (a ? a.slice(i0) : null);
      const hs = H.sectors[sid] || {}; const selId = subSel.value; const selB = selId ? bucketById[selId] : null;
      const evts = (selId ? secEvents.filter((e) => (e.subindustries || []).includes(selId)) : secEvents.filter((e) => e.magnitude === 'major')).filter((e) => e.date >= start);
      // 1. weight history
      const wSeries = [{ label: `${sec.name} weight in universe`, data: cut(hs.w) ? cut(hs.w).map((v) => (v == null ? null : v * 100)) : [] }];
      if (selB && H.buckets[selId]) wSeries.push({ label: `${selB.name} weight`, data: cut(H.buckets[selId].w).map((v) => (v == null ? null : v * 100)) });
      C.lines('wChart', labels, wSeries, { fmt: (v) => `${v.toFixed(2)}%`, yTick: (v) => `${v}%`, regimes, zero: true });
      // 2. relative strength: sector ETF vs S&P (rebased) + sub-industries emphasis
      const rsSeries = [];
      const secRs = cut(hs.rs_etf || hs.rs_basket); if (secRs) rsSeries.push({ label: `${sec.name} (${hs.rs_etf ? sec.history_etf || sec.etf : 'basket'}) vs S&P 500`, data: rebase(secRs, 0), width: 2.5, color: C.tok().ink });
      subs.forEach((b) => { const hb = H.buckets[b.id]; if (!hb || !hb.rs) return; const emph = !selId || selId === b.id; rsSeries.push({ label: `${b.name}`, data: rebase(cut(hb.rs), 0), emphasis: selId ? emph : false, width: emph && selId ? 2.5 : 1.25 }); });
      C.lines('rsChart', labels, rsSeries, { fmt: (v) => v.toFixed(1), yTick: (v) => v, refLine: 100, regimes, events: evts, log: true, legend: !!selId });
      $('#rsNote').textContent = selId ? `${selB.name} highlighted; other sub-industries in gray. Rebased to 100 at the start of the range; log scale. ▲ markers are events from the timeline below (blue = capital in, red = out).` : 'Each line is a sub-industry divided by the S&P 500, rebased to 100 at the start of the range (log scale). Pick one sub-industry above to highlight it; gray lines are the others. ▲ markers = major sector events.';
      // 3. sub-industry vs sector weight share (what share of the sector's capital each sub takes)
      const shareSeries = subs.map((b) => { const hb = H.buckets[b.id]; if (!hb) return null; return { label: b.name, data: cut(hb.w).map((v, i) => { const sw = hs.w ? hs.w[i0 + i] : null; return v == null || !sw ? null : (v / sw) * 100; }) }; }).filter(Boolean);
      const cap8 = shareSeries.length > 8 ? (() => { const sorted = [...shareSeries].sort((a, b) => (b.data[b.data.length - 1] || 0) - (a.data[a.data.length - 1] || 0)); const keep = sorted.slice(0, 7); const rest = sorted.slice(7); const other = { label: 'Other sub-industries', data: labels.map((_, i) => rest.reduce((s, r) => s + (r.data[i] || 0), 0) || null) }; return keep.concat([other]); })() : shareSeries;
      C.stackedArea('shareChart', labels, cap8, { fmt: (v) => `${v.toFixed(1)}%`, yTick: (v) => `${v}%`, max: 100, regimes });
      // 4. ETF flows
      const flowSrc = selB && H.buckets[selId] && H.buckets[selId].flow ? H.buckets[selId].flow : hs.flow; const fc = $('#flowCard');
      if (flowSrc) { fc.style.display = ''; const fi = sliceFrom(flowSrc.dates.map((d) => d + '-01'), start); $('#flowTitle').textContent = `Net ETF creations/redemptions · ${selB && H.buckets[selId].flow ? (selB.etf || selB.reference_etf) : sec.etf} ($m per month)`; C.flowBars('flowChart', flowSrc.dates.slice(fi).map((d) => d + '-01'), flowSrc.flow.slice(fi), { fmt: (v) => `$${num(v)}m`, yTick: (v) => `$${num(v)}m`, regimes, label: 'net flow' }); }
      else fc.style.display = 'none';
      // 5. 13F hedge-fund share
      const f13 = selB && H.buckets[selId] && H.buckets[selId].f13 ? H.buckets[selId].f13 : hs.f13; const hc = $('#hfCard');
      if (f13 && f13.hf_share) { hc.style.display = ''; C.lines('hfChart', f13.periods, [{ label: 'share of hedge-fund 13F value', data: f13.hf_share }, { label: 'share of all 13F value', data: f13.share_of_13f || [] }], { fmt: (v) => `${v.toFixed(2)}%`, yTick: (v) => `${v}%`, zero: true }); }
      else hc.style.display = 'none';
      // macro strip
      if (H.macro && H.macro.ust10y) C.lines('macroChart', labels, [{ label: '10-year Treasury yield', data: cut(H.macro.ust10y) }, H.macro.fed_funds ? { label: 'Fed funds', data: cut(H.macro.fed_funds) } : null].filter(Boolean), { fmt: (v) => `${v.toFixed(2)}%`, yTick: (v) => `${v}%`, regimes, zero: true });
    }
    function drawEvents() {
      const selId = subSel.value; const evts = selId ? secEvents.filter((e) => (e.subindustries || []).includes(selId) || (!e.subindustries || !e.subindustries.length)) : secEvents;
      $('#evCount').textContent = `${evts.length} events`; $('#events').innerHTML = eventsList([...evts].reverse());
    }
    // bucket cards
    $('#buckets').innerHTML = subs.map((b) => `<div class="bucket-card" id="${b.id}"><div class="head"><h3>${esc(b.name)}</h3>${heatPill(b.label, b.heat)}<span class="muted small">${b.etf ? `ETF ${esc(b.etf)}${b.etf_start ? ' since ' + esc(b.etf_start.slice(0, 7)) : ''}` : b.reference_etf ? `reference ETF ${esc(b.reference_etf)} · basket-driven` : 'basket-driven'} · ${b.n_members_now} names · ${bn(b.cap_usd_bn)}</span></div>
      <div class="stats">
        <div class="stat"><div class="l">Weight</div><div class="v">${wpct(b.weight)}</div></div><div class="stat"><div class="l">vs 10y avg</div><div class="v ${cls(b.weight_z10y)}">${sig(b.weight_z10y)}</div></div><div class="stat"><div class="l">Wt percentile</div><div class="v">${b.weight_pctile == null ? '–' : Math.round(b.weight_pctile * 100) + '%'}</div></div>
        <div class="stat"><div class="l">Rel 3m</div><div class="v ${cls(b.rel_3m)}">${pct(b.rel_3m)}</div></div><div class="stat"><div class="l">Rel 12m</div><div class="v ${cls(b.rel_12m)}">${pct(b.rel_12m)}</div></div><div class="stat"><div class="l">RS z (3y)</div><div class="v ${cls(b.rs_z3y)}">${sig(b.rs_z3y)}</div></div>
        <div class="stat"><div class="l">Momentum 12-1</div><div class="v ${cls(b.momentum_12_1)}">${pct(b.momentum_12_1)}</div></div><div class="stat"><div class="l">ETF flow 3m</div><div class="v ${cls(b.flows && b.flows.flow_3m_pct_aum)}">${b.flows ? pct(b.flows.flow_3m_pct_aum) + ' AUM' : '–'}</div></div><div class="stat"><div class="l">HF share</div><div class="v">${b.f13 && b.f13.hf_share_pct != null ? b.f13.hf_share_pct.toFixed(2) + '%' : '–'}</div></div>
      </div>
      ${blurbHtml(b.id)}
      <details style="margin-top:8px"><summary class="small">Drivers, factor loadings, members</summary><ul class="drivers">${(b.drivers || []).map((d) => `<li>${esc(d)}</li>`).join('')}</ul>${factorTags(b)}<div class="small muted" style="margin-top:6px">${esc((b.basket || []).join(', '))}${b.missing_tickers && b.missing_tickers.length ? `<br>No price data (delisted or unavailable): ${esc(b.missing_tickers.join(', '))}` : ''}${b.history_note ? `<br>${esc(b.history_note)}` : ''}</div></details>
    </div>`).join('');
    drawCharts(); drawEvents();
    window.addEventListener('crt-rerender', () => { drawCharts(); });
  }

  // ---------- history page ----------
  function renderHistory() {
    const regimes = E.regimes || []; let range = 'all';
    $$('#rangeSeg button').forEach((btn) => btn.addEventListener('click', () => { $$('#rangeSeg button').forEach((b) => b.classList.remove('on')); btn.classList.add('on'); range = btn.dataset.r; draw(); }));
    function draw() {
      const start = rangeStart(range); const i0 = sliceFrom(H.dates, start); const labels = H.dates.slice(i0);
      const deep = S.sectors.filter((s) => s.deep_dive), ctx = S.sectors.filter((s) => !s.deep_dive);
      const series = deep.map((s) => ({ label: s.name, data: (H.sectors[s.id].w || []).slice(i0).map((v) => (v == null ? null : v * 100)) }));
      series.push({ label: 'Context sectors (Fin, Util, Mat, Media)', data: labels.map((_, i) => { let t = 0, any = false; ctx.forEach((s) => { const v = (H.sectors[s.id].w || [])[i0 + i]; if (v != null) { t += v * 100; any = true; } }); return any ? t : null; }), color: C.tok().gray });
      C.stackedArea('weightsChart', labels, series, { fmt: (v) => `${v.toFixed(1)}%`, yTick: (v) => `${v}%`, max: 100, regimes });
      const rs = S.sectors.map((s) => ({ label: s.name, data: rebase(((H.sectors[s.id].rs_etf || H.sectors[s.id].rs_basket) || []).slice(i0), 0), emphasis: s.deep_dive })).filter((x) => x.data.some((v) => v != null));
      C.lines('rsChart', labels, rs.slice(0, 8).concat(rs.slice(8).map((x) => ({ ...x, emphasis: false }))), { fmt: (v) => v.toFixed(1), yTick: (v) => v, refLine: 100, regimes, log: true });
      if (H.macro && H.macro.ust10y) C.lines('macroChart', labels, [{ label: '10-year Treasury yield', data: H.macro.ust10y.slice(i0) }, H.macro.fed_funds ? { label: 'Fed funds', data: H.macro.fed_funds.slice(i0) } : null, H.macro.real10y ? { label: '10-year real yield (TIPS)', data: H.macro.real10y.slice(i0) } : null].filter(Boolean), { fmt: (v) => `${v.toFixed(2)}%`, yTick: (v) => `${v}%`, regimes });
      if (H.universe_cap) C.lines('capChart', labels, [{ label: 'Tracked universe market cap ($tn)', data: H.universe_cap.slice(i0) }, { label: 'S&P 500 (SPY, rebased to universe)', data: (() => { const sp = rebase(H.spy.slice(i0), 0); const u = H.universe_cap.slice(i0); let j = 0; while (j < u.length && u[j] == null) j++; const b = u[j] || 1; return sp.map((v) => (v == null ? null : (v / 100) * b)); })(), color: C.tok().gray }], { fmt: (v) => `$${v.toFixed(2)}tn`, yTick: (v) => `$${v}tn`, regimes, zero: true });
    }
    draw(); window.addEventListener('crt-rerender', draw);
    $('#regimes').innerHTML = `<table class="data"><thead><tr><th class="l">Regime</th><th class="l">Period</th><th class="l">Fed policy</th><th class="l">10y range</th><th class="l">Factor</th><th class="l">Led</th><th class="l">Lagged</th></tr></thead><tbody>${regimes.map((r) => `<tr><td class="l"><b>${esc(r.name)}</b><div class="small muted" style="white-space:normal;max-width:360px">${esc(r.summary)}</div></td><td class="l">${esc(r.start)} → ${esc(r.end || 'now')}</td><td class="l" style="white-space:normal">${esc(r.fed_policy)}</td><td class="l">${esc(r.ten_year_yield_range)}</td><td class="l">${esc(r.dominant_factor)}</td><td class="l" style="white-space:normal">${esc((r.leading_sectors || []).join(', '))}</td><td class="l" style="white-space:normal">${esc((r.lagging_sectors || []).join(', '))}</td></tr>`).join('')}</tbody></table>`;
    const major = E.events.filter((e) => e.magnitude === 'major').sort((a, b) => (a.date < b.date ? 1 : -1)); $('#evCount').textContent = `${major.length} major events across all sectors`; $('#events').innerHTML = eventsList(major, { showSector: true });
    $('#narr').innerHTML = N.themes ? (window.marked ? marked.parse(N.themes) : esc(N.themes)) : '';
  }

  // ---------- methodology ----------
  function renderMethodology() {
    const u = S.universe || {};
    $('#coverage').innerHTML = `<table class="data"><thead><tr><th class="l">Sector</th><th class="l">Sub-industry</th><th class="l">Primary series</th><th class="l">ETF</th><th class="l">ETF since</th><th>Names priced</th><th class="l">Missing</th></tr></thead><tbody>${S.buckets.map((b) => `<tr><td class="l sector">${esc(b.sector_name)}</td><td class="l">${esc(b.name)}</td><td class="l">${esc(b.series_source)}</td><td class="l">${esc(b.etf || (b.reference_etf ? b.reference_etf + ' (ref)' : '–'))}</td><td class="l">${esc(b.etf_start ? b.etf_start.slice(0, 7) : '–')}</td><td class="num">${b.n_members_now}</td><td class="l small muted" style="white-space:normal">${esc((b.missing_tickers || []).join(', '))}</td></tr>`).join('')}</tbody></table>`;
    $('#uni').innerHTML = `Tracked universe: ${bn((u.cap_usd_tn || 0) * 1000)} across ${u.n_tickers_priced || 0} priced tickers; share counts available for ${u.n_with_shares || 0}; median first share-count observation ${esc(u.first_shares_obs_median || '–')}. ETF flow layer: ${u.flows_available ? 'loaded' : 'not yet loaded'}. 13F layer: ${u.f13_available ? 'loaded' : 'not yet loaded'}.`;
  }

  function render() {
    const page = document.body.dataset.page;
    if (page === 'beta') return;  // beta.js owns its own charts
    C.refresh();
    if (page === 'dashboard') renderDashboard(); else if (page === 'sector') renderSector(); else if (page === 'history') renderHistory(); else if (page === 'methodology') renderMethodology();
  }
  document.addEventListener('DOMContentLoaded', () => { initTheme(); initNav(); render(); });
})();
