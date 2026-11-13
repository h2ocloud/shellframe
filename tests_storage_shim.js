/**
 * A page with no localStorage still starts (web/storage_shim.js).
 *
 * Measured in a clean Ubuntu 24.04 install: pywebview's private mode on WebKitGTK leaves
 * `localStorage` undefined, init() read it for the sidebar state on a fresh config, threw a
 * ReferenceError and stopped, so the first window was empty (no tab launcher). The shim, loaded
 * before every other script, gives such a page an in-memory one and leaves a real one alone.
 *
 * Run: node tests_storage_shim.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

let fails = 0;
function check(name, ok, detail) {
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${ok ? '' : '  ' + (detail || '')}`);
  if (!ok) fails++;
}

const src = fs.readFileSync(path.join(__dirname, 'web/storage_shim.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, 'web/index.html'), 'utf8');

// WebKitGTK in private mode: no localStorage at all. Bare `localStorage` must resolve afterwards.
const bare = { window: {} };
bare.window = bare;
vm.createContext(bare);
vm.runInContext(src, bare);
let got;
try {
  got = vm.runInContext(`
    const before = localStorage.getItem('sf_sidebar');
    localStorage.setItem('sf_sidebar', 1);
    localStorage.setItem('b', 'x');
    const r = { before, after: localStorage.getItem('sf_sidebar'), len: localStorage.length, k0: localStorage.key(0), k9: localStorage.key(9) };
    localStorage.removeItem('b');
    r.lenAfterRemove = localStorage.length;
    localStorage.clear();
    r.lenAfterClear = localStorage.length;
    r;`, bare);
} catch (e) {
  got = { error: String(e) };
}
check('no localStorage: a bare `localStorage` read works instead of throwing', got && !got.error && got.before === null, JSON.stringify(got));
check('…it keeps what is written (as strings), like the real one', got.after === '1' && got.len === 2 && got.k0 === 'sf_sidebar' && got.k9 === null, JSON.stringify(got));
check('…removeItem and clear work', got.lenAfterRemove === 1 && got.lenAfterClear === 0, JSON.stringify(got));

// macOS / Windows: a real localStorage is left alone.
const real = { getItem: () => 'real' };
const ctx = { window: null, localStorage: real };
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(src, ctx);
check('a page that has localStorage keeps its own', vm.runInContext('localStorage', ctx) === real);

// Loaded before anything that could read it.
const firstScript = html.search(/<script\b/);
const shimTag = html.indexOf('<script src="storage_shim.js"></script>');
check('index.html loads the shim as its first script', shimTag !== -1 && shimTag === firstScript, `${shimTag} vs ${firstScript}`);

console.log(fails ? `\n${fails} FAILED` : '\nALL PASS');
process.exit(fails ? 1 : 0);
