/* Trading tab: one portfolio's holdings, exposures, returns decomposition, ratios and option-implied moves.
   Data: window.CRT_PORTFOLIO (data/portfolio.js is the publishable weights-only file; data/portfolio_full.js, when present locally, adds dollar values). */
(function () {
  const PF = window.CRT_PORTFOLIO; const S = window.CRT_SNAPSHOT || { buckets: [] }; const ST = window.CRT_STYLE || { buckets: {} };
  const C = window.CRTCharts;
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pct = (x, d = 1, sign = true) => x == null || isNaN(x) ? '–' : `${sign && x > 0 ? '+' : ''}${(x * 100).toFixed(d)}%`;
  const pp = (x, d = 1) => x == null || isNaN(x) ? '–' : `${x > 0 ? '+' : ''}${(x * 100).toFixed(d)}`;
  const num = (x, d = 2) => x == null || isNaN(x) ? '–' : x.toFixed(d);
  const cad = (x) => x == null || isNaN(x) ? '–' : `${x < 0 ? '−' : ''}$${Math.abs(x).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  const cls = (x) => x == null ? 'muted' : x > 0 ? 'pos' : x < 0 ? 'neg' : '';
  if (!PF) { document.addEventListener('DOMContentLoaded', () => { $('#pfSub').textContent = 'No portfolio loaded yet.'; }); return; }
  const full = !PF.public; const bucketById = Object.fromEntries((S.buckets || []).map((b) => [b.id, b]));
  const insts = PF.instruments; const DRIVERS = ['market', 'sector', 'style', 'fx', 'idio']; const DNAME = { market: 'Market', sector: 'Sector', style: 'Style factors', fx: 'Currency', idio: 'Idiosyncratic' };
  const styleSum = (w) => (w && w.style ? Object.values(w.style).reduce((a, b) => a + b, 0) : 0);
  const piece = (w, k) => (!w ? null : k === 'style' ? styleSum(w) : w[k]);
  const heatPill = (b) => (b ? `<span class="pill ${esc(b.label || 'na')}">${esc(b.label || 'n/a')}${b.heat == null ? '' : ' ' + Math.round(b.heat)}</span>` : '<span class="muted">–</span>');

  function head() {
    $('#pfTag').textContent = full ? '· beta portfolio · private view' : '· beta portfolio · weights only';
    $('#pfSub').innerHTML = `Positions as of ${esc(PF.asof)} · base currency CAD (USDCAD ${num(PF.usdcad, 3)}) · ${insts.length} holdings across ${PF.accounts.length} accounts${full ? '' : ' · dollar amounts, quantities and costs are hidden on the published site'}`;
    const s1 = (PF.stats || {})['1y'] || {}; const r = PF.risk || {};
    const k = [];
    if (full) k.push(['Gross positions', cad(PF.total_cad), `net equity ${cad(PF.net_equity_cad)} · margin loan ${cad(PF.margin_debit_cad)}`]);
    k.push(['Leverage', `${num(PF.leverage, 2)}×`, 'gross positions ÷ net equity across the three accounts']);
    k.push(['12-month return', pct(s1.cum_return), `today's weights · annualised vol ${pct(s1.ann_vol, 1, false)}`]);
    k.push(['Sharpe (1y)', num(s1.sharpe, 2), `Sortino ${num(s1.sortino, 2)} · hit rate ${pct(s1.hit_rate, 0, false)}`]);
    k.push(['Max drawdown (1y)', pct(s1.max_drawdown), `trough ${esc(s1.max_dd_date || '')}`]);
    k.push(['Beta to S&P 500', num(s1.beta_spy, 2), `correlation ${num(s1.corr_spy, 2)} · down capture ${num(s1.down_capture, 2)}`]);
    k.push(['Expected 1m move (1σ)', `±${(r.sigma_1m * 100).toFixed(1)}%`, `±${(r.sigma_1m_on_equity * 100).toFixed(1)}% on equity${full ? ' · ' + cad(r.sigma_1m_cad) : ''}`]);
    $('#pfKpis').innerHTML = k.map(([l, v, d]) => `<div class="kpi"><div class="label">${esc(l)}</div><div class="value">${esc(v)}</div><div class="delta">${esc(d)}</div></div>`).join('');
  }

  function holdings(view) {
    if (view === 'pos') {
      const cols = ['Account', 'Holding', 'Ccy', 'Weight'].concat(full ? ['Qty', 'Price', 'Value (CAD)', 'Cost (CAD)', 'P&L (CAD)'] : []).concat(['P&L %']);
      $('#holdHead').innerHTML = cols.map((c, i) => `<th class="${i < 3 ? 'l' : ''}">${esc(c)}</th>`).join('');
      const rows = [...PF.positions].sort((a, b) => a.account_type.localeCompare(b.account_type) || b.weight - a.weight);
      $('#holdBody').innerHTML = rows.map((p) => `<tr><td class="l sector">${esc(p.account_type)}</td><td class="l"><b>${esc(p.ticker)}</b> <span class="muted">${esc(p.name)}</span></td><td class="l">${esc(p.currency)}</td><td class="num">${pct(p.weight, 1, false)}</td>${full ? `<td class="num">${num(p.qty, 0)}</td><td class="num">${num(p.price, 2)}</td><td class="num">${cad(p.value_cad)}</td><td class="num">${cad(p.cost_cad)}</td><td class="num ${cls(p.pnl_cad)}">${cad(p.pnl_cad)}</td>` : ''}<td class="num ${cls(p.pnl_pct)}">${pct(p.pnl_pct)}</td></tr>`).join('');
      $('#holdNote').textContent = PF.accounts.map((a) => `${a.type}: ${pct(a.weight, 0, false)} of positions${a.leverage && a.leverage > 1.02 ? `, ${num(a.leverage, 2)}× levered` : ''}`).join(' · ');
      return;
    }
    const cols = ['Holding', 'Type', 'Weight'].concat(full ? ['Value (CAD)', 'P&L (CAD)'] : []).concat(['P&L %', '12m return', 'Vol (1y)', 'Sharpe (1y)', 'Market beta', 'Idio share of risk', '1m expected move', 'Next earnings']);
    $('#holdHead').innerHTML = cols.map((c, i) => `<th class="${i < 2 ? 'l' : ''}">${esc(c)}</th>`).join('');
    $('#holdBody').innerHTML = insts.map((i) => { const st = i.stats_1y || {}; const d = i.decomp || {}; const b = d.betas || {}; const mb = b[d.market_factor];
      return `<tr title="${esc(i.note || '')}"><td class="l"><b>${esc(i.ticker)}</b> <span class="muted">${esc(i.name)}</span></td><td class="l sector">${esc(i.kind)}${i.fx_hedged ? ' · hedged' : ''}</td><td class="num">${pct(i.weight, 1, false)}</td>${full ? `<td class="num">${cad(i.value_cad)}</td><td class="num ${cls(i.pnl_cad)}">${cad(i.pnl_cad)}</td>` : ''}<td class="num ${cls(i.pnl_pct)}">${pct(i.pnl_pct)}</td><td class="num ${cls(st.cum_return)}">${pct(st.cum_return)}</td><td class="num">${pct(st.ann_vol, 0, false)}</td><td class="num ${cls(st.sharpe)}">${num(st.sharpe, 2)}</td><td class="num">${num(mb, 2)}</td><td class="num">${d.r2 == null ? '–' : pct(1 - d.r2, 0, false)}</td><td class="num">±${i.moves ? (i.moves.sigma_1m * 100).toFixed(1) : '–'}%</td><td class="l">${esc((i.moves && i.moves.earnings) || '–')}</td></tr>`; }).join('');
    $('#holdNote').textContent = 'Hover a row for the mapping note. Idio share = 1 − R² of the factor regression: how much of the name\'s daily risk is its own.';
  }

  function exposure() {
    const t = C.tok(); const ex = PF.exposure;
    const secs = ex.sectors.map((s) => [s.name, s.weight]); if (ex.diversified_unclassified > 0.001) secs.push(['Diversified fund (no look-through)', ex.diversified_unclassified]);
    secs.sort((a, b) => b[1] - a[1]);
    bars('secChart', secs.map((s) => s[0]), secs.map((s) => +(s[1] * 100).toFixed(1)), t.series[0]);
    const th = ex.themes.filter((x) => x.direct > 0.0005);
    bars('themeChart', th.map((x) => x.name), th.map((x) => +(x.direct * 100).toFixed(1)), t.series[1]);
    $('#subBody').innerHTML = ex.subindustries.map((r) => { const b = bucketById[r.id]; const sb = ST.buckets ? ST.buckets[r.id] : null;
      return `<tr class="${b ? 'row' : ''}" ${b ? `data-href="sector.html?s=${r.sector}#${r.id}"` : ''}><td class="l"><b>${esc(r.name)}</b></td><td class="l sector">${esc(r.sector_name)}</td><td class="num">${pct(r.weight, 1, false)}</td><td class="num">${heatPill(b)}</td><td class="num ${cls(b && b.rel_3m)}">${b ? pct(b.rel_3m) : '–'}</td><td class="num ${cls(sb && sb.spread_3m)}">${sb ? pct(sb.spread_3m) : '–'}</td></tr>`; }).join('');
    $$('#subBody tr.row').forEach((tr) => tr.addEventListener('click', () => (location.href = tr.dataset.href)));
    $('#themeBody').innerHTML = ex.themes.map((x) => `<tr><td class="l"><b>${esc(x.name)}</b></td><td class="num">${pct(x.direct, 1, false)}</td><td class="num ${cls(x.beta)}">${x.beta == null ? '–' : num(x.beta, 2)}</td><td class="l muted">${esc(x.etf || '–')}</td></tr>`).join('');
    $('#topNames').innerHTML = ex.top_names.slice(0, 18).map((n) => `<span class="f" title="${esc(n.sub || '')}">${esc(n.ticker)} ${(n.weight * 100).toFixed(1)}%</span>`).join('');
  }
  function bars(id, labels, values, color) {
    const t = C.tok(); const box = document.getElementById(id).parentElement; box.style.height = `${Math.max(180, labels.length * 26 + 40)}px`;
    C.raw(id, { type: 'bar', data: { labels, datasets: [{ data: values, backgroundColor: color, borderRadius: 3, barPercentage: 0.7 }] },
      options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${c.parsed.x}% of positions` } } },
        scales: { x: { grid: { color: t.grid }, border: { display: false }, ticks: { color: t.muted, callback: (v) => v + '%' } }, y: { grid: { display: false }, border: { color: t.axis }, ticks: { color: t.ink2, font: { size: 12 } } } } } });
  }

  let win = '3m';
  function decomp() {
    const t = C.tok(); const d = PF.decomp[win]; const colors = { market: t.series[0], sector: t.series[1], style: t.series[2], fx: t.series[4], idio: t.series[3] };
    const vals = DRIVERS.map((k) => +(piece(d, k) * 100).toFixed(2)); const total = +(d.ret_cad * 100).toFixed(2);
    C.divergingBars('dcPortChart', DRIVERS.map((k) => DNAME[k]).concat(['Total return']), vals.concat([total]), { horizontal: false, fmt: (v) => `${v > 0 ? '+' : ''}${v} pts`, yTick: (v) => v });
    const big = [...DRIVERS].sort((a, b) => Math.abs(piece(d, b)) - Math.abs(piece(d, a)))[0];
    $('#dcNote').textContent = `Over ${win.toUpperCase()} the book returned ${pp(d.ret_cad)} points; the largest single driver was ${DNAME[big].toLowerCase()} (${pp(piece(d, big))})${big === 'idio' ? ', meaning stock-specific news rather than the market or a factor' : `; idiosyncratic was ${pp(d.idio)}`}.`;
    const rows = insts.filter((i) => i.decomp && i.decomp.windows[win]);
    C.raw('dcHoldChart', { type: 'bar', data: { labels: rows.map((i) => i.ticker), datasets: DRIVERS.map((k) => ({ label: DNAME[k], data: rows.map((i) => +(i.weight * piece(i.decomp.windows[win], k) * 100).toFixed(3)), backgroundColor: colors[k], stack: 's' })) },
      options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: true, position: 'top', align: 'start', labels: { color: t.ink2, boxWidth: 10, boxHeight: 10, font: { size: 11 } } }, tooltip: { mode: 'index', intersect: false, callbacks: { label: (c) => `${c.parsed.y > 0 ? '+' : ''}${c.parsed.y} pts  ${c.dataset.label}` } } },
        scales: { x: { stacked: true, grid: { display: false }, ticks: { color: t.ink2 } }, y: { stacked: true, grid: { color: t.grid }, border: { display: false }, ticks: { color: t.muted, callback: (v) => v + ' pts' } } } } });
    $('#dcHead').innerHTML = ['Holding', 'Weight', 'Return (CAD)'].concat(DRIVERS.map((k) => DNAME[k])).concat(['Market beta', 'Sector beta', 'R²']).map((c, i) => `<th class="${i === 0 ? 'l' : ''}">${esc(c)}</th>`).join('');
    const body = rows.map((i) => { const w = i.decomp.windows[win]; const b = i.decomp.betas;
      return `<tr><td class="l"><b>${esc(i.ticker)}</b> <span class="muted">${esc(i.name)}</span></td><td class="num">${pct(i.weight, 1, false)}</td><td class="num ${cls(w.ret_cad)}"><b>${pp(w.ret_cad)}</b></td>${DRIVERS.map((k) => `<td class="num ${cls(piece(w, k))}">${pp(piece(w, k))}</td>`).join('')}<td class="num">${num(b[i.decomp.market_factor], 2)}</td><td class="num">${b.sector == null ? '–' : num(b.sector, 2)}</td><td class="num">${num(i.decomp.r2, 2)}</td></tr>`; }).join('');
    $('#dcBody').innerHTML = body + `<tr><td class="l"><b>Whole book</b></td><td class="num">100%</td><td class="num ${cls(d.ret_cad)}"><b>${pp(d.ret_cad)}</b></td>${DRIVERS.map((k) => `<td class="num ${cls(piece(d, k))}"><b>${pp(piece(d, k))}</b></td>`).join('')}<td></td><td></td><td></td></tr>`;
  }

  function ratios() {
    const st = PF.stats; const cols = [['Book, 1y', st['1y']], ['S&P 500 in CAD, 1y', st['1y'] && st['1y'].vs_spy_cad], ['TSX 60, 1y', st['1y'] && st['1y'].vs_tsx], ['Book, longest common history', st.common], ['S&P 500 in CAD, same period', st.common && st.common.vs_spy_cad]].filter((c) => c[1]);
    $('#ratHead').innerHTML = '<th class="l">Ratio</th>' + cols.map((c) => `<th>${esc(c[0])}</th>`).join('');
    const rows = [['Annualised return', 'ann_return', (v) => pct(v)], ['Annualised volatility', 'ann_vol', (v) => pct(v, 1, false)], ['Sharpe ratio', 'sharpe', (v) => num(v, 2)], ['Sortino ratio', 'sortino', (v) => num(v, 2)], ['Max drawdown', 'max_drawdown', (v) => pct(v)],
      ['Beta to S&P 500', 'beta_spy', (v) => num(v, 2)], ['Correlation to S&P 500', 'corr_spy', (v) => num(v, 2)], ['Up capture', 'up_capture', (v) => num(v, 2)], ['Down capture', 'down_capture', (v) => num(v, 2)], ['Best day', 'best_day', (v) => pct(v)], ['Worst day', 'worst_day', (v) => pct(v)], ['Share of up days', 'hit_rate', (v) => pct(v, 0, false)]];
    $('#ratBody').innerHTML = rows.map(([l, k, f]) => `<tr><td class="l">${esc(l)}</td>${cols.map((c) => `<td class="num">${f(c[1][k])}</td>`).join('')}</tr>`).join('');
    $('#ratNote').textContent = `Sharpe = return above the 13-week T-bill divided by volatility, annualised. Sortino uses only downside volatility. Capture = the book's average return on days the S&P rose (or fell) divided by the S&P's. The longest common history starts ${st.common ? st.common.start : '–'}, when the youngest holding began trading. Margin leverage of ${num(PF.leverage, 2)}× is not applied to these figures; returns and drawdowns on equity are larger by about that factor, before interest.`;
    const se = PF.series; const lab = se.dates;
    C.lines('perfChart', lab, [{ label: 'Book (today\'s weights)', data: se.portfolio, width: 2.5 }, { label: 'S&P 500 in CAD', data: se.spy_cad }, { label: 'TSX 60', data: se.tsx }], { fmt: (v) => v.toFixed(1), yTick: (v) => v, refLine: 100, legend: true });
    const ch = Chart.getChart('perfChart'); if (ch) { ch.options.scales.x.ticks.callback = function (val, idx) { const l = lab[idx]; return l && l.slice(8, 10) <= '03' && idx % 5 === 0 ? l.slice(0, 7) : ''; }; ch.options.scales.x.ticks.autoSkip = true; ch.options.scales.x.ticks.maxTicksLimit = 12; ch.update(); }
  }

  function moves() {
    const r = PF.risk; const contrib = Object.fromEntries(r.contrib.map((c) => [c.ticker, c.share_of_variance]));
    const next = insts.filter((i) => i.moves && i.moves.earnings && i.moves.earnings >= PF.asof).sort((a, b) => (a.moves.earnings < b.moves.earnings ? -1 : 1))[0];
    const top = r.contrib[0];
    $('#mvKpis').innerHTML = [
      ['Book, 1 month, 1σ', `±${(r.sigma_1m * 100).toFixed(1)}%`, `about two months in three land inside this${full ? ' · ' + cad(r.sigma_1m_cad) : ''}`],
      ['Book, 1 month, 2σ', `±${(r.sigma_1m * 200).toFixed(1)}%`, 'about one month in twenty lands outside this'],
      ['On net equity, 1σ', `±${(r.sigma_1m_on_equity * 100).toFixed(1)}%`, `${num(PF.leverage, 2)}× leverage magnifies every move`],
      ['Biggest risk contributor', top ? top.ticker : '–', top ? `${pct(top.share_of_variance, 0, false)} of the book's variance at ${pct(top.weight, 0, false)} weight` : ''],
      ['Next earnings in the book', next ? `${next.ticker} · ${next.moves.earnings}` : '–', next && next.moves.earnings_move ? `options imply ±${(next.moves.earnings_move * 100).toFixed(1)}% on the print` : 'no implied earnings move available'],
    ].map(([l, v, d]) => `<div class="kpi"><div class="label">${esc(l)}</div><div class="value ${String(v).length > 12 ? 'text' : ''}">${esc(v)}</div><div class="delta">${esc(d)}</div></div>`).join('');
    $('#mvBody').innerHTML = insts.map((i) => { const m = i.moves || {};
      return `<tr><td class="l"><b>${esc(i.ticker)}</b> <span class="muted">${esc(i.name)}</span></td><td class="num">${pct(i.weight, 1, false)}</td><td class="l ${m.has_options ? '' : 'muted'}">${esc(m.basis || '')}</td><td class="num">${m.iv ? pct(m.iv, 0, false) : '–'}</td><td class="num">${pct(m.realised_vol_3m, 0, false)}</td><td class="num"><b>±${(m.sigma_1m * 100).toFixed(1)}%</b></td><td class="num">${m.implied_move_to_expiry ? '±' + (m.implied_move_to_expiry * 100).toFixed(1) + '%' : '–'}</td><td class="l muted">${m.expiry ? esc(m.expiry) + ' (' + m.days_to_expiry + 'd)' : '–'}</td><td class="l">${esc(m.earnings || '–')}</td><td class="num">${m.earnings_move ? '±' + (m.earnings_move * 100).toFixed(1) + '%' : '–'}</td><td class="num">${pct(contrib[i.ticker], 0, false)}</td></tr>`; }).join('');
    $('#scHead').innerHTML = ['Scenario', 'Book', 'On equity'].concat(full ? ['CAD'] : []).concat(['Driven mostly by']).map((c, i) => `<th class="${i === 0 || c === 'Driven mostly by' ? 'l' : ''}">${esc(c)}</th>`).join('');
    $('#scBody').innerHTML = r.scenarios.map((s) => `<tr><td class="l"><b>${esc(s.name)}</b></td><td class="num ${cls(s.portfolio_pct)}">${pct(s.portfolio_pct)}</td><td class="num ${cls(s.equity_pct)}">${pct(s.equity_pct)}</td>${full ? `<td class="num ${cls(s.portfolio_cad)}">${cad(s.portfolio_cad)}</td>` : ''}<td class="l small">${s.drivers.map((d) => `${esc(d.ticker)} ${pct(d.pct, 1)}`).join(' · ') || '<span class="muted">no fitted sensitivity</span>'}</td></tr>`).join('');
  }

  function method() {
    const m = PF.method || {};
    $('#pfMethod').innerHTML = `<ul>
      <li><b>Exposure</b> answers "what do I actually own". An ETF is split into its underlying names, and each name is filed under the same sub-industry and theme baskets Industry Pulse tracks, so the heat readings line up with the book.</li>
      <li><b>Returns decomposition</b> answers "why did it move". Factors: ${esc(m.factors || '')}. Betas use the trailing ${m.lookback_days || 252} trading days. A large idiosyncratic piece means the stock's own news drove it; a large market or style piece means it would have moved without any news.</li>
      <li><b>Key ratios</b> use ${esc(m.weights || '')}. They describe the book as it stands today, not the account's realised track record, which depends on when each position was bought.</li>
      <li><b>Expected moves</b>: ${esc(m.options || '')}. One standard deviation is not a floor: roughly one month in three the move is larger.</li>
      <li><b>Limits</b>: ETF look-throughs are approximate and hand-maintained; the active-extension fund is treated as a single diversified position; betas are estimated and drift; holding periods are not loaded yet; the managed accounts are not modelled.</li></ul>`;
  }

  function drawAll() { head(); holdings($('#holdSeg button.on').dataset.v); exposure(); decomp(); ratios(); moves(); method(); }
  document.addEventListener('DOMContentLoaded', () => {
    $$('#holdSeg button').forEach((b) => b.addEventListener('click', () => { $$('#holdSeg button').forEach((x) => x.classList.remove('on')); b.classList.add('on'); holdings(b.dataset.v); }));
    $$('#winSeg button').forEach((b) => b.addEventListener('click', () => { $$('#winSeg button').forEach((x) => x.classList.remove('on')); b.classList.add('on'); win = b.dataset.w; decomp(); }));
    const tb = $('#themeBtn'); if (tb) tb.addEventListener('click', () => setTimeout(() => { C.refresh(); drawAll(); }, 30));
    setTimeout(drawAll, 0);
  });
})();
