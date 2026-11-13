/* storage_shim.js — a localStorage for a page that has none.

   pywebview starts in private mode, and on Linux (WebKitGTK) private mode turns HTML5 local
   storage off: `localStorage` is not defined at all. Its first read threw a ReferenceError, and
   on a fresh config that read is in init() (the sidebar state), so a new Linux install opened an
   empty window: no tab launcher, nothing restored. This stand-in keeps what the page writes for as
   long as the window is open; the settings that must last are in config.json anyway.

   Loaded before every other script. Where localStorage exists (macOS, Windows) it does nothing. */
(function (root) {
  try { if (root.localStorage) return; } catch (e) { /* reading it is refused: replace it */ }
  const items = new Map();
  const store = {
    getItem: (k) => (items.has(String(k)) ? items.get(String(k)) : null),
    setItem: (k, v) => { items.set(String(k), String(v)); },
    removeItem: (k) => { items.delete(String(k)); },
    clear: () => { items.clear(); },
    key: (i) => { const keys = Array.from(items.keys()); return i >= 0 && i < keys.length ? keys[i] : null; },
    get length() { return items.size; },
  };
  try {
    Object.defineProperty(root, 'localStorage', { value: store, configurable: true, writable: true });
  } catch (e) {
    root.localStorage = store;
  }
})(typeof window !== 'undefined' ? window : globalThis);
