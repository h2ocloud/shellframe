/* Keys typed while the terminal is on screen go to the terminal.
 * Reported: switching a tab from the chat view back to its terminal (the window resized around it),
 * typing reached nothing in the real app: no write_input at all, while Playwright never lost the
 * focus. Whatever kept the keyboard (a button just clicked, the page itself), a key that no text box,
 * dialog or menu wants is handed to the active tab's terminal, and the terminal takes the focus for
 * the next ones. Each rescue writes one debug line naming what had the keyboard.
 */
(function (root) {
  'use strict';
  const doc = root.document;
  if (!doc) return;
  const host = () => root.__sfHost;

  // A text box that is not on screen does not have the keyboard: the chat view's, once the view is
  // hidden, could keep the focus in WKWebView and swallow everything typed into the terminal.
  function editable(el) {
    return !!(el && el !== doc.body && el.matches && el.getClientRects().length > 0 &&
      el.matches('input, textarea, select, [contenteditable]:not([contenteditable="false"])'));
  }
  function terminalShown(sid) {
    const layer = doc.getElementById('chat-layer');
    const chatUp = layer && !layer.hidden && root.SFChat && root.SFChat.modeOf && root.SFChat.modeOf(sid) === 'chat';
    return !chatUp && !doc.querySelector('.modal-overlay.open, .sfb-pop, .sf-menu, .scroll-history-overlay');
  }
  // The focused terminal textarea is the active tab's own: typing there is xterm's business.
  function s_of(el) {
    const h = host(), sid = h && h.active && h.active(), s = sid && h.sessions && h.sessions()[sid];
    return !!(s && s.pane && s.pane.contains(el));
  }
  const KEYS = { Enter: '\r', Backspace: '\x7f', Tab: '\t', Escape: '\x1b',
    ArrowUp: '\x1b[A', ArrowDown: '\x1b[B', ArrowRight: '\x1b[C', ArrowLeft: '\x1b[D' };

  // A key that went somewhere else while the terminal was on screen: one line saying where (at most every
  // 5 s), so a report of "typing does nothing" can be read from the log instead of guessed at.
  let missedAt = 0;
  function missed(sid, el, why) {
    const now = Date.now();
    if (now - missedAt < 5000) return;
    missedAt = now;
    const over = doc.querySelector('.modal-overlay.open, .sfb-pop, .sf-menu, .scroll-history-overlay');
    try {
      root.pywebview.api.js_debug('keys-missed', JSON.stringify({ sid, why,
        at: el ? (el.id || String(el.className || el.tagName)).slice(0, 60) : null,
        over: over ? (over.id || String(over.className)).slice(0, 60) : null, focused: doc.hasFocus() }));
    } catch (_) { /* no backend */ }
  }

  doc.addEventListener('keydown', (e) => {
    if (e.defaultPrevented || e.isComposing || e.key === 'Process' || e.metaKey || e.ctrlKey || e.altKey) return;
    const el = doc.activeElement;
    const h = host();
    const sid = h && h.active && h.active();
    const s = sid && h.sessions && h.sessions()[sid];
    if (el && el.classList && el.classList.contains('xterm-helper-textarea') && el.closest('.scroll-history-overlay') === null && s_of(el)) return;
    if (editable(el)) {
      if (s && s.pane && e.key.length === 1 && terminalShown(sid) && !el.closest('.modal, .sfb-pop, .sf-menu, #sidebar')) missed(sid, el, 'box');
      return;
    }
    if (!s || !s.pane) return;
    if (!terminalShown(sid)) { if (e.key.length === 1 && !(root.SFChat && root.SFChat.covers && root.SFChat.covers())) missed(sid, el, 'covered'); return; }
    const data = e.key.length === 1 ? e.key : KEYS[e.key];
    if (!data) return;
    e.preventDefault();
    e.stopPropagation();
    const ta = s.pane.querySelector('textarea.xterm-helper-textarea');
    try { if (ta) ta.focus({ preventScroll: true }); } catch (_) { /* hidden for a frame */ }
    try {
      root.pywebview.api.write_input(sid, data);
      root.pywebview.api.js_debug('keys-rescued', JSON.stringify({
        sid, from: el ? (el.id || String(el.className || el.tagName)).slice(0, 60) : null }));
    } catch (_) { /* no backend: nothing to send to */ }
  }, true);

  // A click in the terminal gives it the keyboard. With a CLI that reports the mouse (full-screen Claude
  // Code), xterm passes the click on to the CLI; the page must still be typing into that terminal after it.
  doc.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    const h = host(), sid = h && h.active && h.active(), s = sid && h.sessions && h.sessions()[sid];
    if (!s || !s.pane || !s.pane.contains(e.target) || !terminalShown(sid)) return;
    setTimeout(() => {
      const ta = s.pane.querySelector('textarea.xterm-helper-textarea');
      if (ta && doc.activeElement !== ta) { try { ta.focus({ preventScroll: true }); } catch (_) { /* hidden for a frame */ } }
    }, 0);
  }, true);
})(typeof window !== 'undefined' ? window : globalThis);
