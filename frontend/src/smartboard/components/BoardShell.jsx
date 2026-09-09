/**
 * The board surface — panels on the left, conversation on the right.
 *
 * Nothing on this page knows what your domain is. It renders whatever panels
 * are in the store, grouped into whatever sections the layout declares, using
 * whatever renderers are in the registry, labelled with whatever the manifest
 * says. Point the backend at a different manifest and this file is unchanged —
 * which is the test of whether the framework earned its place.
 *
 * Usage:
 *
 *   const board = useBoard({ baseUrl, headers, registry, sections, panels });
 *   <BoardShell board={board} locale={locale} demoScript={script}
 *               labels={{ chat: {...}, empty: {...} }} />
 *
 * `toolbarExtra` renders extra controls into the toolbar (a mode toggle, say);
 * `extraFlags` rides along on every chat turn as the request's `extra` dict.
 */

import { useEffect } from 'react';

import BoardChat from './BoardChat.jsx';
import BoardPanel from './BoardPanel.jsx';
import { useBoardLayout } from './useBoardLayout.js';
import { t as pick } from '../client.js';
import './board.css';

/**
 * Group panels into the sections the layout declares.
 *
 * Panels with no section, and panels pointing at a section that no longer
 * exists, fall into an unlabelled band at the top. A layout command that half
 * applies should still leave a readable board.
 */
function groupIntoSections(panels, sections) {
  const known = new Map(sections.map((s) => [s.id, s]));
  const loose = panels.filter((p) => !p.layout?.section || !known.has(p.layout.section));

  // Empty bands are kept. They used to be dropped as noise, but a section you
  // can drag the last panel out of has to still be there to drag one back into
  // — and a band the assistant declared and has not filled yet is a promise the
  // board should keep visible.
  const bands = sections.map((section) => ({
    section,
    panels: panels.filter((p) => p.layout?.section === section.id),
  }));

  return loose.length ? [{ section: null, panels: loose }, ...bands] : bands;
}

export default function BoardShell({
  board,
  locale = 'en',
  headers = null,
  labels = {},
  demoScript = null,
  toolbarExtra = null,
  extraFlags = {},
  onFlags,
}) {
  const { store, client, registry, state, manifest, health, booting, bootError } = board;

  const panels = state.order
    .map((id) => state.panels.find((p) => p.panelId === id))
    .filter(Boolean);

  const bands = groupIntoSections(panels, state.sections);
  const narration = state.narration.slice(-1)[0];
  const empty = labels.empty || {};

  // Undo/redo labels are read during render. Every undo, redo and commit emits,
  // so the snapshot React is subscribed to has already changed by the time
  // these are read — they never go stale without a re-render behind them.
  const undoLabel = store.undoLabel();
  const redoLabel = store.redoLabel();

  // Drag to reorder, drag to resize. Both emit `set_layout` — the same command
  // the assistant emits when asked to redesign the board — so the two ways of
  // changing the layout are literally the same operation, and Undo, the board
  // snapshot the model sees, and export all get the mouse path for free.
  const arrange = useBoardLayout({ store, state, locale });

  // Keyboard undo/redo. Skipped while the caret is in the chat box, where
  // ctrl-z belongs to the text field and stealing it would be its own bug.
  useEffect(() => {
    const onKey = (event) => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 'z') return;
      const el = document.activeElement;
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) return;
      event.preventDefault();
      if (event.shiftKey) store.redo();
      else store.undo();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [store]);

  const renderPanel = (panel) => (
    <BoardPanel
      key={panel.panelId}
      panel={panel}
      arrange={arrange}
      client={client}
      registry={registry}
      store={store}
      manifest={manifest}
      locale={locale}
      selection={state.selection}
      filters={[...state.globalFilters, ...(state.panelFilters[panel.panelId] || [])]}
    />
  );

  return (
    <div className="board-shell">
      <div className="board-main">
        <div className="board-tools">
          {/* Naming what will be undone costs one string and turns Undo from a
              gamble into a decision. */}
          <button
            className="btn btn-ghost"
            style={{ fontSize: 10 }}
            onClick={() => store.undo()}
            disabled={!undoLabel}
            title={undoLabel ? `Undo: ${undoLabel}` : 'Nothing to undo'}
          >
            ↺ Undo
          </button>
          <button
            className="btn btn-ghost"
            style={{ fontSize: 10 }}
            onClick={() => store.redo()}
            disabled={!redoLabel}
            title={redoLabel ? `Redo: ${redoLabel}` : 'Nothing to redo'}
          >
            ↻ Redo
          </button>
          <button
            className="btn btn-ghost"
            style={{ fontSize: 10 }}
            onClick={() => store.reset()}
            title="Remove every panel"
          >
            Clear board
          </button>

          {state.globalFilters.map((f) => (
            <span className="filter-tag" key={f.dim}>
              {f.dim} {f.op} {JSON.stringify(f.value)}
              <button
                title="Remove this filter"
                onClick={() => store.apply({ action: 'clear_filters', dims: [f.dim] })}
              >
                ×
              </button>
            </span>
          ))}

          <div className="spacer" />

          {toolbarExtra}

          {state.selection.length > 0 && (
            <span className="chip chip-accent" style={{ fontSize: 9 }}>
              {state.selection.length} selected · click empty space to clear
            </span>
          )}

          {health && (
            <span className="chip" style={{ fontSize: 9 }} title="Everything the assistant may name">
              {health.metrics} metrics · {health.dimensions} dimensions · {health.role}
            </span>
          )}
        </div>

        {/* Clicking the board background clears the selection, the way clicking
            off a list does everywhere else. */}
        <div
          className="board"
          onClick={(event) => {
            if (event.target === event.currentTarget) store.clearSelection();
          }}
        >
          {narration && (
            <div className="board-narration" data-tone={narration.tone}>
              <span>◆</span>
              <span>{pick(narration, locale)}</span>
            </div>
          )}

          {bootError && (
            <div className="board-empty">
              <h2>{empty.errorTitle || 'The board could not load its catalog'}</h2>
              <p>{bootError}</p>
            </div>
          )}

          {!bootError && booting && !panels.length && (
            <div className="board-empty">
              <h2>{empty.bootingTitle || 'Building the board…'}</h2>
              <p>
                {empty.bootingBody ||
                  'Running the starting queries through the same guarded path the assistant uses.'}
              </p>
            </div>
          )}

          {!booting && !panels.length && (
            <div className="board-empty">
              <h2>{empty.title || 'Nothing on the board'}</h2>
              <p>
                {empty.body ||
                  'The assistant cannot write SQL and cannot write UI code. It names metrics from a governed catalog and issues typed commands — every panel here is built from one of them. Ask it a question to start.'}
              </p>
            </div>
          )}

          {/* Bands only once something is on the board: three empty sections
              stacked under "Nothing on the board" is noise, not structure. */}
          {!!panels.length &&
            bands.map(({ section, panels: inBand }) => (
              <div
                className={`board-band${
                  arrange.isEmptyDropTarget(section?.id ?? null) ? ' is-drop-target' : ''
                }`}
                /* The drop hit-test reads the section off the DOM, so a panel
                   dragged into a band lands in that band rather than merely
                   between two panels that happen to sit in it. */
                data-section={section?.id || ''}
                key={section?.id || '__loose'}
              >
                {section && (
                  <header className="board-section-head">
                    <h2>{pick(section.title, locale)}</h2>
                    {section.subtitle && <p>{pick(section.subtitle, locale)}</p>}
                  </header>
                )}
                <div className={`board-grid${arrange.gridBusy ? ' is-arranging' : ''}`}>
                  {inBand.map(renderPanel)}
                  {!inBand.length && (
                    <p className="board-band-empty">
                      Empty section — drag a panel here, or ask the assistant to fill it.
                    </p>
                  )}
                </div>
              </div>
            ))}
        </div>
      </div>

      <BoardChat
        client={client}
        store={store}
        state={state}
        manifest={manifest}
        health={health}
        locale={locale}
        headers={headers}
        labels={labels.chat || {}}
        demoScript={demoScript}
        extraFlags={extraFlags}
        onFlags={onFlags}
      />
    </div>
  );
}
