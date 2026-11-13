/* A still page while nobody is looking at it (battery).
 *
 * Measured with 35 tabs (WebKit, the Mac app's engine): one CSS animation running on a part of an
 * SVG face makes the page redo style, layout and paint every frame — a tenth of a core in the web
 * process for one face, close to half for the sidebar's ~240 — and the GPU process and WindowServer
 * draw every one of those frames. The faces now move only while something happens, and only as a
 * whole (bots.css). This file stops even that, and slows the page's polling of Python, while
 *
 *   the window is hidden (minimised, covered),
 *   the window is not the one in front (the person is in another app), or
 *   nobody has typed, clicked, scrolled or moved the pointer in it for IDLE_MS.
 *
 * Then body.sf-still pauses every CSS animation, and a poller wrapped in SFQuiet.slow(fn, ms) runs at
 * most every `ms`; a key, a click or the window coming to the front ends it at once and runs the
 * catch-ups registered with SFQuiet.onWake. Output keeps flowing to the terminals all along.
 * It only listens (passive, capture): no preventDefault, nothing focused, the keyboard and IME paths
 * are untouched. A key or a click counts as someone being there even when the page says it has no
 * focus (the web view can lose the keyboard while its window is in front: termkeys.js), and each
 * change of state leaves one line in the debug log ([quiet]) so it can be checked in the app.
 */
(function (root) {
  'use strict';
  const doc = root && root.document;
  if (!doc) return;
  let idleMs = 60000;
  let lastInput = Date.now();
  let lastPress = 0;            // a key or a click: someone is here, whatever hasFocus() says
  let still = false;
  const wakers = [];

  function why() {
    if (doc.hidden) return 'hidden';
    if (doc.hasFocus && !doc.hasFocus() && Date.now() - lastPress > 30000) return 'blurred';
    return Date.now() - lastInput > idleMs ? 'idle' : '';
  }
  function update() {
    const reason = why(), now = !!reason;
    if (now === still) return;
    still = now;
    if (doc.body) doc.body.classList.toggle('sf-still', still);
    try { root.pywebview.api.js_debug('quiet', still ? `still (${reason})` : 'awake'); } catch (_) {}
    if (!still) wakers.forEach(fn => { try { fn(); } catch (_) {} });   // catch up now, not on the next slow tick
  }
  const touch = () => { lastInput = Date.now(); if (still) update(); };
  const press = () => { lastPress = Date.now(); touch(); };
  ['pointermove', 'wheel', 'compositionstart']
    .forEach(ev => root.addEventListener(ev, touch, { capture: true, passive: true }));
  ['keydown', 'pointerdown'].forEach(ev => root.addEventListener(ev, press, { capture: true, passive: true }));
  root.addEventListener('focus', () => { lastInput = Date.now(); update(); });
  root.addEventListener('blur', () => { lastPress = 0; setTimeout(update, 0); });   // hasFocus() is false once the blur is done
  doc.addEventListener('visibilitychange', update);
  setInterval(update, 2000);                                        // the idle clock, and a blur that came with no event

  // The paused state for everything that loops; the faces have their own rest pose in bots.css. The
  // microphone's pulse keeps going: dictating is hands-off, and a still pulse reads as "stopped recording".
  const css = doc.createElement('style');
  css.textContent = 'body.sf-still *, body.sf-still *::before, body.sf-still *::after { animation-play-state: paused !important; }' +
    ' body.sf-still #btn-mic.rec { animation-play-state: running !important; }';
  (doc.head || doc.documentElement).appendChild(css);

  // fn as it was while someone is there; while the page is still, at most once every stillMs.
  function slow(fn, stillMs) {
    let last = 0;
    return function () {
      const now = Date.now();
      if (still && now - last < stillMs) return undefined;
      last = now;
      return fn.apply(this, arguments);
    };
  }

  root.SFQuiet = {
    still: () => still, why, slow,
    onWake: fn => { wakers.push(fn); },
    setIdleMs: ms => { idleMs = ms; update(); },         // tests
  };
})(typeof window !== 'undefined' ? window : null);
