/* Chart layer for the Capital Rotation Tracker. Built on Chart.js 4; colors come from CSS tokens so light/dark both validate. */
(function () {
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const tok = () => ({
    ink: css('--ink'), ink2: css('--ink-2'), muted: css('--muted'), grid: css('--grid'), axis: css('--axis'), surface: css('--surface'),
    series: [css('--s1'), css('--s2'), css('--s3'), css('--s4'), css('--s5'), css('--s6'), css('--s7'), css('--s8')],
    gray: css('--gray-series'), pos: css('--div-pos'), neg: css('--div-neg'), mid: css('--div-mid'),
    seq: [css('--seq-100'), css('--seq-250'), css('--seq-400'), css('--seq-550'), css('--seq-700')],
  });
  const charts = new Map();

  function baseOptions(t, opts) {
    return {
      responsive: true, maintainAspectRatio: false, animation: false, normalized: true,
      interaction: { mode: 'index', intersect: false },
      layout: { padding: { top: 4, right: 8, bottom: 0, left: 0 } },
      plugins: {
        legend: { display: !!opts.legend, position: 'top', align: 'start', labels: { color: t.ink2, usePointStyle: true, pointStyle: 'line', boxWidth: 18, boxHeight: 2, font: { size: 12 }, filter: (item) => !item.text.startsWith('_') } },
        tooltip: {
          backgroundColor: t.surface, titleColor: t.ink, bodyColor: t.ink2, borderColor: t.axis, borderWidth: 1, padding: 10, displayColors: true, usePointStyle: true, boxWidth: 8, boxHeight: 8,
          filter: (item) => !item.dataset.label.startsWith('_') && item.parsed.y != null,
          callbacks: {
            label: (c) => `${opts.fmt ? opts.fmt(c.parsed.y) : c.parsed.y}  ${c.dataset.label}`,
            title: (items) => items.length ? opts.titleFmt ? opts.titleFmt(items[0].label) : items[0].label : '',
          },
        },
      },
      scales: {
        x: { grid: { display: false }, border: { color: t.axis }, ticks: { color: t.muted, maxRotation: 0, autoSkip: false, font: { size: 11 }, callback: opts.xTick } },
        y: { grid: { color: t.grid, drawTicks: false }, border: { display: false }, ticks: { color: t.muted, font: { size: 11 }, callback: opts.yTick, maxTicksLimit: 7 }, beginAtZero: !!opts.zero, suggestedMin: opts.min, suggestedMax: opts.max, type: opts.log ? 'logarithmic' : 'linear' },
      },
      elements: { line: { borderWidth: 2, tension: 0 }, point: { radius: 0, hitRadius: 12, hoverRadius: 4 } },
    };
  }

  // x labels are 'YYYY-MM-DD'; show the year once at January, and every N years when dense
  function yearTicks(labels) {
    const n = labels.length; const every = n > 240 ? 2 : 1;
    return function (val, idx) { const l = labels[idx]; if (!l) return ''; const y = l.slice(0, 4), m = l.slice(5, 7); if (m !== '01') return ''; return (+y % every === 0) ? y : ''; };
  }

  // Regime bands plugin: alternating washes with the regime name at the top
  const regimePlugin = {
    id: 'regimes',
    beforeDatasetsDraw(chart, args, o) {
      if (!o || !o.regimes || !o.regimes.length) return;
      const { ctx, chartArea: a, scales: { x } } = chart; const labels = chart.data.labels; const t = tok();
      ctx.save();
      o.regimes.forEach((r, i) => {
        const i0 = idxFor(labels, r.start), i1 = r.end ? idxFor(labels, r.end) : labels.length - 1; if (i0 == null || i1 == null || i1 <= i0) return;
        const x0 = x.getPixelForValue(i0), x1 = x.getPixelForValue(i1);
        ctx.fillStyle = i % 2 ? 'rgba(255,176,0,0.07)' : 'rgba(0,0,0,0)'; ctx.fillRect(x0, a.top, x1 - x0, a.bottom - a.top);
        if (x1 - x0 > 46) { ctx.fillStyle = t.muted; ctx.font = '10px "IBM Plex Mono", Consolas, monospace'; ctx.textAlign = 'left'; ctx.fillText(short(r.name, Math.floor((x1 - x0) / 6)), x0 + 3, a.top + 10); }
      });
      ctx.restore();
    },
  };
  // Event markers plugin: small triangles on the baseline at event dates; 'in' blue, 'out' red
  const eventPlugin = {
    id: 'eventMarks',
    afterDatasetsDraw(chart, args, o) {
      if (!o || !o.events || !o.events.length) return;
      const { ctx, chartArea: a, scales: { x } } = chart; const labels = chart.data.labels; const t = tok();
      ctx.save();
      o.events.forEach((e) => {
        const i = idxFor(labels, e.date); if (i == null) return; const px = x.getPixelForValue(i);
        ctx.fillStyle = e.direction === 'in' ? t.pos : e.direction === 'out' ? t.neg : t.muted; ctx.globalAlpha = 0.9;
        ctx.beginPath(); ctx.moveTo(px, a.bottom - 1); ctx.lineTo(px - 4, a.bottom + 6); ctx.lineTo(px + 4, a.bottom + 6); ctx.closePath(); ctx.fill();
      });
      ctx.restore();
    },
  };
  // Crosshair hairline for line charts
  const crosshair = {
    id: 'crosshair',
    afterDraw(chart) {
      const act = chart.tooltip && chart.tooltip._active; if (!act || !act.length) return;
      const { ctx, chartArea: a } = chart; const x = act[0].element.x; const t = tok();
      ctx.save(); ctx.strokeStyle = t.axis; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x, a.top); ctx.lineTo(x, a.bottom); ctx.stroke(); ctx.restore();
    },
  };
  Chart.register(regimePlugin, eventPlugin, crosshair);
  Chart.defaults.font.family = '"IBM Plex Mono", Consolas, "Roboto Mono", monospace';
  Chart.defaults.font.size = 11;

  function idxFor(labels, dateStr) {
    if (!dateStr) return null; const ym = dateStr.slice(0, 7);
    for (let i = 0; i < labels.length; i++) if (labels[i].slice(0, 7) >= ym) return i;
    return null;
  }
  function short(s, n) { return s.length > n ? s.slice(0, Math.max(0, n - 1)) + '…' : s; }

  function make(id, config) {
    const el = document.getElementById(id); if (!el) return null;
    if (charts.has(id)) { charts.get(id).destroy(); charts.delete(id); }
    const ch = new Chart(el.getContext('2d'), config); charts.set(id, ch); return ch;
  }

  /** Multi-line chart. series: [{label, data, color?, emphasis?, width?, dash?}] ; labels: x labels */
  function lines(id, labels, series, opts = {}) {
    const t = tok(); let k = 0;
    const datasets = series.map((s) => {
      const emph = s.emphasis !== undefined ? s.emphasis : true;
      const color = s.color || (emph ? t.series[(k++) % 8] : t.gray);
      return { label: s.label, data: s.data, borderColor: color, backgroundColor: color, borderWidth: s.width || (emph ? 2 : 1.25), borderDash: s.dash || [], order: emph ? 0 : 1, spanGaps: true, fill: s.fill || false, pointRadius: 0 };
    });
    const o = baseOptions(t, { legend: opts.legend !== false && series.filter((s) => !s.label.startsWith('_')).length > 1, fmt: opts.fmt, xTick: yearTicks(labels), yTick: opts.yTick, log: opts.log, min: opts.min, max: opts.max, zero: opts.zero });
    o.plugins.regimes = { regimes: opts.regimes || [] }; o.plugins.eventMarks = { events: opts.events || [] };
    if (opts.refLine != null) datasets.push({ label: '_ref', data: labels.map(() => opts.refLine), borderColor: t.axis, borderWidth: 1, borderDash: [3, 3], pointRadius: 0, order: 2 });
    return make(id, { type: 'line', data: { labels, datasets }, options: o });
  }

  /** Stacked area (part-to-whole over time). series: [{label, data}] capped at 8 */
  function stackedArea(id, labels, series, opts = {}) {
    const t = tok();
    const datasets = series.map((s, i) => ({ label: s.label, data: s.data, borderColor: t.surface, borderWidth: 1, backgroundColor: s.color || t.series[i % 8], fill: true, pointRadius: 0, spanGaps: true, stack: 's' }));
    const o = baseOptions(t, { legend: true, fmt: opts.fmt, xTick: yearTicks(labels), yTick: opts.yTick, zero: true, max: opts.max });
    o.scales.y.stacked = true; o.plugins.legend.labels.pointStyle = 'rect'; o.plugins.legend.labels.boxWidth = 10; o.plugins.legend.labels.boxHeight = 10;
    o.plugins.regimes = { regimes: opts.regimes || [] };
    return make(id, { type: 'line', data: { labels, datasets }, options: o });
  }

  /** Diverging bars (positive blue / negative red), horizontal when many categories. */
  function divergingBars(id, labels, values, opts = {}) {
    const t = tok(); const horiz = opts.horizontal !== false;
    const colors = values.map((v) => (v == null ? t.mid : v >= 0 ? t.pos : t.neg));
    const o = baseOptions(t, { legend: false, fmt: opts.fmt, yTick: opts.yTick });
    o.indexAxis = horiz ? 'y' : 'x'; o.interaction = { mode: 'nearest', intersect: true };
    if (horiz) { o.scales.x = { grid: { color: t.grid, drawTicks: false }, border: { display: false }, ticks: { color: t.muted, font: { size: 11 }, callback: opts.yTick } }; o.scales.y = { grid: { display: false }, border: { color: t.axis }, ticks: { color: t.ink2, font: { size: 12 }, autoSkip: false } }; }
    else { o.scales.x.ticks.callback = undefined; o.scales.x.ticks.autoSkip = true; }
    o.plugins.tooltip.callbacks.label = (c) => `${opts.fmt ? opts.fmt(horiz ? c.parsed.x : c.parsed.y) : (horiz ? c.parsed.x : c.parsed.y)}`;
    return make(id, { type: 'bar', data: { labels, datasets: [{ label: opts.label || 'value', data: values, backgroundColor: colors, borderRadius: 4, borderSkipped: 'start', barPercentage: 0.7, categoryPercentage: 0.85 }] }, options: o });
  }

  /** Monthly flow bars (diverging) with x year ticks */
  function flowBars(id, labels, values, opts = {}) {
    const t = tok(); const colors = values.map((v) => (v == null ? t.mid : v >= 0 ? t.pos : t.neg));
    const o = baseOptions(t, { legend: false, fmt: opts.fmt, xTick: yearTicks(labels), yTick: opts.yTick });
    o.interaction = { mode: 'index', intersect: false };
    o.plugins.tooltip.callbacks.label = (c) => `${opts.fmt ? opts.fmt(c.parsed.y) : c.parsed.y}  ${c.dataset.label}`;
    o.plugins.regimes = { regimes: opts.regimes || [] };
    return make(id, { type: 'bar', data: { labels, datasets: [{ label: opts.label || 'net flow', data: values, backgroundColor: colors, borderRadius: 2, borderSkipped: 'start', barPercentage: 1, categoryPercentage: 0.9 }] }, options: o });
  }

  function refresh() { for (const [id, ch] of charts) { ch.destroy(); } charts.clear(); }

  window.CRTCharts = { lines, stackedArea, divergingBars, flowBars, tok, refresh, idxFor };
})();
