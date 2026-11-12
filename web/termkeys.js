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

  doc.addEventListener('keydown', (e) => {
    if (e.defaultPrevented || e.isComposing || e.key === 'Process' || e.metaKey || e.ctrlKey || e.altKey) return;
    const el = doc.activeElement;
    if ((el && el.classList && el.classList.contains('xterm-helper-textarea') && el.closest('.scroll-history-overlay') === null
         && s_of(el)) || editable(el)) return;
    const h = host();
    const sid = h && h.active && h.active();
    const s = sid && h.sessions && h.sessions()[sid];
    if (!s || !s.pane || !terminalShown(sid)) return;
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
})(typeof window !== 'undefined' ? window : globalThis);
