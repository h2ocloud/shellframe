/* The office's "Arrange positions": where the paired computers' rooms ("Next door: …") stand around this computer's building.
 *
 * Loaded by office.html (the iframe), like office_objects.js: the page's own drawing helpers come in through env, nothing here reaches
 * into its private state. Without a saved `arrange` pref the rooms stay in the old straight column and nothing in this file runs.
 *
 *   OfficeArrange.create(env) -> ctl
 *   ctl.active()  rooms are placed on the ring (a saved arrangement, or the arrange mode is open)     ctl.editing()  the mode is open
 *   ctl.place(PL, BX, BY) -> arr     PL: [{pe, w, h}] the paired computers' rooms; BX, BY: this computer's building (lobby + zones)
 *   ctl.bounds(arr, rooms, base) -> the camera's box     ctl.floor(x, far, stage)  the yard and the walkways (drawPlaza)
 *   ctl.drawWorld(x) / drawOverlay() / sync()   the dashed cells, the guides and the ghost; the cells' words; the handles' places
 *   ctl.esc() -> true when Esc was used here
 *
 * Saved as prefs.arrange = { v: 1, slots: { <paired computer id>: [col, row] } }. col / row count cells from the building's top-left
 * cell; negative = left of / above it. One cell = one room plus a walkway (CELLW x CELLH world units); the ring is the cells that
 * touch the building. Both views (3D and flat) read the same cell codes. The rules (also in the design note):
 *   - a computer with no saved cell takes the next empty one: above, left, the corner, above again … then further out
 *   - a saved cell that is taken (the building grew, two computers claim it) is not used this time, and is not overwritten
 *   - a room for nine or more bots (three rows) takes two cells
 *   - Done saves every computer's place and drops the ones that are no longer paired; Esc goes back to how it was before the mode
 */
(function (root) {
  'use strict';

  const CELLW = 12, CELLH = 6, KEY = 'arrange', MAXC = 40;
  const esc = s => String(s).replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
  const ok = (c, r) => Number.isInteger(c) && Number.isInteger(r) && Math.abs(c) <= MAXC && Math.abs(r) <= MAXC;

  /* ── the rules: pure, no page needed ───────────────────────────────────────── */
  function clean(v) {                       // what the page keeps: a plain {v: 1, slots} with whole-number cells, or nothing
    if (!v || typeof v !== 'object' || v.v !== 1 || !v.slots || typeof v.slots !== 'object' || Array.isArray(v.slots)) return null;
    const slots = {};
    for (const k of Object.keys(v.slots).slice(0, 64)) { const s = v.slots[k]; if (Array.isArray(s) && s.length === 2 && ok(s[0], s[1])) slots[k] = [s[0], s[1]]; }
    return { v: 1, slots };
  }
  function ring1(bc, br) {                  // the ring's cells in the order an unplaced computer takes them: above, left, the corner, above, left …
    const N = [], W = [], E = [], S = [], out = [];
    for (let c = 0; c < bc; c++) N.push([c, -1]);
    for (let r = 0; r < br; r++) W.push([-1, r]);
    for (let r = 0; r < br; r++) E.push([bc, r]);
    for (let c = 0; c < bc; c++) S.push([c, br]);
    for (let i = 0; i < Math.max(N.length, W.length); i++) { if (N[i]) out.push(N[i]); if (W[i]) out.push(W[i]); if (i === 0) out.push([-1, -1]); }
    out.push([bc, -1], [-1, br], ...E, ...S, [bc, br]);
    return out;
  }
  function* autoCells(bc, br) {             // ring 1, then ring 2 (the same order around a building two cells wider each way) …
    for (let k = 1; k < 60; k++) { const o = k - 1; for (const [c, r] of ring1(bc + 2 * o, br + 2 * o)) yield [c - o, r - o]; }
  }
  const spanOf = (h, G) => Math.max(1, Math.ceil((h + G) / (CELLH + G)));       // a room taller than a cell (nine bots or more) takes two cells

  function place(PL, BX, BY, slots, G) {
    const PITX = CELLW + G, PITY = CELLH + G;
    const bc = Math.max(1, Math.ceil((BX + G * .5) / PITX)), br = Math.max(1, Math.ceil((BY + G * .5) / PITY));
    const taken = new Map(), at = new Map(), key = (c, r) => c + ',' + r;     // cell -> who stands on it
    const dist = (c, r) => Math.max(c < 0 ? -c : c >= bc ? c - bc + 1 : 0, r < 0 ? -r : r >= br ? r - br + 1 : 0);   // 0 = inside the building, 1 = the ring that touches it
    const cells = (c, r, sp) => { const o = []; for (let k = 0; k < sp; k++) o.push([c, r < 0 ? r - k : r + k]); return o; };   // an upper room grows upward, a lower one downward
    const free = (c, r, sp, who) => cells(c, r, sp).every(([cc, rr]) => dist(cc, rr) > 0 && (!taken.has(key(cc, rr)) || taken.get(key(cc, rr)) === who));
    const cx = c => c < 0 ? c * PITX : c >= bc ? Math.max(BX + G, bc * PITX) + (c - bc) * PITX : c * PITX;      // a cell outside the building is a walkway away from it
    const cy = r => r < 0 ? r * PITY : r >= br ? Math.max(BY + G, br * PITY) + (r - br) * PITY : r * PITY;
    const rectFor = (c, r, w, h) => { const x0 = cx(c), y0 = cy(r); return { x: c < 0 ? x0 + CELLW - w : x0, y: r < 0 ? y0 + CELLH - h : y0 }; };   // against the building's side of the cell
    const cellRect = (c, r, sp = 1) => { const q = { x: cx(c), y: cy(r), w: CELLW, h: CELLH + (sp - 1) * PITY }; if (r < 0) q.y -= (sp - 1) * PITY; return q; };
    const put = (q, c, r, how) => { const sp = spanOf(q.h, G); for (const [cc, rr] of cells(c, r, sp)) taken.set(key(cc, rr), q.pe.id); const p = rectFor(c, r, q.w, q.h); at.set(q.pe.id, { c, r, how, sp, x: p.x, y: p.y, w: q.w, h: q.h }); };
    const rest = [];
    for (const q of PL) { const s = slots && slots[q.pe.id]; if (s && ok(s[0], s[1]) && free(s[0], s[1], spanOf(q.h, G), q.pe.id)) put(q, s[0], s[1], 'saved'); else rest.push(q); }
    for (const q of rest) { const sp = spanOf(q.h, G); for (const [c, r] of autoCells(bc, br)) if (free(c, r, sp, q.pe.id)) { put(q, c, r, 'auto'); break; } }
    return { bc, br, BX, BY, PITX, PITY, G, at, dist, free, cellRect, rectFor, spanOf: h => spanOf(h, G),
      open: (c, r, sp, who) => dist(c, r) === 1 && free(c, r, sp, who) };    // what a room may be dropped on: the ring only
  }

  function create(env) {
    const { tr, doc, kick, store } = env, T = tr;
    const win = doc.defaultView || window;
    const ST = { on: false, saved: clean(store.get(KEY, null)), slots: {}, before: null, drag: null, sig: '', live: '', barTop: -1 };
    let button = null, bar = null, liveEl = null, ptr = null;
    const handles = new Map();
    const arr = () => env.arr();
    const slotsNow = () => (ST.on ? ST.slots : (ST.saved ? ST.saved.slots : {}));
    const nameOf = pid => { const pe = env.peers().get(pid); return pe ? pe.name : pid; };
    const cl = o => JSON.parse(JSON.stringify(o));

    /* ── placement and the camera's box ────────────────────────────────────── */
    const active = () => ST.on || !!ST.saved;
    const editing = () => ST.on;
    const doPlace = (PL, BX, BY) => place(PL, BX, BY, slotsNow(), env.G);
    function bounds(A, rooms, b) {          // the building's box, every peer room, and — while arranging — the whole ring, so the cells all fit
      const o = Object.assign({}, b);
      for (const r of rooms.values()) if (r.kind === 'peer') { o.x0 = Math.min(o.x0, r.tx - .8); o.y0 = Math.min(o.y0, r.ty - .8); o.x1 = Math.max(o.x1, r.tx + r.tw + .4); o.y1 = Math.max(o.y1, r.ty + r.th + .4); }
      if (ST.on) { const a = A.cellRect(-1, -1), z = A.cellRect(A.bc, A.br); o.x0 = Math.min(o.x0, a.x - .8); o.y0 = Math.min(o.y0, a.y - 1.6); o.x1 = Math.max(o.x1, z.x + z.w + .4); o.y1 = Math.max(o.y1, z.y + z.h + .4); }
      return o;
    }

    /* ── the floor: the yard around the building and a walkway to every room ── */
    function floor(x, far, stage) {
      const A = arr(); if (!A) return;
      const pr = [...A.at.values()], west = pr.some(q => q.c < 0), north = pr.some(q => q.r < 0), east = pr.some(q => q.c >= A.bc), south = pr.some(q => q.r >= A.br), G = A.G;
      if (stage === 'yard') {
        const x0 = west ? -G : 0, y0 = north ? -G : 0, x1 = A.BX + (east ? G : 0), y1 = A.BY + (south ? G : 0);
        env.box(x, x0, y0, x1 - x0, y1 - y0, -16, 12.8, { l: '#161824', r: '#11121a', t: '#1a1d2c' });
        return;
      }
      for (const r of env.rooms().values()) {
        if (r.kind !== 'peer') continue;
        const bx0 = r.x, bx1 = r.x + r.w, by0 = r.y, by1 = r.y + r.h; let ax, bx, ay, by;
        if (bx1 <= 0) { ax = bx1; bx = 0; } else if (bx0 >= A.BX) { ax = bx0; bx = A.BX; } else { ax = bx = (Math.max(bx0, 0) + Math.min(bx1, A.BX)) / 2; }
        if (by1 <= 0) { ay = by1; by = 0; } else if (by0 >= A.BY) { ay = by0; by = A.BY; } else { ay = by = (Math.max(by0, 0) + Math.min(by1, A.BY)) / 2; }
        if (ax === bx && ay !== by) env.box(x, ax - .55, Math.min(ay, by), 1.1, Math.abs(by - ay), -9.6, 9.6, { l: '#171925', r: '#12131b', t: '#1c1f2e' });
        else if (ay === by && ax !== bx) env.box(x, Math.min(ax, bx), ay - .55, Math.abs(bx - ax), 1.1, -9.6, 9.6, { l: '#171925', r: '#12131b', t: '#1c1f2e' });
        const a = env.P(ax, ay, 1), b = env.P(bx, by, 1);
        x.setLineDash([5, 7]); x.lineDashOffset = env.RM ? 0 : -env.time() * 14; x.strokeStyle = env.alpha(env.COL.teal, .65); x.lineWidth = 2; x.beginPath(); x.moveTo(a.x, a.y); x.lineTo(b.x, b.y); x.stroke(); x.setLineDash([]); x.lineDashOffset = 0;
        env.ellipse(x, a.x, a.y, 3.2, 1.8, env.alpha(env.COL.teal, .85));
      }
    }

    /* ── drawing: dashed cells, guides, the room being carried ─────────────── */
    const openList = (who) => {            // the cells a room may be dropped on, in world cells
      const A = arr(), q = A.at.get(who), sp = q ? q.sp : 1, out = [];
      for (let c = -1; c <= A.bc; c++) for (let r = -1; r <= A.br; r++) if (A.open(c, r, sp, who)) out.push([c, r]);
      return out;
    };
    function drawWorld(x) {
      const A = arr(), D = ST.drag, COL = env.COL, al = env.alpha; if (!A) return;
      const quad = (q, z) => env.fquad(q.x, q.y, q.x + q.w, q.y + q.h, z);
      if (D) {       // the building's four edges, carried outward: the line to line a room up with
        x.setLineDash([9, 6]); x.strokeStyle = al(COL.yellow, .95); x.lineWidth = 2.2; x.beginPath();
        const ex = 2.5 * A.PITX, ey = 2 * A.PITY;
        for (const [a, b] of [[[-ex, 0], [A.BX + ex, 0]], [[-ex, A.BY], [A.BX + ex, A.BY]], [[0, -ey], [0, A.BY + ey]], [[A.BX, -ey], [A.BX, A.BY + ey]]]) { const p0 = env.P(a[0], a[1], .8), p1 = env.P(b[0], b[1], .8); x.moveTo(p0.x, p0.y); x.lineTo(p1.x, p1.y); }
        x.stroke(); x.setLineDash([]);
      }
      const who = D && D.pid, list = openList(who || null);
      if (!D) { const sp1 = []; for (let c = -1; c <= A.bc; c++) for (let r = -1; r <= A.br; r++) if (A.open(c, r, 1, null)) sp1.push([c, r]); list.length = 0; list.push(...sp1); }
      for (const [c, r] of list) {
        const q = A.cellRect(c, r, D ? A.at.get(D.pid).sp : 1), hot = D && D.cell && D.cell[0] === c && D.cell[1] === r;
        x.setLineDash(hot ? [] : [8, 6]); env.poly(x, quad(q, .5), al(hot ? COL.blue : COL.cyan, hot ? .26 : .07), al(hot ? COL.blue : COL.cyan, hot ? .95 : .7), hot ? 2.6 : 1.6); x.setLineDash([]);
      }
      if (D) { const q0 = A.at.get(D.pid), cq = A.cellRect(q0.c, q0.r, q0.sp); x.setLineDash([8, 6]); env.poly(x, quad(cq, .5), al(COL.yellow, .04), al(COL.yellow, .5), 1.6); x.setLineDash([]); }
      for (const r of env.rooms().values()) { if (r.kind !== 'peer') continue; const lifted = D && ('peer:' + D.pid) === r.id; env.poly(x, env.fquad(r.x - .3, r.y - .3, r.x + r.w + .3, r.y + r.h + .3, .5), null, al(COL.yellow, lifted ? .25 : .8), 1.8); }
      env.poly(x, env.fquad(-.5, -.5, A.BX + .5, A.BY + .5, .5), null, al(COL.purple, .75), 2.2);      // this computer: fixed
      if (D && D.cell) { const q0 = A.at.get(D.pid), p = A.rectFor(D.cell[0], D.cell[1], q0.w, q0.h);
        env.poly(x, env.fquad(p.x + .3, p.y + .2, p.x + q0.w + .3, p.y + q0.h + .2, .2), 'rgba(0,0,0,.28)');
        env.box(x, p.x + .6, p.y - .5, q0.w, q0.h, 6, 22, { t: al(COL.blue, .5), l: al(COL.blue, .38), r: al(COL.blue, .3) }, al(COL.blue, .95)); }
    }
    function drawOverlay() {
      const A = arr(), D = ST.drag, COL = env.COL, al = env.alpha, c = env.ctx(), v = env.view(), bs = Math.min(1.12, Math.max(.72, v.z * .95)); if (!A) return;
      c.setTransform(env.dpr(), 0, 0, env.dpr(), 0, 0);
      const list = D ? openList(D.pid) : openList(null).filter(([cc, rr]) => A.open(cc, rr, 1, null));
      const sp = D ? A.at.get(D.pid).sp : 1;
      for (const [cc, rr] of list) {
        const q = A.cellRect(cc, rr, sp), p = env.t2s(q.x + q.w / 2, q.y + q.h / 2, 0), hot = D && D.cell && D.cell[0] === cc && D.cell[1] === rr;
        c.font = `700 ${Math.round((hot ? 15 : 12.5) * bs)}px ${env.FONT}`; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillStyle = hot ? '#dfe9ff' : al(COL.cyan, .85);
        c.fillText(hot ? T('Drop here', '放這裡') : T('Empty', '空格'), p.x, p.y);
      }
      if (D) { const q0 = A.at.get(D.pid), cq = A.cellRect(q0.c, q0.r, q0.sp), p = env.t2s(cq.x + cq.w * .5, cq.y + .4, 26);
        env.pill(p.x, p.y, T('Was here (empty once dropped)', '原本在這裡（放開後變空格）'), { free: true, h: Math.round(22 * bs), font: `600 ${Math.round(12 * bs)}px ${env.FONT}`, color: '#2b2113', bg: al(COL.yellow, .92) }); }
      const bp = env.t2s(A.BX * .5, A.BY, 0);
      env.pill(bp.x, bp.y + 22 * bs, T('This computer · fixed in the center', '這台電腦 · 固定在中央'), { free: true, h: Math.round(26 * bs), font: `700 ${Math.round(12.5 * bs)}px ${env.FONT}`, color: '#efe6ff', bg: 'rgba(60,45,100,.95)', border: al(COL.purple, .8) });
    }

    /* ── the handles: one real button per room (Tab, touch and the mouse all land on it) ── */
    const cellScreen = (c, r, sp) => { const A = arr(), q = A.cellRect(c, r, sp || 1), pts = [[q.x, q.y], [q.x + q.w, q.y], [q.x + q.w, q.y + q.h], [q.x, q.y + q.h]].map(p => env.t2s(p[0], p[1], 0));
      const xs = pts.map(p => p.x), ys = pts.map(p => p.y); return { x: env.t2s(q.x + q.w / 2, q.y + q.h / 2, 0).x, y: env.t2s(q.x + q.w / 2, q.y + q.h / 2, 0).y, hw: (Math.max(...xs) - Math.min(...xs)) / 2, hh: (Math.max(...ys) - Math.min(...ys)) / 2 }; };
    const handlePos = pid => { const r = env.rooms().get('peer:' + pid); return r ? env.t2s(r.x + r.w / 2, r.y, r.wallH + 20) : null; };
    function ensureHandles() {
      const A = arr(), want = A && ST.on ? [...A.at.keys()] : [], sig = want.map(k => k + '\u0001' + nameOf(k)).join('\u0002');
      if (sig === ST.sig) return; ST.sig = sig;
      for (const [k, b] of handles) if (!want.includes(k)) { b.remove(); handles.delete(k); }
      for (const pid of want) {
        let b = handles.get(pid);
        if (!b) { b = doc.createElement('button'); b.type = 'button'; b.className = 'arr-h'; b.tabIndex = 0; b.dataset.pid = pid;       // an explicit tabindex: WebKit's Tab skips plain buttons unless the system asks for it
        wire(b, pid); doc.body.appendChild(b); handles.set(pid, b); }
        b.innerHTML = `<i aria-hidden="true">${root.SFIcons ? root.SFIcons.html('grip') : ''}</i><span><em></em></span>`;
        b.lastChild.firstChild.textContent = T('Next door: ', '隔壁：'); b.lastChild.append(nameOf(pid));
        b.setAttribute('aria-label', T('Move ', '移動 ') + T('Next door: ', '隔壁：') + nameOf(pid));
        b.title = T('Drag onto a dashed cell, or press Enter and use the arrow keys', '拖到虛線格，或按 Enter 再用方向鍵移動');
      }
      for (const pid of want) { const b = handles.get(pid); if (b) doc.body.appendChild(b); }     // keep Tab order the same as the rooms
    }
    function placeBar() {                   // under the top bar, whatever height that has (it wraps when narrow)
      if (!bar) return;
      const top = doc.getElementById('top'), tb = top ? Math.ceil(top.getBoundingClientRect().bottom) : 64;
      if (tb !== ST.barTop) { ST.barTop = tb; const ui = Number(win.getComputedStyle(doc.documentElement).getPropertyValue('--ui')) || 1; bar.style.top = Math.round((tb + 8) / ui) + 'px'; }
    }
    const inset = () => (ST.on && bar && !bar.hidden ? Math.ceil(bar.getBoundingClientRect().bottom) : 0);   // the camera keeps clear of the bar
    function sync() {                      // every frame: nothing to do (and nothing measured) unless the mode is open
      if (!ST.on) return;
      ensureHandles();
      placeBar();
      const A = arr(); if (!A) return;
      if (ST.drag && !A.at.has(ST.drag.pid)) cancelDrag();
      for (const [pid, b] of handles) {
        const p = handlePos(pid); if (!p) { b.hidden = true; continue; }
        b.hidden = false; let x = p.x, y = p.y;
        if (ST.drag && ST.drag.pid === pid && ST.drag.off) { x += ST.drag.off.dx; y += ST.drag.off.dy; }
        const bw = b.offsetWidth, bh = b.offsetHeight, vw = win.innerWidth, vh = win.innerHeight;       // a handle never leaves the window
        x = Math.min(Math.max(x, bw / 2 + 4), Math.max(bw / 2 + 4, vw - bw / 2 - 4)); y = Math.min(Math.max(y, bh / 2 + 4), Math.max(bh / 2 + 4, vh - bh / 2 - 4));
        b.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px) translate(-50%, -50%)`;
        b.classList.toggle('lifted', !!(ST.drag && ST.drag.pid === pid));
      }
    }

    /* ── lifting, aiming and dropping ───────────────────────────────────────── */
    const say = txt => { if (liveEl && ST.live !== txt) { ST.live = txt; liveEl.textContent = txt; } };
    function lift(pid, how) {
      const q = arr().at.get(pid); if (!q) return false;
      ST.drag = { pid, from: [q.c, q.r], cell: null, how, off: null }; kick(); return true;
    }
    function cancelDrag() { if (ST.drag) { ST.drag = null; ptr = null; kick(); } }
    function aimAt(gx, gy) {                // the carried room's centre is at (gx, gy) on the screen: which open cell is it over?
      const D = ST.drag, A = arr(), q0 = A.at.get(D.pid); let best = null, bd = 1e9;
      for (const [c, r] of openList(D.pid)) {
        const s = cellScreen(c, r, q0.sp), nx = Math.abs(gx - s.x) / Math.max(1, s.hw), ny = Math.abs(gy - s.y) / Math.max(1, s.hh), v = env.view();
        const d = (1 - v.m) * Math.max(nx, ny) + v.m * (nx + ny);      // a diamond in 3D, a box when flat
        if (d <= 1.12 && d < bd) { bd = d; best = [c, r]; }
      }
      D.cell = best; kick();
    }
    function drop() {
      const D = ST.drag; if (!D) return;
      const pid = D.pid, cell = D.cell; ST.drag = null; ptr = null;
      if (cell && !(cell[0] === D.from[0] && cell[1] === D.from[1])) {
        freeze(); ST.slots[pid] = [cell[0], cell[1]]; env.relayout();
        say(T(`${nameOf(pid)} placed.`, `${nameOf(pid)} 已放好。`));
      } else say('');
      kick();
    }
    function wire(b, pid) {
      b.addEventListener('pointerdown', e => {
        if (e.pointerType === 'mouse' && e.button !== 0) return;
        e.preventDefault(); e.stopPropagation(); try { b.focus({ preventScroll: true }); } catch (_) { }
        if (ST.drag) cancelDrag();
        ptr = { id: e.pointerId, pid, sx: e.clientX, sy: e.clientY, moved: false };
        try { b.setPointerCapture(e.pointerId); } catch (_) { }
      });
      b.addEventListener('keydown', e => {
        if (e.isComposing || e.keyCode === 229) return;
        const dir = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
        if (dir) { e.preventDefault(); e.stopPropagation(); if (!ST.drag || ST.drag.pid !== pid) { if (!lift(pid, 'key')) return; } stepAim(dir); }
        else if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault(); e.stopPropagation();
          if (ST.drag && ST.drag.pid === pid) drop();
          else if (lift(pid, 'key')) say(T(`Picked up ${nameOf(pid)}. Arrow keys choose a cell, Enter drops it, Esc cancels.`, `已拿起${nameOf(pid)}。方向鍵選格子，Enter 放下，Esc 取消。`));
        }
      });
      b.addEventListener('blur', () => { if (ST.drag && ST.drag.pid === pid && ST.drag.how === 'key') { cancelDrag(); say(''); } });
    }
    function stepAim(dir) {                 // an arrow key: the nearest open cell (or back to the room's own) in that direction on the screen
      const D = ST.drag, A = arr(), q0 = A.at.get(D.pid); if (!q0) return;
      const here = D.cell || D.from, cur = cellScreen(here[0], here[1], q0.sp);
      const cands = openList(D.pid).concat([D.from.slice()]).filter(([c, r]) => !(c === here[0] && r === here[1]));
      let best = null, bs = 1e9;
      for (const [c, r] of cands) {
        const s = cellScreen(c, r, q0.sp), vx = s.x - cur.x, vy = s.y - cur.y, proj = vx * dir[0] + vy * dir[1], perp = Math.abs(vx * dir[1] - vy * dir[0]);
        if (proj <= 1 || perp > proj * 1.9) continue;
        const sc = proj + 1.3 * perp; if (sc < bs) { bs = sc; best = [c, r]; }
      }
      if (best) { D.cell = best[0] === D.from[0] && best[1] === D.from[1] ? null : best; say(D.cell ? T('Over an empty cell: Enter drops it here.', '在空格上：按 Enter 放在這裡。') : T('Back at its own cell.', '回到原本的格子。')); kick(); }
    }
    win.addEventListener('pointermove', e => {
      if (!ptr || e.pointerId !== ptr.id) return;
      const dx = e.clientX - ptr.sx, dy = e.clientY - ptr.sy;
      if (!ptr.moved && Math.hypot(dx, dy) > 4) { ptr.moved = true; const p = handlePos(ptr.pid), q = arr().at.get(ptr.pid), r = env.rooms().get('peer:' + ptr.pid);
        if (!lift(ptr.pid, 'pointer')) { ptr = null; return; }
        const c0 = r ? env.t2s(r.x + r.w / 2, r.y + r.h / 2, 0) : p; ptr.c0 = c0; ptr.q = q; }
      if (ptr && ptr.moved && ST.drag) { ST.drag.off = { dx, dy }; aimAt(ptr.c0.x + dx, ptr.c0.y + dy); sync(); }
    });
    const up = e => {
      if (!ptr || e.pointerId !== ptr.id) return;
      const was = ptr; if (was.moved && ST.drag) { if (e.type === 'pointercancel') cancelDrag(); else drop(); } ptr = null;
    };
    win.addEventListener('pointerup', up); win.addEventListener('pointercancel', up);

    /* ── the mode: enter, Done, Cancel (Esc), Reset ─────────────────────────── */
    function showBar(on) {
      if (bar) bar.hidden = !on;
      if (button) { button.setAttribute('aria-pressed', String(on)); }
      doc.body.classList.toggle('arranging', on);
    }
    function freeze() {                      // computers placed by the "next empty cell" rule keep that cell while you arrange, so moving one room never shuffles the others
      const A = arr(); if (!A) return;
      for (const [pid, q] of A.at) if (!ST.slots[pid] && q.how === 'auto') ST.slots[pid] = [q.c, q.r];
    }
    function enter() {
      if (ST.on) return;
      env.prepare();
      ST.before = { saved: ST.saved ? cl(ST.saved) : null };
      ST.slots = ST.saved ? cl(ST.saved.slots) : {}; ST.on = true; ST.drag = null; ST.sig = ''; showBar(true);
      placeBar(); env.relayout(); freeze(); env.showAll(); say(''); kick();
      setTimeout(() => { try { const b = bar && bar.querySelector('[data-act="done"]'); if (b) b.focus({ preventScroll: true }); } catch (_) { } }, 0);
    }
    function leave() { ST.on = false; ST.drag = null; ptr = null; ST.sig = ''; for (const b of handles.values()) b.remove(); handles.clear(); showBar(false); env.relayout(); env.showAll(); say(''); kick(); }
    function done() {
      if (!ST.on) return;
      const A = arr(), out = {};
      if (A) for (const [pid, q] of A.at) { const s = ST.slots[pid]; out[pid] = s && ok(s[0], s[1]) ? [s[0], s[1]] : [q.c, q.r]; }   // what you dragged stays; the rest keeps the cell it stands on now
      else for (const pid of Object.keys(ST.slots)) if (env.peers().has(pid)) out[pid] = ST.slots[pid];
      const keep = env.peers().size ? out : Object.assign({}, ST.saved ? ST.saved.slots : {}, ST.slots);   // no computer known yet (the first snapshot is late): drop nothing
      ST.saved = { v: 1, slots: keep }; store.set(KEY, ST.saved); leave();
      env.toast(T('Positions saved', '位置已存好'));
    }
    function cancel() {
      if (!ST.on) return;
      ST.saved = ST.before ? ST.before.saved : ST.saved; leave();
    }
    function reset() {
      if (!ST.on) return;
      ST.saved = null; ST.slots = {}; store.set(KEY, null); leave();
      env.toast(T('Positions reset: the paired computers are back in one column', '位置已還原成預設的直排'));
    }
    function onEsc() {
      if (!ST.on) return false;
      if (ST.drag) { cancelDrag(); say(''); return true; }
      cancel(); return true;
    }

    /* ── the toolbar button, the bar on top, the page's own style ──────────── */
    function mount() {
      if (env.hidden) return;
      const st = doc.createElement('style'); st.id = 'arrStyle';
      st.textContent = `
#arrBar{position:fixed;left:50%;transform:translateX(-50%);top:64px;z-index:6;display:flex;align-items:center;gap:12px;padding:9px 12px 9px 14px;background:rgba(31,35,53,.96);border:1px solid rgba(224,175,104,.7);border-radius:14px;box-shadow:0 12px 36px rgba(0,0,0,.5);zoom:var(--ui,1);width:max-content;max-width:calc((100vw - 24px) / var(--ui,1));box-sizing:border-box}
#arrBar[hidden]{display:none}
#arrBar .ab-ic{width:30px;height:30px;border-radius:9px;background:rgba(224,175,104,.18);color:var(--yellow,#e0af68);display:grid;place-items:center;flex:none}
#arrBar .ab-ic svg{width:17px;height:17px}
#arrBar .ab-t{display:flex;flex-direction:column;gap:1px;min-width:0;flex:1 1 auto}
#arrBar .ab-t b{font-size:14px}
#arrBar .ab-t span{font-size:12px;color:var(--dim,#7c84ad)}
#arrBar .ab-b{display:flex;gap:8px;flex:none;flex-wrap:wrap;justify-content:flex-end}
#arrBar .ab-live{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
#arrange[aria-pressed=true]{background:var(--yellow,#e0af68);border-color:var(--yellow,#e0af68);color:#2b2113}
.arr-h{position:fixed;left:0;top:0;z-index:3;display:inline-flex;align-items:center;gap:5px;height:calc(26px * var(--ui,1));padding:0 calc(10px * var(--ui,1)) 0 calc(6px * var(--ui,1));border-radius:999px;border:1px solid #fff3c4;background:#e0af68;color:#2b2113;font:700 calc(12.5px * var(--ui,1))/1 var(--font,system-ui,sans-serif);white-space:nowrap;cursor:grab;touch-action:none;user-select:none;-webkit-user-select:none;box-shadow:0 3px 10px rgba(0,0,0,.4)}
.arr-h em{font-style:normal}
.arr-h i{display:inline-grid;width:calc(14px * var(--ui,1));height:calc(14px * var(--ui,1))}
.arr-h svg{width:100%;height:100%}
.arr-h:focus-visible{outline:2px solid #fff;outline-offset:2px}
.arr-h.lifted{cursor:grabbing;background:#7aa2f7;border-color:#dfe9ff;color:#16161e;z-index:5}
.arr-h[hidden]{display:none}
@media (max-width:720px),(pointer:coarse){.arr-h{height:calc(36px * var(--ui,1));padding:0 calc(14px * var(--ui,1)) 0 calc(10px * var(--ui,1))}}
@media (max-width:720px){.arr-h em{display:none}#arrBar{flex-wrap:wrap;gap:8px 10px;padding:8px 10px}#arrBar .ab-t span{font-size:11.5px}#arrBar .ab-b{width:100%;justify-content:flex-end}}`;
      doc.head.appendChild(st);
      const tools = doc.getElementById('tools');
      if (tools) {
        button = doc.createElement('button'); button.type = 'button'; button.className = 'tb'; button.id = 'arrange';
        button.setAttribute('data-guide', 'office-arrange'); button.setAttribute('aria-pressed', 'false');
        button.textContent = T('Arrange', '整理位置');
        button.title = T("Arrange where the paired computers' rooms stand around this computer", '把配對電腦的房間排到這台電腦四周想要的位置');
        button.setAttribute('aria-label', T('Arrange positions', '整理位置'));
        button.addEventListener('click', () => (ST.on ? cancel() : enter()));
        tools.insertBefore(button, doc.getElementById('zoomOut'));
      }
      bar = doc.createElement('div'); bar.id = 'arrBar'; bar.hidden = true; bar.setAttribute('role', 'region'); bar.setAttribute('aria-label', T('Arrange positions', '整理位置'));
      bar.innerHTML = `<span class="ab-ic" aria-hidden="true">${root.SFIcons ? root.SFIcons.html('grip') : ''}</span>` +
        `<div class="ab-t"><b>${esc(T('Arrange positions', '整理位置'))}</b><span>${esc(T('Drag a "Next door" room by its handle onto a dashed cell and it snaps in. This computer stays in the center.', '拖曳「隔壁」的房間到虛線格，會吸附在格子上。這台電腦固定在中央。'))}</span></div>` +
        `<div class="ab-b"><button class="tb" type="button" data-act="reset">${esc(T('Reset to default', '還原預設'))}</button><button class="tb" type="button" data-act="cancel" title="Esc">${esc(T('Cancel', '取消'))}</button><button class="tb acc" type="button" data-act="done">${esc(T('Done', '完成'))}</button></div>` +
        '<span class="ab-live" role="status" aria-live="polite"></span>';
      liveEl = bar.querySelector('.ab-live');
      bar.addEventListener('click', e => { const b = e.target.closest('button[data-act]'); if (!b) return; ({ done, cancel, reset })[b.dataset.act](); });
      doc.body.appendChild(bar);
    }
    mount();

    const api = {
      state: () => ({ on: ST.on, saved: ST.saved ? cl(ST.saved) : null, slots: cl(ST.slots), dragging: ST.drag ? { pid: ST.drag.pid, from: ST.drag.from, cell: ST.drag.cell, how: ST.drag.how } : null }),
      cells: () => { const A = arr(); if (!A) return null; const open = []; for (let c = -1; c <= A.bc; c++) for (let r = -1; r <= A.br; r++) if (A.open(c, r, 1, null)) open.push([c, r]);
        return { bc: A.bc, br: A.br, BX: A.BX, BY: A.BY, open, at: Object.fromEntries([...A.at].map(([k, v]) => [k, { c: v.c, r: v.r, sp: v.sp, how: v.how, x: +v.x.toFixed(2), y: +v.y.toFixed(2), w: v.w, h: +v.h.toFixed(2) }])) }; },
      cellPoint: (c, r, sp) => { const s = cellScreen(c, r, sp || 1); return { x: s.x, y: s.y }; },
      roomPoint: pid => { const r = env.rooms().get('peer:' + pid); if (!r) return null; const p = env.t2s(r.x + r.w / 2, r.y + r.h / 2, 0); return { x: p.x, y: p.y }; },
      buildingPoint: () => { const A = arr(); if (!A) return null; const p = env.t2s(A.BX / 2, A.BY / 2, 0); return { x: p.x, y: p.y }; },
      handlePoint: pid => { const b = handles.get(pid); if (!b || b.hidden) return null; const r = b.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height }; },
      handles: () => [...handles.values()].filter(b => !b.hidden).map(b => ({ pid: b.dataset.pid, text: b.textContent })),
      enter, done, cancel, reset,
    };
    return { active, editing, place: doPlace, bounds, floor, drawWorld, drawOverlay, sync, esc: onEsc, inset, enter, api };
  }

  root.OfficeArrange = { create, place, clean, ring1, autoCells, spanOf, CELLW, CELLH };
})(typeof window !== 'undefined' ? window : globalThis);
