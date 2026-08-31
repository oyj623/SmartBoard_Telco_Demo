/**
 * The board runtime: one store, one client, one viz registry — configured, not
 * hardcoded.
 *
 * Everything project-specific arrives in the config object:
 *
 *   useBoard({
 *     baseUrl: '/api/board',
 *     headers: () => ({ Authorization: `Bearer ${token}` }),   // or a plain object
 *     registry,                       // pre-composed VizRegistry; defaults to the ECharts kinds
 *     sections: [...],                // the starting board's titled bands
 *     panels: [...],                  // the starting panels (shape below)
 *     panelFilter: (panel, health) => true,   // gate entries per role, etc.
 *   })
 *
 * A starting panel is { panel_id, ir, viz, encoding, title, note?, style?,
 * layout? } — `ir` goes through POST /query, the same validate → guard →
 * compile → scope → execute path a model-issued query takes, with no model
 * involved. That is the "one reducer, two callers" claim made concrete: the
 * default dashboard and the conversational dashboard are the same machinery,
 * so the assistant can reason about panels it did not draw, and the user can
 * remove panels it did.
 */

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { createStore } from '../store.js';
import { SmartBoardClient, VizRegistry } from '../client.js';
import { registerAll } from '../adapters/echarts.js';

export function useBoard({
  baseUrl = '/api/board',
  headers = null,
  registry: providedRegistry = null,
  sections = [],
  panels = [],
  panelFilter = null,
} = {}) {
  const storeRef = useRef(null);
  if (!storeRef.current) storeRef.current = createStore();
  const store = storeRef.current;

  const client = useMemo(
    () => new SmartBoardClient({ baseUrl, store, headers }),
    [baseUrl, store, headers],
  );

  // Every kind the assistant may name must be registered; anything it names
  // that is not registered simply does not render, which is the whole
  // view-side security story.
  const registry = useMemo(
    () => providedRegistry || registerAll(new VizRegistry()),
    [providedRegistry],
  );

  const state = useSyncExternalStore(store.subscribe, store.getState, store.getState);

  const [manifest, setManifest] = useState(null);
  const [health, setHealth] = useState(null);
  const [booting, setBooting] = useState(true);
  const [bootError, setBootError] = useState(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const { manifest: mf, health: hl } = await client.init();
        if (cancelled) return;
        setManifest(mf);
        setHealth(hl);

        // Sections before panels: a panel that names a section has to find it.
        if (sections.length) {
          store.apply({ action: 'set_layout', order: [], sections });
        }

        const wanted = panelFilter ? panels.filter((p) => panelFilter(p, hl)) : panels;

        // Fetched in parallel; drawn in declared order so the board does not
        // shuffle itself depending on which query returned first.
        const results = await Promise.all(
          wanted.map((p) =>
            client.query(p.ir).then(
              (r) => ({ panel: p, result: r }),
              (err) => ({ panel: p, error: err }),
            ),
          ),
        );
        if (cancelled) return;

        for (const { panel, result, error } of results) {
          if (error || !result) {
            console.warn(`default panel ${panel.panel_id} skipped:`, error?.message);
            continue;
          }
          store.apply({
            action: 'add_panel',
            panel_id: panel.panel_id,
            result_id: result.result_id,
            viz: panel.viz,
            encoding: panel.encoding,
            title: panel.title,
            note: panel.note,
            style: panel.style,
            layout: panel.layout,
            slot: 'append',
          });
        }
      } catch (err) {
        if (!cancelled) setBootError(err.message || String(err));
      } finally {
        if (!cancelled) setBooting(false);
      }
    })();

    return () => {
      cancelled = true;
    };
    // The starting board is a mount-time decision; changing it later means
    // remounting the page, not silently rebuilding under the user.
  }, [client, store]);

  return { store, client, registry, state, manifest, health, booting, bootError };
}
