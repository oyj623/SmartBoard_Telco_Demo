/**
 * One panel.
 *
 * React owns the frame — title, note, close button, grid span. Everything
 * inside the body is drawn imperatively by whichever renderer the viz registry
 * holds for `panel.viz`. That boundary is deliberate: the registry contract is
 * `(element, ctx) => cleanup`, which keeps the renderers usable from any
 * framework, and keeps ECharts and Leaflet — both of which want to own a DOM
 * node — out of React's reconciliation.
 *
 * If a viz kind is not in the registry, nothing renders. That is the entire
 * view-side security story, and it is why the model cannot put arbitrary markup
 * on the screen: it names a kind from an enum, and unnamed kinds do not exist.
 *
 * The frame also carries the direct-manipulation affordances — a grab handle
 * and two resize grips — supplied by `arrange` (see useBoardLayout.js). They
 * are deliberately outside `bodyRef`: the renderer owns that node exclusively,
 * and a handle drawn inside it would be wiped on the next redraw.
 */

import { useEffect, useRef, useState } from 'react';
import { applyFilters, t } from '../client.js';

/** Maps repaint themselves on selection and highlight; charts must be re-rendered. */
const SELF_REPAINTING = new Set(['map_points', 'map_regions']);

export default function BoardPanel({
  panel,
  client,
  registry,
  store,
  manifest,
  locale,
  filters,
  selection,
  arrange = null,
}) {
  const bodyRef = useRef(null);
  const [error, setError] = useState(null);
  const [landing, setLanding] = useState(true);

  // Spans come from the arrange controller so a resize previews live rather
  // than only after the drop. With no controller they fall back to the store.
  const { colSpan, rowSpan } = arrange
    ? arrange.spansFor(panel)
    : { colSpan: panel.layout?.colSpan || 6, rowSpan: panel.layout?.rowSpan || 1 };
  const dropEdge = arrange?.dropEdge(panel.panelId) || null;
  const selfPaints = SELF_REPAINTING.has(panel.viz);

  // Keys selected inside THIS panel. Charts have to redraw to reflect them;
  // maps do not, so feeding them into the signature would tear the map down
  // and lose its camera on every click.
  const mine = selection.filter((e) => e.panelId === panel.panelId);
  const selectedHere = selfPaints ? '' : mine.map((e) => e.key ?? '·').join(',');
  const highlightKey = selfPaints ? '' : (store.getState().highlight?.keys || []).join(',');
  const panelSelected = mine.some((e) => e.key == null);

  // A signature rather than a dependency list: `encoding`, `style` and
  // `filters` are fresh objects on every render, so structural comparison is
  // what actually decides whether a redraw is needed.
  const signature = JSON.stringify([
    panel.viz,
    panel.resultId,
    panel.encoding,
    panel.style,
    locale,
    filters,
    selectedHere,
    highlightKey,
  ]);

  useEffect(() => {
    let cleanup;
    let cancelled = false;

    (async () => {
      const render = registry.get(panel.viz);
      if (!render) {
        setError(`No renderer is registered for '${panel.viz}'.`);
        return;
      }

      try {
        const result = await client.result(panel.resultId);
        if (cancelled || !bodyRef.current) return;
        setError(null);

        // Filters recorded by `set_filter` are applied here, at draw time. See
        // the note on applyFilters in client.js for why this is done
        // client-side rather than as a re-query.
        const rows = applyFilters(result.rows, result.columns, filters);

        bodyRef.current.innerHTML = '';
        cleanup = render(bodyRef.current, {
          rows,
          columns: result.columns,
          encoding: panel.encoding || {},
          panel,
          store,
          manifest,
          locale,
        });
      } catch (err) {
        if (!cancelled) setError(err.message || String(err));
      }
    })();

    return () => {
      cancelled = true;
      cleanup?.();
    };
  }, [signature]);

  // The landing flash marks a panel the assistant just placed or replaced.
  useEffect(() => {
    setLanding(true);
    const id = setTimeout(() => setLanding(false), 950);
    return () => clearTimeout(id);
  }, [panel.resultId, panel.viz]);

  const selectPanel = (event) => {
    event.stopPropagation();
    store.toggleSelection(
      { panelId: panel.panelId, key: null, label: t(panel.title, locale) || panel.panelId },
      event.ctrlKey || event.metaKey,
    );
  };

  return (
    <article
      className={[
        'board-panel',
        landing ? 'is-landing' : '',
        panelSelected ? 'is-selected' : '',
        mine.length && !panelSelected ? 'has-selection' : '',
        arrange?.isDragging(panel.panelId) ? 'is-dragging' : '',
        arrange?.isResizing(panel.panelId) ? 'is-resizing' : '',
        dropEdge ? `drop-${dropEdge}` : '',
      ]
        .filter(Boolean)
        .join(' ')}
      style={{ gridColumn: `span ${colSpan}` }}
      data-row-span={rowSpan}
      data-viz={panel.viz}
      data-panel={panel.panelId}
    >
      {/* The header is a selection target in its own right, so a panel can be
          quoted into the chat without clicking through to its contents. */}
      <div className="board-panel-head" onClick={selectPanel} title="Click to quote this panel into the chat">
        {/* A dedicated handle rather than a draggable header. The header is
            already a click target — it quotes the panel into the chat — and
            overloading one gesture on one target means every drag risks a
            stray selection and every click risks a stray move. */}
        {arrange && (
          <button
            className="board-panel-grip"
            aria-label={`Move or resize ${t(panel.title, locale) || panel.panelId}`}
            title="Drag to move. Arrow keys resize, shift-arrows reorder."
            onPointerDown={(event) => arrange.beginReorder(event, panel)}
            onKeyDown={(event) => arrange.onHandleKeyDown(event, panel)}
            onClick={(event) => event.stopPropagation()}
          >
            ⠿
          </button>
        )}
        <div style={{ minWidth: 0 }}>
          <div className="board-panel-eyebrow">
            {panel.viz.replace(/_/g, ' ')} · {panel.panelId}
          </div>
          <div className="board-panel-title">{t(panel.title, locale) || panel.panelId}</div>
          {panel.subtitle && <div className="board-panel-subtitle">{t(panel.subtitle, locale)}</div>}
        </div>
        <button
          className="board-panel-close"
          title="Remove this panel"
          onClick={(event) => {
            event.stopPropagation();
            store.apply({ action: 'remove_panel', panel_id: panel.panelId });
          }}
        >
          ×
        </button>
      </div>

      {/* Two separate nodes. The renderer owns `bodyRef` exclusively — React
          never puts children inside it, so reconciliation can never wipe an
          ECharts canvas or a Leaflet pane out from under it. */}
      <div className="board-panel-body" ref={bodyRef} style={error ? { display: 'none' } : undefined} />
      {error && (
        <div className="board-panel-body">
          <div className="panel-empty">{error}</div>
        </div>
      )}

      {panel.note && <div className="board-panel-note">{t(panel.note, locale)}</div>}

      {arrange && (
        <>
          {/* Three grips, because the two axes mean different things: width is
              relative importance on a twelve-column grid, height is how much
              room the marks need. Conflating them into one corner would make
              the common case (this chart should be wider) harder. */}
          <span
            className="board-panel-resize is-col"
            title="Drag to change width"
            onPointerDown={(event) => arrange.beginResize(event, panel, 'col')}
            onClick={(event) => event.stopPropagation()}
          />
          <span
            className="board-panel-resize is-row"
            title="Drag to change height"
            onPointerDown={(event) => arrange.beginResize(event, panel, 'row')}
            onClick={(event) => event.stopPropagation()}
          />
          <span
            className="board-panel-resize is-both"
            title="Drag to resize"
            onPointerDown={(event) => arrange.beginResize(event, panel, 'both')}
            onClick={(event) => event.stopPropagation()}
          />
          {/* The numbers you are dragging towards. Snapping without a readout
              feels like the interface fighting you; with one it reads as a
              grid you are working with. */}
          {arrange.isResizing(panel.panelId) && (
            <span className="board-panel-span">
              {colSpan}/12 · h{rowSpan}
            </span>
          )}
        </>
      )}
    </article>
  );
}
