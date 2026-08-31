/**
 * The chat column — the conversational half of the board.
 *
 * Every SSE event from one turn lands here in order, and each is handled the
 * moment it arrives rather than at the end of the turn: a first panel appearing
 * in about a second reads as a conversation, and a six-second wait for the
 * finished board does not.
 *
 * The transcript shows what the assistant did — which queries ran and which
 * commands landed — but not the SQL those queries compiled to. The compiled
 * statement is logged server-side for audit; it is not something the person
 * reading the answer should have to scroll past.
 *
 * Everything project-specific arrives as props:
 *   labels      — { title, hint, placeholder, placeholderSelected, footer,
 *                   emptyHint(manifest) } · all optional, sensible defaults
 *   demoScript  — { acts, faq } for the demo rail, or null to hide the button
 *   extraFlags  — per-turn flags sent as the request's `extra` (a mode toggle)
 *   onFlags     — called with a demo step's `flags` before its prompt is sent
 */

import { useEffect, useRef, useState } from 'react';
import { t } from '../client.js';
import DemoRail from './DemoRail.jsx';

/**
 * Turn the current selection into a line the model can act on.
 *
 * The selection also travels in `board_state` every turn, but that is a
 * description of the board, not an instruction about this message. Putting it
 * in front of the user's own words is what makes "why is this one low?"
 * resolve — the same way quoting a message in a chat app makes a one-word
 * reply make sense.
 */
function quoteContext(store, locale) {
  const selection = store.getState().selection;
  if (!selection.length) return '';

  const parts = selection.map((entry) => {
    const panel = store.panelById(entry.panelId);
    const title = t(panel?.title, locale) || entry.panelId;
    return entry.key == null
      ? `the panel "${title}" (${entry.panelId})`
      : `"${entry.key}" in "${title}" (${entry.panelId})`;
  });

  return `[The user has selected ${parts.join(' and ')}. Their message is about that.]\n\n`;
}

const defaultEmptyHint = (manifest) =>
  manifest?.metrics
    ? `Ask about your data. ${Object.keys(manifest.metrics).length} metrics and ${
        Object.keys(manifest.dimensions).length
      } dimensions are in the catalog — the assistant can only name those, and can only draw the chart kinds registered in this app. Click a chart, a bar or a map mark to ask about it specifically; ctrl-click to pick several.`
    : 'Loading catalog…';

export default function BoardChat({
  client,
  store,
  state,
  manifest,
  health,
  locale = 'en',
  headers,
  labels = {},
  demoScript = null,
  extraFlags = {},
  onFlags,
}) {
  const [entries, setEntries] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);
  const [demo, setDemo] = useState(false);
  const endRef = useRef(null);
  const seq = useRef(0);

  const add = (entry) => setEntries((prev) => [...prev, { ...entry, id: ++seq.current }]);
  const selection = state?.selection || [];

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [entries, status]);

  async function ask(text) {
    const message = text.trim();
    if (!message || busy) return;

    const quoted = selection.map((e) => ({ ...e }));
    const prefix = quoteContext(store, locale);

    setInput('');
    setBusy(true);
    setStatus('thinking');
    add({ kind: 'user', text: message, quoted });

    // The selection has been spent on this message. Clearing it here means the
    // next question starts clean rather than silently inheriting a context the
    // person has stopped thinking about.
    store.clearSelection();

    try {
      await client.send(prefix + message, {
        locale,
        extra: extraFlags,
        onEvent(event) {
          switch (event.type) {
            case 'status':
              setStatus(
                event.stage === 'querying'
                  ? `querying${event.label ? ` · ${event.label}` : ''}`
                  : event.stage === 'retrying'
                    // Rejections go back to the model, not to the user as a
                    // failure. It nearly always self-corrects on the next round.
                    ? `correcting · ${event.detail}`
                    : 'thinking',
              );
              break;

            case 'text':
              add({ kind: 'brain', text: event.text });
              break;

            case 'result':
              add({ kind: 'trace', label: event.label || 'query', rows: event.row_count, ms: event.elapsed_ms });
              break;

            case 'command':
              add({
                kind: 'command',
                action: event.command.action,
                target: event.command.panel_id || event.command.filter?.dim || '',
                error: event.clientError,
              });
              break;

            case 'error':
              add({ kind: 'error', text: event.message });
              break;

            case 'done':
              setStatus(null);
              break;
            default:
              break;
          }
        },
      });
    } catch (err) {
      add({ kind: 'error', text: err.message || String(err) });
    } finally {
      setBusy(false);
      setStatus(null);
    }
  }

  const pending = store.getState().pending;
  const emptyHint = labels.emptyHint || defaultEmptyHint;

  return (
    <aside className="board-chat">
      <div className="board-chat-head">
        <div className="live-dot" />
        <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--fg)' }}>
          {labels.title || 'Board Assistant'}
        </span>
        <span
          className={`chip ${health?.live_model ? 'chip-accent' : ''}`}
          style={{ fontSize: 9 }}
          title={
            health?.live_model
              ? 'A live model is driving the board.'
              : 'No API key found — a keyword-matching fallback brain is driving the board. Set a model key in .env for real reasoning.'
          }
        >
          {health?.live_model ? health.brain.split('@')[0] : 'heuristic brain'}
        </span>
        <div style={{ flex: 1 }} />
        {demoScript && (
          <button
            className={`btn btn-ghost${demo ? ' is-on' : ''}`}
            style={{ fontSize: 10, color: demo ? 'var(--accent)' : 'var(--fg-3)' }}
            onClick={() => setDemo((v) => !v)}
            title="Presenter script and FAQ — every line is a button, so you never type on stage"
          >
            Demo
          </button>
        )}
        <button
          className="btn btn-ghost"
          style={{ fontSize: 10, color: 'var(--fg-3)' }}
          onClick={() => {
            setEntries([]);
            client.clearHistory();
          }}
          disabled={!entries.length}
        >
          Clear
        </button>
      </div>

      <div className="board-chat-log">
        {!entries.length && <div className="msg msg--system">{emptyHint(manifest)}</div>}

        {entries.map((e) => {
          if (e.kind === 'user') {
            return (
              <div key={e.id} className="msg-group">
                {e.quoted?.length > 0 && (
                  <div className="quoted-context">
                    {e.quoted.map((q, i) => (
                      <span key={i} className="quoted-chip">
                        {q.label ?? q.key ?? q.panelId}
                      </span>
                    ))}
                  </div>
                )}
                <div className="msg msg--user">{e.text}</div>
              </div>
            );
          }
          if (e.kind === 'brain') return <div key={e.id} className="msg msg--brain">{e.text}</div>;
          if (e.kind === 'error') return <div key={e.id} className="msg msg--error">{e.text}</div>;

          if (e.kind === 'trace') {
            return (
              <div key={e.id} className="trace-line">
                {e.label} · {e.rows} rows · {e.ms}ms
              </div>
            );
          }

          return (
            <div
              key={e.id}
              className="cmd-chip"
              style={e.error ? { background: 'var(--crit-soft)', borderColor: 'var(--crit)' } : undefined}
            >
              {e.action}
              {e.target ? ` · ${e.target}` : ''}
              {e.error ? ` · rejected: ${e.error}` : ''}
            </div>
          );
        })}

        {pending && (
          <div className="clarify">
            {t(pending.question, locale)}
            <div className="clarify-options">
              {(pending.options || []).map((opt) => (
                <button key={opt} onClick={() => ask(opt)}>
                  {opt}
                </button>
              ))}
            </div>
          </div>
        )}

        {status && (
          <div className="msg msg--system">
            {status === 'thinking' ? (
              <span className="thinking">
                <i />
                <i />
                <i />
              </span>
            ) : (
              status
            )}
          </div>
        )}

        <div ref={endRef} />
      </div>

      {demoScript && (
        <DemoRail
          open={demo}
          onClose={() => setDemo(false)}
          onSend={ask}
          busy={busy}
          acts={demoScript.acts || []}
          faq={demoScript.faq || []}
          onFlags={onFlags}
        />
      )}

      {!demo && !entries.length && manifest?.suggestions?.length > 0 && (
        <div className="board-suggestions">
          {manifest.suggestions.slice(0, 6).map((s) => (
            <button key={s} onClick={() => ask(s)} disabled={busy}>
              {s}
            </button>
          ))}
        </div>
      )}

      <div className="board-composer">
        {/* The quote bar. Same affordance as replying to a message: what you
            picked sits above what you are about to type, and you can drop it. */}
        {selection.length > 0 && (
          <div className="composer-quote">
            <div className="composer-quote-marks">
              {selection.map((entry, i) => (
                <span key={i} className="quoted-chip">
                  {entry.label ?? entry.key ?? entry.panelId}
                  <button title="Remove from the question" onClick={() => store.toggleSelection(entry, true)}>
                    ×
                  </button>
                </span>
              ))}
            </div>
            <button className="composer-quote-clear" onClick={() => store.clearSelection()} title="Clear selection">
              ×
            </button>
          </div>
        )}

        <div className="board-composer-row">
          <textarea
            rows={2}
            value={input}
            placeholder={
              selection.length
                ? labels.placeholderSelected || 'Ask about what you selected…'
                : labels.placeholder || 'Ask about your data…'
            }
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                ask(input);
              }
            }}
          />
          <button
            className="btn btn-primary"
            style={{ fontSize: 10, padding: '4px 12px' }}
            onClick={() => ask(input)}
            disabled={busy || !input.trim()}
          >
            {busy ? '…' : 'Ask'}
          </button>
        </div>
        <p style={{ fontSize: 9, color: 'var(--fg-4)', marginTop: 4, textAlign: 'center' }}>
          {labels.footer || 'Enter to send · Shift+Enter for a new line · Ctrl+click to select several'}
        </p>
      </div>
    </aside>
  );
}
