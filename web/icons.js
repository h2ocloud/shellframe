/* ShellBot icons: one set of hand-drawn vector icons on a 24-unit grid (thin line: 1.5 px stroke, round caps and joins, no fill).
 * SFIcons.html(name, opts) returns the <svg> as a string (opts.cls adds a class); static markup says data-sfi="name" and
 * SFIcons.hydrate() swaps it for the drawing at load. Everything is currentColor, so an icon takes its button's colour, hover and
 * dimmed states. The drawings are decorative (aria-hidden): the button around an icon-only one carries the title / aria-label.
 * Sizes live in the CSS of the part that uses an icon (icons.css has the base rule). No fonts, no network. */
(function (root) {
  'use strict';
  const ICONS = {
    "bot": "<rect x=\"4.5\" y=\"8.5\" width=\"15\" height=\"11\" rx=\"3.5\"/><path d=\"M12 8.5V5.2\"/><circle cx=\"12\" cy=\"3.9\" r=\"1.1\"/><path d=\"M2.5 13v3M21.5 13v3M9.2 13.4v1.6M14.8 13.4v1.6\"/>",
    "office": "<path d=\"M5.5 20.5V5A1.5 1.5 0 0 1 7 3.5h7A1.5 1.5 0 0 1 15.5 5v15.5\"/><path d=\"M15.5 9.5H18a1.5 1.5 0 0 1 1.5 1.5v9.5\"/><path d=\"M3.5 20.5h17\"/><path d=\"M9 7.5h3M9 11h3M9 14.5h3\"/>",
    "clipboard": "<rect x=\"5\" y=\"4.5\" width=\"14\" height=\"16.5\" rx=\"2.5\"/><rect x=\"8.5\" y=\"2.5\" width=\"7\" height=\"4\" rx=\"1.2\"/><path d=\"M8.5 12h7M8.5 15.8h4.5\"/>",
    "mic": "<rect x=\"9\" y=\"3\" width=\"6\" height=\"11.5\" rx=\"3\"/><path d=\"M5.5 11.5a6.5 6.5 0 0 0 13 0\"/><path d=\"M12 18v3M8.5 21h7\"/>",
    "stop": "<rect x=\"6\" y=\"6\" width=\"12\" height=\"12\" rx=\"2.5\"/>",
    "hourglass": "<path d=\"M6.5 3.5h11M6.5 20.5h11\"/><path d=\"M8 3.5v3.4a4.2 4.2 0 0 0 1.7 3.3L12 12l-2.3 1.8A4.2 4.2 0 0 0 8 17.1v3.4M16 3.5v3.4a4.2 4.2 0 0 1-1.7 3.3L12 12l2.3 1.8a4.2 4.2 0 0 1 1.7 3.3v3.4\"/>",
    "settings": "<path d=\"M10.42 4.87L10.65 2.39L13.35 2.39L13.58 4.87L15.92 5.84L17.84 4.25L19.75 6.16L18.16 8.08L19.13 10.42L21.61 10.65L21.61 13.35L19.13 13.58L18.16 15.92L19.75 17.84L17.84 19.75L15.92 18.16L13.58 19.13L13.35 21.61L10.65 21.61L10.42 19.13L8.08 18.16L6.16 19.75L4.25 17.84L5.84 15.92L4.87 13.58L2.39 13.35L2.39 10.65L4.87 10.42L5.84 8.08L4.25 6.16L6.16 4.25L8.08 5.84z\"/><circle cx=\"12\" cy=\"12\" r=\"3\"/>",
    "sparkle": "<path d=\"M12 2.8c.7 5 2.5 7.5 9.2 9.2-6.7 1.7-8.5 4.2-9.2 9.2-.7-5-2.5-7.5-9.2-9.2 6.7-1.7 8.5-4.2 9.2-9.2z\"/>",
    "book": "<path d=\"M12 6.8C10 5.2 7.4 4.6 4.5 4.6v13c2.9 0 5.5.6 7.5 2.1 2-1.5 4.6-2.1 7.5-2.1v-13c-2.9 0-5.5.6-7.5 2.2zM12 6.8v12.9\"/>",
    "help": "<circle cx=\"12\" cy=\"12\" r=\"9\"/><path d=\"M9.4 9.7a2.7 2.7 0 1 1 4.2 2.2c-1 .7-1.6 1.2-1.6 2.3\"/><path d=\"M12 17.3h.01\"/>",
    "refresh": "<path d=\"M5.11 8.79A7.6 7.6 0 0 1 19.14 9.4M18.89 15.21A7.6 7.6 0 0 1 4.86 14.6\"/><path d=\"M22.15 8.31L20.3 12.6L16.13 10.5M1.85 15.69L3.7 11.4L7.87 13.5\"/>",
    "loop": "<path d=\"M17 3.5 20.5 7 17 10.5M3.5 11v-1a3 3 0 0 1 3-3H20M7 20.5 3.5 17 7 13.5M20.5 13v1a3 3 0 0 1-3 3H4\"/>",
    "plus": "<path d=\"M12 5v14M5 12h14\"/>",
    "minus": "<path d=\"M5 12h14\"/>",
    "close": "<path d=\"M6 6l12 12M18 6 6 18\"/>",
    "check": "<path d=\"M5 12.5l4.5 4.5L19 7.5\"/>",
    "check-circle": "<circle cx=\"12\" cy=\"12\" r=\"9\"/><path d=\"M8 12.4l2.9 2.9 5.4-5.8\"/>",
    "warn": "<path d=\"M12 4 21.2 19.6H2.8z\"/><path d=\"M12 10v4.2M12 17h.01\"/>",
    "circle": "<circle cx=\"12\" cy=\"12\" r=\"6.5\"/>",
    "circle-dot": "<circle cx=\"12\" cy=\"12\" r=\"7\"/><path d=\"M12 12h.01\"/>",
    "dot": "<circle cx=\"12\" cy=\"12\" r=\"4.5\" fill=\"currentColor\" stroke=\"none\"/>",
    "chev-down": "<path d=\"M6 9.5l6 6 6-6\"/>",
    "chev-right": "<path d=\"M9.5 6l6 6-6 6\"/>",
    "chev-up": "<path d=\"M6 14.5l6-6 6 6\"/>",
    "chev-left": "<path d=\"M14.5 6l-6 6 6 6\"/>",
    "menu": "<path d=\"M4.5 7h15M4.5 12h15M4.5 17h15\"/>",
    "grip": "<g fill=\"currentColor\" stroke=\"none\"><circle cx=\"9\" cy=\"6\" r=\"1.15\"/><circle cx=\"15\" cy=\"6\" r=\"1.15\"/><circle cx=\"9\" cy=\"12\" r=\"1.15\"/><circle cx=\"15\" cy=\"12\" r=\"1.15\"/><circle cx=\"9\" cy=\"18\" r=\"1.15\"/><circle cx=\"15\" cy=\"18\" r=\"1.15\"/></g>",
    "edit": "<path d=\"M4.5 19.5l.9-4 10.3-10.3a2 2 0 0 1 2.8 0l.5.5a2 2 0 0 1 0 2.8L8.7 18.8z\"/><path d=\"M14 7l3 3\"/>",
    "star": "<path d=\"M12 3.6l2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z\"/>",
    "phone": "<rect x=\"7\" y=\"2.5\" width=\"10\" height=\"19\" rx=\"2.5\"/><path d=\"M10.6 18.5h2.8\"/>",
    "link": "<path d=\"M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1\"/>",
    "message": "<path d=\"M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7a2.5 2.5 0 0 1-2.5 2.5H11l-4.5 3.5V16A2.5 2.5 0 0 1 4 13.5z\"/>",
    "folder": "<path d=\"M3.5 7.5a2 2 0 0 1 2-2h4l2 2.2h7a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z\"/>",
    "file": "<path d=\"M6.5 3.5h7.5l4 4v13h-11.5z\"/><path d=\"M13.5 3.5v4.5h4.5\"/>",
    "camera": "<path d=\"M4 8h3l1.5-2.5h7L17 8h3a1 1 0 0 1 1 1v9.5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z\"/><circle cx=\"12\" cy=\"13.3\" r=\"3.4\"/>",
    "paperclip": "<path d=\"M18.5 11.5l-6.6 6.6a4 4 0 0 1-5.7-5.7l7-7a2.7 2.7 0 0 1 3.8 3.8l-7 7a1.3 1.3 0 0 1-1.9-1.9L14 8.5\"/>",
    "globe": "<circle cx=\"12\" cy=\"12\" r=\"9\"/><path d=\"M3.5 12h17M12 3.5c2.4 2.4 3.6 5.3 3.6 8.5s-1.2 6.1-3.6 8.5c-2.4-2.4-3.6-5.3-3.6-8.5s1.2-6.1 3.6-8.5z\"/>",
    "computer": "<rect x=\"3\" y=\"4.5\" width=\"18\" height=\"12\" rx=\"2\"/><path d=\"M8.5 20h7M12 16.5V20\"/>",
    "swap": "<path d=\"M4 8h14M14.5 4.5 18 8l-3.5 3.5M20 16H6M9.5 12.5 6 16l3.5 3.5\"/>",
    "arrow-up-right": "<path d=\"M7 17 17 7M9 7h8v8\"/>",
    "arrow-down-right": "<path d=\"M7 7l10 10M17 9v8H9\"/>",
    "arrow-right": "<path d=\"M4.5 12h15M13.5 6l6 6-6 6\"/>",
    "arrow-left": "<path d=\"M19.5 12h-15M10.5 6l-6 6 6 6\"/>",
    "download": "<path d=\"M12 4v11M7 10.5l5 5 5-5M5 20h14\"/>",
    "search": "<circle cx=\"10.5\" cy=\"10.5\" r=\"6\"/><path d=\"M15 15l5 5\"/>",
    "history": "<path d=\"M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3.5 8.5M3.5 4v4.5H8M12 7.5V12l3 2\"/>",
    "clock": "<circle cx=\"12\" cy=\"12\" r=\"8.5\"/><path d=\"M12 7.5V12l3 1.8\"/>",
    "bulb": "<path d=\"M9 17.5h6M10 20.5h4M12 3.5a6 6 0 0 0-3.4 10.9c.6.5.9 1.1.9 1.8v.8h5v-.8c0-.7.3-1.3.9-1.8A6 6 0 0 0 12 3.5z\"/>",
    "pause": "<path d=\"M9 6v12M15 6v12\"/>",
    "play": "<path d=\"M8 5.5v13l10-6.5z\"/>"
  };

  function html(name, opts) {
    const d = ICONS[name];
    if (d == null) { try { console.warn('SFIcons: no icon named ' + name); } catch (e) { /* no console */ } return ''; }
    const cls = opts && opts.cls ? ' ' + String(opts.cls).replace(/[^\w -]/g, '') : '';
    return '<svg class="sfi' + cls + '" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + d + '</svg>';
  }
  // Static markup marks its icon with data-sfi="name" (an optional data-sfi-cls adds a class); the element's own text is replaced.
  function hydrate(scope) {
    (scope || document).querySelectorAll('[data-sfi]:not([data-sfi-done])').forEach(el => {
      el.innerHTML = html(el.getAttribute('data-sfi'), { cls: el.getAttribute('data-sfi-cls') });
      el.setAttribute('data-sfi-done', '1');
    });
  }
  try {
    hydrate();
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => hydrate());
  } catch (e) { /* no document (a node test) */ }
  root.SFIcons = { html, hydrate, names: Object.keys(ICONS) };
})(typeof window !== 'undefined' ? window : globalThis);
