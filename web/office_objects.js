/* The office's objects: furniture on the lobby wall and the ONE popup they share (office_objects.py is the other half).
 *
 * Loaded by office.html (the iframe). Nothing here knows the bookshelf or the personnel cabinet: an object is a
 * declaration {id, title, furniture, place, button, filters, sorts, actions, badge …} and the popup draws whatever the
 * host answers, so a new object (a deliverables desk, an ops TV) is a backend object plus a line of furniture, not new UI.
 *
 *   OfficeObjects.create(env) -> ctl            env: { host, tr, doc, kick, callbacks… } (office.html passes them)
 *   ctl.setObjects(list)    the declarations with their badge (the snapshot's `objects`); builds the toolbar buttons
 *   ctl.keys() / wall(k) / hull(k, t2s) / draw(x, g)   the furniture: a hit target like the whiteboard (keys are 'obj:<id>')
 *   ctl.open(id, {filters, query, item}) / close() / isOpen() / esc()
 *
 * The popup: a list on the left (search, filters, sort, groups; a row may carry a small picture, E.rowIcon), a preview on the right (title, chips, meta,
 * Markdown or a picture the host read for this entry, links), the actions at the bottom (an action may first ask for one line, or for a yes: the
 * host answers `confirm`; an answer with `refresh` makes the list read again; E.changed() tells the office the numbers moved); under 900 px wide the list is above the preview. host.objects answers:
 *   list(id, {query, filters, sort}) -> {success, groups, items, total}      item(id, itemId) -> {success, item}
 *   action(id, itemId, actionId)     -> {success, ok, message, tell, go}      md(text) -> html | null
 * and host.chat.has(key) / send(key, text, false) is how 「Ask X to update」 reaches a bot's tab (one line; the button is
 * disabled when the bot has none). A `go` or a link leads to another object, the whiteboard or calendar, or a bot's editor.
 * Texts are the host's, already in the page's language and masked; they reach the page through esc() only (Markdown through
 * the chat view's own safe renderer).
 */
(function (root) {
  'use strict';

  const SVG = {
    bookshelf: '<svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><rect x="3" y="2" width="14" height="16" rx="1.5" fill="#4b3f66"/><rect x="4.5" y="4" width="2" height="4" fill="#e0af68"/><rect x="7" y="3.5" width="2" height="4.5" fill="#7aa2f7"/><rect x="9.5" y="4.3" width="2" height="3.7" fill="#f7768e"/><rect x="12" y="3.6" width="2" height="4.4" fill="#9ece6a"/><rect x="4.5" y="11" width="2.4" height="4" fill="#bb9af7"/><rect x="7.4" y="10.4" width="2" height="4.6" fill="#7dcfff"/><rect x="10" y="11" width="2.4" height="4" fill="#e0af68"/></svg>',
    cabinet: '<svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><rect x="4" y="2" width="12" height="16" rx="1.5" fill="#6f7ba6"/><rect x="5.5" y="3.5" width="9" height="3.8" rx=".8" fill="#8d98c4"/><rect x="5.5" y="8.1" width="9" height="3.8" rx=".8" fill="#8d98c4"/><rect x="5.5" y="12.7" width="9" height="3.8" rx=".8" fill="#8d98c4"/><rect x="8.4" y="4.8" width="3.2" height="1.1" fill="#c0caf5"/><rect x="8.4" y="9.4" width="3.2" height="1.1" fill="#c0caf5"/><rect x="8.4" y="14" width="3.2" height="1.1" fill="#c0caf5"/></svg>',
    generic: '<svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><rect x="3" y="5" width="14" height="11" rx="2" fill="#6f7ba6"/><rect x="3" y="5" width="14" height="3" rx="1.5" fill="#8d98c4"/></svg>',
  };
  SVG.noticeboard = SVG.mailbox = SVG.generic;
  const BOOKS = ['#e0af68', '#7aa2f7', '#f7768e', '#9ece6a', '#bb9af7', '#7dcfff'];
  // The built-in furniture: its footprint on the lobby's back wall (tiles, depth) and its height; an extension picks a kind, never draws.
  const KINDS = { bookshelf: { w: 1.15, d: .75, h: 58 }, cabinet: { w: .95, d: .7, h: 44 }, noticeboard: { w: 1.4, d: .14, h: 50, z0: 14 },
                  mailbox: { w: .6, d: .5, h: 34 }, generic: { w: .9, d: .7, h: 36 } };
  const WALL_X0 = 6.25, GAP = .2, WALL_X1 = 13.2;        // the lobby's back wall right of the calendar; the secretary's desk starts at 9.4 in front of it
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = n => Number(n || 0).toLocaleString('en-US');

  function hullOf(pts) {                                     // convex hull (monotone chain) of screen points
    const p = pts.slice().sort((a, b) => a.x - b.x || a.y - b.y), cr = (o, a, b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
    const lo = [], up = [];
    for (const q of p) { while (lo.length >= 2 && cr(lo[lo.length - 2], lo[lo.length - 1], q) <= 0) lo.pop(); lo.push(q); }
    for (const q of p.slice().reverse()) { while (up.length >= 2 && cr(up[up.length - 2], up[up.length - 1], q) <= 0) up.pop(); up.push(q); }
    return lo.slice(0, -1).concat(up.slice(0, -1));
  }

  function create(env) {
    const E = env || {}, tr = E.tr || ((en) => en), doc = E.doc || document, host = E.host || null;
    const kick = E.kick || (() => {});
    let objs = [], sig = '', veil = null, S = null, seq = 0, typeTimer = 0, msgTimer = 0, ask = null;
    const find = id => objs.find(o => o.id === id) || null;
    const guideOf = id => id;

    // ── the furniture ────────────────────────────────────────────────────────
    function place() {                                      // id → {x0, x1, kind}: left to right along the back wall, in declaration order
      const out = {};
      let x = WALL_X0;
      for (const o of objs) {
        if (o.place === 'button') continue;                      // no furniture: only the toolbar button (the deliverables' desks are on the people's desks)
        const k = KINDS[o.furniture] || KINDS.generic;
        if (x + k.w > WALL_X1) break;
        out[o.id] = { x0: x, x1: x + k.w, k };
        x += k.w + GAP;
      }
      return out;
    }
    const keys = () => Object.keys(place()).map(id => 'obj:' + id);
    const wall = k => { const o = find(String(k).slice(4)); return o ? { name: o.title, room: 'lobby' } : null; };
    function corners(p) {
      const out = [];
      for (const x of [p.x0, p.x1]) for (const y of [0, p.k.d]) for (const z of [p.k.z0 || 0, (p.k.z0 || 0) + p.k.h]) out.push([x, y, z]);
      return out;
    }
    function hull(k, t2s) {
      const p = place()[String(k).slice(4)];
      return p ? hullOf(corners(p).map(c => t2s(c[0], c[1], c[2]))) : [];
    }
    const badge = (g, x, y, z, n) => {
      const q = g.P(x, y, z);
      g.ellipse(g.x, q.x, q.y, 7.4, 7.4, g.COL.red, '#e6e9f5', 1.4);
      g.x.font = `700 ${n > 9 ? 8 : 9.5}px ${g.FONT}`; g.x.fillStyle = '#fff'; g.x.textAlign = 'center'; g.x.textBaseline = 'middle';
      g.x.fillText(n > 9 ? '9+' : String(n), q.x, q.y + .6);
    };
    // g: {x (ctx), P, poly, box, ellipse, wq, COL, FONT, alpha, hover (key), active (id)}
    function draw(x, g0) {
      const g = Object.assign({ x }, g0), at = place();
      for (const o of objs) {
        const p = at[o.id];
        if (!p) continue;
        const k = p.k, w = p.x1 - p.x0, z0 = k.z0 || 0;
        const front = (u1, u2, z1, z2) => g.wq(p.x0, k.d, p.x1, k.d, u1, u2, z0 + z1, z0 + z2);
        if (o.furniture === 'bookshelf') {
          g.box(x, p.x0, 0, w, k.d, 0, k.h, { t: '#6a5a86', l: '#4b3f66', r: '#3a3052' }, 'rgba(192,202,245,.14)');
          g.poly(x, front(.06, .94, 3, k.h - 4), '#2e2742');
          for (let r = 0; r < 3; r++) {
            const z1 = 5 + r * 17.5;
            g.poly(x, front(.06, .94, z1 + 14.4, z1 + 15.6), '#5b4d79');
            for (let i = 0, u = .1; i < 7 && u < .86; i++) {
              const bw = .075 + ((i * 7 + r * 3) % 4) * .012, bh = 9 + ((i * 5 + r * 2) % 5) * 1.1;
              g.poly(x, front(u, u + bw, z1, z1 + bh), BOOKS[(i + r * 2) % BOOKS.length]);
              u += bw + .012;
            }
          }
        } else if (o.furniture === 'cabinet') {
          g.box(x, p.x0, 0, w, k.d, 0, k.h, { t: '#8d98c4', l: '#6f7ba6', r: '#566086' }, 'rgba(255,255,255,.2)');
          for (let r = 0; r < 3; r++) {
            const z1 = 3 + r * 13.6;
            g.poly(x, front(.08, .92, z1, z1 + 11.6), '#7e89b4', 'rgba(255,255,255,.18)', 1);
            g.poly(x, front(.36, .64, z1 + 6.4, z1 + 8), '#dfe5ff');
            g.poly(x, front(.2, .5, z1 + 2, z1 + 4), 'rgba(20,24,40,.35)');
          }
        } else if (o.furniture === 'noticeboard') {
          g.box(x, p.x0, 0, w, k.d, z0, k.h, { t: '#a98f6a', l: '#8a7352', r: '#6f5c42' });
          g.poly(x, front(.05, .95, 4, k.h - 4), '#caa979');
          for (let i = 0; i < 5; i++) g.poly(x, front(.12 + i * .17, .24 + i * .17, 10 + (i % 2) * 12, 22 + (i % 2) * 12), BOOKS[i % BOOKS.length]);
        } else {
          g.box(x, p.x0, 0, w, k.d, 0, k.h, { t: '#8d98c4', l: '#6f7ba6', r: '#566086' }, 'rgba(255,255,255,.18)');
          g.poly(x, front(.2, .8, k.h * .55, k.h * .7), 'rgba(20,24,40,.4)');
        }
        if (o.badge > 0) badge(g, p.x1, k.d, z0 + k.h - 1, o.badge);
        if (g.hover === 'obj:' + o.id || (S && S.id === o.id)) {
          const on = !!(S && S.id === o.id);
          g.poly(x, hullOf(corners(p).map(c => g.P(c[0], c[1], c[2]))), null, on ? g.COL.blue : 'rgba(192,202,245,.7)', on ? 2 : 1.6);
        }
      }
    }

    // ── the toolbar buttons (one per object that wants one) ──────────────────
    function paintButtons() {
      const bar = doc.getElementById('tools');
      if (!bar) return;
      for (const b of Array.from(bar.querySelectorAll('button[data-obj]'))) if (!find(b.dataset.obj) || !find(b.dataset.obj).button) b.remove();
      let anchor = doc.getElementById('resetSeats');
      for (const o of objs) {
        if (!o.button) continue;
        let b = bar.querySelector(`button[data-obj="${o.id}"]`);
        if (!b) {
          b = doc.createElement('button');
          b.type = 'button'; b.className = 'tb'; b.dataset.obj = o.id;
          if (o.id === 'bookshelf') b.setAttribute('data-guide', 'office-bookshelf');
          else if (o.id === 'personnel') b.setAttribute('data-guide', 'office-personnel');
          else if (o.id === 'deliverables') b.setAttribute('data-guide', 'office-deliverables');
          b.addEventListener('click', () => (S && S.id === o.id ? close() : open(o.id)));
          bar.insertBefore(b, anchor);
        }
        b.textContent = o.short || o.title;              // a short word on the toolbar (人事), the full name on the popup (人事檔案櫃)
        b.title = o.subtitle || o.title;
        b.setAttribute('aria-label', o.title + (o.badge ? ` (${o.badge})` : ''));
        b.classList.toggle('has-badge', o.badge > 0);
        if (o.badge > 0) b.dataset.badge = o.badge > 9 ? '9+' : String(o.badge); else delete b.dataset.badge;
      }
    }
    function setObjects(list) {
      const next = (Array.isArray(list) ? list : []).filter(o => o && typeof o.id === 'string').map(o => Object.assign({ badge: 0, filters: [], sorts: [], actions: [] }, o));
      const s = JSON.stringify(next);
      if (s === sig) return;
      sig = s; objs = next;
      paintButtons();
      if (S && !find(S.id)) close();
      kick();
    }

    // ── the popup ────────────────────────────────────────────────────────────
    const T = tr;
    function ensureVeil() {
      if (veil) return veil;
      veil = doc.createElement('div');
      veil.id = 'objpop'; veil.className = 'oo-veil'; veil.hidden = true;
      veil.innerHTML =
        '<section class="oo-box" role="dialog" aria-modal="true" aria-labelledby="ooTitle">' +
        '<header class="oo-head"><span class="oo-ic" id="ooIc" aria-hidden="true"></span><div class="oo-t"><h2 id="ooTitle"></h2><p id="ooSub"></p></div>' +
        `<button class="oo-x" type="button" id="ooClose" title="${esc(T('Close (Esc)', '關起來（Esc）'))}" aria-label="${esc(T('Close', '關起來'))}">${root.SFIcons.html('close')}</button></header>` +
        '<div class="oo-bar" id="ooBar"></div>' +
        '<div class="oo-main"><nav class="oo-list" id="ooList" aria-label="list"></nav><article class="oo-view" id="ooView"></article></div>' +
        '<div class="oo-ask" id="ooAsk" hidden></div>' +
        '<footer class="oo-foot"><span class="oo-note" id="ooNote"></span><span class="oo-msg" id="ooMsg" role="status" aria-live="polite"></span><div class="oo-acts" id="ooActs"></div></footer></section>';
      doc.body.appendChild(veil);
      veil.addEventListener('click', onClick);
      veil.addEventListener('input', onInput);
      veil.addEventListener('change', onInput);
      veil.addEventListener('keydown', onKey);
      return veil;
    }
    const $ = sel => veil.querySelector(sel);

    function open(id, opts) {
      const o = find(id);
      if (!o) return false;
      opts = opts || {};
      if (E.beforeOpen) E.beforeOpen(o);
      ensureVeil();
      const was = S && S.id === id;
      S = { id, query: String(opts.query || ''), filters: Object.assign({}, opts.filters || {}), sort: (o.sorts[0] || [''])[0], facets: was ? S.facets : {},
            list: null, sel: opts.item || '', item: null, error: '', loading: true, busy: false };
      veil.hidden = false;
      veil.dataset.obj = id;
      $('#ooIc').innerHTML = SVG[o.furniture] || SVG.generic;
      $('#ooTitle').textContent = o.title;
      $('#ooSub').textContent = o.subtitle || '';
      $('#ooNote').textContent = o.readonly_note || '';
      $('#ooMsg').textContent = ''; hideAsk();
      paintBar(o);
      paintView();
      paintActs();
      kick();
      reload(true);
      setTimeout(() => { const s = $('#ooSearch'); if (s) try { s.focus({ preventScroll: true }); } catch (_) {} }, 0);
      return true;
    }
    function close() {
      if (!S) return;
      S = null; seq++; ask = null;
      clearTimeout(typeTimer); clearTimeout(msgTimer);
      if (veil) { veil.hidden = true; veil.querySelector('#ooList').innerHTML = ''; veil.querySelector('#ooView').innerHTML = ''; }
      kick();
      if (E.onClose) E.onClose();
    }
    // Esc: the popup closes (the search box first clears itself); true when it took the key
    function esc2() {
      if (!S) return false;
      if (ask) { hideAsk(); return true; }                    // an open question (one line, a yes) is put away first
      const s = $('#ooSearch');
      if (s && doc.activeElement === s && s.value) { s.value = ''; S.query = ''; reload(false); return true; }
      close();
      return true;
    }

    function paintBar(o) {
      const filt = o.filters.map(f => {
        const opts = f.options && f.options.length ? f.options : ((S.facets[f.id] || []).map(g => [g.id, g.title]));
        return `<label class="oo-sel"><span>${esc(f.label)}</span><select data-f="${esc(f.id)}" aria-label="${esc(f.label)}"><option value="">${esc(T('All', '全部'))}</option>` +
          opts.map(p => `<option value="${esc(p[0])}"${S.filters[f.id] === p[0] ? ' selected' : ''}>${esc(p[1])}</option>`).join('') + '</select></label>';
      }).join('');
      const sort = o.sorts.length ? `<label class="oo-sel"><span>${esc(T('Sort', '排序'))}</span><select data-sort aria-label="${esc(T('Sort', '排序'))}">` +
        o.sorts.map(p => `<option value="${esc(p[0])}"${S.sort === p[0] ? ' selected' : ''}>${esc(p[1])}</option>`).join('') + '</select></label>' : '';
      $('#ooBar').innerHTML = `<label class="oo-search"><span class="oo-mag" aria-hidden="true">⌕</span><input id="ooSearch" type="search" autocomplete="off" spellcheck="false" placeholder="${esc(o.search_hint || T('Search…', '搜尋…'))}" aria-label="${esc(o.search_hint || T('Search', '搜尋'))}" value="${esc(S.query)}"></label>${filt}${sort}`;
    }
    function facetsFrom(o, r) {                              // a filter with no options of its own takes the list's groups (seen unfiltered)
      if (Object.keys(S.filters).some(k => S.filters[k])) return;
      let changed = false;
      for (const f of o.filters) if (!(f.options && f.options.length)) { S.facets[f.id] = (r.groups || []).map(g => ({ id: g.id, title: g.title })); changed = true; }
      if (changed) { const keep = { q: ($('#ooSearch') || {}).value, f: doc.activeElement && doc.activeElement.id }; paintBar(o); const s = $('#ooSearch'); if (s) s.value = keep.q || ''; if (keep.f === 'ooSearch' && s) s.focus(); }
    }
    async function reload(first) {
      if (!S || !host || !host.objects) return;
      const my = ++seq, id = S.id, o = find(id);
      S.loading = true;
      let r = null;
      try { r = await host.objects.list(id, { query: S.query, filters: S.filters, sort: S.sort }); } catch (_) { r = null; }
      if (!S || my !== seq || S.id !== id) return;
      S.loading = false;
      if (!r || r.success === false) { S.list = null; S.error = (r && r.message) || T('This object is not available right now.', '這個物件現在打不開。'); }
      else {
        S.list = r; S.error = '';
        if (Object.keys(S.filters).some(k => S.filters[k])) {            // opened with a filter (a link from another object): the other choices still need to be known
          if (first && o.filters.some(f => !(f.options && f.options.length))) {
            let all = null; try { all = await host.objects.list(id, {}); } catch (_) { all = null; }
            if (S && S.id === id && all && all.success !== false) { const f = S.filters; S.filters = {}; facetsFrom(o, all); S.filters = f; paintBar(o); }
          }
        } else facetsFrom(o, r);
      }
      paintList();
      if (S.sel && first) pick(S.sel); else if (S.sel && !itemIn(S.sel)) { S.sel = ''; S.item = null; paintView(); paintActs(); }
    }
    const itemIn = iid => !!(S && S.list && S.list.items.some(i => i.id === iid));
    function paintList() {
      const L = $('#ooList'), o = find(S.id);
      if (S.error) { L.innerHTML = `<div class="oo-empty"><b>${esc(S.error)}</b><button class="oo-btn" data-act="retry" type="button">${esc(T('Try again', '再試一次'))}</button></div>`; return; }
      if (!S.list) { L.innerHTML = `<div class="oo-empty">${esc(T('Reading…', '讀取中…'))}</div>`; return; }
      const items = S.list.items;
      if (!items.length) { L.innerHTML = `<div class="oo-empty">${esc(S.query || Object.values(S.filters).some(Boolean) ? T('Nothing matches.', '沒有符合的。') : (o.empty || T('Nothing here yet.', '這裡還沒有東西。')))}</div>`; return; }
      let html = '';
      const groups = S.list.groups.length ? S.list.groups : [{ id: '', title: '' }];
      for (const g of groups) {
        const rows = items.filter(i => (i.group || '') === g.id);
        if (!rows.length) continue;
        if (g.title) html += `<div class="oo-grp" role="presentation"><span>${esc(g.title)}</span>${g.tag ? `<em>${esc(g.tag)}</em>` : ''}<b>${rows.length}</b></div>`;
        html += rows.map(rowHtml).join('');
      }
      const top = L.scrollTop;
      L.innerHTML = `<div class="oo-list-in" role="listbox">${html}</div>` + (S.list.total > items.length ? `<div class="oo-more">${esc(T(`Showing ${items.length} of ${S.list.total}`, `顯示 ${items.length} / ${S.list.total}`))}</div>` : '') +
        (S.list.note ? `<div class="oo-more">${esc(S.list.note)}</div>` : '');
      L.scrollTop = top;
    }
    function rowHtml(i) {
      const meter = i.limit > 0 ? `<i class="oo-meter${i.tone ? ' ' + i.tone : ''}" aria-hidden="true"><u style="width:${Math.max(2, Math.min(100, Math.round(i.size / i.limit * 100)))}%"></u></i>` : '';
      const tags = (i.tags || []).map(t => `<span class="oo-tag ${esc(t.tone || '')}">${esc(t.label)}</span>`).join('');
      let pic = '';
      try { pic = E.rowIcon ? E.rowIcon(find(S.id), i) || '' : ''; } catch (_) { pic = ''; }   // a small picture of the entry's kind (the host's drawing; markup of its own, never the entry's text)
      return `<button type="button" class="oo-it${pic ? ' has-pic' : ''}${S.sel === i.id ? ' on' : ''}${i.tone ? ' ' + esc(i.tone) : ''}" role="option" aria-selected="${S.sel === i.id}" data-id="${esc(i.id)}">` +
        (pic ? `<span class="oo-pic" aria-hidden="true">${pic}</span>` : '') + `<span class="oo-it-t">${esc(i.title)}</span>${tags ? `<span class="oo-tags">${tags}</span>` : ''}` +
        (i.subtitle ? `<span class="oo-it-s">${esc(i.subtitle)}</span>` : '') + meter + '</button>';
    }
    async function pick(iid) {
      if (!S || !host || !host.objects) return;
      const my = ++seq, id = S.id;
      S.sel = iid; S.item = null; S.itemError = '';
      paintList();
      paintView(true); paintActs();
      let r = null;
      try { r = await host.objects.item(id, iid); } catch (_) { r = null; }
      if (!S || my !== seq || S.id !== id || S.sel !== iid) return;
      if (!r || r.success === false || !r.item) { S.item = null; S.itemError = (r && r.message) || T('That entry is gone.', '這一筆已經不在了。'); }
      else S.item = r.item;
      paintView(); paintActs();
      if (S.item && S.item.refresh) changed(false);                 // seeing it changed the list (a 看過 mark, the numbers elsewhere)
    }
    function md(text) {
      let html = null;
      try { html = host && host.md ? host.md(String(text || '')) : null; } catch (_) { html = null; }
      return html != null ? html : `<pre class="oo-plain">${esc(text)}</pre>`;
    }
    function paintView(loading) {
      const V = $('#ooView');
      if (!S) return;
      if (loading && !S.item) { V.innerHTML = `<div class="oo-view-in"><div class="oo-hint">${esc(T('Reading…', '讀取中…'))}</div></div>`; return; }
      if (S.itemError) { V.innerHTML = `<div class="oo-view-in"><div class="oo-hint"><b>${esc(S.itemError)}</b></div></div>`; return; }
      const it = S.item;
      if (!it) { V.innerHTML = `<div class="oo-view-in"><div class="oo-hint">${esc(T('Pick something on the left to read it.', '從左邊挑一項來看。'))}</div></div>`; return; }
      const chips = (it.chips || []).map(c => `<span class="oo-chip ${esc(c.tone || '')}">${esc(c.text)}</span>`).join('');
      const meta = (it.meta || []).map(m => `<span><i>${esc(m[0])}</i> ${esc(m[1])}</span>`).join('');
      const linkHtml = (l, n) => `<button type="button" class="oo-link${l.tone ? ' ' + esc(l.tone) : ''}" data-link="${n}"><span>${esc(l.label)}</span>${l.note ? `<em>${esc(l.note)} ›</em>` : '<em>›</em>'}</button>`;
      // A link with a `section` goes where the markdown has a line {{links:<section>}} (under its own heading); the others,
      // and any whose marker is missing, at the end. data-link stays the index into it.links.
      const placed = new Set();
      let body = md(it.markdown).replace(/<p>\s*\{\{links:([a-z0-9_-]{1,40})\}\}\s*<\/p>/g, (m, sec) => {
        const here = (it.links || []).map((l, n) => [l, n]).filter(([l]) => l.section === sec);
        here.forEach(([, n]) => placed.add(n));
        return here.length ? `<div class="oo-links oo-links-in">${here.map(([l, n]) => linkHtml(l, n)).join('')}</div>` : '';
      });
      body = body.replace(/\{\{links:[a-z0-9_-]{1,40}\}\}/g, '');    // a marker the renderer did not put on a line of its own
      const links = (it.links || []).map((l, n) => (placed.has(n) ? '' : linkHtml(l, n))).join('');
      V.innerHTML = '<div class="oo-view-in">' +
        `<h3>${esc(it.title)}</h3>${it.subtitle ? `<p class="oo-sub">${esc(it.subtitle)}</p>` : ''}` +
        (chips ? `<div class="oo-chips">${chips}</div>` : '') + (meta ? `<div class="oo-meta">${meta}</div>` : '') +
        (it.image ? `<div class="oo-shot"><img src="${esc(it.image)}" alt=""></div>` : '') +
        (it.markdown ? `<div class="oo-md">${body}</div>` : '') +
        (it.note ? `<p class="oo-fine">${esc(it.note)}</p>` : '') +
        (links ? `<div class="oo-links">${links}</div>` : '') + '</div>';
      V.scrollTop = 0;
    }
    function actionLabel(a, it) { return String((it && it.action_labels && it.action_labels[a.id]) || a.label || a.id).replace('{owner}', (it && it.owner) || ''); }
    function paintActs() {
      const A = $('#ooActs'), o = S && find(S.id);
      if (!S || !o) return;
      const it = S.item;
      const acts = it ? o.actions.filter(a => (it.actions || []).indexOf(a.id) >= 0) : [];
      A.innerHTML = acts.map(a => {
        const noTab = a.needs_tab && !(it.owner_key && E.hasTab && E.hasTab(it.owner_key));
        const why = noTab ? T('It has no open tab right now (resting): wake it first.', '它現在沒有開著的分頁（在休息）：先叫醒它。') : '';
        const hint = (it.action_hints || {})[a.id] || '';
        return `<button type="button" class="oo-btn${a.primary ? ' primary' : ''}" data-action="${esc(a.id)}"${noTab ? ' disabled' : ''}${why || hint ? ` title="${esc(why || hint)}"` : ''}>${esc(actionLabel(a, it))}</button>`;
      }).join('');
    }
    function say(text, bad) {
      const m = $('#ooMsg');
      if (!m) return;
      m.textContent = text || ''; m.classList.toggle('bad', !!bad);
      clearTimeout(msgTimer);
      if (text) msgTimer = setTimeout(() => { if (m) m.textContent = ''; }, 6000);
    }
    // an action that needs one line from the person first, or a yes (the host's `confirm`): a bar above the buttons
    function hideAsk() { ask = null; const b = veil && veil.querySelector('#ooAsk'); if (b) { b.hidden = true; b.innerHTML = ''; } }
    function showAsk(aid, a, conf) {
      const bar = $('#ooAsk');
      ask = { aid, conf: conf || null };
      bar.hidden = false;
      bar.innerHTML = conf
        ? `<span class="oo-ask-t" role="alert">${esc(conf.text)}</span><button type="button" class="oo-btn primary" data-ask="yes">${esc(conf.label || T('OK', '好'))}</button><button type="button" class="oo-btn" data-ask="no">${esc(T('Cancel', '取消'))}</button>`
        : `<label class="oo-ask-l" for="ooAskIn">${esc(actionLabel(a, S.item))}</label><input id="ooAskIn" type="text" maxlength="300" autocomplete="off" placeholder="${esc(a.input)}" aria-label="${esc(a.input)}"><button type="button" class="oo-btn primary" data-ask="go">${esc(T('Send', '送出'))}</button><button type="button" class="oo-btn" data-ask="no">${esc(T('Cancel', '取消'))}</button>`;
      const inp = bar.querySelector('input');
      try { (inp || bar.querySelector('[data-ask="yes"]')).focus({ preventScroll: true }); } catch (_) {}
    }
    async function changed(keep) {                           // the host changed what is listed: read it again, keep the entry open when it is still there
      if (E.changed) { try { E.changed(); } catch (_) {} }
      if (!S) return;
      const sel = S.sel;
      await reload(false);
      if (S && sel && keep !== false && itemIn(sel)) await pick(sel);
    }
    async function act(aid, args) {
      if (!S || !S.item || S.busy || !host || !host.objects) return;
      const id = S.id, iid = S.sel, o = find(id), a = o.actions.find(x => x.id === aid), it = S.item;
      if (a && a.input && !args) { showAsk(aid, a, null); return; }
      hideAsk();
      S.busy = true;
      let r = null;
      try { r = await host.objects.action(id, iid, aid, args || {}); } catch (_) { r = null; }
      if (S) S.busy = false;
      if (!S || S.id !== id) return;
      if (!r || r.success === false || r.ok === false) { say((r && r.message) || T('That did not work.', '這個沒成功。'), true); if (r && r.refresh) changed(); return; }
      if (r.confirm) { showAsk(aid, a, r.confirm); return; }
      if (r.tell) {
        let sent = null;
        try { sent = E.send ? await E.send(r.tell.to, r.tell.text) : null; } catch (_) { sent = null; }
        if (!S) return;
        if (sent && sent.success !== false) {
          if (r.tell.after) { try { await host.objects.action(id, iid, r.tell.after.action, r.tell.after.args || {}); } catch (_) {} }   // e.g. a note on the task, only now that the line went in
          say(T(`Sent to ${it.owner || 'the bot'}: it is in their tab.`, `已傳給${it.owner || '那個 bot'}，在它的分頁裡。`));
        } else if (sent && sent.code === 'draft') say(T('Their tab has an unsent draft: it was not sent. Clear it in the tab first.', '它的分頁裡有還沒送出的草稿，這句沒送。先到分頁處理草稿。'), true);
        else say((sent && sent.message) || T('Could not send: it has no open tab.', '沒送出去：它沒有開著的分頁。'), true);
        return;
      }
      if (r.go) { go(r.go); return; }
      const msg = r.message || (a ? actionLabel(a, it) : '');
      if (r.refresh) await changed();
      say(msg);
    }
    // where a link or a `go` leads: another object (with a filter), a wall view, a bot's tab or its editor
    function go(l) {
      if (l.object) { open(l.object, { filters: l.filters, query: l.query, item: l.item }); return; }
      if (l.settings) { if (E.editBot && E.editBot(l.settings)) { close(); return; } say(T('The editor could not be opened here.', '這裡打不開設定頁。'), true); return; }
      close();
      if (l.view && E.view) E.view(l.view, l.task);
      else if (l.follow && E.follow) E.follow(l.follow);
    }

    // ── events ───────────────────────────────────────────────────────────────
    function onClick(ev) {
      if (!S) return;
      if (ev.target === veil || ev.target.closest('#ooClose')) { close(); return; }
      const row = ev.target.closest('.oo-it');
      if (row) { pick(row.dataset.id); return; }
      const lk = ev.target.closest('[data-link]');
      if (lk && S.item) { const l = S.item.links[+lk.dataset.link]; if (l) go(l); return; }
      const sk = ev.target.closest('[data-ask]');
      if (sk && ask) {
        const cur = ask, a = find(S.id).actions.find(x => x.id === cur.aid);
        if (sk.dataset.ask === 'no') { hideAsk(); return; }
        if (sk.dataset.ask === 'yes') { act(cur.aid, cur.conf.args || {}); return; }
        const v = (veil.querySelector('#ooAskIn') || {}).value || '';
        if (!v.trim()) { say(T('Write one line first.', '先寫一句話。'), true); return; }
        if (a) act(cur.aid, { text: v.trim() });
        return;
      }
      const ac = ev.target.closest('[data-action]');
      if (ac && !ac.disabled) { act(ac.dataset.action); return; }
      const a = ev.target.closest('a.cm-link[data-url]');
      if (a) { ev.preventDefault(); if (E.openUrl) E.openUrl(a.dataset.url); return; }
      if (ev.target.closest('[data-act="retry"]')) reload(false);
    }
    function onInput(ev) {
      if (!S) return;
      const t = ev.target;
      if (t.id === 'ooSearch') { S.query = t.value; clearTimeout(typeTimer); typeTimer = setTimeout(() => reload(false), 180); return; }
      if (t.matches('select[data-f]')) { S.filters[t.dataset.f] = t.value; reload(false); return; }
      if (t.matches('select[data-sort]')) { S.sort = t.value; reload(false); }
    }
    function onKey(ev) {
      if (!S) return;
      if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); esc2(); return; }
      if (ev.key === 'Enter' && ev.target.id === 'ooAskIn' && !ev.isComposing && ev.keyCode !== 229) { ev.preventDefault(); const b = veil.querySelector('[data-ask="go"]'); if (b) b.click(); return; }
      if ((ev.key === 'ArrowDown' || ev.key === 'ArrowUp') && !ev.target.matches('select, textarea, input[type=text]')) {
        const rows = Array.from(veil.querySelectorAll('.oo-it'));
        if (!rows.length) return;
        ev.preventDefault();
        const cur = rows.findIndex(r => r.dataset.id === S.sel), at = Math.max(0, Math.min(rows.length - 1, cur + (ev.key === 'ArrowDown' ? 1 : -1)));
        pick(rows[at].dataset.id);
        const el = veil.querySelector(`.oo-it[data-id="${CSS.escape(rows[at].dataset.id)}"]`);
        if (el) el.scrollIntoView({ block: 'nearest' });
      }
    }

    // what an object said per person (its summary's `by_owner`): the office draws a pile on each desk from it
    const byOwner = (id, key) => { const o = find(id); return (o && o.by_owner && o.by_owner[key]) || null; };
    return { setObjects, keys, wall, hull, draw, open, close, esc: esc2, isOpen: () => !!S, find, guideOf, byOwner,
             state: () => ({ open: S ? S.id : null, objects: objs.map(o => ({ id: o.id, title: o.title, badge: o.badge, furniture: o.furniture, place: o.place, onWall: Object.prototype.hasOwnProperty.call(place(), o.id) })) }) };
  }

  // A stand-in for the page's own host when the office is opened alone (demo data; nothing here is real).
  function demoHost(tr) {
    const T = tr || ((en) => en);
    const items = [
      { id: 'a', group: 'demo', title: T('Memory · MEMORY.md', '記憶 · MEMORY.md'), subtitle: '4,500 / 6,000', size: 4500, limit: 6000, tone: 'warn', tags: [{ label: T('Almost full', '快滿了'), tone: 'warn' }] },
      { id: 'b', group: 'demo', title: T('Skill · demo-release', '技能 · demo-release'), subtitle: T('Release steps', '發版步驟') }];
    return { objects: {
      async list(id, a) { const q = String((a && a.query) || '').toLowerCase(); return { success: true, groups: [{ id: 'demo', title: T('Demo bot', '示範 bot'), count: 2 }], items: items.filter(i => !q || i.title.toLowerCase().includes(q)), total: 2 }; },
      async item(id, iid) { const i = items.find(x => x.id === iid); return i ? { success: true, item: { title: i.title, markdown: '# Demo\n\n- ' + T('Demo data only.', '只是示範資料。'), meta: [], chips: [], links: [], actions: [] } } : { success: false }; },
      async action() { return { success: true, ok: true, message: '' }; },
    }, md: null };
  }
  const DEMO_OBJECTS = tr => [
    { id: 'bookshelf', title: tr('Bookshelf', '書櫃'), furniture: 'bookshelf', place: 'lobby:wall', button: true, badge: 2, filters: [], sorts: [], actions: [] },
    { id: 'personnel', title: tr('Personnel cabinet', '人事檔案櫃'), short: tr('Personnel', '人事'), furniture: 'cabinet', place: 'lobby:wall', button: true, badge: 0, filters: [], sorts: [], actions: [] }];

  root.OfficeObjects = { create, demoHost, DEMO_OBJECTS, KINDS, hullOf };
})(typeof window !== 'undefined' ? window : globalThis);
