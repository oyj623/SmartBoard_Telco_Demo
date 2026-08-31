/**
 * The demo rail.
 *
 * A presenter-facing panel that turns a demo script into buttons. It exists
 * because live demos fail on typing, not on technology: a typo in front of a
 * room costs ten seconds of silence and all of your composure.
 *
 * The script arrives as data via props:
 *
 *   acts: [{ id, title, point, needsLiveModel?, steps: [
 *     { kind: 'prompt', label, text, say, badge?, flags? } |
 *     { kind: 'cue',    label, say } ] }]
 *   faq:  [{ q, say, note?, text?, expect, badge?, flags? }]
 *
 * A step's optional `flags` object is handed to `onFlags` before the prompt is
 * sent — the hook a deployment uses to flip a mode toggle so a scripted step
 * never fails quietly in front of a room. `badge` renders as a small tag on
 * the button.
 */

import { useState } from 'react';

export default function DemoRail({ open, onClose, onSend, busy, acts = [], faq = [], onFlags }) {
  const [tab, setTab] = useState('script');
  const [done, setDone] = useState(() => new Set());
  const [openFaq, setOpenFaq] = useState(null);

  if (!open) return null;

  const run = (step, id) => {
    if (busy || !step.text) return;
    if (step.flags) onFlags?.(step.flags);
    setDone((prev) => new Set(prev).add(id));
    onSend(step.text);
  };

  return (
    <div className="demo-rail">
      <div className="demo-rail-head">
        <div className="demo-tabs">
          <button className={tab === 'script' ? 'is-on' : ''} onClick={() => setTab('script')}>
            Script
          </button>
          {faq.length > 0 && (
            <button className={tab === 'faq' ? 'is-on' : ''} onClick={() => setTab('faq')}>
              FAQ
            </button>
          )}
        </div>
        <button className="demo-reset" onClick={() => setDone(new Set())} title="Clear the ticked steps">
          reset
        </button>
        <button className="demo-close" onClick={onClose} title="Hide the demo rail">
          ×
        </button>
      </div>

      <div className="demo-rail-body">
        {tab === 'script' &&
          acts.map((act) => (
            <section className="demo-act" key={act.id}>
              <header>
                <h3>{act.title}</h3>
                <span>{act.point}</span>
                {/* Composition is the one thing the deterministic fallback
                    cannot fake, so an act that depends on it says so rather
                    than failing quietly in front of a room. */}
                {act.needsLiveModel && (
                  <em className="demo-needs-model">needs a live model — the fallback brain cannot compose</em>
                )}
              </header>

              {act.steps.map((step, i) => {
                const id = `${act.id}:${i}`;
                const isDone = done.has(id);

                if (step.kind === 'cue') {
                  return (
                    <div className="demo-cue" key={id}>
                      <span className="demo-cue-label">▸ {step.label}</span>
                      <p>{step.say}</p>
                    </div>
                  );
                }

                return (
                  <div className={`demo-step${isDone ? ' is-done' : ''}`} key={id}>
                    <button className="demo-step-btn" disabled={busy} onClick={() => run(step, id)}>
                      <span className="demo-step-label">
                        {step.label}
                        {step.badge && <span className="demo-badge">{step.badge}</span>}
                      </span>
                      <span className="demo-step-text">“{step.text}”</span>
                    </button>
                    <p className="demo-say">{step.say}</p>
                  </div>
                );
              })}
            </section>
          ))}

        {tab === 'faq' && (
          <section className="demo-act">
            <header>
              <h3>Questions the room asks</h3>
              <span>Each one has a prompt that answers it on screen</span>
            </header>

            {faq.map((item, i) => (
              <div className={`demo-faq${openFaq === i ? ' is-open' : ''}`} key={i}>
                <button className="demo-faq-q" onClick={() => setOpenFaq(openFaq === i ? null : i)}>
                  {item.q}
                </button>

                {openFaq === i && (
                  <div className="demo-faq-body">
                    <p className="demo-say">{item.say}</p>
                    {item.note && <p className="demo-note">Setup: {item.note}</p>}
                    {item.text && (
                      <button
                        className="demo-step-btn"
                        disabled={busy}
                        onClick={() => run(item, `faq:${i}`)}
                      >
                        <span className="demo-step-label">
                          Run it
                          {item.badge && <span className="demo-badge">{item.badge}</span>}
                        </span>
                        <span className="demo-step-text">“{item.text}”</span>
                      </button>
                    )}
                    {item.expect && <p className="demo-expect">Expect: {item.expect}</p>}
                  </div>
                )}
              </div>
            ))}
          </section>
        )}
      </div>
    </div>
  );
}
