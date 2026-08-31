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
 */

import { useEffect, useRef, useState } from 'react';
import { applyFilters, t } from '../client.js';

/** Maps repaint themselves on selection and highlight; charts must be re-rendered. */
const SELF_REPAINTING = new Set(['map_points', 'map_regions']);

export default function BoardPanel({ panel, client, registry, store, manifest, locale, filters, selection }) {
  const bodyRef = useRef(null);
  const [error, setError] = useState(null);
  const [landing, setLanding] = useState(true);

  const layout = panel.layout || {};
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
      className={`board-panel${landing ? ' is-landing' : ''}${panelSelected ? ' is-selected' : ''}${
        mine.length && !panelSelected ? ' has-selection' : ''
      }`}
      style={{ gridColumn: `span ${layout.colSpan || 6}` }}
      data-row-span={layout.rowSpan || 1}
      data-viz={panel.viz}
      data-panel={panel.panelId}
    >
      {/* The header is a selection target in its own right, so a panel can be
          quoted into the chat without clicking through to its contents. */}
      <div className="board-panel-head" onClick={selectPanel} title="Click to quote this panel into the chat">
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
    </article>
  );
}
