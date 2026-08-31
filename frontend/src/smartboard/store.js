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
 */

const INITIAL = {
  panels: [],          // [{ panelId, resultId, viz, encoding, title, subtitle, note, size, style, layout }]
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

export function createStore(initial = {}) {
  let state = { ...structuredClone(INITIAL), ...initial };
  const listeners = new Set();
  const undoStack = [];
  let highlightTimer = null;

  const emit = (event) => {
    for (const fn of listeners) fn(state, event);
  };

  const commit = (next, event) => {
    undoStack.push(state);
    if (undoStack.length > 40) undoStack.shift();
    state = next;
    emit(event);
  };

  /** Apply one command. Returns { ok, error } — never throws at the caller. */
  function apply(cmd) {
    const next = structuredClone(state);
    try {
      switch (cmd.action) {
        case 'add_panel': {
          const prior = next.panels.find((p) => p.panelId === cmd.panel_id);
          const panel = {
            panelId: cmd.panel_id,
            resultId: cmd.result_id,
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

    commit(next, { type: 'command', command: cmd });
    return { ok: true };
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
    applyAll(cmds) {
      return cmds.map(apply);
    },
    undo() {
      const prev = undoStack.pop();
      if (!prev) return false;
      state = prev;
      emit({ type: 'undo' });
      return true;
    },
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
