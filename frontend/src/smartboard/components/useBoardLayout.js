/**
 * Direct manipulation of the board: drag to reorder, drag to resize.
 *
 * The whole design rests on one rule already load-bearing everywhere else in
 * SmartBoard — *every change to the board is a command*. A drag is not a
 * parallel path into the state tree; it previews locally and then emits exactly
 * one `set_layout`, the same command the assistant emits when you ask it to
 * redesign the board. Three things fall out of that, and they are the reason
 * this is worth doing properly rather than reaching for a grid library:
 *
 *   - Undo works on a drag for free, and can name it.
 *   - The assistant sees what you rearranged, because `boardState()` reports
 *     spans and sections. "Make that one wider" after you moved it resolves.
 *   - The mouse and the conversation are interchangeable. Anything you can drag
 *     you can also ask for, and the other way round.
 *
 * ONE GESTURE IS ONE UNDO STEP. The preview lives in React state and never
 * touches the store, so a drag across the board does not push sixty entries
 * onto a forty-deep undo stack and erase the afternoon. The store is written
 * once, on drop.
 *
 * SPANS, NOT COORDINATES. The store models a panel as a span on a twelve-column
 * grid that flows, not as a rectangle at an (x, y). Resizing therefore snaps to
 * whole columns and to the three row heights the schema allows. Free pixel
 * dragging would be a lie about the underlying model, and the lie surfaces the
 * moment the window narrows or the assistant touches the same panel.
 */

import { useCallback, useRef, useState } from 'react';
import { t } from '../client.js';

const COLUMNS = 12;
const MAX_ROW_SPAN = 3;
/** Movement before a press on the handle becomes a drag. Below it, it was a click. */
const DRAG_THRESHOLD = 4;
/** One row unit, in pixels. Matches the body heights in board.css. */
const ROW_STEP = 170;

const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));

/** Width of one grid column including its gap, so a pixel delta becomes a column delta. */
function columnStep(gridEl) {
  const rect = gridEl.getBoundingClientRect();
  const gap = parseFloat(getComputedStyle(gridEl).columnGap) || 12;
  // W = 12c + 11g, so the distance between two column starts is (W + g) / 12.
  return (rect.width + gap) / COLUMNS;
}

/**
 * Which panel is the pointer nearest, and which side of it?
 *
 * Nearest-centre rather than strict hit-testing: a twelve-column grid is mostly
 * gutters and ragged final rows, and a drop that does nothing because the
 * pointer was four pixels into a gap reads as a broken feature rather than as a
 * miss. Being inside a panel always beats being near one.
 */
function dropTargetAt(x, y, dragId) {
  let best = null;
  let bestDist = Infinity;

  for (const el of document.querySelectorAll('.board-panel[data-panel]')) {
    const id = el.dataset.panel;
    if (id === dragId) continue;
    const r = el.getBoundingClientRect();
    const inside = x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
    const dist = inside ? -1 : Math.hypot(x - (r.left + r.width / 2), y - (r.top + r.height / 2));
    if (dist < bestDist) {
      bestDist = dist;
      best = {
        overId: id,
        edge: x < r.left + r.width / 2 ? 'before' : 'after',
        section: el.closest('.board-band')?.dataset.section || null,
      };
    }
  }

  // An empty section still has to be somewhere you can drop, or a band the
  // assistant declared but has not filled is unreachable by mouse.
  if (!best || bestDist > 0) {
    for (const band of document.querySelectorAll('.board-band')) {
      const r = band.getBoundingClientRect();
      const inside = x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
      if (inside && !band.querySelector('.board-panel[data-panel]')) {
        return { overId: null, edge: 'end', section: band.dataset.section || null };
      }
    }
  }
  return best;
}

export function useBoardLayout({ store, state, locale = 'en' }) {
  // `drag` drives the render. `live` is the mutable copy the pointer handlers
  // read and write, because pointermove fires far more often than React
  // re-renders and reading through a stale closure would lag the preview.
  const [drag, setDrag] = useState(null);
  const live = useRef(null);

  const titleOf = useCallback(
    (panelId) => t(store.panelById(panelId)?.title, locale) || panelId,
    [store, locale],
  );

  /**
   * Run one pointer gesture to completion.
   *
   * Listeners go on `window`, not on the panel: the pointer routinely leaves
   * the element it started on — that is the entire point of a drag — and a
   * gesture that dies when you overshoot the edge of a panel is worse than no
   * gesture at all.
   */
  const startGesture = useCallback(({ initial, onMove, onCommit }) => {
    live.current = initial;
    setDrag(initial);
    document.body.classList.add('is-board-dragging');

    const detach = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', abandon);
      window.removeEventListener('keydown', onKey);
      document.body.classList.remove('is-board-dragging');
    };
    const clear = () => {
      detach();
      const finished = live.current;
      live.current = null;
      setDrag(null);
      return finished;
    };

    function move(event) {
      const next = onMove(event, live.current);
      if (!next) return;
      live.current = next;
      setDrag(next);
    }
    function stop() {
      const finished = clear();
      // A press that never crossed the threshold was a click on the handle,
      // not a drag. Committing it would put a no-op on the undo stack.
      if (finished?.active) onCommit(finished);
    }
    function abandon() {
      clear();
    }
    // Escape abandons a drag mid-flight, which is the convention everywhere
    // else and the only cheap way out once you have seen it going wrong.
    function onKey(event) {
      if (event.key === 'Escape') abandon();
    }

    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop);
    window.addEventListener('pointercancel', abandon);
    window.addEventListener('keydown', onKey);
  }, []);

  // -- reorder -------------------------------------------------------------

  const beginReorder = useCallback(
    (event, panel) => {
      if (event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();

      const originX = event.clientX;
      const originY = event.clientY;

      startGesture({
        initial: {
          mode: 'reorder',
          panelId: panel.panelId,
          active: false,
          overId: null,
          edge: null,
          section: null,
        },
        onMove(moveEvent, current) {
          const moved = Math.hypot(moveEvent.clientX - originX, moveEvent.clientY - originY);
          if (!current.active && moved < DRAG_THRESHOLD) return null;
          const target = dropTargetAt(moveEvent.clientX, moveEvent.clientY, panel.panelId);
          if (
            current.active &&
            target?.overId === current.overId &&
            target?.edge === current.edge &&
            target?.section === current.section
          ) {
            return null; // nothing to repaint
          }
          return { ...current, active: true, ...(target || { overId: null, edge: null, section: null }) };
        },
        onCommit(finished) {
          const { panelId, overId, edge, section } = finished;
          const order = state.order.filter((id) => id !== panelId);

          if (overId) {
            const at = order.indexOf(overId);
            order.splice(at < 0 ? order.length : edge === 'before' ? at : at + 1, 0, panelId);
          } else {
            order.push(panelId);
          }

          const current = store.panelById(panelId);
          const sectionChanged = (current?.layout?.section ?? null) !== (section ?? null);
          const orderChanged = order.join(' ') !== state.order.join(' ');
          if (!orderChanged && !sectionChanged) return; // dropped where it already was

          store.apply(
            { action: 'set_layout', order, panels: [{ panel_id: panelId, section }] },
            `move "${titleOf(panelId)}"`,
          );
        },
      });
    },
    [startGesture, state.order, store, titleOf],
  );

  // -- resize --------------------------------------------------------------

  const beginResize = useCallback(
    (event, panel, axis) => {
      if (event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();

      const gridEl = event.currentTarget.closest('.board-grid');
      if (!gridEl) return;

      const step = columnStep(gridEl);
      const originX = event.clientX;
      const originY = event.clientY;
      const startCol = panel.layout?.colSpan || 6;
      const startRow = panel.layout?.rowSpan || 1;

      startGesture({
        initial: {
          mode: 'resize',
          panelId: panel.panelId,
          axis,
          active: false,
          colSpan: startCol,
          rowSpan: startRow,
        },
        onMove(moveEvent, current) {
          const dx = moveEvent.clientX - originX;
          const dy = moveEvent.clientY - originY;
          if (!current.active && Math.hypot(dx, dy) < DRAG_THRESHOLD) return null;

          const colSpan = axis === 'row' ? startCol : clamp(startCol + Math.round(dx / step), 1, COLUMNS);
          const rowSpan =
            axis === 'col' ? startRow : clamp(startRow + Math.round(dy / ROW_STEP), 1, MAX_ROW_SPAN);
          if (current.active && colSpan === current.colSpan && rowSpan === current.rowSpan) return null;
          return { ...current, active: true, colSpan, rowSpan };
        },
        onCommit(finished) {
          if (finished.colSpan === startCol && finished.rowSpan === startRow) return;
          store.apply(
            {
              action: 'set_layout',
              panels: [{ panel_id: finished.panelId, col_span: finished.colSpan, row_span: finished.rowSpan }],
            },
            `resize "${titleOf(finished.panelId)}"`,
          );
        },
      });
    },
    [startGesture, store, titleOf],
  );

  // -- keyboard ------------------------------------------------------------

  /**
   * The same two operations without a mouse.
   *
   * Arrows resize, shift-arrows move. Not a courtesy: a drag target is a poor
   * affordance for anyone on a keyboard, on a trackpad they find imprecise, or
   * driving a projector from the back of a room — and the store cannot tell the
   * difference, because both paths emit the same command.
   */
  const onHandleKeyDown = useCallback(
    (event, panel) => {
      const arrows = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'];
      if (!arrows.includes(event.key)) return;
      event.preventDefault();

      const id = panel.panelId;

      if (event.shiftKey) {
        const order = [...state.order];
        const at = order.indexOf(id);
        if (at < 0) return;
        const to = clamp(at + (event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 1), 0, order.length - 1);
        if (at === to) return;
        order.splice(to, 0, ...order.splice(at, 1));
        store.apply({ action: 'set_layout', order }, `move "${titleOf(id)}"`);
        return;
      }

      const colSpan = clamp(
        (panel.layout?.colSpan || 6) + (event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0),
        1,
        COLUMNS,
      );
      const rowSpan = clamp(
        (panel.layout?.rowSpan || 1) + (event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0),
        1,
        MAX_ROW_SPAN,
      );
      store.apply(
        { action: 'set_layout', panels: [{ panel_id: id, col_span: colSpan, row_span: rowSpan }] },
        `resize "${titleOf(id)}"`,
      );
    },
    [state.order, store, titleOf],
  );

  // -- what the renderer needs --------------------------------------------

  return {
    drag,
    beginReorder,
    beginResize,
    onHandleKeyDown,
    /** The spans to draw right now: the live preview during a resize, the stored value otherwise. */
    spansFor(panel) {
      if (drag?.mode === 'resize' && drag.active && drag.panelId === panel.panelId) {
        return { colSpan: drag.colSpan, rowSpan: drag.rowSpan };
      }
      return { colSpan: panel.layout?.colSpan || 6, rowSpan: panel.layout?.rowSpan || 1 };
    },
    isDragging: (panelId) => drag?.mode === 'reorder' && drag.active && drag.panelId === panelId,
    isResizing: (panelId) => drag?.mode === 'resize' && drag.active && drag.panelId === panelId,
    /** 'before' | 'after' | null — which side of this panel the insertion bar goes. */
    dropEdge: (panelId) =>
      drag?.mode === 'reorder' && drag.active && drag.overId === panelId ? drag.edge : null,
    /** True while an empty band is the drop target. */
    isEmptyDropTarget: (sectionId) =>
      drag?.mode === 'reorder' &&
      drag.active &&
      drag.overId === null &&
      (drag.section ?? null) === (sectionId ?? null),
    /** True while any gesture is live, so the grid can show its column guides. */
    gridBusy: !!drag?.active,
  };
}
