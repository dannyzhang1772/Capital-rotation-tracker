/* Trading tab. Signed out, the page shows a fictional sample book (data/portfolio.js). Signed in (Supabase), it becomes a
   portal: the visitor's own book is loaded from the backend (CRT_CONFIG.api), which keeps their brokerage link and last
   analysis, and live quotes are laid on top every minute. Nothing about a visitor's book is ever written into the site. */
(function () {
  const DEMO = window.CRT_PORTFOLIO || null; let PF = DEMO; let source = 'demo'; let MINE = null; let LIVE = null; let ME = null; let integrateOpen = false;
  const S = window.CRT_SNAPSHOT || { buckets: [] }; const ST = window.CRT_STYLE || { buckets: {} }; const C = window.CRTCharts;
  const CFG = window.CRT_CONFIG || {}; const qs = new URLSearchParams(location.search);
  const LOCAL = /^(localhost|127\.0\.0\.1)$/.test(location.hostname);
  const API = (LOCAL && qs.get('api')) || CFG.api || '';
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pct = (x, d = 1, sign = true) => x == null || isNaN(x) ? '–' : `${sign && x > 0 ? '+' : ''}${(x * 100).toFixed(d)}%`;
  const pp = (x, d = 1) => x == null || isNaN(x) ? '–' : `${x > 0 ? '+' : ''}${(x * 100).toFixed(d)}`;
  const num = (x, d = 2) => x == null || isNaN(x) ? '–' : (+x).toFixed(d);
  const money = (x) => x == null || isNaN(x) ? '–' : `${x < 0 ? '−' : ''}$${Math.abs(x).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  const cls = (x) => x == null ? 'muted' : x > 0 ? 'pos' : x < 0 ? 'neg' : '';
  const bucketById = Object.fromEntries((S.buckets || []).map((b) => [b.id, b]));
  const DRIVERS = ['market', 'sector', 'style', 'fx', 'idio']; const DNAME = { market: 'Market', sector: 'Sector', style: 'Style factors', fx: 'Currency', idio: 'Idiosyncratic' };
  const styleSum = (w) => (w && w.style ? Object.values(w.style).reduce((a, b) => a + b, 0) : 0);
  const piece = (w, k) => (!w ? null : k === 'style' ? styleSum(w) : w[k]);
  const heatPill = (b) => (b ? `<span class="pill ${esc(b.label || 'na')}">${esc(b.label || 'n/a')}${b.heat == null ? '' : ' ' + Math.round(b.heat)}</span>` : '<span class="muted">–</span>');
  const isFull = () => !PF.public; const ccy = () => PF.base_currency || 'CAD';

  function head() {
    const full = isFull();
    $('#pfTag').textContent = source === 'demo' ? '· example portfolio · made-up holdings' : '· my portfolio · private';
    $('#pfSub').innerHTML = `Positions as of ${esc(PF.asof)} · base currency ${esc(ccy())} (USDCAD ${num(PF.usdcad, 3)}) · ${PF.instruments.length} holdings across ${PF.accounts.length} account${PF.accounts.length === 1 ? '' : 's'}${source === 'demo' ? ' · an example, not your holdings' : ''}${PF.unmodelled && PF.unmodelled.length ? ` · not modelled (no price history): ${PF.unmodelled.map((u) => esc(u.ticker)).join(', ')}` : ''}`;
    const s1 = (PF.stats || {})['1y'] || (PF.stats || {}).common || {}; const r = PF.risk || {}; const k = [];
    if (full) k.push(['Gross positions', money(PF.total_base), PF.leverage > 1.02 ? `net equity ${money(PF.net_equity_base)} · margin loan ${money(PF.margin_debit_base)}` : `${esc(ccy())} · ${PF.positions.length} positions`]);
    k.push(['Leverage', `${num(PF.leverage, 2)}×`, 'gross positions ÷ net account value']);
    k.push(['12-month return', pct(s1.cum_return), `today's weights · annualised vol ${pct(s1.ann_vol, 1, false)}`]);
    k.push(['Sharpe (1y)', num(s1.sharpe, 2), `Sortino ${num(s1.sortino, 2)} · hit rate ${pct(s1.hit_rate, 0, false)}`]);
    k.push(['Max drawdown (1y)', pct(s1.max_drawdown), `trough ${esc(s1.max_dd_date || '')}`]);
    k.push(['Beta to S&P 500', num(s1.beta_spy, 2), `correlation ${num(s1.corr_spy, 2)} · down capture ${num(s1.down_capture, 2)}`]);
    k.push(['Expected 1m move (1σ)', `±${(r.sigma_1m * 100).toFixed(1)}%`, `±${(r.sigma_1m_on_equity * 100).toFixed(1)}% on equity${full ? ' · ' + money(r.sigma_1m_base) : ''}`]);
    $('#pfKpis').innerHTML = k.map(([l, v, d]) => `<div class="kpi"><div class="label">${esc(l)}</div><div class="value">${esc(v)}</div><div class="delta">${esc(d)}</div></div>`).join('');
  }

  function holdings(view) {
    const full = isFull(); const c = ccy();
    if (view === 'pos') {
      const anyHeld = PF.positions.some((p) => p.held_days != null);
      const cols = ['Account', 'Holding', 'Ccy', 'Weight'].concat(full ? ['Qty', 'Price', `Value (${c})`, `Cost (${c})`, `P&L (${c})`] : []).concat(['P&L %']).concat(anyHeld ? ['First bought', 'Held'] : []);
      $('#holdHead').innerHTML = cols.map((x, i) => `<th class="${i < 3 ? 'l' : ''}">${esc(x)}</th>`).join('');
      const rows = [...PF.positions].sort((a, b) => String(a.account_type).localeCompare(String(b.account_type)) || b.weight - a.weight);
      $('#holdBody').innerHTML = rows.map((p) => `<tr><td class="l sector">${esc(p.account_type)}</td><td class="l"><b>${esc(p.ticker)}</b> <span class="muted">${esc(p.name)}</span></td><td class="l">${esc(p.currency)}</td><td class="num">${pct(p.weight, 1, false)}</td>${full ? `<td class="num">${num(p.qty, p.qty % 1 ? 2 : 0)}</td><td class="num">${num(p.price, 2)}</td><td class="num">${money(p.value_base)}</td><td class="num">${money(p.cost_base)}</td><td class="num ${cls(p.pnl_base)}">${money(p.pnl_base)}</td>` : ''}<td class="num ${cls(p.pnl_pct)}">${pct(p.pnl_pct)}</td>${anyHeld ? `<td class="num">${esc(p.first_buy || '–')}</td><td class="num">${p.held_days == null ? '–' : p.held_days + 'd'}</td>` : ''}</tr>`).join('');
      $('#holdNote').textContent = PF.accounts.map((a) => `${a.type}: ${pct(a.weight, 0, false)} of positions${a.leverage && a.leverage > 1.02 ? `, ${num(a.leverage, 2)}× levered` : ''}`).join(' · ');
      return;
    }
    const live = (LIVE && source === 'mine') ? Object.fromEntries(LIVE.holdings.map((h) => [h.ticker, h])) : null;
    const cols = ['Holding', 'Type', 'Weight'].concat(live ? ['Today'] : []).concat(full ? [`Value (${c})`, `P&L (${c})`] : []).concat(['P&L %', '12m return', 'Vol (1y)', 'Sharpe (1y)', 'Market beta', 'Idio share of risk', '1m expected move', 'Next earnings']);
    $('#holdHead').innerHTML = cols.map((x, i) => `<th class="${i < 2 ? 'l' : ''}">${esc(x)}</th>`).join('');
    $('#holdBody').innerHTML = PF.instruments.map((i) => { const st = i.stats_1y || {}; const d = i.decomp || {}; const b = d.betas || {}; const mb = b[d.market_factor];
      return `<tr title="${esc(i.note || i.sub_name || '')}"><td class="l"><b>${esc(i.ticker)}</b> <span class="muted">${esc(i.name)}</span></td><td class="l sector">${esc(i.kind)}${i.fx_hedged ? ' · hedged' : ''}</td><td class="num">${pct(i.weight, 1, false)}</td>${live ? `<td class="num ${cls(live[i.ticker] && live[i.ticker].day_pct)}">${live[i.ticker] ? pct(live[i.ticker].day_pct, 2) : '–'}</td>` : ''}${full ? `<td class="num">${money(live && live[i.ticker] && live[i.ticker].value_base != null ? live[i.ticker].value_base : i.value_base)}</td><td class="num ${cls(i.pnl_base)}">${money(i.pnl_base)}</td>` : ''}<td class="num ${cls(i.pnl_pct)}">${pct(i.pnl_pct)}</td><td class="num ${cls(st.cum_return)}">${pct(st.cum_return)}</td><td class="num">${pct(st.ann_vol, 0, false)}</td><td class="num ${cls(st.sharpe)}">${num(st.sharpe, 2)}</td><td class="num">${num(mb, 2)}</td><td class="num">${d.r2 == null ? '–' : pct(1 - d.r2, 0, false)}</td><td class="num">±${i.moves ? (i.moves.sigma_1m * 100).toFixed(1) : '–'}%</td><td class="l">${esc((i.moves && i.moves.earnings) || '–')}</td></tr>`; }).join('');
    $('#holdNote').textContent = 'Hover a row for the mapping note. Idio share = 1 − R² of the factor regression: how much of the name\'s daily risk is its own.';
  }

  function exposure() {
    const t = C.tok(); const ex = PF.exposure;
    const secs = ex.sectors.map((s) => [s.name, s.weight]); if (ex.diversified_unclassified > 0.001) secs.push(['Unclassified (no look-through)', ex.diversified_unclassified]);
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
    const rows = PF.instruments.filter((i) => i.decomp && i.decomp.windows[win]);
    C.raw('dcHoldChart', { type: 'bar', data: { labels: rows.map((i) => i.ticker), datasets: DRIVERS.map((k) => ({ label: DNAME[k], data: rows.map((i) => +(i.weight * piece(i.decomp.windows[win], k) * 100).toFixed(3)), backgroundColor: colors[k], stack: 's' })) },
      options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: true, position: 'top', align: 'start', labels: { color: t.ink2, boxWidth: 10, boxHeight: 10, font: { size: 11 } } }, tooltip: { mode: 'index', intersect: false, callbacks: { label: (c) => `${c.parsed.y > 0 ? '+' : ''}${c.parsed.y} pts  ${c.dataset.label}` } } },
        scales: { x: { stacked: true, grid: { display: false }, ticks: { color: t.ink2 } }, y: { stacked: true, grid: { color: t.grid }, border: { display: false }, ticks: { color: t.muted, callback: (v) => v + ' pts' } } } } });
    $('#dcHead').innerHTML = ['Holding', 'Weight', `Return (${ccy()})`].concat(DRIVERS.map((k) => DNAME[k])).concat(['Market beta', 'Sector beta', 'R²']).map((c, i) => `<th class="${i === 0 ? 'l' : ''}">${esc(c)}</th>`).join('');
    const body = rows.map((i) => { const w = i.decomp.windows[win]; const b = i.decomp.betas;
      return `<tr><td class="l"><b>${esc(i.ticker)}</b> <span class="muted">${esc(i.name)}</span></td><td class="num">${pct(i.weight, 1, false)}</td><td class="num ${cls(w.ret_cad)}"><b>${pp(w.ret_cad)}</b></td>${DRIVERS.map((k) => `<td class="num ${cls(piece(w, k))}">${pp(piece(w, k))}</td>`).join('')}<td class="num">${num(b[i.decomp.market_factor], 2)}</td><td class="num">${b.sector == null ? '–' : num(b.sector, 2)}</td><td class="num">${num(i.decomp.r2, 2)}</td></tr>`; }).join('');
    $('#dcBody').innerHTML = body + `<tr><td class="l"><b>Whole book</b></td><td class="num">100%</td><td class="num ${cls(d.ret_cad)}"><b>${pp(d.ret_cad)}</b></td>${DRIVERS.map((k) => `<td class="num ${cls(piece(d, k))}"><b>${pp(piece(d, k))}</b></td>`).join('')}<td></td><td></td><td></td></tr>`;
  }

  function ratios() {
    const st = PF.stats; const c = ccy();
    const cols = [['Book, 1y', st['1y']], [`S&P 500 in ${c}, 1y`, st['1y'] && st['1y'].vs_spy_cad], [`TSX 60, 1y`, st['1y'] && st['1y'].vs_tsx], ['Book, longest common history', st.common], [`S&P 500 in ${c}, same period`, st.common && st.common.vs_spy_cad]].filter((x) => x[1]);
    $('#ratHead').innerHTML = '<th class="l">Ratio</th>' + cols.map((x) => `<th>${esc(x[0])}</th>`).join('');
    const rows = [['Annualised return', 'ann_return', (v) => pct(v)], ['Annualised volatility', 'ann_vol', (v) => pct(v, 1, false)], ['Sharpe ratio', 'sharpe', (v) => num(v, 2)], ['Sortino ratio', 'sortino', (v) => num(v, 2)], ['Max drawdown', 'max_drawdown', (v) => pct(v)],
      ['Beta to S&P 500', 'beta_spy', (v) => num(v, 2)], ['Correlation to S&P 500', 'corr_spy', (v) => num(v, 2)], ['Up capture', 'up_capture', (v) => num(v, 2)], ['Down capture', 'down_capture', (v) => num(v, 2)], ['Best day', 'best_day', (v) => pct(v)], ['Worst day', 'worst_day', (v) => pct(v)], ['Share of up days', 'hit_rate', (v) => pct(v, 0, false)]];
    $('#ratBody').innerHTML = rows.map(([l, k, f]) => `<tr><td class="l">${esc(l)}</td>${cols.map((x) => `<td class="num">${f(x[1][k])}</td>`).join('')}</tr>`).join('');
    $('#ratNote').textContent = `Sharpe = return above the 13-week T-bill divided by volatility, annualised. Sortino uses only downside volatility. Capture = the book's average return on days the S&P rose (or fell) divided by the S&P's. The longest common history starts ${st.common ? st.common.start : '–'}, when the youngest holding began trading.${PF.leverage > 1.02 ? ` Margin leverage of ${num(PF.leverage, 2)}× is not applied to these figures; returns and drawdowns on equity are larger by about that factor, before interest.` : ''}`;
    const se = PF.series; const lab = se.dates;
    C.lines('perfChart', lab, [{ label: 'Book (today\'s weights)', data: se.portfolio, width: 2.5 }, { label: `S&P 500 in ${c}`, data: se.spy_cad }, { label: 'TSX 60', data: se.tsx }], { fmt: (v) => v.toFixed(1), yTick: (v) => v, refLine: 100, legend: true });
    const ch = Chart.getChart('perfChart'); if (ch) { let lastM = ''; ch.options.scales.x.ticks.callback = function (val, idx) { const l = lab[idx] || ''; const m = l.slice(0, 7); if (m !== lastM && idx > 0) { lastM = m; return m; } if (idx === 0) lastM = m; return ''; }; ch.update(); }
  }

  function moves() {
    const full = isFull(); const r = PF.risk; const contrib = Object.fromEntries(r.contrib.map((c) => [c.ticker, c.share_of_variance]));
    const next = PF.instruments.filter((i) => i.moves && i.moves.earnings && i.moves.earnings >= PF.asof).sort((a, b) => (a.moves.earnings < b.moves.earnings ? -1 : 1))[0];
    const top = r.contrib[0];
    $('#mvKpis').innerHTML = [
      ['Book, 1 month, 1σ', `±${(r.sigma_1m * 100).toFixed(1)}%`, `about two months in three land inside this${full ? ' · ' + money(r.sigma_1m_base) : ''}`],
      ['Book, 1 month, 2σ', `±${(r.sigma_1m * 200).toFixed(1)}%`, 'about one month in twenty lands outside this'],
      ['On net equity, 1σ', `±${(r.sigma_1m_on_equity * 100).toFixed(1)}%`, PF.leverage > 1.02 ? `${num(PF.leverage, 2)}× leverage magnifies every move` : 'no leverage, same as the book'],
      ['Biggest risk contributor', top ? top.ticker : '–', top ? `${pct(top.share_of_variance, 0, false)} of the book's variance at ${pct(top.weight, 0, false)} weight` : ''],
      ['Next earnings in the book', next ? `${next.ticker} · ${next.moves.earnings}` : '–', next && next.moves.earnings_move ? `options imply ±${(next.moves.earnings_move * 100).toFixed(1)}% on the print` : 'no implied earnings move available'],
    ].map(([l, v, d]) => `<div class="kpi"><div class="label">${esc(l)}</div><div class="value ${String(v).length > 12 ? 'text' : ''}">${esc(v)}</div><div class="delta">${esc(d)}</div></div>`).join('');
    $('#mvBody').innerHTML = PF.instruments.map((i) => { const m = i.moves || {};
      return `<tr><td class="l"><b>${esc(i.ticker)}</b> <span class="muted">${esc(i.name)}</span></td><td class="num">${pct(i.weight, 1, false)}</td><td class="l ${m.has_options ? '' : 'muted'}">${esc(m.basis || '')}</td><td class="num">${m.iv ? pct(m.iv, 0, false) : '–'}</td><td class="num">${pct(m.realised_vol_3m, 0, false)}</td><td class="num"><b>±${m.sigma_1m == null ? '–' : (m.sigma_1m * 100).toFixed(1)}%</b></td><td class="num">${m.implied_move_to_expiry ? '±' + (m.implied_move_to_expiry * 100).toFixed(1) + '%' : '–'}</td><td class="l muted">${m.expiry ? esc(m.expiry) + ' (' + m.days_to_expiry + 'd)' : '–'}</td><td class="l">${esc(m.earnings || '–')}</td><td class="num">${m.earnings_move ? '±' + (m.earnings_move * 100).toFixed(1) + '%' : '–'}</td><td class="num">${pct(contrib[i.ticker], 0, false)}</td></tr>`; }).join('');
    const lev = PF.leverage > 1.02; const bp = (x) => (x == null ? '–' : `${x > 0 ? '+' : ''}${Math.round(x)} bp`);
    $('#scHead').innerHTML = ['If this happened again', 'S&P 500 then', '10y yield then', 'This book'].concat(lev ? ['On equity'] : []).concat(full ? [ccy()] : []).concat(['vs S&P', 'Driven mostly by']).map((c, i) => `<th class="${i === 0 || c === 'Driven mostly by' ? 'l' : ''}">${esc(c)}</th>`).join('');
    const ncol = 6 + (lev ? 1 : 0) + (full ? 1 : 0); let lastGroup = null;
    $('#scBody').innerHTML = (r.scenarios || []).filter((s) => s.what).map((s) => {
      const grp = s.group !== lastGroup ? `<tr class="grp"><td class="l" colspan="${ncol}">${esc(s.group)}</td></tr>` : ''; lastGroup = s.group;
      const sb = s.spx_base != null ? s.spx_base : s.spx; const rel = sb == null ? null : s.portfolio_pct - sb;
      return `${grp}<tr><td class="l scen"><b>${esc(s.name)}</b><div class="what">${esc(s.what)} <span class="muted">${esc(s.start)} to ${esc(s.end)}${s.ongoing ? ' (so far)' : ''}${s.proxy_weight > 0.005 ? ` · ${pct(s.proxy_weight, 0, false)} of the book did not trade then and uses a stand-in` : ''}</span></div></td><td class="num ${cls(s.spx)}">${pct(s.spx)}</td><td class="num">${bp(s.y10_bp)}</td><td class="num ${cls(s.portfolio_pct)}"><b>${pct(s.portfolio_pct)}</b></td>${lev ? `<td class="num ${cls(s.equity_pct)}">${pct(s.equity_pct)}</td>` : ''}${full ? `<td class="num ${cls(s.portfolio_base)}">${money(s.portfolio_base)}</td>` : ''}<td class="num ${cls(rel)}">${rel == null ? '–' : pp(rel) + ' pts'}</td><td class="l small">${s.drivers.map((d) => `${esc(d.ticker)}${d.proxy ? '*' : ''} ${pct(d.pct, 1)}`).join(' · ')}</td></tr>`; }).join('');
  }

  function method() {
    const m = PF.method || {};
    $('#pfMethod').innerHTML = `<ul>
      <li><b>Exposure</b> answers "what do I actually own". ${esc(m.lookthrough ? m.lookthrough.charAt(0).toUpperCase() + m.lookthrough.slice(1) : 'Funds are split into their underlying names')}. Each name is filed under the same sub-industry and theme baskets Industry Pulse tracks, so the heat readings line up with the book.</li>
      <li><b>Returns decomposition</b> answers "why did it move". Factors: ${esc(m.factors || '')}. Betas use the trailing ${m.lookback_days || 252} trading days. A large idiosyncratic piece means the stock's own news drove it; a large market or style piece means it would have moved without any news.</li>
      <li><b>Key ratios</b> use ${esc(m.weights || '')}. They describe the book as it stands today, not the account's realised track record, which depends on when each position was bought.</li>
      <li><b>Expected moves</b>: ${esc(m.options || '')}. One standard deviation is not a floor: roughly one month in three the move is larger.</li>
      <li><b>Limits</b>: fund look-through covers sector weights and the ten largest names only; betas are estimated and drift; positions without a price history on Yahoo (options, private funds, some mutual funds) are left out.</li></ul>`;
  }

  function drawAll() { C.refresh(); head(); holdings($('#holdSeg button.on').dataset.v); exposure(); decomp(); ratios(); moves(); method(); }

  // ---------------------------------------------------------------- portal: sign-in, the visitor's own book, live quotes
  const status = (msg, kind) => { const el = $('#loadStatus'); if (el) { el.textContent = msg || ''; el.className = 'tier ' + (kind || ''); } };
  const Auth = (() => {
    if (CFG.supabaseUrl && CFG.supabaseAnonKey && window.supabase && window.supabase.createClient) {
      const sb = window.supabase.createClient(CFG.supabaseUrl, CFG.supabaseAnonKey);
      return { kind: 'supabase',
        session: async () => { const { data } = await sb.auth.getSession(); return data && data.session ? { token: data.session.access_token, email: data.session.user.email } : null; },
        signIn: async (email) => { const { error } = await sb.auth.signInWithOtp({ email, options: { emailRedirectTo: location.origin + location.pathname } }); if (error) throw new Error(error.message); },
        signOut: async () => { await sb.auth.signOut(); },
        onChange: (cb) => sb.auth.onAuthStateChange(() => cb()) };
    }
    if (LOCAL && qs.get('dev')) {   // local development only: the backend must run with DEV_AUTH=1
      let on = true; const email = qs.get('dev');
      return { kind: 'dev', session: async () => (on ? { token: 'dev:' + email, email } : null), signIn: async () => { on = true; }, signOut: async () => { on = false; }, onChange: () => {} };
    }
    return null;
  })();
  async function api(path, method, body) {
    const sess = Auth ? await Auth.session() : null; if (!sess) throw new Error('sign in first');
    const r = await fetch(API + path, { method: method || 'GET', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + sess.token }, body: body ? JSON.stringify(body) : undefined });
    let j = null; try { j = await r.json(); } catch (e) {}
    if (!r.ok) { const err = new Error((j && j.detail) || `request failed (${r.status})`); err.status = r.status; throw err; }
    return j;
  }
  function show(pf, src) { PF = pf; source = src; if (src !== 'mine') { LIVE = null; liveStrip(); } drawAll(); panel(); }
  function setMine(j) { ME = j; if (j.portfolio) { MINE = j.portfolio; show(MINE, 'mine'); pollQuotes(); } else panel(); }

  function panel() {
    const vis = (id, on) => { const el = $(id); if (el) el.style.display = on ? '' : 'none'; };
    const ready = !!API && !!Auth; const signed = !!ME; const example = source === 'demo';
    // the example banner shows whenever the book on screen is not the visitor's own
    vis('#exampleBanner', example);
    const tx = $('#exampleText');
    if (tx) tx.textContent = !signed ? 'These are made-up holdings that show what the page does. They are not yours.'
      : MINE ? `Signed in as ${ME.email}. You are looking at the example, not your own portfolio.`
      : `Signed in as ${ME.email}. This is still the example: your own portfolio is not linked yet.`;
    const ib = $('#btnIntegrate'); if (ib) ib.textContent = signed && MINE ? 'Show my portfolio' : 'Integrate your own';
    // the account card stays out of the way until asked for, or once the visitor has their own book on screen
    vis('#portal', integrateOpen || (signed && !example));
    vis('#authOut', ready && !signed); vis('#authIn', signed); vis('#portalOff', !ready);
    if (!signed) return;
    $('#authWho').textContent = ME.email + (ME.analysis_at ? ` · analysis saved ${ME.analysis_at.slice(0, 16).replace('T', ' ')} UTC${ME.source ? ' · from ' + ME.source : ''}` : '');
    const st = ME.snaptrade_available;
    vis('#btnConnect', st && !ME.connected); vis('#btnAdd', st && ME.connected); vis('#btnRefresh', ME.connected || ME.has_analysis); vis('#btnDisconnect', ME.connected);
    vis('#btnPaste', true); vis('#btnMine', !!MINE && source !== 'mine'); vis('#btnDemo', !!DEMO && source === 'mine');
    const bd = $('#btnDemo'); if (bd) bd.textContent = 'Show the example';
  }

  function liveStrip() {
    const el = $('#liveKpis'); if (!el) return;
    if (!LIVE || source !== 'mine') { el.style.display = 'none'; el.innerHTML = ''; return; }
    el.style.display = ''; const L = LIVE; const lev = PF.leverage > 1.02;
    const best = [...L.holdings].filter((h) => h.day_pct != null).sort((a, b) => b.day_pct - a.day_pct);
    el.innerHTML = [
      ['Value now', money(L.total_base), `${esc(L.base_currency)} · positions at latest quotes`],
      ['Today', `${L.day_change_base >= 0 ? '+' : '−'}${money(Math.abs(L.day_change_base))}`, `${pct(L.day_pct, 2)} on positions${lev && L.net_equity_base ? ` · ${pct(L.day_change_base / (L.net_equity_base - L.day_change_base), 2)} on equity` : ''}`],
      lev ? ['Net equity now', money(L.net_equity_base), 'positions minus the margin loan, plus cash'] : null,
      best.length ? ['Best / worst today', `${best[0].ticker} ${pct(best[0].day_pct, 1)}`, `${best[best.length - 1].ticker} ${pct(best[best.length - 1].day_pct, 1)}`] : null,
      ['Quotes', new Date(L.asof).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), 'refreshed every minute · about 15 min delayed'],
    ].filter(Boolean).map(([l, v, d]) => `<div class="kpi live"><div class="label">${esc(l)}</div><div class="value ${String(v).length > 12 ? 'text' : ''} ${l === 'Today' ? cls(L.day_change_base) : ''}">${esc(v)}</div><div class="delta">${esc(d)}</div></div>`).join('');
  }
  let pollTimer = null;
  async function pollQuotes() {
    if (source !== 'mine' || document.hidden) return;
    try { const j = await api('/api/me/quotes'); if (j.status === 'ok') { LIVE = j.live; liveStrip(); if ($('#holdSeg button.on').dataset.v === 'inst') holdings('inst'); } } catch (e) { /* keep the last quotes on screen */ }
  }
  function startPolling() { if (pollTimer) clearInterval(pollTimer); pollTimer = setInterval(pollQuotes, 60000); document.addEventListener('visibilitychange', () => { if (!document.hidden) pollQuotes(); }); }

  async function refresh(msg) {
    status(msg || 'Reading your accounts and running the analysis (about 20 seconds)…', '');
    try {
      const j = await api('/api/me/refresh', 'POST');
      if (j.status === 'ok') { setMine(j); status(`Up to date: ${j.portfolio.instruments.length} holdings.${j.skipped && j.skipped.length ? ` Left out (not priced): ${j.skipped.map((s) => s.ticker).join(', ')}.` : ''}`, 'pos'); }
      else if (j.status === 'no_connection') { ME = j; panel(); status('No brokerage is linked yet. Finish the connection in the portal tab, then press "Refresh".', ''); }
      else if (j.status === 'no_positions') { ME = j; panel(); status('The brokerage is linked but returned no stock or fund positions.', ''); }
      else { ME = j; panel(); status('Nothing saved yet: connect a brokerage or type your holdings.', ''); }
    } catch (e) { status(e.message, 'neg'); }
  }
  async function loadMine() {
    const sess = Auth ? await Auth.session() : null;
    if (!sess) { ME = null; MINE = null; if (source === 'mine' && DEMO) show(DEMO, 'demo'); panel(); return; }
    status('Loading your portfolio…', '');
    try {
      const j = await api('/api/me/portfolio');
      if (j.status === 'ok') {
        setMine(j); status('', '');
        if (qs.get('connected')) { history.replaceState(null, '', location.pathname); refresh('Brokerage linked. Pulling your positions (about 20 seconds)…'); }
        else if (j.stale && j.connected) refresh('Saved analysis is from an earlier session; refreshing from your brokerage…');
      } else {
        ME = j; integrateOpen = true; panel();
        if (qs.get('connected')) { history.replaceState(null, '', location.pathname); refresh('Brokerage linked. Pulling your positions (about 20 seconds)…'); }
        else status('You are signed in and still looking at the example. Link your brokerage (read-only) or type your holdings to replace it with your own.', '');
      }
    } catch (e) { ME = e.status === 403 ? null : ME; integrateOpen = true; panel(); status(e.message, 'neg'); if (e.status === 403) { const o = $('#authOut'); if (o) o.style.display = ''; } }
  }
  function parsePaste(text, base) {
    const positions = [];
    text.split(/\r?\n/).forEach((line) => {
      const parts = line.split(/[,\t;]+/).map((x) => x.trim()).filter((x) => x !== ''); if (parts.length < 2) return;
      const qty = parseFloat(parts[1].replace(/[$,\s]/g, '')); if (!parts[0] || isNaN(qty)) return;
      const cost = parts[2] != null ? parseFloat(parts[2].replace(/[$,\s]/g, '')) : NaN;
      const cur = (parts.find((x, i) => i >= 2 && /^(CAD|USD)$/i.test(x)) || base).toUpperCase();
      const rest = parts.slice(3).filter((x) => !/^(CAD|USD)$/i.test(x));
      positions.push({ ticker: parts[0].toUpperCase(), qty, avg_cost: isNaN(cost) ? null : cost, currency: cur, account: rest[0] || 'Account' });
    });
    return positions;
  }
  function openIntegrate() {
    if (ME && MINE) { show(MINE, 'mine'); pollQuotes(); return; }
    integrateOpen = true; panel();
    const card = $('#portal'); if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    if (!API || !Auth) { status('Linking your own portfolio opens as soon as the portal service is switched on.', ''); return; }
    if (!ME) { status('Step 1: sign in with the email you were invited with. Step 2: link your brokerage (read-only) or type your holdings.', ''); const e = $('#authEmail'); if (e) setTimeout(() => e.focus(), 300); return; }
    status(ME.snaptrade_available ? 'Link your brokerage with read-only access, or type your holdings in.' : 'Type your holdings in below (brokerage linking is not switched on yet).', '');
    if (!ME.snaptrade_available) { const p = $('#pastePanel'); if (p) p.style.display = ''; }
  }
  function initPortal() {
    panel();
    const on = (id, fn) => { const el = $(id); if (el) el.addEventListener('click', fn); };
    on('#btnIntegrate', openIntegrate);
    on('#btnSignIn', async () => {
      const email = ($('#authEmail').value || '').trim(); if (!/^\S+@\S+\.\S+$/.test(email)) { status('Enter the email address you were invited with.', 'neg'); return; }
      try { await Auth.signIn(email); status(Auth.kind === 'dev' ? '' : `Sign-in link sent to ${email}. Open it on this device; the link expires in an hour.`, 'pos'); if (Auth.kind === 'dev') loadMine(); }
      catch (e) { status(e.message, 'neg'); }
    });
    const portalLink = async () => {
      status('Opening the secure connection portal…', '');
      try { const j = await api('/api/me/connect/start', 'POST'); window.open(j.portal_url, '_blank', 'noopener'); status('Log in to your brokerage in the tab that just opened (read-only access). When it says connected, come back here and press "Refresh".', ''); ME = { ...ME, connected: true }; panel(); }
      catch (e) { status(e.message, 'neg'); }
    };
    on('#btnConnect', portalLink); on('#btnAdd', portalLink); on('#btnRefresh', () => refresh());
    on('#btnDisconnect', async () => {
      if (!confirm('Remove the brokerage connection and the saved analysis? You stay signed in.')) return;
      try { const j = await api('/api/me/disconnect', 'POST'); ME = j; MINE = null; if (DEMO) show(DEMO, 'demo'); else panel(); status('Connection removed.', ''); } catch (e) { status(e.message, 'neg'); }
    });
    on('#btnDelete', async () => {
      if (!confirm('Delete everything held for you: the brokerage connection, saved holdings and analysis, and your sign-in account? This cannot be undone.')) return;
      try { await api('/api/me', 'DELETE'); await Auth.signOut(); ME = null; MINE = null; if (DEMO) show(DEMO, 'demo'); panel(); status('Your data has been deleted.', ''); } catch (e) { status(e.message, 'neg'); }
    });
    on('#btnSignOut', async () => { await Auth.signOut(); ME = null; MINE = null; if (DEMO) show(DEMO, 'demo'); panel(); status('Signed out.', ''); });
    on('#btnPaste', () => { const p = $('#pastePanel'); p.style.display = p.style.display === 'none' ? '' : 'none'; });
    on('#btnAnalyse', async () => {
      const base = $('#pasteBase').value; const positions = parsePaste($('#pasteText').value, base);
      if (!positions.length) { status('Nothing to analyse: enter one holding per line as TICKER, quantity, average cost.', 'neg'); return; }
      status(`Analysing ${positions.length} holdings (about 15 seconds)…`, '');
      try { const j = await api('/api/me/holdings', 'POST', { holdings: { base_currency: base, positions } }); setMine(j); $('#pastePanel').style.display = 'none'; status(`Saved and analysed ${j.portfolio.instruments.length} holdings.`, 'pos'); }
      catch (e) { status(e.message, 'neg'); }
    });
    on('#btnDemo', () => show(DEMO, 'demo')); on('#btnMine', () => { show(MINE, 'mine'); pollQuotes(); });
    if (Auth && API) { Auth.onChange(() => loadMine()); loadMine(); startPolling(); }
  }

  document.addEventListener('DOMContentLoaded', () => {
    $$('#holdSeg button').forEach((b) => b.addEventListener('click', () => { $$('#holdSeg button').forEach((x) => x.classList.remove('on')); b.classList.add('on'); holdings(b.dataset.v); }));
    $$('#winSeg button').forEach((b) => b.addEventListener('click', () => { $$('#winSeg button').forEach((x) => x.classList.remove('on')); b.classList.add('on'); win = b.dataset.w; decomp(); }));
    const tb = $('#themeBtn'); if (tb) tb.addEventListener('click', () => setTimeout(() => { if (PF) drawAll(); }, 30));
    initPortal();
    if (!PF) { $('#pfSub').textContent = 'No portfolio loaded yet.'; return; }
    setTimeout(drawAll, 0);
  });
})();
