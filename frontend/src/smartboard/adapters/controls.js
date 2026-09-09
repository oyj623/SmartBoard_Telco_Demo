/**
 * Control adapter — the panels that ask rather than answer.
 *
 * Every other entry in the viz registry turns a result into marks. This one
 * turns the *catalog* into controls: the dimensions the manifest declares
 * become chips, ranges and search boxes, and touching one emits `set_filter` or
 * `clear_filters` — the same commands the assistant emits when you ask it to
 * narrow the board. So filtering by hand and filtering by asking are one
 * mechanism, and Undo, the board snapshot the model reads, and export all cover
 * the hand path without knowing it exists.
 *
 * WHY THIS IS A PANEL AND NOT A SIDEBAR. Filters used to live only as tags in
 * the toolbar: chrome, created by the assistant, with no provenance and no
 * place on the grid. As a panel a filter becomes content — it takes a column
 * span, sits in a section, travels in an exported board file, and is something
 * the assistant can decide the board needs. The control strip across the top of
 * a dashboard is a design decision, and design decisions belong on the grid.
 *
 * THE STORE STAYS THE SOURCE OF TRUTH. This panel writes filters; it does not
 * own them. It renders whatever is in `globalFilters`, so a filter the
 * assistant set shows up in the controls, and removing the panel leaves the
 * filters applied — they were never the panel's to take away. Two surfaces
 * disagreeing about which filters are live is the bug this rule prevents.
 */

import { t } from '../client.js';

/** Dimension types that get a from/to pair rather than a value list. */
const RANGE_TYPES = new Set(['time', 'number']);

export function registerControls(registry) {
  registry.set('filter_panel', renderFilterPanel);
  return registry;
}

// ---------------------------------------------------------------------------
// Small DOM helpers. Everything is built with createElement and textContent —
// dimension labels and values come from our own manifest, but a renderer that
// concatenates strings into innerHTML is one careless commit away from being
// the one place in this project where a value becomes markup.
// ---------------------------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

const labelFor = (dim, id, locale) => t(dim?.label, locale) || id;

// ---------------------------------------------------------------------------

/**
 * A control panel repaints itself.
 *
 * Every other renderer here is a pure function of one result and redraws when
 * BoardPanel decides its inputs changed. A control panel's input is the board
 * itself — which filters are live, which panels a filter can reach, how the
 * charts are sorted — so it subscribes to the store directly and keeps itself
 * in step. Selection is skipped: clicking a bar is not a reason to rebuild the
 * filters, and repainting under a person's cursor is its own small bug.
 */
function renderFilterPanel(root, ctx) {
  const paint = () => {
    root.innerHTML = '';
    build(root, ctx);
  };
  paint();
  const unsubscribe = ctx.store.subscribe((_state, event) => {
    if (event?.type === 'selection') return;
    paint();
  });
  return () => unsubscribe();
}

function build(root, { encoding, store, manifest, locale = 'en' }) {
  const dims = (encoding?.dims || []).filter((id) => manifest?.dimensions?.[id]);
  const wrap = el('div', 'ctl-panel');

  if (!dims.length) {
    wrap.appendChild(
      el(
        'div',
        'panel-empty',
        'This filter has no dimensions. Ask the assistant to add some, or name them in encoding.dims.',
      ),
    );
    root.appendChild(wrap);
    return;
  }

  const active = () => store.getState().globalFilters;
  const filterFor = (dimId) => active().find((f) => f.dim === dimId) || null;

  /** One write. Clearing on an empty selection rather than storing an empty `in` list,
      which would match nothing and read as a board that had broken. */
  const write = (dimId, filter, what) => {
    if (!filter) store.apply({ action: 'clear_filters', dims: [dimId] }, `clear the ${what} filter`);
    else store.apply({ action: 'set_filter', filter }, `filter ${what}`);
  };

  for (const dimId of dims) {
    const dim = manifest.dimensions[dimId];
    const name = labelFor(dim, dimId, locale);
    const row = el('div', 'ctl-row');

    const head = el('div', 'ctl-row-head');
    head.appendChild(el('span', 'ctl-label', name));

    const current = filterFor(dimId);
    if (current) {
      const clear = el('button', 'ctl-clear', 'clear');
      clear.title = `Remove the ${name} filter`;
      clear.addEventListener('click', () => write(dimId, null, name));
      head.appendChild(clear);
    }
    row.appendChild(head);

    if (dim.values?.length) row.appendChild(chips(dim, dimId, name, current, write, locale));
    else if (RANGE_TYPES.has(dim.type)) row.appendChild(range(dimId, name, current, write, dim));
    else row.appendChild(search(dimId, name, current, write));

    wrap.appendChild(row);
  }

  wrap.appendChild(sortRow(store, manifest));

  // What this filter does not reach. A filter only bites on a panel whose
  // result actually carries that column, which is the honest behaviour — a
  // panel grouped only by month genuinely does not know which state its
  // numbers came from. Saying so is the difference between a person trusting
  // the board and a person deciding the filter is broken.
  const unreached = untouchedPanels(store, dims, locale);
  if (unreached.length) {
    wrap.appendChild(
      el(
        'p',
        'ctl-foot',
        `Does not reach: ${unreached.join(', ')} — ${
          unreached.length === 1 ? 'that panel is' : 'those panels are'
        } not grouped by any of these dimensions.`,
      ),
    );
  }

  root.appendChild(wrap);
}

// ---------------------------------------------------------------------------
// Control kinds
// ---------------------------------------------------------------------------

/**
 * Enumerated values become toggle chips backed by an `in` filter.
 *
 * Multi-select by default and with no Apply button: on a board where filtering
 * is client-side and instant, staging a selection behind a commit step buys
 * nothing and costs the person the feedback that makes direct manipulation
 * worth having.
 */
function chips(dim, dimId, name, current, write, locale) {
  const picked = new Set(
    current ? (Array.isArray(current.value) ? current.value : [current.value]).map(String) : [],
  );
  const box = el('div', 'ctl-chips');

  for (const value of dim.values) {
    const on = picked.has(String(value));
    const chip = el('button', `ctl-chip${on ? ' is-on' : ''}`, String(value));
    chip.type = 'button';
    chip.setAttribute('aria-pressed', on ? 'true' : 'false');
    chip.addEventListener('click', () => {
      const next = new Set(picked);
      if (on) next.delete(String(value));
      else next.add(String(value));
      const list = [...next];
      write(dimId, list.length ? { dim: dimId, op: 'in', value: list } : null, name);
    });
    box.appendChild(chip);
  }
  return box;
}

/**
 * Time and numeric dimensions get a from/to pair.
 *
 * Committed on `change`, never on `input`. Filtering per keystroke would put
 * one undo entry on the stack per character typed, and the board would redraw
 * against half-typed bounds on the way.
 */
function range(dimId, name, current, write, dim) {
  const bounds = current?.op === 'between' && Array.isArray(current.value) ? current.value : ['', ''];
  const box = el('div', 'ctl-range');
  const hint = dim.type === 'time' ? 'YYYY-MM' : 'number';

  const inputs = [0, 1].map((side) => {
    const input = el('input', 'ctl-input');
    input.type = 'text';
    input.value = bounds[side] ?? '';
    input.placeholder = side === 0 ? `from ${hint}` : `to ${hint}`;
    input.setAttribute('aria-label', `${name} ${side === 0 ? 'from' : 'to'}`);
    return input;
  });

  const commit = () => {
    const from = inputs[0].value.trim();
    const to = inputs[1].value.trim();
    // A half-filled range is a range in progress, not a filter. Waiting for
    // both ends beats applying a bound the person has not finished choosing.
    write(dimId, from && to ? { dim: dimId, op: 'between', value: [from, to] } : null, name);
  };

  for (const input of inputs) {
    input.addEventListener('change', commit);
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') commit();
    });
    box.appendChild(input);
  }
  return box;
}

/** Anything with too many values to enumerate — a site code, a district — gets substring search. */
function search(dimId, name, current, write) {
  const box = el('div', 'ctl-range');
  const input = el('input', 'ctl-input');
  input.type = 'text';
  input.value = current?.op === 'contains' ? String(current.value) : '';
  input.placeholder = `contains…`;
  input.setAttribute('aria-label', `${name} contains`);

  const commit = () => {
    const text = input.value.trim();
    write(dimId, text ? { dim: dimId, op: 'contains', value: text } : null, name);
  };
  input.addEventListener('change', commit);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') commit();
  });

  box.appendChild(input);
  return box;
}

/**
 * Sort, applied across the board.
 *
 * Sort is genuinely a per-panel property — `style.sort` reorders categories at
 * draw time, and what "descending" means differs between a bar chart of states
 * and a line chart of months. It is offered here anyway, and labelled with its
 * scope, because comparing sixteen states across three charts is exactly when
 * you want them all ranked the same way and do not want to say so three times.
 * The per-panel menu stays the precise instrument; this is the blunt one.
 *
 * One transaction, so re-ranking the board is one step back.
 */
function sortRow(store, manifest) {
  const dataless = new Set(manifest?.viz_dataless || []);
  const row = el('div', 'ctl-row');
  const head = el('div', 'ctl-row-head');
  head.appendChild(el('span', 'ctl-label', 'Sort every chart'));
  row.appendChild(head);

  const box = el('div', 'ctl-chips');
  const options = [
    ['desc', 'high → low'],
    ['asc', 'low → high'],
    ['none', 'as queried'],
  ];

  const targets = () => store.getState().panels.filter((p) => !dataless.has(p.viz));
  const shared = (() => {
    const set = new Set(targets().map((p) => p.style?.sort || 'none'));
    return set.size === 1 ? [...set][0] : null;
  })();

  for (const [value, label] of options) {
    const chip = el('button', `ctl-chip${shared === value ? ' is-on' : ''}`, label);
    chip.type = 'button';
    chip.setAttribute('aria-pressed', shared === value ? 'true' : 'false');
    chip.addEventListener('click', () => {
      const panels = targets();
      if (!panels.length) return;
      store.transaction(`sort every chart ${label}`, () => {
        for (const p of panels) {
          store.apply({ action: 'update_panel', panel_id: p.panelId, style: { sort: value } });
        }
      });
    });
    box.appendChild(chip);
  }

  row.appendChild(box);
  return row;
}

// ---------------------------------------------------------------------------

/** Panels none of these dimensions can reach, named so the gap is visible rather than mysterious. */
function untouchedPanels(store, dims, locale) {
  const wanted = [...new Set(dims)];
  return store
    .getState()
    .panels.filter((p) => {
      // No IR means a control panel, or a panel drawn before this browser saw
      // the query behind it. Either way there is nothing to judge, and guessing
      // would put a panel on this list that the filter reaches perfectly well.
      if (!p.ir) return false;
      const grouped = new Set(p.ir.dimensions || []);
      return !wanted.some((d) => grouped.has(d));
    })
    .map((p) => t(p.title, locale) || p.panelId);
}
