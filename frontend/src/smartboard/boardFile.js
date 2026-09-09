/**
 * A board as a file.
 *
 * WHAT IS SAVED IS THE QUESTIONS, NOT THE ANSWERS. A panel records the IR that
 * produced it — metrics, dimensions, filters, time range — and opening a board
 * re-runs every one of them through `POST /query`. Saving rows instead would be
 * easier and wrong twice over: results live in a server-side cache with a
 * one-hour TTL, so a file full of result_ids is dead by tomorrow; and a board
 * that reopens showing last month's numbers under this month's title is a
 * dashboard that lies quietly, which is worse than one that fails loudly.
 *
 * THE IMPORT PATH IS THE GUARDED PATH. A board file is untrusted input — it
 * arrived from a disk, an email, a colleague. Every query in it goes through
 * the same validate → guard → compile → scope → execute path a model-issued
 * query takes, so the tenancy predicate and the entitlement guard apply exactly
 * as they always do. A board exported by an executive and opened by a regional
 * manager comes back without the revenue panels, because the server refuses
 * them — not because this file was polite enough to leave them out. Panels that
 * are refused are named rather than silently dropped.
 *
 * ONE UNDO STEP. Opening a board replaces the current one inside a transaction,
 * so it is one thing you did and one thing you can take back.
 */

const KIND = 'smartboard.board';
export const BOARD_FILE_VERSION = 1;

/** Style keys a renderer will honour, with the type each must be. Anything else is dropped. */
const STYLE_FIELDS = {
  palette: 'array',
  color: 'string',
  y_min: 'number',
  y_max: 'number',
  x_min: 'number',
  x_max: 'number',
  y_label: 'string',
  x_label: 'string',
  legend: 'boolean',
  grid: 'boolean',
  labels: 'boolean',
  smooth: 'boolean',
  stack: 'boolean',
  horizontal: 'boolean',
  sort: 'string',
  opacity: 'number',
  reference_line: 'number',
  reference_label: 'string',
};

const ID = /^[A-Za-z0-9_-]{1,64}$/;

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------

/**
 * The current board, as a plain object ready to be JSON'd.
 *
 * Panels come out in render order rather than in the order they were added, so
 * the file reads the way the board looks.
 */
export function serialiseBoard(state, { manifest = null, health = null } = {}) {
  const panels = state.order
    .map((id) => state.panels.find((p) => p.panelId === id))
    .filter(Boolean)
    .map((p) => ({
      panel_id: p.panelId,
      viz: p.viz,
      // No `ir` means a control panel — there was never a query behind it.
      ir: p.ir || null,
      encoding: p.encoding || {},
      title: p.title,
      subtitle: p.subtitle || null,
      note: p.note || null,
      size: p.size || 'md',
      style: p.style || {},
      layout: {
        col_span: p.layout?.colSpan ?? 6,
        row_span: p.layout?.rowSpan ?? 1,
        section: p.layout?.section ?? null,
      },
    }));

  return {
    kind: KIND,
    version: BOARD_FILE_VERSION,
    // Which catalog this board speaks. Opening it against a different one would
    // resolve metric ids that mean something else, so it is checked on import.
    manifest: manifest?.name || null,
    saved_at: new Date().toISOString(),
    // Provenance, for the person reading the file — never a grant. The server
    // decides what the *importer* may see, from their own credentials.
    saved_by_role: health?.role || null,
    sections: state.sections.map((s) => ({
      id: s.id,
      title: s.title,
      subtitle: s.subtitle || null,
      collapsed: !!s.collapsed,
    })),
    global_filters: state.globalFilters || [],
    panels,
  };
}

/** A filename that sorts usefully in a downloads folder. */
export function boardFilename(manifest) {
  const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-');
  return `${manifest?.name || 'board'}-${stamp}.json`;
}

/**
 * Hand the file to the browser.
 *
 * The object URL is revoked on the next frame rather than immediately: some
 * browsers have not started reading it when `click()` returns, and revoking too
 * early produces a download that silently does nothing.
 */
export function downloadBoard(doc, filename) {
  const blob = new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

// ---------------------------------------------------------------------------
// Load
// ---------------------------------------------------------------------------

export class BoardFileError extends Error {}

const typeOk = (value, kind) =>
  kind === 'array' ? Array.isArray(value) : typeof value === kind; // eslint-disable-line valid-typeof

/** i18n text: a plain string, or an object of locale -> string. Anything else is dropped. */
function cleanText(value) {
  if (typeof value === 'string') return { en: value };
  if (!value || typeof value !== 'object') return null;
  const out = {};
  for (const [locale, text] of Object.entries(value)) {
    if (typeof text === 'string' && /^[a-z]{2}(-[A-Za-z]{2,4})?$/.test(locale)) out[locale] = text.slice(0, 300);
  }
  return Object.keys(out).length ? out : null;
}

function cleanStyle(style) {
  if (!style || typeof style !== 'object') return {};
  const out = {};
  for (const [key, kind] of Object.entries(STYLE_FIELDS)) {
    if (key in style && typeOk(style[key], kind)) out[key] = style[key];
  }
  return out;
}

/**
 * Read one panel out of a file, keeping only fields we recognise.
 *
 * A whitelist rather than a sanitiser: the interesting failure is not a hostile
 * board file, it is a board file from a newer version of this app carrying a
 * field this one would pass straight through to a renderer. Copying only what
 * is understood makes forward-compatibility a non-event.
 */
function cleanPanel(raw, manifest) {
  if (!raw || typeof raw !== 'object') throw new BoardFileError('a panel is not an object');
  if (!ID.test(String(raw.panel_id || ''))) throw new BoardFileError(`bad panel id '${raw.panel_id}'`);

  const viz = String(raw.viz || '');
  // The registry would refuse an unknown kind anyway — it simply would not
  // render — but refusing here means the person is told, instead of getting a
  // blank rectangle with no explanation.
  if (manifest?.viz?.length && !manifest.viz.includes(viz)) {
    throw new BoardFileError(`viz '${viz}' is not available in this deployment`);
  }

  const layout = raw.layout && typeof raw.layout === 'object' ? raw.layout : {};
  const clampSpan = (n, lo, hi, fallback) =>
    Number.isFinite(n) ? Math.min(hi, Math.max(lo, Math.round(n))) : fallback;

  return {
    panel_id: raw.panel_id,
    viz,
    ir: raw.ir && typeof raw.ir === 'object' ? raw.ir : null,
    encoding: raw.encoding && typeof raw.encoding === 'object' ? raw.encoding : {},
    title: cleanText(raw.title) || { en: raw.panel_id },
    subtitle: cleanText(raw.subtitle),
    note: cleanText(raw.note),
    style: cleanStyle(raw.style),
    layout: {
      col_span: clampSpan(layout.col_span, 1, 12, 6),
      row_span: clampSpan(layout.row_span, 1, 3, 1),
      section: typeof layout.section === 'string' ? layout.section : null,
    },
  };
}

/** Structural checks, before anything is fetched or applied. */
export function parseBoardFile(text, manifest) {
  let doc;
  try {
    doc = JSON.parse(text);
  } catch {
    throw new BoardFileError('That file is not valid JSON.');
  }
  if (!doc || typeof doc !== 'object' || doc.kind !== KIND) {
    throw new BoardFileError('That file is not a saved board.');
  }
  if (Number(doc.version) > BOARD_FILE_VERSION) {
    throw new BoardFileError(
      `That board was saved by a newer version of this app (file v${doc.version}, this app reads v${BOARD_FILE_VERSION}).`,
    );
  }
  // Metric ids are only meaningful against the catalog that defines them, so a
  // board from another deployment would resolve names that mean something else.
  if (doc.manifest && manifest?.name && doc.manifest !== manifest.name) {
    throw new BoardFileError(`That board was saved from '${doc.manifest}', not '${manifest.name}'.`);
  }
  if (!Array.isArray(doc.panels)) throw new BoardFileError('That board has no panels.');

  return {
    sections: Array.isArray(doc.sections)
      ? doc.sections
          .filter((s) => s && ID.test(String(s.id || '')))
          .map((s) => ({ id: s.id, title: cleanText(s.title) || { en: s.id }, subtitle: cleanText(s.subtitle) }))
      : [],
    globalFilters: Array.isArray(doc.global_filters)
      ? doc.global_filters.filter((f) => f && typeof f.dim === 'string' && typeof f.op === 'string')
      : [],
    panels: doc.panels.map((p) => cleanPanel(p, manifest)),
    savedAt: typeof doc.saved_at === 'string' ? doc.saved_at : null,
    savedByRole: typeof doc.saved_by_role === 'string' ? doc.saved_by_role : null,
  };
}

/**
 * Replace the board with the one in `doc`.
 *
 * Every query runs first, and only then is the store touched: a board that
 * half-loaded while eight requests were in flight would leave the person
 * watching panels arrive one at a time with no way to tell a slow import from a
 * broken one. Fetch, then commit, in a single transaction.
 *
 * Returns { added, skipped: [{ panelId, reason }] }. Refusals are the
 * interesting outcome, not the error case — a board exported by someone with
 * wider entitlements is *supposed* to come back smaller.
 */
export async function applyBoardFile(parsed, { client, store }) {
  const fetched = await Promise.all(
    parsed.panels.map((panel) =>
      panel.ir
        ? client.query(panel.ir).then(
            (result) => ({ panel, result }),
            (error) => ({ panel, error }),
          )
        : Promise.resolve({ panel, result: null }),
    ),
  );

  const skipped = fetched
    .filter((entry) => entry.error)
    .map(({ panel, error }) => ({ panelId: panel.panel_id, reason: error.message || 'refused' }));
  const usable = fetched.filter((entry) => !entry.error);

  store.transaction('open a saved board', () => {
    store.reset();

    if (parsed.sections.length) {
      store.apply({ action: 'set_layout', order: [], sections: parsed.sections });
    }

    for (const { panel, result } of usable) {
      store.apply({
        action: 'add_panel',
        panel_id: panel.panel_id,
        result_id: result?.result_id,
        ir: panel.ir || undefined,
        viz: panel.viz,
        encoding: panel.encoding,
        title: panel.title,
        subtitle: panel.subtitle || undefined,
        note: panel.note || undefined,
        style: panel.style,
        layout: panel.layout,
        slot: 'append',
      });
    }

    for (const filter of parsed.globalFilters) {
      store.apply({ action: 'set_filter', filter });
    }

    // Said on the board rather than in a toast, because it is a fact about what
    // you are now looking at and it should still be there in ten seconds.
    if (skipped.length) {
      store.apply({
        action: 'narrate',
        tone: 'warning',
        text: {
          en:
            `Opened without ${skipped.length} panel${skipped.length === 1 ? '' : 's'} ` +
            `(${skipped.map((s) => s.panelId).join(', ')}) — not available to your role.`,
        },
      });
    }
  });

  return { added: usable.length, skipped };
}

// ---------------------------------------------------------------------------
// Autosave
//
// A refresh should not cost you an afternoon's work. Restoring is deliberately
// NOT automatic: this board is something people present from, and a demo that
// starts wherever the last session happened to end is a demo that starts
// differently every time. The last board is kept, and offered.
// ---------------------------------------------------------------------------

const autosaveKey = (manifest) => `smartboard:last:${manifest?.name || 'board'}`;

export function autosaveBoard(state, { manifest, health }) {
  if (!state.panels.length) return; // never overwrite a good save with an empty board
  try {
    localStorage.setItem(autosaveKey(manifest), JSON.stringify(serialiseBoard(state, { manifest, health })));
  } catch {
    // A full or disabled localStorage is not worth interrupting anyone over.
  }
}

export function readAutosave(manifest) {
  try {
    const text = localStorage.getItem(autosaveKey(manifest));
    if (!text) return null;
    const parsed = parseBoardFile(text, manifest);
    return { parsed, savedAt: parsed.savedAt };
  } catch {
    return null;
  }
}

export function clearAutosave(manifest) {
  try {
    localStorage.removeItem(autosaveKey(manifest));
  } catch {
    /* nothing to do */
  }
}
