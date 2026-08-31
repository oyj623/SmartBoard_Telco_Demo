/**
 * SmartBoard client — transport and registry.
 *
 * The registry is the other half of the "AI picks from a menu you wrote" idea.
 * The manifest declares which viz kinds are enabled; the registry maps each one
 * to a render function you own. The brain never supplies component code, only a
 * kind and an encoding.
 */

export class VizRegistry {
  constructor() {
    this.kinds = new Map();
  }

  /**
   * @param {string} kind      matches manifest viz.enabled
   * @param {Function} render  (element, { rows, columns, encoding, panel, store, manifest }) => cleanup?
   */
  set(kind, render) {
    this.kinds.set(kind, render);
    return this;
  }

  get(kind) {
    return this.kinds.get(kind) || null;
  }

  has(kind) {
    return this.kinds.has(kind);
  }
}

export class SmartBoardClient {
  /**
   * `baseUrl` is the API root ('/api/board'); endpoint paths hang off it
   * directly, so the board can live under an existing API namespace.
   * `headers` supplies auth headers on every call. It may be a function so a
   * refreshed token is picked up without rebuilding the client.
   */
  constructor({ baseUrl = '', store, headers = null }) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.headers = headers;
    this.store = store;
    this.manifest = null;
    this.health = null;
    this._results = new Map();
    this._history = [];
  }

  /** Auth (and content-type) headers for one request. */
  _headers(extra = {}) {
    const base = typeof this.headers === 'function' ? this.headers() : (this.headers || {});
    return { ...base, ...extra };
  }

  async init() {
    // Status is checked before parsing. Without this an expired token returns a
    // 401 body, `manifest.metrics` is undefined, and the first `Object.keys` on
    // it throws during render — which blanks the page instead of showing the
    // one thing the person needs to know, which is that they are signed out.
    const load = async (path) => {
      const res = await fetch(`${this.baseUrl}${path}`, { headers: this._headers() });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(
          res.status === 401
            ? 'Your session has expired. Sign in again.'
            : detail.detail || `${path} returned ${res.status}`,
        );
      }
      return res.json();
    };

    const [manifest, health] = await Promise.all([load('/manifest'), load('/health')]);
    this.manifest = manifest;
    this.health = health;
    return { manifest, health };
  }

  /** Rows are fetched by the browser, never routed through the model. */
  async result(resultId) {
    if (this._results.has(resultId)) return this._results.get(resultId);
    const res = await fetch(`${this.baseUrl}/result/${resultId}`, { headers: this._headers() });
    if (!res.ok) throw new Error(`result ${resultId} unavailable`);
    const data = await res.json();
    this._results.set(resultId, data);
    return data;
  }

  /** Run IR directly, with no model in the loop. Same guarded path. */
  async query(ir) {
    const res = await fetch(`${this.baseUrl}/query`, {
      method: 'POST',
      headers: this._headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ query: ir }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'query failed');
    const data = await res.json();
    this._results.set(data.result_id, data);
    return data;
  }

  /**
   * Send a message and stream events back.
   *
   * Commands are applied the moment they arrive rather than at the end of the
   * turn. A first panel landing in about a second reads as a conversation; a
   * six-second wait for the finished board does not.
   */
  async send(message, { locale = 'en', onEvent, extra = {} } = {}) {
    const res = await fetch(`${this.baseUrl}/chat`, {
      method: 'POST',
      headers: this._headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        message,
        locale,
        history: this._history,
        board_state: this.store.boardState(),
        // Per-turn flags the deployment adds (a mode toggle, say). The binding
        // hands them to `prepare_turn` untouched.
        extra,
      }),
    });

    if (!res.ok || !res.body) throw new Error(`chat failed: ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const frames = buffer.split('\n\n');
      buffer = frames.pop() || '';

      for (const frame of frames) {
        const line = frame.split('\n').find((l) => l.startsWith('data: '));
        if (!line) continue;

        let event;
        try {
          event = JSON.parse(line.slice(6));
        } catch {
          continue;
        }

        if (event.type === 'command') {
          const outcome = this.store.apply(event.command);
          if (!outcome.ok) event.clientError = outcome.error;
        }
        if (event.type === 'done' && Array.isArray(event.messages)) {
          this._history = event.messages;
        }
        onEvent?.(event);
      }
    }
  }

  clearHistory() {
    this._history = [];
    this._results.clear();
  }
}

/** Format a value using the manifest's declared unit and format. */
export function formatValue(value, column, manifest) {
  if (value == null) return '—';
  if (typeof value !== 'number') return String(value);

  const spec = manifest?.metrics?.[column?.id] || column || {};
  const fmt = spec.format || 'number';
  const abs = Math.abs(value);

  if (fmt === 'percent') return `${value.toFixed(value < 10 ? 2 : 1)}%`;
  if (fmt === 'currency') {
    // The prefix comes from the manifest's top-level `currency:` key ("RM", "$").
    const cur = manifest?.currency ? `${manifest.currency} ` : '';
    if (abs >= 1e9) return `${cur}${(value / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `${cur}${(value / 1e6).toFixed(1)}M`;
    if (abs >= 1e3) return `${cur}${(value / 1e3).toFixed(1)}k`;
    return `${cur}${value.toFixed(2)}`;
  }
  if (abs >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
  if (abs >= 1e4) return `${(value / 1e3).toFixed(1)}k`;
  if (abs >= 100) return value.toFixed(0);
  return value.toFixed(abs < 1 ? 3 : 2);
}

export function t(i18n, locale = 'en') {
  if (!i18n) return '';
  if (typeof i18n === 'string') return i18n;
  return i18n[locale] || i18n.en || Object.values(i18n)[0] || '';
}


/**
 * Apply active filters to a result's rows, client-side.
 *
 * A `set_filter` command records intent in the store; something has to act on
 * it. Re-querying every panel would be more correct but costs a round trip per
 * panel and loses the sub-100ms feel that makes filtering read as *direct
 * manipulation of the board* rather than as another request.
 *
 * A filter only bites on a panel whose result actually carries that dimension
 * as a column. A filter on `division` does nothing to a panel grouped only by
 * month — which is the honest behaviour: the panel genuinely does not know
 * which division its numbers came from, and silently pretending otherwise
 * would be worse than leaving it alone.
 */
export function applyFilters(rows, columns, filters) {
  if (!filters?.length || !rows?.length) return rows;
  const present = new Set(columns.map((c) => c.id));
  const live = filters.filter((f) => present.has(f.dim));
  if (!live.length) return rows;

  return rows.filter((row) => live.every((f) => matches(row[f.dim], f)));
}

function matches(value, { op, value: target }) {
  const num = (v) => (typeof v === 'number' ? v : parseFloat(v));
  switch (op) {
    case '=':        return String(value) === String(target);
    case '!=':       return String(value) !== String(target);
    case 'in':       return (target || []).map(String).includes(String(value));
    case 'not_in':   return !(target || []).map(String).includes(String(value));
    case '>':        return num(value) >  num(target);
    case '>=':       return num(value) >= num(target);
    case '<':        return num(value) <  num(target);
    case '<=':       return num(value) <= num(target);
    case 'between':  return num(value) >= num(target?.[0]) && num(value) <= num(target?.[1]);
    case 'contains': return String(value ?? '').toLowerCase().includes(String(target).toLowerCase());
    default:         return true;
  }
}
