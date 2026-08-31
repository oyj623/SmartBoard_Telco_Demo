/**
 * ECharts adapter — the chart half of the viz registry.
 *
 * Each function here is one entry on the menu the brain picks from. The brain
 * sends `viz: "line"`, an encoding and optionally a `style`; this file decides
 * everything about how a line actually looks. That separation is the point —
 * you can redesign every chart in this file without touching a prompt, and the
 * model cannot make your dashboard ugly because it has no say in the matter.
 *
 * Three things worth knowing before editing:
 *
 * 1. COLOURS ARE RESOLVED TO RGB before they reach ECharts. See ./colour.js.
 * 2. NO CALLBACK ITEM COLOURS. Per-datum colours are computed up front and
 *    every series declares an explicit `emphasis.itemStyle`. See markStyle().
 * 3. Clicks become SELECTION, not commands. See mount().
 */

import * as echarts from 'echarts';
import { formatValue, t } from '../client.js';
import { css, fade, palette } from './colour.js';

// ---------------------------------------------------------------------------
// Colour
//
// Everything goes through colour.js, which converts CSS colours the chart
// libraries cannot parse — oklch, in this project's case — into plain rgb.
// See the note at the top of that file; the vanishing-bar-on-hover bug lived
// here until it existed.
// ---------------------------------------------------------------------------

/** The colour a highlighted or selected mark turns. Deliberately not a series colour. */
const litColour = () => css('--high', '#e08a4a');

// ---------------------------------------------------------------------------
// Shared option scaffolding
// ---------------------------------------------------------------------------

function baseOption(ctx, style = {}) {
  return {
    color: palette(style),
    animationDuration: 380,
    animationEasing: 'cubicOut',
    textStyle: { fontFamily: 'Inter, system-ui, sans-serif', fontSize: 11 },
    grid: { top: style.legend === false ? 14 : 22, right: 18, bottom: 26, left: 54, containLabel: true },
    tooltip: {
      trigger: ctx.trigger || 'axis',
      backgroundColor: css('--bg-1', '#14121F'),
      borderColor: css('--line', '#262238'),
      borderWidth: 1,
      textStyle: { color: css('--fg', '#EDEAF6'), fontSize: 11 },
      axisPointer: { type: 'line', lineStyle: { color: css('--line', '#262238') } },
    },
    legend: {
      show: style.legend ?? ctx.showLegend ?? false,
      top: 0,
      right: 0,
      itemWidth: 8,
      itemHeight: 8,
      textStyle: { color: css('--fg-3', '#7E7899'), fontSize: 10 },
    },
  };
}

const axisStyle = (style = {}) => ({
  axisLine: { lineStyle: { color: css('--line', '#262238') } },
  axisTick: { show: false },
  axisLabel: { color: css('--fg-3', '#7E7899'), fontSize: 10, hideOverlap: true },
  splitLine: {
    show: style.grid ?? true,
    lineStyle: { color: css('--line', '#262238'), opacity: 0.45 },
  },
});

/** Value-axis bounds and title, straight from the assistant's style block. */
const valueAxis = (style = {}, name) => ({
  type: 'value',
  ...(style.y_min != null ? { min: style.y_min } : {}),
  ...(style.y_max != null ? { max: style.y_max } : {}),
  ...(style.y_label || name
    ? { name: style.y_label || name, nameTextStyle: { color: css('--fg-3'), fontSize: 9 }, nameGap: 12 }
    : {}),
  ...axisStyle(style),
});

/** A benchmark marker line, from `style.reference_line`. */
function referenceLine(style = {}) {
  if (style.reference_line == null) return undefined;
  return {
    silent: true,
    symbol: 'none',
    label: {
      show: !!style.reference_label,
      formatter: style.reference_label || '',
      position: 'insideEndTop',
      color: css('--fg-3'),
      fontSize: 9,
    },
    lineStyle: { color: css('--warn', '#e8b84a'), type: 'dashed', width: 1.4 },
    data: [{ yAxis: style.reference_line }],
  };
}

const colFor = (columns, id) => columns.find((c) => c.id === id) || { id };
const label = (columns, id, manifest, locale) =>
  t(manifest?.metrics?.[id]?.label || manifest?.dimensions?.[id]?.label, locale) ||
  colFor(columns, id).label ||
  id;

/**
 * Which keys in this panel are currently emphasised, and why.
 *
 * Two independent mechanisms land here. The user's SELECTION is sticky and is
 * what gets quoted into the chat. The assistant's HIGHLIGHT expires. Selection
 * wins when both are live, because the person's own click should never be
 * overridden by something the assistant did twenty seconds ago.
 */
function emphasisSet(ctx) {
  const { store, panel } = ctx;
  if (!store || !panel) return null;

  const selected = store.getState().selection.filter((e) => e.panelId === panel.panelId && e.key != null);
  if (selected.length) return { keys: new Set(selected.map((e) => String(e.key))), source: 'selection' };

  const hl = store.getState().highlight;
  if (hl?.keys?.length && (!hl.panelId || hl.panelId === panel.panelId) && Date.now() <= hl.expiresAt) {
    return { keys: new Set(hl.keys.map(String)), source: 'highlight' };
  }
  return null;
}

/**
 * Per-datum item style, computed up front.
 *
 * Never a callback. ECharts derives the hover colour from the resolved normal
 * colour, and a callback is not resolvable at that point — the mark renders and
 * then vanishes under the cursor. Declaring both states explicitly is the fix.
 */
function markStyle(baseColour, isLit, hasEmphasis, style = {}) {
  const opacity = style.opacity ?? 1;
  const colour = hasEmphasis ? (isLit ? litColour() : fade(baseColour, 0.18)) : baseColour;
  return {
    itemStyle: { color: colour, opacity, borderRadius: style.__radius },
    emphasis: {
      itemStyle: {
        color: hasEmphasis && !isLit ? fade(baseColour, 0.4) : colour,
        opacity: 1,
        borderColor: css('--fg', '#fff'),
        borderWidth: 1,
      },
    },
  };
}

/** Reorder categories by value, if the style asked for it. Does not re-query. */
function sortCategories(categories, valueOf, style = {}) {
  if (!style.sort || style.sort === 'none') return categories;
  const dir = style.sort === 'asc' ? 1 : -1;
  return [...categories].sort((a, b) => dir * ((valueOf(a) ?? 0) - (valueOf(b) ?? 0)));
}

// ---------------------------------------------------------------------------
// Mount
// ---------------------------------------------------------------------------

/**
 * Build an ECharts instance, keep it sized, wire selection, dispose on teardown.
 *
 * Clicking a mark SELECTS it rather than issuing a command. That is the change
 * from the reference adapter, and it is the right one: a click is the person
 * saying "this one", which is a question waiting to be asked, not an assertion
 * about what the board should look like. The selection is quoted into the chat
 * composer the way a reply quotes a message, and the next thing they type is
 * about the thing they picked.
 *
 * Ctrl or Cmd extends the selection. Clicking the chart's empty background
 * selects the whole panel, so "summarise this chart" needs one click.
 */
function mount(el, buildOption, ctx) {
  const chart = echarts.init(el, null, { renderer: 'canvas' });
  chart.setOption(buildOption());

  const ro = new ResizeObserver(() => chart.resize());
  ro.observe(el);

  const { store, panel } = ctx;

  const onSeriesClick = (params) => {
    if (!store || !panel) return;
    const key = params.name ?? params.value?.[0];
    if (key == null || key === '') return;
    const native = params.event?.event;
    store.toggleSelection(
      { panelId: panel.panelId, key: String(key), label: String(key) },
      !!(native?.ctrlKey || native?.metaKey),
    );
  };
  chart.on('click', onSeriesClick);

  // zrender sees every click on the canvas, including the ones that miss a
  // mark. `event.target` is undefined exactly when nothing was hit.
  const zr = chart.getZr();
  const onCanvasClick = (event) => {
    if (event.target || !store || !panel) return;
    store.toggleSelection(
      { panelId: panel.panelId, key: null, label: t(panel.title, ctx.locale) || panel.panelId },
      !!(event.event?.ctrlKey || event.event?.metaKey),
    );
  };
  zr.on('click', onCanvasClick);

  return () => {
    ro.disconnect();
    zr.off('click', onCanvasClick);
    chart.dispose();
  };
}

/** Panel-level click target for the HTML-rendered kinds (kpi, stat, table). */
function bindHtmlSelection(el, ctx, keyOf) {
  const { store, panel } = ctx;
  if (!store || !panel) return () => {};

  const onClick = (event) => {
    const target = event.target.closest('[data-select-key]');
    const raw = target ? target.getAttribute('data-select-key') : null;
    const key = raw && raw !== '' ? raw : null;
    store.toggleSelection(
      {
        panelId: panel.panelId,
        key,
        label: key ?? (t(panel.title, ctx.locale) || panel.panelId),
      },
      !!(event.ctrlKey || event.metaKey),
    );
  };

  el.addEventListener('click', onClick);
  return () => el.removeEventListener('click', onClick);
}

const esc = (v) =>
  String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

// ---------------------------------------------------------------------------
// Kinds
// ---------------------------------------------------------------------------

/**
 * A single number, large, with an optional benchmark comparison.
 *
 * The tile a dashboard opens with. `style.reference_line` turns it into a
 * comparison — the delta is coloured by the metric's declared `direction`, so a
 * rising cost reads red and a rising yield reads green without anyone writing
 * that conditional in a component.
 */
export function stat(el, ctx) {
  const { rows, columns, encoding, manifest, panel, store, locale } = ctx;
  const style = panel?.style || {};
  const metric =
    encoding.value || (encoding.y || [])[0] || columns.find((c) => c.role === 'metric')?.id;
  const col = colFor(columns, metric);
  const spec = manifest?.metrics?.[metric] || {};
  const value = rows[0]?.[metric];

  let delta = '';
  if (style.reference_line != null && Number.isFinite(value)) {
    const diff = value - style.reference_line;
    const pct = style.reference_line ? (100 * diff) / Math.abs(style.reference_line) : 0;
    const good = spec.direction === 'down_good' ? diff < 0 : diff > 0;
    const tone = spec.direction === 'neutral' ? 'var(--fg-3)' : good ? 'var(--ok)' : 'var(--crit)';
    delta = `<div class="stat-delta" style="color:${tone}">
      ${diff >= 0 ? '▲' : '▼'} ${Math.abs(pct).toFixed(1)}%
      <span class="stat-delta-vs">vs ${esc(style.reference_label || formatValue(style.reference_line, col, manifest))}</span>
    </div>`;
  }

  const lit = store?.isSelected(panel?.panelId, null) || store?.isSelected(panel?.panelId, metric);

  el.innerHTML = `<div class="stat${lit ? ' is-selected' : ''}" data-select-key="${esc(metric)}">
    <div class="stat-label">${esc(label(columns, metric, manifest, locale))}</div>
    <div class="stat-value">${esc(formatValue(value, col, manifest))}</div>
    <div class="stat-unit">${esc(spec.unit || '')}</div>
    ${delta}
  </div>`;

  return bindHtmlSelection(el, ctx);
}

/** A row of numbers. Same idea as `stat`, for two to four metrics side by side. */
export function kpi(el, ctx) {
  const { rows, columns, encoding, manifest, store, panel, locale } = ctx;
  const ids = encoding.value
    ? [encoding.value]
    : encoding.y || columns.filter((c) => c.role === 'metric').map((c) => c.id);
  const row = rows[0] || {};

  el.innerHTML = `<div class="kpi-row">${ids
    .map((id) => {
      const col = colFor(columns, id);
      const spec = manifest?.metrics?.[id] || {};
      const lit = store?.isSelected(panel?.panelId, id);
      return `<div class="kpi${lit ? ' is-selected' : ''}" data-select-key="${esc(id)}">
        <div class="kpi-label">${esc(label(columns, id, manifest, locale))}</div>
        <div class="kpi-value">${esc(formatValue(row[id], col, manifest))}</div>
        <div class="kpi-unit">${esc(spec.unit || '')}</div>
      </div>`;
    })
    .join('')}</div>`;

  return bindHtmlSelection(el, ctx);
}

export function line(el, ctx) {
  return timeSeries(el, ctx, false);
}

export function area(el, ctx) {
  return timeSeries(el, ctx, true);
}

function timeSeries(el, ctx, filled) {
  const { rows, columns, encoding, manifest, panel, locale } = ctx;
  const style = panel?.style || {};
  const x = encoding.x || columns.find((c) => c.role === 'dimension')?.id;
  const ys = encoding.y || columns.filter((c) => c.role === 'metric').map((c) => c.id);
  const seriesDim = encoding.series;

  const categories = [...new Set(rows.map((r) => r[x]))];
  const groups = seriesDim ? [...new Set(rows.map((r) => r[seriesDim]))] : [null];
  const colours = palette(style);
  const emph = emphasisSet(ctx);

  const series = [];
  let n = 0;
  for (const g of groups) {
    for (const y of ys) {
      const subset = g == null ? rows : rows.filter((r) => r[seriesDim] === g);
      const byX = new Map(subset.map((r) => [r[x], r[y]]));
      const colour = colours[n % colours.length];
      const dim = emph && !emph.keys.has(String(g ?? y));
      n += 1;

      series.push({
        name: g == null ? label(columns, y, manifest, locale) : `${g}${ys.length > 1 ? ` · ${y}` : ''}`,
        type: 'line',
        smooth: style.smooth ?? 0.25,
        symbol: 'circle',
        symbolSize: 5,
        showSymbol: categories.length <= 20,
        stack: style.stack ? 'total' : undefined,
        lineStyle: { width: dim ? 1 : 2, color: dim ? fade(colour, 0.25) : colour },
        itemStyle: { color: dim ? fade(colour, 0.25) : colour },
        emphasis: { itemStyle: { color: colour, borderColor: css('--fg'), borderWidth: 1 } },
        areaStyle: filled ? { opacity: dim ? 0.04 : 0.14, color: colour } : undefined,
        label: style.labels
          ? { show: true, fontSize: 9, color: css('--fg-3'), formatter: (p) => formatValue(p.value, colFor(columns, ys[0]), manifest) }
          : undefined,
        // A second measure on a very different scale needs its own axis, or it
        // reads as a flat line at zero.
        yAxisIndex: ys.length > 1 && !seriesDim ? ys.indexOf(y) % 2 : 0,
        data: categories.map((c) => byX.get(c) ?? null),
        markLine: referenceLine(style),
      });
    }
  }

  return mount(
    el,
    () => ({
      ...baseOption({ showLegend: series.length > 1 }, style),
      xAxis: {
        type: 'category',
        data: categories,
        boundaryGap: false,
        ...(style.x_label ? { name: style.x_label, nameLocation: 'middle', nameGap: 26 } : {}),
        ...axisStyle(style),
      },
      yAxis:
        ys.length > 1 && !seriesDim
          ? [
              valueAxis(style, label(columns, ys[0], manifest, locale)),
              { ...valueAxis(style, label(columns, ys[1], manifest, locale)), splitLine: { show: false } },
            ]
          : valueAxis(style),
      series,
    }),
    ctx,
  );
}

export function bar(el, ctx) {
  return bars(el, ctx, false);
}

export function stacked_bar(el, ctx) {
  return bars(el, ctx, true);
}

function bars(el, ctx, stacked) {
  const { rows, columns, encoding, manifest, panel, locale } = ctx;
  const style = panel?.style || {};
  const x = encoding.x || columns.find((c) => c.role === 'dimension')?.id;
  const ys = encoding.y || columns.filter((c) => c.role === 'metric').map((c) => c.id);
  const seriesDim = encoding.series;
  const doStack = stacked || style.stack;

  let categories = [...new Set(rows.map((r) => r[x]))];
  categories = sortCategories(
    categories,
    (c) => rows.filter((r) => r[x] === c).reduce((sum, r) => sum + (r[ys[0]] ?? 0), 0),
    style,
  );

  const groups = seriesDim ? [...new Set(rows.map((r) => r[seriesDim]))] : null;
  const colours = palette(style);
  const emph = emphasisSet(ctx);
  const radius = doStack ? 0 : [3, 3, 0, 0];

  const valueLabel = style.labels
    ? {
        show: true,
        position: style.horizontal ? 'right' : 'top',
        fontSize: 9,
        color: css('--fg-3'),
        formatter: (p) => formatValue(p.value, colFor(columns, ys[0]), manifest),
      }
    : undefined;

  const series = groups
    ? groups.map((g, i) => {
        const colour = colours[i % colours.length];
        return {
          name: String(g),
          type: 'bar',
          stack: doStack ? 'total' : undefined,
          label: valueLabel,
          markLine: i === 0 ? referenceLine(style) : undefined,
          data: categories.map((c) => {
            const value = rows.find((r) => r[x] === c && r[seriesDim] === g)?.[ys[0]] ?? 0;
            const lit = !emph || emph.keys.has(String(c)) || emph.keys.has(String(g));
            return { value, ...markStyle(colour, lit, !!emph, { ...style, __radius: radius }) };
          }),
        };
      })
    : ys.map((y, i) => {
        const colour = colours[i % colours.length];
        return {
          name: label(columns, y, manifest, locale),
          type: 'bar',
          stack: doStack ? 'total' : undefined,
          yAxisIndex: ys.length > 1 && !doStack ? i % 2 : 0,
          label: valueLabel,
          markLine: i === 0 ? referenceLine(style) : undefined,
          data: categories.map((c) => {
            const value = rows.find((r) => r[x] === c)?.[y] ?? 0;
            const lit = !emph || emph.keys.has(String(c));
            return { value, ...markStyle(colour, lit, !!emph, { ...style, __radius: radius }) };
          }),
        };
      });

  const category = {
    type: 'category',
    data: categories,
    ...(style.x_label ? { name: style.x_label, nameLocation: 'middle', nameGap: 28 } : {}),
    ...axisStyle(style),
    axisLabel: {
      ...axisStyle(style).axisLabel,
      rotate: style.horizontal ? 0 : categories.length > 7 ? 32 : 0,
    },
  };
  const measure =
    ys.length > 1 && !groups && !doStack
      ? [valueAxis(style), { ...valueAxis(style), splitLine: { show: false } }]
      : valueAxis(style);

  return mount(
    el,
    () => ({
      ...baseOption({ showLegend: series.length > 1 }, style),
      // Horizontal bars are the same chart with the axes swapped. Long category
      // names — twelve division names, say — are readable this way and are not
      // readable rotated 32 degrees.
      xAxis: style.horizontal ? measure : category,
      yAxis: style.horizontal ? category : measure,
      series,
    }),
    ctx,
  );
}

export function scatter(el, ctx) {
  const { rows, columns, encoding, manifest, panel, locale } = ctx;
  const style = panel?.style || {};
  const metrics = columns.filter((c) => c.role === 'metric').map((c) => c.id);
  const xId = encoding.x && metrics.includes(encoding.x) ? encoding.x : metrics[0];
  const yId = (encoding.y || [])[0] || metrics[1] || metrics[0];
  const nameDim = columns.find((c) => c.role === 'dimension')?.id;
  const sizeId = encoding.size;

  const sizes = sizeId ? rows.map((r) => r[sizeId] ?? 0) : [];
  const maxSize = Math.max(...sizes, 1);
  const colour = palette(style)[0];
  const emph = emphasisSet(ctx);

  const data = rows.map((r) => {
    const key = nameDim ? String(r[nameDim]) : '';
    const lit = !emph || emph.keys.has(key);
    return {
      name: key,
      value: [r[xId], r[yId], key, sizeId ? r[sizeId] : 0],
      ...markStyle(colour, lit, !!emph, style),
    };
  });

  return mount(
    el,
    () => ({
      ...baseOption({ trigger: 'item' }, style),
      tooltip: {
        ...baseOption({ trigger: 'item' }, style).tooltip,
        formatter: (p) =>
          `${p.value[2] ?? ''}<br/>${label(columns, xId, manifest, locale)}: ${formatValue(p.value[0], colFor(columns, xId), manifest)}` +
          `<br/>${label(columns, yId, manifest, locale)}: ${formatValue(p.value[1], colFor(columns, yId), manifest)}`,
      },
      xAxis: {
        type: 'value',
        name: style.x_label || label(columns, xId, manifest, locale),
        nameLocation: 'middle',
        nameGap: 26,
        nameTextStyle: { color: css('--fg-3'), fontSize: 10 },
        ...(style.x_min != null ? { min: style.x_min } : {}),
        ...(style.x_max != null ? { max: style.x_max } : {}),
        ...axisStyle(style),
      },
      yAxis: valueAxis(style, label(columns, yId, manifest, locale)),
      series: [
        {
          type: 'scatter',
          symbolSize: (d) => (sizeId ? 6 + 22 * Math.sqrt((d[3] ?? 0) / maxSize) : 10),
          markLine: referenceLine(style),
          data,
        },
      ],
    }),
    ctx,
  );
}

export function donut(el, ctx) {
  const { rows, columns, encoding, manifest, panel } = ctx;
  const style = panel?.style || {};
  const dim = encoding.x || columns.find((c) => c.role === 'dimension')?.id;
  const metric = encoding.value || (encoding.y || [])[0] || columns.find((c) => c.role === 'metric')?.id;
  const colours = palette(style);
  const emph = emphasisSet(ctx);

  const data = rows.map((r, i) => {
    const key = String(r[dim]);
    const lit = !emph || emph.keys.has(key);
    const base = colours[i % colours.length];
    return { name: key, value: r[metric], ...markStyle(base, lit, !!emph, style) };
  });

  return mount(
    el,
    () => ({
      ...baseOption({ trigger: 'item', showLegend: true }, style),
      legend: {
        ...baseOption({ showLegend: true }, style).legend,
        show: style.legend ?? true,
        orient: 'vertical',
        left: 0,
        top: 'middle',
      },
      series: [
        {
          type: 'pie',
          radius: ['52%', '76%'],
          center: ['64%', '50%'],
          // The slice border is the panel background, which makes the ring read
          // as separate segments. It must be a resolved colour like every other.
          itemStyle: { borderColor: css('--bg-1', '#14121F'), borderWidth: 2 },
          label: { show: style.labels ?? true, color: css('--fg-3'), fontSize: 10, formatter: '{b}' },
          labelLine: { lineStyle: { color: css('--line') } },
          data,
        },
      ],
    }),
    ctx,
  );
}

export function gauge(el, ctx) {
  const { rows, encoding, columns, manifest, panel, locale } = ctx;
  const style = panel?.style || {};
  const metric = encoding.value || columns.find((c) => c.role === 'metric')?.id;
  const value = rows[0]?.[metric] ?? 0;
  const colour = palette(style)[0];

  return mount(
    el,
    () => ({
      ...baseOption({ trigger: 'item' }, style),
      series: [
        {
          type: 'gauge',
          radius: '92%',
          startAngle: 210,
          endAngle: -30,
          min: style.y_min ?? 0,
          max: style.y_max ?? 100,
          progress: { show: true, width: 12, itemStyle: { color: colour } },
          axisLine: { lineStyle: { width: 12, color: [[1, css('--line', '#262238')]] } },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { color: css('--fg-3'), fontSize: 9, distance: 14 },
          pointer: { show: false },
          detail: {
            valueAnimation: true,
            color: css('--fg', '#EDEAF6'),
            fontSize: 26,
            offsetCenter: [0, '10%'],
            formatter: (v) => v.toFixed(1),
          },
          title: { color: css('--fg-3'), fontSize: 10, offsetCenter: [0, '38%'] },
          data: [{ value, name: style.y_label || label(columns, metric, manifest, locale) }],
        },
      ],
    }),
    ctx,
  );
}

export function table(el, ctx) {
  const { rows, columns, manifest, store, panel } = ctx;
  const style = panel?.style || {};
  const cols = columns.filter((c) => !c.id.endsWith('__lat') && !c.id.endsWith('__lng'));
  const dimId = cols.find((c) => c.role === 'dimension')?.id;
  const emph = emphasisSet(ctx);

  let body = rows;
  if (style.sort && style.sort !== 'none') {
    const metricId = cols.find((c) => c.role === 'metric')?.id;
    const dir = style.sort === 'asc' ? 1 : -1;
    body = [...rows].sort((a, b) => dir * ((a[metricId] ?? 0) - (b[metricId] ?? 0)));
  }

  const head = cols.map((c) => `<th class="${c.role}">${esc(c.label || c.id)}</th>`).join('');
  const tbody = body
    .slice(0, 200)
    .map((r) => {
      const key = dimId ? String(r[dimId]) : '';
      const lit = emph ? emph.keys.has(key) : false;
      const cells = cols
        .map(
          (c) =>
            `<td class="${c.role}">${
              c.role === 'metric' ? esc(formatValue(r[c.id], c, manifest)) : esc(r[c.id] ?? '—')
            }</td>`,
        )
        .join('');
      return `<tr class="${lit ? 'is-lit' : ''}${
        store?.isSelected(panel?.panelId, key) ? ' is-selected' : ''
      }" data-select-key="${esc(key)}">${cells}</tr>`;
    })
    .join('');

  el.innerHTML = `<div class="table-wrap"><table class="data-table"><thead><tr>${head}</tr></thead><tbody>${tbody}</tbody></table>${
    rows.length > 200 ? `<div class="table-more">showing 200 of ${rows.length} rows</div>` : ''
  }</div>`;

  return bindHtmlSelection(el, ctx);
}

export function registerAll(registry) {
  return registry
    .set('stat', stat)
    .set('kpi', kpi)
    .set('line', line)
    .set('area', area)
    .set('bar', bar)
    .set('stacked_bar', stacked_bar)
    .set('scatter', scatter)
    .set('donut', donut)
    .set('gauge', gauge)
    .set('table', table);
}
