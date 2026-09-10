/**
 * SmartBoard store.
 *
 * One state tree, one reducer, two callers. An AI command and a user click land
 * in the same place through the same validation, which is why the brain can see
 * what the user did and the user can undo what the brain did. Bolting the AI on
 * as a parallel path is the mistake this design exists to avoid.
 *
 * Framework-free on purpose. `react.js` wraps this in a hook; a Vue or Svelte
 * binding would be about fifteen lines.
 *
 * UNDO IS A HISTORY OF INTENTIONS, NOT OF STATES. One thing a person did is one
 * step back, whatever it cost internally: a drag across the board is one entry,
 * and so is importing a board file that lands fourteen panels. That is why
 * `commit` takes a label and why `applyAll` can coalesce. A stack that recorded
 * every intermediate state would be technically faithful and practically
 * useless — forty entries deep, one drag would erase your afternoon.
 */

const INITIAL = {
  panels: [],          // [{ panelId, resultId, ir, viz, encoding, title, subtitle, note, size, style, layout }]
  order: [],           // panelIds, render order
  sections: [],        // [{ id, title, subtitle, collapsed }] — the board's design
  globalFilters: [],   // [{ dim, op, value }]
  panelFilters: {},    // panelId -> [filter]
  highlight: null,     // { panelId|null, keys: [], reason, expiresAt }
  mapFocus: null,      // { panelId|null, featureIds: [], zoom }
  narration: [],       // [{ text, tone, at }]
  pending: null,       // { question, options } from ask_clarification
  selection: [],       // [{ panelId, key|null, label }] — what the user has picked out
};

/** Panel layout defaults, by declared size. Spans are out of twelve columns. */
const SIZE_SPAN = { sm: 3, md: 6, lg: 8, full: 12 };

function normaliseLayout(cmd, previous) {
  const l = cmd.layout || {};
  return {
    colSpan: l.col_span ?? previous?.colSpan ?? SIZE_SPAN[cmd.size || previous?.size || 'md'] ?? 6,
    rowSpan: l.row_span ?? previous?.rowSpan ?? 1,
    section: l.section ?? previous?.section ?? null,
  };
}

/**
 * A human label for one change, for the Undo button's tooltip.
 *
 * Deliberately terse and deliberately in the vocabulary of the board rather
 * than of the code: a person about to press Undo wants to know they are about
 * to un-resize a panel, not that they are about to pop a `set_layout`.
 */
function describe(event) {
  if (!event) return 'change';
  if (event.type === 'reset') return 'clear the board';
  const cmd = event.command;
  if (!cmd) return 'change';
  const which = cmd.panel_id ? ` “${cmd.panel_id}”` : '';
  switch (cmd.action) {
    case 'add_panel':       return `add${which}`;
    case 'update_panel':    return `change${which}`;
    case 'remove_panel':    return `remove${which}`;
    case 'set_filter':      return `filter on ${cmd.filter?.dim ?? ''}`.trim();
    case 'clear_filters':   return cmd.dims ? `clear the ${cmd.dims.join(', ')} filter` : 'clear all filters';
    case 'highlight':       return 'highlight';
    case 'focus_map':       return 'move the map';
    case 'set_layout':      return 'rearrange the board';
    case 'narrate':         return 'narration';
    default:                return cmd.action.replace(/_/g, ' ');
  }
}

export function createStore(initial = {}) {
  let state = { ...structuredClone(INITIAL), ...initial };
  const listeners = new Set();

  // Two stacks of { state, label }. The label is what the toolbar shows, so a
  // person can read what Undo is about to do before they do it — the single
  // cheapest thing you can add to make an undoable surface feel safe.
  const undoStack = [];
  const redoStack = [];
  let highlightTimer = null;
  let batching = false;

  const emit = (event) => {
    for (const fn of listeners) fn(state, event);
  };

  const commit = (next, event, label = null) => {
    if (batching) {
      // Inside a transaction the undo entry was taken when it opened; the
      // commits within it are steps of one thing the person did, not things.
      state = next;
      emit(event);
      return;
    }
    undoStack.push({ state, label: label || describe(event) });
    if (undoStack.length > 40) undoStack.shift();
    // A new change forks the timeline. Anything that was redoable is now a
    // future that did not happen, and keeping it around would let Redo apply a
    // change the person has already moved past.
    redoStack.length = 0;
    state = next;
    emit(event);
  };

  /**
   * Apply one command. Returns { ok, error } — never throws at the caller.
   *
   * `label` overrides what Undo will call this. Pass it when the command is a
   * poor description of the gesture that produced it: a drag emits `set_layout`,
   * but the person resized a panel.
   */
  function apply(cmd, label = null) {
    const next = structuredClone(state);
    try {
      switch (cmd.action) {
        case 'add_panel': {
          const prior = next.panels.find((p) => p.panelId === cmd.panel_id);
          const panel = {
            panelId: cmd.panel_id,
            resultId: cmd.result_id,
            // The query that produced this panel, when the caller knows it.
            // Results live in a server-side cache with a one-hour TTL, so a
            // result_id is worthless tomorrow; the IR is what makes a board
            // portable — see boardFile.js. Kept off `boardState()` so it costs
            // the model nothing.
            ir: cmd.ir ?? prior?.ir ?? null,
            viz: cmd.viz,
            encoding: cmd.encoding || {},
            title: cmd.title || { en: cmd.panel_id },
            subtitle: cmd.subtitle || null,
            note: cmd.note || null,
            size: cmd.size || 'md',
            style: cmd.style || {},
            // Replacing a panel in place keeps its slot on the grid unless the
            // command says otherwise. Otherwise "make that a bar chart" would
            // silently resize it too.
            layout: normaliseLayout(cmd, prior?.layout),
          };
          if (cmd.slot === 'replace_all') {
            next.panels = [panel];
            next.order = [panel.panelId];
            break;
          }
          const existing = next.panels.findIndex((p) => p.panelId === panel.panelId);
          if (existing >= 0) {
            // Reusing a panel id replaces in place and keeps its position.
            next.panels[existing] = panel;
            break;
          }
          if (cmd.slot === 'replace_panel' && cmd.replaces) {
            const idx = next.order.indexOf(cmd.replaces);
            next.panels = next.panels.filter((p) => p.panelId !== cmd.replaces);
            next.order = next.order.filter((id) => id !== cmd.replaces);
            next.panels.push(panel);
            next.order.splice(idx < 0 ? 0 : idx, 0, panel.panelId);
            break;
          }
          next.panels.push(panel);
          if (cmd.slot === 'append') next.order.push(panel.panelId);
          else next.order.unshift(panel.panelId);
          break;
        }

        case 'update_panel': {
          const p = next.panels.find((x) => x.panelId === cmd.panel_id);
          if (!p) throw new Error(`no panel '${cmd.panel_id}'`);
          if (cmd.viz) p.viz = cmd.viz;
          if (cmd.encoding) p.encoding = { ...p.encoding, ...cmd.encoding };
          if (cmd.title) p.title = cmd.title;
          if (cmd.subtitle) p.subtitle = cmd.subtitle;
          if (cmd.note) p.note = cmd.note;
          if (cmd.result_id) p.resultId = cmd.result_id;
          if (cmd.ir) p.ir = cmd.ir;
          if (cmd.size) {
            p.size = cmd.size;
            p.layout = { ...p.layout, colSpan: SIZE_SPAN[cmd.size] ?? p.layout.colSpan };
          }
          // Style MERGES. "Make it orange" should not clear the axis bounds set
          // two turns ago, and the model should not have to restate them.
          if (cmd.style) p.style = { ...p.style, ...cmd.style };
          if (cmd.layout) p.layout = normaliseLayout(cmd, p.layout);
          break;
        }

        case 'remove_panel':
          next.panels = next.panels.filter((p) => p.panelId !== cmd.panel_id);
          next.order = next.order.filter((id) => id !== cmd.panel_id);
          next.selection = next.selection.filter((sel) => sel.panelId !== cmd.panel_id);
          break;

        case 'set_filter': {
          const scope = cmd.scope || 'global';
          const list = scope === 'global'
            ? next.globalFilters
            : (next.panelFilters[scope] = next.panelFilters[scope] || []);
          const at = list.findIndex((f) => f.dim === cmd.filter.dim);
          if (at >= 0) list[at] = cmd.filter;
          else list.push(cmd.filter);
          break;
        }

        case 'clear_filters':
          next.globalFilters = cmd.dims
            ? next.globalFilters.filter((f) => !cmd.dims.includes(f.dim))
            : [];
          if (!cmd.dims) next.panelFilters = {};
          break;

        case 'highlight': {
          const ttl = cmd.ttl_ms ?? 25000;
          next.highlight = {
            panelId: cmd.panel_id || null,
            keys: cmd.keys || [],
            reason: cmd.reason || null,
            expiresAt: Date.now() + ttl,
          };
          // Highlights expire. Otherwise a board accumulates stale emphasis
          // until nothing on it means anything.
          clearTimeout(highlightTimer);
          highlightTimer = setTimeout(() => {
            state = { ...state, highlight: null };
            emit({ type: 'highlight_expired' });
          }, ttl);
          break;
        }

        case 'focus_map':
          next.mapFocus = {
            panelId: cmd.panel_id || null,
            featureIds: cmd.feature_ids || [],
            zoom: cmd.zoom ?? null,
          };
          break;

        case 'set_layout': {
          // Sections first: panels reference them by id, so a panel assigned to
          // a section declared in this same command has to find it.
          if (cmd.sections) {
            next.sections = cmd.sections.map((sec) => ({
              id: sec.id,
              title: sec.title,
              subtitle: sec.subtitle || null,
              collapsed: !!sec.collapsed,
            }));
          }

          for (const spec of cmd.panels || []) {
            const p = next.panels.find((x) => x.panelId === spec.panel_id);
            if (!p) continue;
            p.layout = {
              colSpan: spec.col_span ?? p.layout.colSpan,
              rowSpan: spec.row_span ?? p.layout.rowSpan,
              section: spec.section !== undefined ? spec.section : p.layout.section,
            };
          }

          if (cmd.order?.length) {
            next.order = cmd.order.filter((id) => next.panels.some((p) => p.panelId === id));
            for (const p of next.panels) if (!next.order.includes(p.panelId)) next.order.push(p.panelId);
          }
          break;
        }

        case 'narrate':
          next.narration = [...next.narration.slice(-4), { ...cmd.text, tone: cmd.tone || 'neutral', at: Date.now() }];
          break;

        case 'ask_clarification':
          next.pending = { question: cmd.question, options: cmd.options || [] };
          break;

        default:
          throw new Error(`unknown action '${cmd.action}'`);
      }
    } catch (err) {
      return { ok: false, error: err.message };
    }

    commit(next, { type: 'command', command: cmd }, label);
    return { ok: true };
  }

  /**
   * Run several commands as one undoable step.
   *
   * Synchronous by design: everything slow — fetching results for an imported
   * board, say — happens before the transaction opens, so the board never sits
   * half-built inside one. `fn` receives nothing and should call `apply`.
   */
  function transaction(label, fn) {
    if (batching) return fn();          // a nested transaction joins the outer one
    const before = state;
    batching = true;
    try {
      return fn();
    } finally {
      batching = false;
      // Nothing changed, nothing to undo. A transaction that applied only
      // rejected commands should not leave a dead step on the stack.
      if (state !== before) {
        undoStack.push({ state: before, label });
        if (undoStack.length > 40) undoStack.shift();
        redoStack.length = 0;
      }
    }
  }

  /**
   * Selection: what the user has pointed at.
   *
   * Not a command, and deliberately not on the undo stack. Commands describe
   * what the board *is*; selection describes what the person is currently
   * looking at, which is conversational context rather than board state. Undo
   * should step back through charts, not through clicks.
   *
   * An entry is either a whole panel ({ panelId, key: null }) or one mark
   * inside it ({ panelId, key: 'BIN-C09' }). Selecting a mark implies its
   * panel, so the assistant always knows which chart the mark came from.
   */
  function setSelection(entries) {
    state = { ...state, selection: entries };
    emit({ type: 'selection', selection: entries });
  }

  const sameEntry = (a, b) => a.panelId === b.panelId && String(a.key ?? '') === String(b.key ?? '');

  return {
    getState: () => state,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    apply,

    /**
     * Toggle one entry. `additive` (ctrl/cmd-click) keeps what was already
     * selected; without it a click replaces the selection, which is what every
     * other list-like surface does and therefore what people expect.
     */
    toggleSelection(entry, additive = false) {
      const existing = state.selection.find((e) => sameEntry(e, entry));
      if (!additive) {
        setSelection(existing && state.selection.length === 1 ? [] : [entry]);
        return;
      }
      setSelection(
        existing
          ? state.selection.filter((e) => !sameEntry(e, entry))
          : [...state.selection, entry].slice(-12),
      );
    },
    clearSelection() {
      if (state.selection.length) setSelection([]);
    },
    isSelected(panelId, key = null) {
      return state.selection.some((e) => sameEntry(e, { panelId, key }));
    },
    /** True when this panel has any selection at all — whole-panel or a mark inside it. */
    panelHasSelection(panelId) {
      return state.selection.some((e) => e.panelId === panelId);
    },
    /**
     * Apply a list of commands. With a `label` they collapse into one undo
     * step; without one each command is its own step, which is what a
     * conversational turn wants — the assistant drawing three panels is three
     * things it did, and you may want only the last one gone.
     */
    applyAll(cmds, label = null) {
      return label ? transaction(label, () => cmds.map((c) => apply(c))) : cmds.map((c) => apply(c));
    },
    transaction,
    undo() {
      const entry = undoStack.pop();
      if (!entry) return false;
      redoStack.push({ state, label: entry.label });
      state = entry.state;
      emit({ type: 'undo', label: entry.label });
      return true;
    },
    /**
     * Step forward again.
     *
     * Undo without redo is a trap on a surface you can drag: a misdrop is
     * cheap to undo and expensive to re-do by hand, and knowing you can step
     * back is what makes people willing to try the gesture at all.
     */
    redo() {
      const entry = redoStack.pop();
      if (!entry) return false;
      undoStack.push({ state, label: entry.label });
      state = entry.state;
      emit({ type: 'redo', label: entry.label });
      return true;
    },
    canUndo: () => undoStack.length > 0,
    canRedo: () => redoStack.length > 0,
    /** What Undo would step back — shown in its tooltip, so the button is never a gamble. */
    undoLabel: () => undoStack[undoStack.length - 1]?.label || null,
    redoLabel: () => redoStack[redoStack.length - 1]?.label || null,
    reset() {
      commit(structuredClone(INITIAL), { type: 'reset' });
    },
    /** The panels a selection points at, resolved. Used to build chat context. */
    selectedPanels() {
      const ids = [...new Set(state.selection.map((e) => e.panelId))];
      return ids.map((id) => state.panels.find((p) => p.panelId === id)).filter(Boolean);
    },
    /**
     * The snapshot sent back to the brain each turn. Deliberately compact —
     * without it, "make that a bar chart instead" has no referent; with all of
     * it, you pay for the whole board in tokens on every message.
     */
    boardState() {
      return {
        panels: state.order
          .map((id) => state.panels.find((p) => p.panelId === id))
          .filter(Boolean)
          .map((p) => {
            const out = {
              panel_id: p.panelId,
              viz: p.viz,
              title: p.title,
              col_span: p.layout?.colSpan,
              encoding: p.encoding,
            };
            // Only send style that has actually been set. An empty object per
            // panel is pure token cost, but without the styles that ARE set,
            // "make it a bit darker" has nothing to work from.
            if (p.style && Object.keys(p.style).length) out.style = p.style;
            if (p.layout?.section) out.section = p.layout.section;
            return out;
          }),
        sections: state.sections.map((sec) => ({ id: sec.id, title: sec.title })),
        global_filters: state.globalFilters,
        selection: state.selection.map((e) => ({ panel_id: e.panelId, key: e.key ?? null })),
      };
    },
    panelById: (id) => state.panels.find((p) => p.panelId === id) || null,
    isHighlighted(panelId, key) {
      const h = state.highlight;
      if (!h || Date.now() > h.expiresAt) return false;
      if (h.panelId && h.panelId !== panelId) return false;
      return h.keys.includes(String(key));
    },
  };
}
