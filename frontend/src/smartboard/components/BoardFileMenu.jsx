/**
 * Save and open a board.
 *
 * A menu rather than two more buttons: the toolbar already carries undo, redo,
 * clear, every active filter, a language picker, a theme switch, the catalog
 * chip and sign-out. Two more naked buttons would push the thing people
 * actually look at — the live filters — off the end of the row on a laptop.
 *
 * The interesting behaviour is in boardFile.js; this is the surface for it.
 */

import { useEffect, useRef, useState } from 'react';
import {
  applyBoardFile,
  boardFilename,
  BoardFileError,
  clearAutosave,
  downloadBoard,
  parseBoardFile,
  readAutosave,
  serialiseBoard,
} from '../boardFile.js';

export default function BoardFileMenu({ store, client, state, manifest, health }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const fileRef = useRef(null);
  const menuRef = useRef(null);

  // The autosave is read when the menu opens, not on every render: it is a
  // localStorage hit, and it should reflect the moment you went looking for it.
  const [saved, setSaved] = useState(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event) => {
      if (!menuRef.current?.contains(event.target)) setOpen(false);
    };
    const onKey = (event) => {
      if (event.key === 'Escape') setOpen(false);
    };
    window.addEventListener('pointerdown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('pointerdown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // Messages clear themselves. A stale "opened 9 panels" sitting in the toolbar
  // twenty minutes later is worse than no confirmation at all.
  useEffect(() => {
    if (!message) return undefined;
    const id = setTimeout(() => setMessage(null), 9000);
    return () => clearTimeout(id);
  }, [message]);

  const report = (text, tone = 'ok') => setMessage({ text, tone });

  const save = () => {
    setOpen(false);
    if (!state.panels.length) return report('Nothing on the board to save.', 'warn');
    downloadBoard(serialiseBoard(state, { manifest, health }), boardFilename(manifest));
    report(`Saved ${state.panels.length} panels.`);
  };

  const load = async (parsed, what) => {
    setBusy(true);
    try {
      const { added, skipped } = await applyBoardFile(parsed, { client, store });
      if (!added) {
        report('That board opened empty — every panel in it was refused.', 'warn');
      } else if (skipped.length) {
        // The count goes here; which panels, and why, is narrated onto the
        // board itself, where it stays put.
        report(`${what}: ${added} panels, ${skipped.length} refused.`, 'warn');
      } else {
        report(`${what}: ${added} panels.`);
      }
    } catch (err) {
      report(err.message || 'Could not open that board.', 'warn');
    } finally {
      setBusy(false);
    }
  };

  const onFile = async (event) => {
    const file = event.target.files?.[0];
    // Cleared immediately so choosing the same file twice in a row still fires.
    event.target.value = '';
    if (!file) return;
    setOpen(false);
    try {
      const parsed = parseBoardFile(await file.text(), manifest);
      await load(parsed, `Opened ${file.name}`);
    } catch (err) {
      report(err instanceof BoardFileError ? err.message : 'Could not read that file.', 'warn');
    }
  };

  const restore = async () => {
    setOpen(false);
    if (!saved) return;
    await load(saved.parsed, 'Restored your last board');
  };

  return (
    <div className="board-file" ref={menuRef}>
      <button
        className="btn btn-ghost"
        style={{ fontSize: 10 }}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={busy}
        onClick={() => {
          setSaved(readAutosave(manifest));
          setOpen((was) => !was);
        }}
        title="Save this board to a file, or open one"
      >
        {busy ? '…' : 'Board ▾'}
      </button>

      {open && (
        <div className="board-file-menu" role="menu">
          <button role="menuitem" onClick={save}>
            Save to file…
          </button>
          <button role="menuitem" onClick={() => fileRef.current?.click()}>
            Open from file…
          </button>

          {saved && (
            <button role="menuitem" onClick={restore}>
              Restore last session
              <span className="board-file-when">{new Date(saved.savedAt).toLocaleString()}</span>
            </button>
          )}
          {saved && (
            <button
              role="menuitem"
              className="is-quiet"
              onClick={() => {
                clearAutosave(manifest);
                setSaved(null);
              }}
            >
              Forget last session
            </button>
          )}

          <p className="board-file-note">
            Saves the layout and the questions behind each panel, not the numbers. Opening a board
            re-runs its queries against your own access, so you see today's data and only what you
            are entitled to.
          </p>
        </div>
      )}

      <input
        ref={fileRef}
        type="file"
        accept="application/json,.json"
        hidden
        onChange={onFile}
      />

      {message && (
        <span className={`board-file-msg${message.tone === 'warn' ? ' is-warn' : ''}`}>{message.text}</span>
      )}
    </div>
  );
}
