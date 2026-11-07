/* Beta Mode: Opportunities page. Reads window.CRT_BETA (from pipeline/calibrate.py). */
(function () {
  const B = window.CRT_BETA; if (!B || document.body.dataset.page !== 'beta') return;
  const S = window.CRT_SNAPSHOT || { sectors: [] };
  const C = window.CRTCharts; const $ = (s) => document.querySelector(s); const $$ = (s) => Array.from(document.querySelectorAll(s));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pct = (x, d = 1) => x == null || isNaN(x) ? '–' : `${x > 0 ? '+' : ''}${(x * 100).toFixed(d)}%`;
  const num = (x, d = 1) => x == null || isNaN(x) ? '–' : `${x > 0 ? '+' : ''}${x.toFixed(d)}`;
  const cls = (x) => x == null ? 'muted' : x > 0 ? 'pos' : x < 0 ? 'neg' : '';
  const vcls = (v) => ({ oversold: 'pos', underbought: 'pos', overbought: 'neg', undersold: 'neg' }[String(v).split(' ')[0]] || 'muted');
  const stanceClass = (s) => s.startsWith('Long') ? 'Hot' : s.startsWith('Short') ? 'Cold' : 'Neutral';
  const f63 = B.fits.reaction_63, fs = B.fits.settled, bt = B.backtest, fsig = bt.fundamental_signal || {}, clean = bt.clean || {};

  function render() {
    C.refresh();
    // KPIs
    $('#kpis').innerHTML = [
      ['Events rated', `${B.n_rated}`, `${B.events.length} event × sub-industry pairs`],
      ['Rating → 3m move', `${(f63.slope * 100).toFixed(2)}%`, `per rating point, sector-relative · corr ${f63.corr.toFixed(2)}`],
      ['Rating → 12m hit rate', fsig.hit_rate_12m == null ? '–' : `${Math.round(fsig.hit_rate_12m * 100)}%`, `|rating| ≥ 15 catalysts, n=${fsig.n || 0} · avg ${pct(fsig.mean_12m_positive)} vs ${pct(fsig.mean_12m_negative)}`],
      ['Do reactions revert?', clean.corr_resid_drift_4_12 == null ? '–' : (clean.corr_resid_drift_4_12 > 0.05 ? 'No' : clean.corr_resid_drift_4_12 < -0.05 ? 'Yes' : 'Weakly'), `residual vs months 4-12 drift corr ${clean.corr_resid_drift_4_12 == null ? '–' : clean.corr_resid_drift_4_12.toFixed(2)}, n=${clean.n || 0}`],
      ['Long / short candidates', `${B.buckets.filter((b) => b.stance.startsWith('Long')).length} / ${B.buckets.filter((b) => b.stance.startsWith('Short')).length}`, `of ${B.buckets.length} sub-industries with recent events`],
    ].map(([l, v, d]) => `<div class="kpi"><div class="label">${esc(l)}</div><div class="value ${String(v).length > 12 ? 'text' : ''}">${esc(v)}</div><div class="delta">${esc(d)}</div></div>`).join('');
    const rev = clean.corr_resid_drift_4_12;
    $('#verdictNote').innerHTML = `<b>What the backtest says.</b> The materiality rating has predictive content: among ${fsig.n || 0} clean catalysts with a rating of at least ±15, the sector-relative move over the following twelve months had the same sign as the rating ${fsig.hit_rate_12m == null ? '–' : Math.round(fsig.hit_rate_12m * 100)}% of the time, averaging ${pct(fsig.mean_12m_positive)} after positive events and ${pct(fsig.mean_12m_negative)} after negative ones. The reversion hypothesis did <b>not</b> hold: the correlation between the 3-month residual and the drift over months 4 to 12 is ${rev == null ? '–' : rev.toFixed(2)}${rev != null && rev > 0 ? ', so over- and under-reactions tended to continue, not close' : ''}. The shortlist therefore leads with the direction of fundamentals and uses the residual to say whether capital has already moved, not as a contrarian trigger.`;

    // shortlist
    const card = (b) => `<div class="bucket-card" style="margin-bottom:8px"><div class="head"><h3><a href="sector.html?s=${esc(b.sector)}#${esc(b.id)}">${esc(b.name)}</a></h3><span class="pill ${stanceClass(b.stance)}">${esc(b.stance)}</span><span class="muted small">${esc(b.sector_name)} · ${b.n_events} events · confidence ${esc(b.confidence)}</span></div>
      <div class="stats"><div class="stat"><div class="l">Fundamental score</div><div class="v ${cls(b.fundamental)}">${num(b.fundamental, 0)}</div></div><div class="stat"><div class="l">Residual z</div><div class="v ${cls(-b.z)}">${num(b.z, 2)}σ</div></div><div class="stat"><div class="l">Unexplained move</div><div class="v ${cls(b.unexplained)}">${pct(b.unexplained)}</div></div><div class="stat"><div class="l">Heat now</div><div class="v">${b.heat == null ? '–' : Math.round(b.heat)}</div></div><div class="stat"><div class="l">Rel 3m / 12m</div><div class="v"><span class="${cls(b.rel_3m)}">${pct(b.rel_3m)}</span> / <span class="${cls(b.rel_12m)}">${pct(b.rel_12m)}</span></div></div></div>
      <div class="small" style="color:var(--ink-2)">${esc(b.setup)}</div>
      <ul class="drivers">${b.events.slice(0, 4).map((e) => `<li><span class="muted">${esc(e.date)}</span> ${esc(e.title)} <span class="muted">· rating ${num(e.rating, 0)} · actual ${pct(e.actual)} vs expected ${pct(e.expected)} (${esc(e.window)}) ·</span> <span class="${vcls(e.verdict)}">${esc(e.verdict)}</span></li>`).join('')}</ul></div>`;
    const longs = B.buckets.filter((b) => b.stance.startsWith('Long')).sort((a, b) => b.fundamental - a.fundamental);
    const shorts = B.buckets.filter((b) => b.stance.startsWith('Short')).sort((a, b) => a.fundamental - b.fundamental);
    $('#longs').innerHTML = longs.length ? longs.map(card).join('') : '<p class="muted">None right now.</p>';
    $('#shorts').innerHTML = shorts.length ? shorts.map(card).join('') : '<p class="muted">None right now.</p>';

    // scatter: rating vs sec_63 with fit line and bin medians
    const t = C.tok();
    const pts = B.events.filter((e) => e.sec_63 != null && e.window === '3m').map((e) => ({ x: e.rating, y: Math.max(-0.8, Math.min(1.5, e.sec_63)) * 100, title: e.title, bucket: e.bucket_name, date: e.date }));
    const line = [-100, 100].map((x) => ({ x, y: (f63.intercept + f63.slope * x) * 100 }));
    const meds = B.binned.filter((b) => b.med_63 != null).map((b) => ({ x: b.mid, y: b.med_63 * 100, n: b.n }));
    C.raw('scatter', { type: 'scatter', data: { datasets: [
      { label: 'event (3m sector-relative)', data: pts, backgroundColor: 'rgba(65,199,242,0.45)', borderColor: 'rgba(65,199,242,0.8)', pointRadius: 3, pointHoverRadius: 6, pointHitRadius: 8 },
      { label: 'bin median', data: meds, backgroundColor: t.ink, borderColor: t.ink, pointStyle: 'rectRot', pointRadius: 7, pointHoverRadius: 9 },
      { label: `fit: ${(f63.intercept * 100).toFixed(1)}% + ${(f63.slope * 100).toFixed(3)}% × rating`, data: line, type: 'line', borderColor: t.series[0], borderWidth: 2, pointRadius: 0, fill: false },
    ] }, options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: true, position: 'top', align: 'start', labels: { color: t.ink2, usePointStyle: true, font: { size: 11 } } }, tooltip: { backgroundColor: t.surface, titleColor: t.ink, bodyColor: t.ink2, borderColor: t.axis, borderWidth: 1, callbacks: { title: (it) => it[0].raw.title || it[0].dataset.label, label: (it) => it.raw.title ? `${it.raw.bucket} · ${it.raw.date} · rating ${it.raw.x} · ${it.raw.y.toFixed(1)}%` : `rating ${it.raw.x}: ${it.raw.y.toFixed(1)}%${it.raw.n ? ` (n=${it.raw.n})` : ''}` } } },
      scales: { x: { min: -100, max: 100, title: { display: true, text: 'materiality rating', color: t.muted }, grid: { color: t.grid }, ticks: { color: t.muted } }, y: { title: { display: true, text: '3-month move vs sector (%)', color: t.muted }, grid: { color: t.grid }, ticks: { color: t.muted, callback: (v) => `${v}%` } } } } });

    // fundamental signal chart: mean & median 12m by rating bucket
    const fb = fsig.by_rating_bucket || [];
    const labels = fb.map((b) => b.bucket.replace('(', '').replace(']', '').replace(', ', ' to '));
    C.lines('fundChart', labels, [{ label: 'mean 12m sector-relative', data: fb.map((b) => b.mean_12m * 100) }, { label: 'median 12m sector-relative', data: fb.map((b) => b.median_12m * 100) }], { fmt: (v) => `${v.toFixed(1)}%`, yTick: (v) => `${v}%`, refLine: 0 });
    const ch = Chart.getChart('fundChart'); if (ch) { ch.options.scales.x.ticks.callback = (v, i) => labels[i]; ch.data.datasets.forEach((d) => { d.pointRadius = 5; }); ch.update(); }
    // reversion chart: drift by verdict
    const bv = (clean.by_verdict || []).filter((v) => !v.verdict.includes('no fundamental'));
    C.divergingBars('revertChart', bv.map((v) => `${v.verdict} (n=${v.n})`), bv.map((v) => v.mean_drift_4_12 * 100), { fmt: (v) => `${v.toFixed(1)}% mean drift, months 4-12`, yTick: (v) => `${v}%` });

    // backtest table
    $('#backtestTable').innerHTML = `<table class="data"><thead><tr><th class="l">Test</th><th>n</th><th>Result</th><th class="l">Reading</th></tr></thead><tbody>
      <tr><td class="l">Rating vs 3-month sector-relative move (all events)</td><td class="num">${f63.n}</td><td class="num">corr ${f63.corr.toFixed(2)}, slope ${(f63.slope * 100).toFixed(3)}%/pt</td><td class="l">ratings explain part of the immediate reaction; residual sd ${(f63.resid_sd * 100).toFixed(0)}%</td></tr>
      <tr><td class="l">Rating vs settled move (12-24 months)</td><td class="num">${fs ? fs.n : '–'}</td><td class="num">${fs ? `corr ${fs.corr.toFixed(2)}, slope ${(fs.slope * 100).toFixed(3)}%/pt` : '–'}</td><td class="l">the long-run move scales with the rating about twice as steeply as the 3-month move</td></tr>
      <tr><td class="l">Sign of rating vs sign of 12-month move (|rating| ≥ 15, catalysts)</td><td class="num">${fsig.n || '–'}</td><td class="num">${fsig.hit_rate_12m == null ? '–' : Math.round(fsig.hit_rate_12m * 100) + '%'}</td><td class="l">the rubric is directionally right more often than not</td></tr>
      <tr><td class="l">3-month residual vs drift months 4-12 (clean catalysts)</td><td class="num">${clean.n || '–'}</td><td class="num">corr ${clean.corr_resid_drift_4_12 == null ? '–' : clean.corr_resid_drift_4_12.toFixed(2)}</td><td class="l">${clean.corr_resid_drift_4_12 > 0.05 ? 'positive: over- and under-reactions persisted (momentum), they did not revert' : 'no persistence'}</td></tr>
      <tr><td class="l">3-month residual vs drift months 4-6 (clean catalysts)</td><td class="num">${clean.n || '–'}</td><td class="num">corr ${clean.corr_resid_drift_4_6 == null ? '–' : clean.corr_resid_drift_4_6.toFixed(2)}</td><td class="l">no short-horizon reversion either</td></tr>
      ${(bt.by_sector || []).filter((s) => s.n >= 10).map((s) => `<tr><td class="l">Residual persistence by sector: ${esc(S.sectors.find((x) => x.id === s.sector) ? S.sectors.find((x) => x.id === s.sector).name : s.sector)}</td><td class="num">${s.n}</td><td class="num">corr ${s.corr == null ? '–' : s.corr.toFixed(2)} · reversal hit ${Math.round(s.hit_rate * 100)}%</td><td class="l">${s.corr != null && s.corr < -0.1 ? 'some reversion here' : s.corr != null && s.corr > 0.1 ? 'momentum here' : 'noise'}</td></tr>`).join('')}
    </tbody></table>`;

    // bucket table
    const secSel = $('#fSector'); secSel.innerHTML = '<option value="">All sectors</option>' + S.sectors.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
    const bcols = [['name', 'Sub-industry', true], ['sector_name', 'Sector', true], ['stance', 'Stance', true], ['fundamental', 'Fund. score'], ['z', 'Residual z'], ['unexplained', 'Unexplained'], ['n_events', 'Events'], ['heat', 'Heat'], ['weight_pctile', 'Wt pctile'], ['rel_3m', 'Rel 3m'], ['rel_12m', 'Rel 12m'], ['setup', 'Setup', true]];
    let bk = 'fundamental', basc = false;
    function drawB() {
      let rows = B.buckets.filter((b) => (!secSel.value || b.sector === secSel.value) && (!$('#fStance').value || b.stance.startsWith($('#fStance').value)));
      rows.sort((a, b) => { const va = a[bk], vb = b[bk]; if (va == null) return 1; if (vb == null) return -1; return typeof va === 'string' ? (basc ? va.localeCompare(vb) : vb.localeCompare(va)) : (basc ? va - vb : vb - va); });
      $('#bHead').innerHTML = bcols.map(([k, l, left]) => `<th class="${left ? 'l' : ''} ${bk === k ? 'sorted' + (basc ? ' asc' : '') : ''}" data-k="${k}">${esc(l)}</th>`).join('');
      $('#bBody').innerHTML = rows.map((b) => `<tr class="row" data-href="sector.html?s=${b.sector}#${b.id}"><td class="l"><b>${esc(b.name)}</b></td><td class="l sector">${esc(b.sector_name)}</td><td class="l"><span class="pill ${stanceClass(b.stance)}">${esc(b.stance)}</span></td><td class="num ${cls(b.fundamental)}">${num(b.fundamental, 0)}</td><td class="num ${cls(-b.z)}">${num(b.z, 2)}</td><td class="num ${cls(b.unexplained)}">${pct(b.unexplained)}</td><td class="num">${b.n_events}</td><td class="num">${b.heat == null ? '–' : Math.round(b.heat)}</td><td class="num">${b.weight_pctile == null ? '–' : Math.round(b.weight_pctile * 100) + '%'}</td><td class="num ${cls(b.rel_3m)}">${pct(b.rel_3m)}</td><td class="num ${cls(b.rel_12m)}">${pct(b.rel_12m)}</td><td class="l next">${esc(b.setup)}</td></tr>`).join('');
      $$('#bHead th').forEach((th) => th.addEventListener('click', () => { const k = th.dataset.k; if (bk === k) basc = !basc; else { bk = k; basc = ['name', 'sector_name', 'stance', 'setup'].includes(k); } drawB(); }));
      $$('#bBody tr.row').forEach((tr) => tr.addEventListener('click', () => (location.href = tr.dataset.href)));
      $('#bCount').textContent = `${rows.length} sub-industries`;
    }
    secSel.addEventListener('change', drawB); $('#fStance').addEventListener('change', drawB); drawB();

    // events table
    const eSel = $('#eSector'); eSel.innerHTML = '<option value="">All sectors</option>' + S.sectors.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
    const cutoff = new Date(B.asof); cutoff.setDate(cutoff.getDate() - B.params.recent_days); const cut = cutoff.toISOString().slice(0, 10);
    const ecols = [['date', 'Date', true], ['bucket_name', 'Sub-industry', true], ['title', 'Event', true], ['rating', 'Rating'], ['components', 'e/p/b/c', true], ['pre_63', 'Pre 3m'], ['sec_21', '+1m'], ['sec_63', '+3m'], ['sec_252', '+12m'], ['flow_63', 'ETF flow 3m'], ['expected_63', 'Expected 3m'], ['z', 'Resid z'], ['verdict', 'Verdict', true], ['confidence', 'Conf.', true]];
    let ek = 'date', easc = false;
    function drawE() {
      const q = ($('#eSearch').value || '').toLowerCase(); const v = $('#eVerdict').value; const rec = $('#eRecent').checked;
      let rows = B.events.filter((e) => (!eSel.value || e.sector === eSel.value) && (!v || String(e.verdict).startsWith(v)) && (!rec || e.date >= cut) && (!q || `${e.title} ${e.bucket_name}`.toLowerCase().includes(q)));
      rows.sort((a, b) => { const va = a[ek], vb = b[ek]; if (va == null) return 1; if (vb == null) return -1; return typeof va === 'string' ? (easc ? va.localeCompare(vb) : vb.localeCompare(va)) : (easc ? va - vb : vb - va); });
      $('#eHead').innerHTML = ecols.map(([k, l, left]) => `<th class="${left ? 'l' : ''} ${ek === k ? 'sorted' + (easc ? ' asc' : '') : ''}" data-k="${k}">${esc(l)}</th>`).join('');
      $('#eBody').innerHTML = rows.slice(0, 400).map((e) => `<tr title="${esc(e.why)}${e.summary ? ' — ' + esc(e.summary) : ''}"><td class="l">${esc(e.date)}</td><td class="l">${esc(e.bucket_name)}${e.level === 'sector' ? ' <span class="muted">(sector)</span>' : ''}</td><td class="l next" style="max-width:360px">${esc(e.title)}${e.episode ? ' <span class="tag">episode</span>' : ''}${e.sources && e.sources[0] && e.sources[0].url ? ` <a href="${esc(e.sources[0].url)}" target="_blank" rel="noopener" class="muted">↗</a>` : ''}</td><td class="num ${cls(e.rating)}"><b>${num(e.rating, 0)}</b></td><td class="l muted">${e.components.e}/${e.components.p}/${e.components.b}/${e.components.c}</td><td class="num ${cls(e.pre_63)}">${pct(e.pre_63)}</td><td class="num ${cls(e.sec_21)}">${pct(e.sec_21)}</td><td class="num ${cls(e.sec_63)}">${pct(e.sec_63)}</td><td class="num ${cls(e.sec_252)}">${pct(e.sec_252)}</td><td class="num ${cls(e.flow_63)}">${pct(e.flow_63)}</td><td class="num">${pct(e.expected_63)}</td><td class="num ${cls(-(e.z || 0))}">${num(e.z, 2)}</td><td class="l ${vcls(e.verdict)}">${esc(e.verdict)}</td><td class="l muted">${esc(e.confidence)}</td></tr>`).join('');
      $$('#eHead th').forEach((th) => th.addEventListener('click', () => { const k = th.dataset.k; if (ek === k) easc = !easc; else { ek = k; easc = ['date', 'bucket_name', 'title', 'verdict', 'confidence', 'components'].includes(k); } drawE(); }));
      $('#eCount').textContent = `${rows.length} rows${rows.length > 400 ? ' (first 400 shown)' : ''}`;
    }
    ['#eSector', '#eVerdict', '#eRecent', '#eSearch'].forEach((s) => $(s).addEventListener('input', drawE)); drawE();

    // method
    const r = B.rubric || {};
    $('#method').innerHTML = `<p><b>Rating.</b> ${esc(r.formula || '')}. Components: ${esc(r.e || '')}; ${esc(r.p || '')}; ${esc(r.b || '')}; ${esc(r.c || '')}. Anchors: ${esc(r.anchors || '')}. Ratings are assigned from the event's fundamentals and are not adjusted to match what the stock did afterwards.</p>
      <p><b>Reaction.</b> For each event and tagged sub-industry, the bucket's total return minus its own sector (to strip shared shocks) over 1, 3, 6, 12 and 24 months after the event date, plus the 3-month ETF creation flow where an ETF exists. Episodes (year-long flow descriptions dated to the start of the year) are flagged and get half weight in the fits; events with another major event in the same bucket within six months get a lower weight and a lower confidence label.</p>
      <p><b>Expected reaction and residual.</b> A weighted linear fit of the 3-month sector-relative move on the rating across all history gives the expected move for any rating; the residual is actual minus expected, expressed in standard deviations (z). |z| under ${B.params.z_threshold} is "justified"; beyond that, the verdict depends on the sign of the rating and of the residual: oversold and undersold for negative events, underbought and overbought for positive ones. Events younger than three months use the 1-month fit.</p>
      <p><b>Stance.</b> For each sub-industry, the fundamental score is the recency-weighted (half-life ${B.params.half_life_days} days) average rating of its events in the last ${B.params.recent_days} days, and the residual z is weighted the same way. Fundamental score ≥ 15 gives a Long and ≤ -15 a Short; the residual then qualifies it: capital not yet moved (plain Long/Short), capital following (momentum), or capital already concentrated or washed out (crowded / late), using the tracker's heat score. This ordering follows the backtest: ratings predict the next year, residuals have not reliably reverted.</p>`;
  }
  document.addEventListener('DOMContentLoaded', render); window.addEventListener('crt-rerender', render);
})();
