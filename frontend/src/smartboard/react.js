/**
 * React binding.
 *
 * The store is framework-free, so this is thin by design — proof that the core
 * is not secretly coupled to any view layer. A Vue or Svelte binding would be
 * about the same length.
 *
 *     const { state, client, send, apply } = useSmartBoard({ baseUrl: '' });
 */

import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { createStore } from './store.js';
import { SmartBoardClient, VizRegistry } from './client.js';

export function useSmartBoard({ baseUrl = '', registry: providedRegistry } = {}) {
  const storeRef = useRef(null);
  if (!storeRef.current) storeRef.current = createStore();
  const store = storeRef.current;

  const registry = useMemo(() => providedRegistry || new VizRegistry(), [providedRegistry]);
  const client = useMemo(() => new SmartBoardClient({ baseUrl, store }), [baseUrl, store]);

  const state = useSyncExternalStore(store.subscribe, store.getState, store.getState);

  useEffect(() => {
    client.init().catch((err) => console.error('smartboard init failed', err));
  }, [client]);

  const send = useCallback(
    (message, opts) => client.send(message, opts),
    [client],
  );

  return {
    state,
    store,
    client,
    registry,
    send,
    apply: store.apply,
    undo: store.undo,
    reset: store.reset,
  };
}

/**
 * Renders one panel by looking its viz kind up in the registry.
 *
 * The registry lookup is the whole security story on the view side: the brain
 * names a kind, and if you have not registered that kind, nothing renders. It
 * cannot supply markup or a component.
 */
export function useVizPanel({ panel, client, registry, store, manifest, locale = 'en' }) {
  const ref = useRef(null);

  useEffect(() => {
    let cleanup;
    let cancelled = false;

    (async () => {
      const render = registry.get(panel.viz);
      if (!render || !ref.current) return;
      const result = await client.result(panel.resultId);
      if (cancelled || !ref.current) return;
      ref.current.innerHTML = '';
      cleanup = render(ref.current, {
        rows: result.rows,
        columns: result.columns,
        encoding: panel.encoding,
        panel,
        store,
        manifest,
        locale,
      });
    })();

    return () => {
      cancelled = true;
      cleanup?.();
    };
  }, [panel.panelId, panel.viz, panel.resultId, JSON.stringify(panel.encoding), locale]);

  return ref;
}
