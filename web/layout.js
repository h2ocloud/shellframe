/* How wide the sidebar may show, for the window it is in.
 *
 * Measured on Windows at 125% scaling: the window stops at 800px, which is 640 CSS pixels; with
 * the sidebar dragged to its widest (480 saved) the terminal or the chat view was left ~150px,
 * and the chat view's title and input box shrank to a few letters. The sidebar's width is the
 * person's to choose, so only what is DRAWN is limited: the saved width (settings.sidebar_width)
 * is never touched, and the sidebar is as wide as it was saved again when the window allows.
 *
 *   --sidebar-w    the saved width, set by index.html (config restore, a drag)
 *   --sidebar-cap  what the window allows, set here; #sidebar is min() of the two
 *
 * The cap is what is left after the main column's minimum (MAIN_MIN, in the window's pixels), the
 * panel on the right when it is open and the drag handles, divided by the interface size (the
 * sidebar is zoomed by it), and never below the narrowest a drag can make it. It is worked out
 * again when the window is resized, the right panel opens, closes or is resized, the interface
 * size changes, and the sidebar opens. The pure part (sidebarCap) is what tests_sidebar_limit.js runs.
 */
(function (root) {
  'use strict';
  const SIDEBAR_MIN = 140;      // the narrowest a drag makes it
  const MAIN_MIN = 400;         // what the terminal or the chat view keeps, in the window's pixels

  // All lengths in the window's pixels except the result, which is in the sidebar's own (zoomed) ones.
  function sidebarCap(total, boardPx, gutterPx, scale) {
    const s = scale > 0 ? scale : 1;
    return Math.max(SIDEBAR_MIN, Math.floor((total - boardPx - gutterPx - MAIN_MIN) / s));
  }

  if (typeof module === 'object' && module.exports) module.exports = { SIDEBAR_MIN, MAIN_MIN, sidebarCap };
  const doc = root.document;
  if (!doc) return;

  function measure() {
    const body = doc.getElementById('app-body');
    if (!body) return null;
    const scale = parseFloat(root.getComputedStyle(doc.documentElement).getPropertyValue('--ui-scale')) || 1;
    const board = doc.getElementById('board-panel');
    let boardPx = 0;
    if (board && !board.classList.contains('collapsed')) {     // its saved width, not the one mid-transition
      boardPx = (parseFloat(root.getComputedStyle(board).getPropertyValue('--board-w')) || 280) * scale;
    }
    let gutterPx = 0;
    ['sidebar-resizer', 'board-resizer'].forEach(id => {
      const r = doc.getElementById(id);
      if (r) gutterPx += r.getBoundingClientRect().width;      // 0 while its panel is closed
    });
    return { total: body.getBoundingClientRect().width, boardPx, gutterPx, scale };
  }
  function cap() {
    const m = measure();
    return m ? sidebarCap(m.total, m.boardPx, m.gutterPx, m.scale) : Infinity;
  }
  let last = '';
  function apply() {
    const c = cap();
    const v = Number.isFinite(c) ? c + 'px' : '';
    if (v === last) return;
    last = v;
    if (v) doc.documentElement.style.setProperty('--sidebar-cap', v);
    else doc.documentElement.style.removeProperty('--sidebar-cap');
  }

  root.addEventListener('resize', apply);
  const watch = (el, attrs) => { if (el && root.MutationObserver) new MutationObserver(apply).observe(el, { attributes: true, attributeFilter: attrs }); };
  watch(doc.documentElement, ['style']);                       // the interface size
  watch(doc.getElementById('board-panel'), ['class', 'style']);
  watch(doc.getElementById('sidebar'), ['class']);             // opening it shows its drag handle
  apply();
  root.addEventListener('load', apply);

  root.SFLayout = { sidebarCap: cap, apply, SIDEBAR_MIN, MAIN_MIN };
})(typeof window !== 'undefined' ? window : globalThis);
