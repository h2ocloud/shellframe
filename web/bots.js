/* ShellFrame bots (settings.experimental_bots) — see docs/bots.md.
 *
 * Atomic layout, so the look can be tuned later without touching behaviour:
 *   atoms      SHAPES (body + where the eyes sit), PALETTE, the eye/mark parts
 *   molecule   faceSvg(): one avatar; its expression follows the .busy-dot state
 *              classes index.html's 400 ms loop already sets (see bots.css)
 *   organisms  sidebar rows (name + latest line), sleeping bots, the primary-bot
 *              card, the avatar picker, the bot editor, close-with-memory dialog,
 *              the external-bot panel
 *
 * index.html exposes one facade, window.__sfHost, and calls four hooks:
 * SFBots.face(sid), afterSidebar(container), afterTabs(container) and
 * interceptClose(sid). With the flag off every hook returns '' / false / nothing.
 */
(function () {
  'use strict';

  // ── atoms ──────────────────────────────────────────────────────────────
  // viewBox 0 0 100 100. `eyes` is the centre of the eye pair.
  const SHAPES = {
    round:    { name: '圓', eyes: [54, 48], body: '<circle class="body" cx="50" cy="51" r="42"/>' },
    pill:     { name: '膠囊', eyes: [55, 51], body: '<rect class="body" x="6" y="26" width="88" height="50" rx="25"/>' },
    squircle: { name: '方圓', eyes: [54, 48], body: '<rect class="body" x="10" y="11" width="80" height="80" rx="25"/>' },
    blob:     { name: '軟糖', eyes: [54, 47], body: '<path class="body" d="M51 8c22 0 41 15 41 40 0 27-18 44-43 44C25 92 8 76 8 52 8 27 28 8 51 8z"/>' },
    drop:     { name: '水滴', eyes: [52, 63], body: '<path class="body" d="M50 9C57 25 82 44 82 63a32 32 0 0 1-64 0C18 44 43 25 50 9z"/>' },
    hex:      { name: '六角', eyes: [53, 50], body: '<path class="body-round" stroke-width="12" transform="rotate(-8 50 50)" d="M50 13L82 31.5L82 68.5L50 87L18 68.5L18 31.5Z"/>' },
    cloud:    { name: '雲', eyes: [55, 57], body: '<g class="body"><circle cx="30" cy="58" r="20"/><circle cx="52" cy="44" r="25"/><circle cx="72" cy="58" r="20"/><rect x="30" y="54" width="42" height="24"/></g>' },
    ghost:    { name: '小幽靈', eyes: [54, 47], body: '<path class="body" d="M16 50a34 34 0 0 1 68 0v34q-5.67 6-11.33 0t-11.33 0t-11.33 0t-11.33 0t-11.33 0t-11.35 0z"/>' },
    tilt:     { name: '歪方', eyes: [54, 49], body: '<rect class="body" x="13" y="14" width="74" height="74" rx="22" transform="rotate(-14 50 51)"/>' },
    star:     { name: '星', eyes: [50, 55], body: '<path class="body-round" stroke-width="14" d="M50 16L65.9 32.2L86.1 42.3L75.7 62.3L72.3 84.7L50 81L27.7 84.7L24.3 62.3L13.9 42.3L34.1 32.2Z"/>' },
    egg:      { name: '蛋', eyes: [53, 48], body: '<ellipse class="body" cx="50" cy="52" rx="41" ry="33" transform="rotate(-24 50 52)"/>' },
    tri:      { name: '三角', eyes: [50, 62], body: '<path class="body-round" stroke-width="14" d="M50 16L88 80L12 80Z"/>' },
  };
  const PALETTE = {
    red: '#F04E5E', orange: '#F57C2B', amber: '#F0A934', yellow: '#DDBB3C',
    green: '#3FB97A', teal: '#1FA99E', sky: '#38B2E8', blue: '#3E7BF6',
    indigo: '#6A5CF0', purple: '#A15BF0', pink: '#F0679E', brown: '#A8714C',
    gray: '#8B90A0', ink: '#23262F',
  };
  const DEFAULT_COLORS = ['red', 'orange', 'amber', 'green', 'teal', 'sky',
                          'blue', 'indigo', 'purple', 'pink', 'brown', 'yellow'];
  const STATES = [['', '發呆'], ['sig-working', '運作'], ['sig-decision', '問號'],
                  ['sig-stuck', '卡住'], ['sig-done', '完成']];

  const MARKS =
    '<g class="q"><circle cx="83" cy="15" r="15"/><text x="83" y="23">?</text></g>' +
    '<path class="sweat" d="M17 15c3 5 6.5 8.5 6.5 12a6.5 6.5 0 0 1-13 0c0-3.5 3.5-7 6.5-12z"/>' +
    '<text class="zz" x="85" y="22">z</text>' +
    '<rect class="pen" x="66" y="70" width="26" height="7" rx="2" transform="rotate(-35 79 73)"/>' +
    '<g class="badge"><circle cx="82" cy="82" r="14"/>' +
    '<path d="M82 74.5l2.2 4.6 5 .6-3.7 3.4 1 5-4.5-2.5-4.5 2.5 1-5-3.7-3.4 5-.6z"/></g>';

  function hash(s) {
    let h = 0x811c9dc5;
    for (const ch of String(s)) { h ^= ch.codePointAt(0); h = Math.imul(h, 0x01000193) >>> 0; }
    return h >>> 0;
  }
  function defaultAvatar(seed) {
    const h = hash(seed || '');
    const keys = Object.keys(SHAPES);
    return { shape: keys[h % keys.length], color: DEFAULT_COLORS[(h >>> 8) % DEFAULT_COLORS.length] };
  }
  function colorOf(c) {
    return PALETTE[c] || (/^#[0-9a-f]{6}$/i.test(c || '') ? c : PALETTE.gray);
  }
  function cleanAvatar(av, seed) {
    const d = defaultAvatar(seed);
    return { shape: SHAPES[av && av.shape] ? av.shape : d.shape,
             color: (av && (PALETTE[av.color] || /^#[0-9a-f]{6}$/i.test(av.color || ''))) ? av.color : d.color };
  }

  // ── molecule: one face ─────────────────────────────────────────────────
  // opts: seed (desynchronises blinking), primary, asleep, writing
  function faceSvg(av, opts) {
    opts = opts || {};
    const a = cleanAvatar(av, opts.seed);
    const S = SHAPES[a.shape];
    const cx = S.eyes[0], cy = S.eyes[1], L = cx - 8, R = cx + 8;
    // Wall-clock phase: a row rebuilt a second later continues the same loop
    // instead of restarting it (the sidebar re-renders every few seconds).
    const t = -((Date.now() + (hash(opts.seed || a.shape) % 9973)) % 600000);
    const eye = x => `<rect class="eye" x="${x - 4}" y="${cy - 7.5}" width="8" height="15" rx="4"/>`;
    // Narrow arcs so two eyes read as ^ ^ (or closed ‿ ‿), not as one wavy line.
    const arc = (x, y1, y2) => `M${x - 4.5} ${cy + y1}Q${x} ${cy + y2} ${x + 4.5} ${cy + y1}`;
    const cls = ['sfb', a.color === 'ink' ? 'ink' : '', opts.primary ? 'is-primary' : '',
                 opts.asleep ? 'is-asleep' : '', opts.writing ? 'is-writing' : ''].join(' ').trim();
    return `<svg class="${cls}" viewBox="0 0 100 100" data-shape="${a.shape}" ` +
      `style="--c:${colorOf(a.color)};--t:${t}ms" aria-hidden="true"><g class="rig">${S.body}` +
      `<g class="eyes">${eye(L)}${eye(R)}</g>` +
      `<path class="happy" d="${arc(L, 3, -9)} ${arc(R, 3, -9)}"/>` +
      `<path class="shut" d="${arc(L, -1, 6)} ${arc(R, -1, 6)}"/></g>${MARKS}</svg>`;
  }

  // ── state ──────────────────────────────────────────────────────────────
  let enabled = false;
  let st = { roster: {}, sessions: {}, primary: '', memory_root: '' };
  const host = () => window.__sfHost || null;
  const api = () => (window.pywebview && window.pywebview.api) || null;
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const sessionsMap = () => (host() && host().sessions()) || {};
  const rosterEntry = role => (st.roster || {})[role] || null;
  const isBot = role => !!(rosterEntry(role) && rosterEntry(role).bot);
  const isExternal = role => !!(isBot(role) && rosterEntry(role).bot.external);
  const memOn = role => isBot(role) && rosterEntry(role).bot.memory !== false && !isExternal(role);
  const botName = role => (rosterEntry(role) && rosterEntry(role).label) || role;

  function botOf(sid) {
    const bound = (st.sessions[sid] || {}).bot;
    if (bound && isBot(bound)) return bound;
    const s = sessionsMap()[sid];
    if (!s || s.isRemote) return '';
    for (const role of Object.keys(st.roster || {})) {
      if (isBot(role) && !isExternal(role) && botName(role) === s.label) return role;
    }
    return '';
  }
  function sidOfBot(role) {
    const S = sessionsMap();
    for (const sid of Object.keys(S)) if (!S[sid].isRemote && botOf(sid) === role) return sid;
    return '';
  }
  function avatarFor(sid) {
    const role = botOf(sid);
    if (role) return cleanAvatar(rosterEntry(role).bot.avatar, role);
    const own = (st.sessions[sid] || {}).avatar;
    if (own) return cleanAvatar(own, sid);
    const s = sessionsMap()[sid];
    return defaultAvatar(s && s.isRemote ? `${s.peerName || ''}/${s.label || sid}` : sid);
  }
  const isClosing = sid => !!(st.sessions[sid] || {}).closing;

  function face(sid) {
    if (!enabled) return '';
    const role = botOf(sid);
    return faceSvg(avatarFor(sid), { seed: sid, primary: !!role && role === st.primary,
                                     writing: isClosing(sid) });
  }

  // What the row's second line says: the agent's latest line, like a chat list.
  function previewOf(sid) {
    if (isClosing(sid)) return '正在把這次對話寫進記憶…';
    const s = sessionsMap()[sid] || {};
    const v = s._serverStatus || {};
    const pick = v.state === 'working' ? (v.action || v.narration || v.summary)
      : (v.narration || v.task || (v.state && v.state !== 'unknown' && v.state !== 'idle' ? v.summary : ''));
    const line = String(pick || '').replace(/[`*#>_~]+/g, '').replace(/\s+/g, ' ').trim();
    if (line) return line.slice(0, 140);
    const role = botOf(sid);
    return role ? (rosterEntry(role).responsibility || '') : '';
  }

  // ── organisms: sidebar ────────────────────────────────────────────────
  function afterSidebar(container) {
    if (!enabled || !container) return;
    const S = sessionsMap();
    container.querySelectorAll('.sb-item[data-section="local"][data-sid]').forEach(row => {
      const sid = row.dataset.sid;
      const s = S[sid];
      const lab = row.querySelector('.sb-label');
      if (!s || !lab || lab.classList.contains('sfb-two')) return;
      const role = botOf(sid);
      const tag = role && memOn(role) ? '<span class="sfb-tag" title="Bot：有自己的記憶，關閉前會先寫記憶">Bot</span>' : '';
      lab.classList.add('sfb-two');
      lab.innerHTML = `<span class="sfb-name">${esc(s.label)}${tag}</span>` +
        `<span class="sfb-sub">${esc(previewOf(sid)) || '&nbsp;'}</span>`;
      const dot = row.querySelector('.busy-dot');
      if (dot) dot.title = '右鍵：換頭像／開成 Bot。點一下：手動切換表情（下次對話自動接管）';
    });
    renderSleepers(container);
    renderPrimary();
    afterTabs(document.getElementById('tabs'));
  }
  // Rows of another ShellFrame's tabs (Frame Link zone): same faces, seeded by
  // machine + tab so each keeps a stable look across refreshes.
  function remoteFace(peer, rsid, label) {
    if (!enabled) return '';
    return faceSvg(defaultAvatar(`${peer || ''}/${label || rsid}`), { seed: `${peer}/${rsid}` });
  }
  function afterTabs(container) {
    if (!enabled || !container) return;
    container.querySelectorAll('.tab[data-sid] .busy-dot').forEach(dot => {
      dot.title = '右鍵：換頭像／開成 Bot';
    });
  }

  function renderSleepers(container) {
    const awake = new Set();
    const S = sessionsMap();
    Object.keys(S).forEach(sid => { if (!S[sid].isRemote) { const r = botOf(sid); if (r) awake.add(r); } });
    const roles = Object.keys(st.roster || {}).filter(r => isBot(r) && r !== st.primary && !awake.has(r));
    const frag = document.createDocumentFragment();
    const head = document.createElement('div');
    head.className = 'sfb-section';
    head.innerHTML = `<span>BOTS${roles.length ? ' · 休息中' : ''}</span>` +
      '<button class="sfb-add" data-sfb="new" title="建立一個有自己記憶的 bot">＋ Bot</button>';
    frag.appendChild(head);
    roles.forEach(role => {
      const e = rosterEntry(role);
      const ext = isExternal(role);
      const row = document.createElement('div');
      row.className = 'sfb-sleeper';
      row.dataset.sfbRole = role;
      row.title = ext ? '外部 bot：點一下看怎麼跟它往來' : '點一下叫醒：開一個分頁並帶入它的記憶';
      row.innerHTML = `<span class="sfb-avatar">${faceSvg(e.bot.avatar, { seed: role, asleep: !ext })}</span>` +
        `<span class="sfb-two" style="flex:1;margin-left:9px"><span class="sfb-name">${esc(botName(role))}` +
        `${ext ? '<span class="sfb-tag">外部</span>' : ''}</span>` +
        `<span class="sfb-sub">${ext ? '在 ShellFrame 外面' : '休息中'}${e.responsibility ? ' · ' + esc(e.responsibility) : ''}</span></span>` +
        `<span class="sfb-wake">${ext ? '往來' : '叫醒'}</span>`;
      frag.appendChild(row);
    });
    const divider = container.querySelector('.sb-divider');
    container.insertBefore(frag, divider || null);
  }

  function renderPrimary() {
    const list = document.getElementById('sidebar-sessions');
    if (!list) return;
    let el = document.getElementById('sfb-primary');
    if (!el) {
      el = document.createElement('div');
      el.id = 'sfb-primary';
      list.parentNode.insertBefore(el, list);
      el.addEventListener('click', onPrimaryClick);
    }
    if (!enabled) { el.innerHTML = ''; return; }
    const role = st.primary;
    if (!role || !rosterEntry(role)) {
      el.innerHTML = '<div class="sfb-card empty" data-sfb="new-primary" title="主要 Bot 是日常任務的首選，負責把工作派給其他 bot">＋ 建立主要 Bot</div>';
      return;
    }
    const e = rosterEntry(role);
    const sid = sidOfBot(role);
    const ext = isExternal(role);
    const s = sid ? sessionsMap()[sid] : null;
    const cls = s ? (s._dotClass || '') : '';
    const sub = ext ? '外部 · 點一下往來' : (sid ? previewOf(sid) : '休息中 · 點一下叫醒');
    const active = sid && host() && host().active() === sid;
    el.innerHTML = `<div class="sfb-card${active ? ' active' : ''}" data-sfb="primary">` +
      `<span class="sfb-avatar busy-dot ${cls}" style="--sfb-size:var(--sfb-size-card);cursor:pointer">` +
      `${faceSvg(e.bot.avatar, { seed: role, primary: true, asleep: !sid && !ext, writing: sid && isClosing(sid) })}</span>` +
      `<div class="sfb-card-name">${esc(botName(role))}</div><div class="sfb-card-sub">${esc(sub)}</div></div>`;
  }

  function onPrimaryClick(e) {
    const card = e.target.closest('[data-sfb]');
    if (!card) return;
    if (card.dataset.sfb === 'new-primary') { openEditor(null, { primary: true }); return; }
    const role = st.primary;
    if (!role) return;
    if (isExternal(role)) { openExternal(role); return; }
    const sid = sidOfBot(role);
    if (sid) host().switchTab(sid); else wake(role);
  }

  async function wake(role) {
    const a = api();
    if (!a) return;
    toast(`叫醒「${botName(role)}」：開分頁、帶入記憶…`);
    try {
      const res = JSON.parse(await a.bots_open(role));
      if (!res.success) { toast(res.message || '叫不醒', true); return; }
      const sid = (res.details || {}).sid;
      await refresh();
      if (sid) waitAndSwitch(sid);
    } catch (err) { toast(String(err), true); }
  }
  function waitAndSwitch(sid, tries = 0) {
    if (sessionsMap()[sid]) { host().switchTab(sid); return; }
    if (tries < 40) setTimeout(() => waitAndSwitch(sid, tries + 1), 250);
  }

  // Live bits that change without a re-render: second lines, card, writing faces.
  function tick() {
    if (!enabled) return;
    document.querySelectorAll('#sidebar-sessions .sb-item[data-sid] .sfb-sub').forEach(sub => {
      const sid = sub.closest('.sb-item').dataset.sid;
      const text = previewOf(sid) || ' ';
      if (sub.textContent !== text) sub.textContent = text;
    });
    const role = st.primary;
    const card = document.querySelector('#sfb-primary .sfb-card .busy-dot');
    if (card && role) {
      const sid = sidOfBot(role);
      const s = sid ? sessionsMap()[sid] : null;
      const want = 'sfb-avatar busy-dot ' + (s ? (s._dotClass || '') : '');
      if (card.className !== want) card.className = want;
      const sub = document.querySelector('#sfb-primary .sfb-card-sub');
      if (sub && sid) { const t = previewOf(sid); if (sub.textContent !== t) sub.textContent = t; }
    }
  }

  // ── avatar picker (right-click an avatar) ─────────────────────────────
  let pop = null;
  function closePop() { if (pop) { pop.remove(); pop = null; } }
  function openPicker(sid, anchor) {
    closePop();
    const S = sessionsMap();
    const s = S[sid];
    if (!s) return;
    const role = botOf(sid);
    const cur = avatarFor(sid);
    pop = document.createElement('div');
    pop.className = 'sfb-pop';
    const shapes = Object.keys(SHAPES).map(k =>
      `<button data-shape="${k}" class="${k === cur.shape ? 'sel' : ''}" title="${SHAPES[k].name}">` +
      `<span class="sfb-avatar">${faceSvg({ shape: k, color: cur.color }, { seed: k })}</span></button>`).join('');
    const colors = Object.keys(PALETTE).map(k =>
      `<button data-color="${k}" class="${k === cur.color ? 'sel' : ''}" title="${k}">` +
      `<span class="sfb-swatch" style="background:${PALETTE[k]}"></span></button>`).join('');
    const preview = STATES.map(([c, n]) =>
      `<figure><span class="busy-dot ${c}">${faceSvg(cur, { seed: sid + c })}</span><figcaption>${n}</figcaption></figure>`).join('');
    const remote = !!s.isRemote;
    pop.innerHTML = `<h4>${esc(s.label)}${role ? '（Bot）' : ''}</h4>` +
      `<div class="sfb-grid">${shapes}</div><div class="sfb-grid" style="margin-top:8px">${colors}</div>` +
      `<div class="sfb-preview">${preview}</div>` +
      `<div class="sfb-row">${remote ? '' : (role
        ? '<button class="btn btn-ghost" data-act="edit">編輯 Bot…</button>'
        : '<button class="btn btn-primary" data-act="make" title="這個分頁變成有自己記憶的 bot">開成 Bot…</button>')}` +
      `<button class="btn btn-ghost" data-act="random">隨機</button>` +
      `${role || remote ? '' : '<button class="btn btn-ghost" data-act="reset">預設</button>'}</div>`;
    document.body.appendChild(pop);
    const r = anchor.getBoundingClientRect();
    const w = pop.offsetWidth, h = pop.offsetHeight;
    pop.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.right + 8)) + 'px';
    pop.style.top = Math.max(8, Math.min(window.innerHeight - h - 8, r.top - 12)) + 'px';
    pop.addEventListener('click', async e => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.act === 'make') { closePop(); openEditor(null, { fromSid: sid }); return; }
      if (b.dataset.act === 'edit') { closePop(); openEditor(role); return; }
      let next = Object.assign({}, cur);
      if (b.dataset.shape) next.shape = b.dataset.shape;
      if (b.dataset.color) next.color = b.dataset.color;
      if (b.dataset.act === 'random') {
        const ks = Object.keys(SHAPES);
        next = { shape: ks[Math.floor(Math.random() * ks.length)],
                 color: DEFAULT_COLORS[Math.floor(Math.random() * DEFAULT_COLORS.length)] };
      }
      if (b.dataset.act === 'reset') next = null;
      if (remote) { toast('遠端分頁的頭像由那台 ShellFrame 決定'); return; }
      await setAvatar(sid, next);
      closePop();
      openPicker(sid, anchorFor(sid) || anchor);
    });
  }
  function anchorFor(sid) {
    return document.querySelector(`#sidebar-sessions .sb-item[data-sid="${sid}"] .busy-dot`)
        || document.querySelector(`.tab[data-sid="${sid}"] .busy-dot`);
  }
  async function setAvatar(sid, av) {
    try {
      const res = JSON.parse(await api().bots_set_avatar(sid, JSON.stringify(av)));
      if (!res.success) toast(res.message || '存不了', true);
    } catch (err) { toast(String(err), true); }
    await refresh(true);
  }

  // ── bot editor ────────────────────────────────────────────────────────
  let catalog = null;
  async function skillCatalog() {
    if (catalog) return catalog;
    try { catalog = JSON.parse(await api().bots_skill_catalog()).skills || []; } catch (_) { catalog = []; }
    return catalog;
  }

  async function openEditor(role, opts) {
    opts = opts || {};
    const existing = role ? rosterEntry(role) : null;
    const S = sessionsMap();
    const from = opts.fromSid ? S[opts.fromSid] : null;
    const bot = (existing && existing.bot) || {};
    let av = cleanAvatar(bot.avatar || (opts.fromSid ? avatarFor(opts.fromSid) : null),
                         role || (from && from.label) || String(Date.now()));
    const cfg = (host() && host().cfg()) || {};
    const presets = (cfg.presets || []).filter(p => p && p.cmd && !/^(bash|zsh|sh|fish)$/.test(p.cmd.trim()));
    const curCmd = existing ? existing.cmd : (from ? from.cmd : (presets[0] || {}).cmd || 'claude');
    const cmdOptions = presets.map(p => `<option value="${esc(p.cmd)}"${p.cmd === curCmd ? ' selected' : ''}>${esc(p.name)}</option>`);
    if (curCmd && !presets.some(p => p.cmd === curCmd)) {
      cmdOptions.unshift(`<option value="${esc(curCmd)}" selected>${esc(from ? '這個分頁的指令' : '目前的指令')}：${esc(curCmd.slice(0, 40))}</option>`);
    }
    const skills = await skillCatalog();
    const chosen = new Set(bot.skills || []);
    const chips = skills.slice(0, 60).map(k =>
      `<label title="${esc(k.description || '')}"><input type="checkbox" value="${esc(k.name)}"${chosen.has(k.name) ? ' checked' : ''}>${esc(k.name)}</label>`).join('');
    const extra = [...chosen].filter(n => !skills.some(k => k.name === n)).join(', ');
    const ext = bot.external || null;
    const primaryOn = existing ? (st.primary === role) : !!opts.primary;
    const ov = document.createElement('div');
    ov.className = 'modal-overlay open sfb-modal';
    ov.style.zIndex = '450';
    const shapeBtns = () => Object.keys(SHAPES).map(k =>
      `<button data-shape="${k}" class="${k === av.shape ? 'sel' : ''}" title="${SHAPES[k].name}"><span class="sfb-avatar">${faceSvg({ shape: k, color: av.color }, { seed: k })}</span></button>`).join('');
    const colorBtns = () => Object.keys(PALETTE).map(k =>
      `<button data-color="${k}" class="${k === av.color ? 'sel' : ''}"><span class="sfb-swatch" style="background:${PALETTE[k]}"></span></button>`).join('');
    ov.innerHTML = `<div class="modal">
      <h2><span class="sfb-avatar" id="sfb-ed-face">${faceSvg(av, { primary: primaryOn })}</span>
        ${existing ? `編輯「${esc(botName(role))}」` : (from ? `把「${esc(from.label)}」開成 Bot` : (opts.primary ? '建立主要 Bot' : '建立 Bot'))}</h2>
      <div class="sfb-form">
        <label>名稱</label><input type="text" id="sfb-ed-name" value="${esc(existing ? role : (from ? from.label : ''))}" ${existing ? 'readonly' : ''} placeholder="例如：研究、Coding、日程">
        <label>執掌</label><textarea id="sfb-ed-resp" placeholder="它負責什麼、邊界在哪；叫醒時會交給它">${esc(existing ? existing.responsibility : '')}</textarea>
        <label>CLI</label><select id="sfb-ed-cmd">${cmdOptions.join('')}</select>
        <label>模型</label><input type="text" id="sfb-ed-model" value="${esc(existing ? existing.model || '' : '')}" placeholder="選填，例如 sonnet；留空用 CLI 預設">
        <label>Skills</label><div><div class="sfb-chips" id="sfb-ed-skills">${chips || '<span style="font-size:10px;color:#565f89">找不到已安裝的 skill</span>'}</div>
          <input type="text" id="sfb-ed-skill-extra" value="${esc(extra)}" placeholder="其他 skill 名稱，用逗號分隔" style="margin-top:5px"></div>
        <label>頭像</label><div><div class="sfb-grid" id="sfb-ed-shapes">${shapeBtns()}</div>
          <div class="sfb-grid" id="sfb-ed-colors" style="margin-top:6px">${colorBtns()}</div></div>
        <label>記憶</label><div style="display:flex;align-items:center;gap:6px"><button class="toggle-switch${bot.memory === false ? '' : ' on'}" id="sfb-ed-mem"></button>
          <span style="font-size:10px;color:#565f89">關閉前先寫記憶，下次叫醒自動帶回</span></div>
        <label>主要 Bot</label><div style="display:flex;align-items:center;gap:6px"><button class="toggle-switch${primaryOn ? ' on' : ''}" id="sfb-ed-primary"></button>
          <span style="font-size:10px;color:#565f89">置頂；日常任務的首選，負責派工給其他 bot</span></div>
        <label>外部</label><div style="display:flex;align-items:center;gap:6px"><button class="toggle-switch${ext ? ' on' : ''}" id="sfb-ed-ext"></button>
          <span style="font-size:10px;color:#565f89">住在 ShellFrame 外面（例如 Grok Bot），用 webhook 往來</span></div>
        <div id="sfb-ed-extbox" style="grid-column:1/-1;display:${ext ? 'grid' : 'none'};grid-template-columns:74px 1fr;gap:8px 10px;align-items:center">
          <label>Webhook</label><input type="text" id="sfb-ed-url" value="${esc(ext && ext.url_set ? '••••（已設定，留空不改）' : '')}" placeholder="Grok Bot routine 的 webhook URL">
          <label>授權</label><input type="text" id="sfb-ed-auth" value="${esc(ext && ext.auth_set ? '••••（已設定，留空不改）' : '')}" placeholder="Bearer <key>">
          <div class="hint" style="grid-column:2">URL 和 key 只存在這台的設定檔，畫面與 log 都會遮住。</div>
        </div>
      </div>
      <div class="sfb-row" style="justify-content:flex-end">
        ${existing ? '<button class="btn btn-ghost" data-act="folder">記憶資料夾</button><button class="btn btn-ghost" data-act="delete" style="margin-right:auto;color:#f7768e">刪除</button>' : ''}
        <button class="btn btn-ghost" data-act="cancel">取消</button>
        <button class="btn btn-primary" data-act="save">${existing ? '儲存' : (from ? '開成 Bot' : '建立')}</button>
      </div>
      <div class="sfb-msg" id="sfb-ed-msg"></div></div>`;
    document.body.appendChild(ov);
    const $ = id => ov.querySelector('#' + id);
    const repaint = () => {
      $('sfb-ed-shapes').innerHTML = shapeBtns();
      $('sfb-ed-colors').innerHTML = colorBtns();
      $('sfb-ed-face').innerHTML = faceSvg(av, { primary: $('sfb-ed-primary').classList.contains('on') });
    };
    ov.addEventListener('click', async e => {
      if (e.target === ov) { ov.remove(); return; }
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.shape) { av.shape = b.dataset.shape; repaint(); return; }
      if (b.dataset.color) { av.color = b.dataset.color; repaint(); return; }
      if (b.classList.contains('toggle-switch')) {
        b.classList.toggle('on');
        if (b.id === 'sfb-ed-ext') $('sfb-ed-extbox').style.display = b.classList.contains('on') ? 'grid' : 'none';
        repaint();
        return;
      }
      const act = b.dataset.act;
      if (act === 'cancel') { ov.remove(); return; }
      if (act === 'folder') { api().bots_open_folder(role); return; }
      if (act === 'delete') {
        if (!confirm(`刪除「${botName(role)}」？記憶資料夾會留著，不會刪。`)) return;
        const res = JSON.parse(await api().bots_delete(role));
        if (res.success) { ov.remove(); await refresh(true); } else $('sfb-ed-msg').textContent = res.message;
        return;
      }
      if (act !== 'save') return;
      const name = $('sfb-ed-name').value.trim();
      const picked = [...ov.querySelectorAll('#sfb-ed-skills input:checked')].map(i => i.value);
      const more = $('sfb-ed-skill-extra').value.split(/[,，\s]+/).map(x => x.trim()).filter(Boolean);
      const extOn = $('sfb-ed-ext').classList.contains('on');
      const keep = v => /^••••/.test(v) ? null : v.trim();
      const data = {
        responsibility: $('sfb-ed-resp').value.trim(),
        cmd: $('sfb-ed-cmd').value,
        model: $('sfb-ed-model').value.trim(),
        skills: [...new Set(picked.concat(more))],
        avatar: av,
        memory: $('sfb-ed-mem').classList.contains('on'),
        primary: $('sfb-ed-primary').classList.contains('on'),
        external: extOn ? { kind: 'webhook', url: keep($('sfb-ed-url').value), auth: keep($('sfb-ed-auth').value) } : null,
      };
      const msg = $('sfb-ed-msg');
      msg.style.color = '#a9b1d6';
      msg.textContent = '儲存中…';
      try {
        const res = JSON.parse(await api().bots_save(name, JSON.stringify(data), opts.fromSid || ''));
        msg.style.color = res.success ? '#9ece6a' : '#f7768e';
        msg.textContent = res.message || '';
        if (res.success) { ov.remove(); await refresh(true); }
      } catch (err) { msg.style.color = '#f7768e'; msg.textContent = String(err); }
    });
  }

  // ── close with memory ─────────────────────────────────────────────────
  function interceptClose(sid) {
    if (!enabled) return false;
    const role = botOf(sid);
    if (!role || !memOn(role)) return false;
    if (isClosing(sid)) { toast('正在寫記憶，寫完會自動關閉'); return true; }
    const s = sessionsMap()[sid];
    const busy = /sig-working|(^|\s)on(\s|$)/.test((s && s._dotClass) || '') && !/attention/.test(s._dotClass || '');
    const ov = document.createElement('div');
    ov.className = 'modal-overlay open sfb-modal';
    ov.style.zIndex = '450';
    ov.innerHTML = `<div class="modal" style="width:380px">
      <h2><span class="sfb-avatar">${faceSvg(avatarFor(sid), { seed: sid, writing: true })}</span>關閉「${esc(botName(role))}」前先寫記憶？</h2>
      <p class="sfb-note">它會把這次對話的重點寫進自己的記憶資料夾，下次叫醒時自動帶回。
      ${busy ? '<br><b style="color:#e0af68">它還在工作中</b>：會等這一輪做完再寫（最多 10 分鐘）。' : ''}</p>
      <div class="sfb-row" style="justify-content:flex-end">
        <button class="btn btn-ghost" data-act="cancel">取消</button>
        <button class="btn btn-ghost" data-act="skip" style="color:#f7768e">直接關閉（不寫）</button>
        <button class="btn btn-primary" data-act="save">寫記憶後關閉</button></div></div>`;
    document.body.appendChild(ov);
    const finish = async act => {
      document.removeEventListener('keydown', onKey, true);
      ov.remove();
      if (act === 'cancel') return;
      try {
        const res = JSON.parse(await api().bots_close(sid, act));
        if (!res.success) { toast(res.message || '關不掉', true); return; }
        if (act === 'save') toast(`「${botName(role)}」正在寫記憶，寫完會自動關閉`);
      } catch (err) { toast(String(err), true); }
      await refresh(true);
    };
    const onKey = e => {
      if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); finish('save'); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish('cancel'); }
    };
    document.addEventListener('keydown', onKey, true);
    ov.addEventListener('click', e => {
      if (e.target === ov) { finish('cancel'); return; }
      const b = e.target.closest('[data-act]');
      if (b) finish(b.dataset.act);
    });
    return true;
  }

  // ── external bot (e.g. Grok Bot) ──────────────────────────────────────
  async function openExternal(role) {
    let info = {};
    try { info = JSON.parse(await api().bots_external_info(role)); } catch (_) { info = {}; }
    const e = rosterEntry(role) || {};
    const ext = (e.bot || {}).external || {};
    const log = (info.log || []).map(x =>
      `<div class="item" style="cursor:default"><div class="who"><b>${esc(x.from_label || x.from_sid || '?')}` +
      `${x.kind === 'result' ? ' 回覆' : ' → ' + esc(x.target || '')}</b>` +
      `<span>${esc(x.kind === 'failed' ? '失敗：' + (x.reason || '') : (x.text || ''))}</span></div></div>`).join('');
    const ov = document.createElement('div');
    ov.className = 'modal-overlay open sfb-modal';
    ov.style.zIndex = '450';
    ov.innerHTML = `<div class="modal" style="width:480px">
      <h2><span class="sfb-avatar">${faceSvg(e.bot && e.bot.avatar, { seed: role, primary: role === st.primary })}</span>${esc(botName(role))}<span class="sfb-tag">外部</span></h2>
      <p class="sfb-note">這個 bot 不在 ShellFrame 裡。分頁用 <code>sfctl a2a-send ${esc(role)} "…"</code> 找它時，ShellFrame 會 POST 到它的 webhook；
      它要回覆或派工，就在這台 Mac 用「本機執行」跑 <code>sfctl a2a-send --as ${esc(role)} …</code>。</p>
      <p class="sfb-note">Webhook：${ext.url_set ? `<b style="color:#9ece6a">已設定</b>（${esc(ext.url_mask || '')}）` : '<b style="color:#f7768e">還沒設定</b>：到「編輯」貼上 routine 的 webhook URL 和 key'}</p>
      <textarea id="sfb-x-text" placeholder="傳一則訊息給它（會以「使用者」身分送出）" style="width:100%;box-sizing:border-box;min-height:56px;margin-top:8px;background:#24283b;border:1px solid #3b4261;border-radius:4px;color:#c0caf5;padding:6px;font-size:12px"></textarea>
      <div class="sfb-row" style="justify-content:flex-end">
        <button class="btn btn-ghost" data-act="routine" title="複製貼到 Grok Bot routine 的指令">複製 routine 指令</button>
        <button class="btn btn-ghost" data-act="edit">編輯</button>
        <button class="btn btn-ghost" data-act="close">關閉</button>
        <button class="btn btn-primary" data-act="send">送出</button></div>
      <div class="sfb-msg" id="sfb-x-msg"></div>
      <div class="sfb-list">${log || '<span class="sfb-note">還沒有往來紀錄</span>'}</div></div>`;
    document.body.appendChild(ov);
    const msg = ov.querySelector('#sfb-x-msg');
    ov.addEventListener('click', async ev => {
      if (ev.target === ov) { ov.remove(); return; }
      const b = ev.target.closest('[data-act]');
      if (!b) return;
      const act = b.dataset.act;
      if (act === 'close') { ov.remove(); return; }
      if (act === 'edit') { ov.remove(); openEditor(role); return; }
      if (act === 'routine') {
        try { await navigator.clipboard.writeText(info.routine || ''); msg.style.color = '#9ece6a'; msg.textContent = '已複製，貼到 Grok Bot 那個 routine 的指令欄'; }
        catch (_) { msg.style.color = '#e0af68'; msg.textContent = info.routine || ''; }
        return;
      }
      if (act === 'send') {
        const text = ov.querySelector('#sfb-x-text').value.trim();
        if (!text) return;
        msg.style.color = '#a9b1d6'; msg.textContent = '送出中…';
        try {
          const res = JSON.parse(await api().bots_external_send(role, text));
          msg.style.color = res.success ? '#9ece6a' : '#f7768e';
          msg.textContent = res.message || '';
        } catch (err) { msg.style.color = '#f7768e'; msg.textContent = String(err); }
      }
    });
  }

  // ── settings toggle (injected next to the other experimental switches) ─
  function injectSettings() {
    if (document.getElementById('setting-experimental-bots')) return;
    const anchor = document.getElementById('setting-experimental-loops');
    const block = anchor && anchor.closest('div') && anchor.closest('div').parentElement;
    if (!block || !block.parentElement) return;
    const wrap = document.createElement('div');
    wrap.innerHTML = `<label style="font-size:11px;color:#565f89;display:block;margin-bottom:4px">Bots — 頭像表情、獨立記憶、主要 Bot（實驗）</label>
      <div style="display:flex;align-items:center;gap:6px">
        <button class="toggle-switch" id="setting-experimental-bots"></button>
        <span style="font-size:11px;color:#565f89">燈號換成會動的 bot 頭像（發呆／運作／問號／卡住／完成）；分頁可以開成 bot：叫醒時帶入執掌、skill 與記憶，關閉前先寫記憶。預設關閉。</span>
      </div>
      <div id="sfb-settings-more" style="display:none;margin-top:6px;gap:6px;align-items:center;flex-wrap:wrap">
        <button class="btn btn-ghost" id="sfb-settings-new" style="font-size:11px;padding:2px 10px">＋ 建立 Bot</button>
        <span style="font-size:10px;color:#565f89" id="sfb-settings-root"></span>
      </div>`;
    block.parentElement.insertBefore(wrap, block);
    const btn = wrap.querySelector('#setting-experimental-bots');
    btn.addEventListener('click', async () => {
      const cfg = host() && host().cfg();
      if (!cfg) return;
      const on = !btn.classList.contains('on');
      cfg.settings = cfg.settings || {};
      cfg.settings.experimental_bots = on;
      await api().save_settings(JSON.stringify(cfg.settings));
      setEnabled(on);
    });
    wrap.querySelector('#sfb-settings-new').addEventListener('click', () => openEditor(null));
    syncSettings();
  }
  function syncSettings() {
    const btn = document.getElementById('setting-experimental-bots');
    if (btn) btn.classList.toggle('on', enabled);
    const more = document.getElementById('sfb-settings-more');
    if (more) more.style.display = enabled ? 'flex' : 'none';
    const root = document.getElementById('sfb-settings-root');
    if (root) root.textContent = st.memory_root ? `記憶資料夾：${st.memory_root}` : '';
  }

  // ── plumbing ──────────────────────────────────────────────────────────
  let toastEl = null, toastTimer = 0;
  function toast(text, bad) {
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.style.cssText = 'position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:600;' +
        'background:#1f2335;border:1px solid #3b4261;border-radius:8px;padding:7px 14px;font-size:12px;' +
        'box-shadow:0 8px 24px rgba(0,0,0,.4);max-width:70vw;pointer-events:none';
      document.body.appendChild(toastEl);
    }
    toastEl.style.color = bad ? '#f7768e' : '#c0caf5';
    toastEl.textContent = text;
    toastEl.style.display = 'block';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.style.display = 'none'; }, 3200);
  }

  let lastSig = '';
  async function refresh(force) {
    const a = api();
    if (!a || !a.bots_state) return;
    try {
      const next = JSON.parse(await a.bots_state());
      const sig = JSON.stringify(next);
      st = Object.assign({ roster: {}, sessions: {}, primary: '' }, next);
      if (next.enabled !== undefined && !!next.enabled !== enabled) {
        // The flag changed outside this page: keep index.html's settings copy in step,
        // or its next unrelated settings save would write the old value back.
        const c = host() && host().cfg();
        if (c) { c.settings = c.settings || {}; c.settings.experimental_bots = !!next.enabled; }
        setEnabled(!!next.enabled);
        return;
      }
      if (force || sig !== lastSig) {
        lastSig = sig;
        rerender();
      }
    } catch (_) { /* never let bots break the UI */ }
  }
  function rerender() {
    const h = host();
    if (!h) return;
    try { h.renderTabs(); } catch (_) {}
    try { h.renderSidebar(); } catch (_) {}
    renderPrimary();
    syncSettings();
  }
  function setEnabled(on) {
    enabled = !!on;
    document.body.classList.toggle('sfb-on', enabled);
    if (!enabled) closePop();
    rerender();
    if (enabled) refresh(true);
  }

  // Right-click an avatar anywhere (tab bar or sidebar) → picker.
  document.addEventListener('contextmenu', e => {
    if (!enabled) return;
    const dot = e.target.closest && e.target.closest('.busy-dot');
    const holder = dot && dot.closest('[data-sid]');
    if (!holder) return;
    e.preventDefault();
    e.stopPropagation();
    openPicker(holder.dataset.sid, dot);
  }, true);
  document.addEventListener('click', e => {
    if (pop && !pop.contains(e.target)) closePop();
    if (!enabled) return;
    const add = e.target.closest && e.target.closest('[data-sfb="new"]');
    if (add) { e.preventDefault(); e.stopPropagation(); openEditor(null); return; }
    const row = e.target.closest && e.target.closest('.sfb-sleeper');
    if (row) {
      e.preventDefault(); e.stopPropagation();
      const role = row.dataset.sfbRole;
      if (isExternal(role)) openExternal(role); else wake(role);
    }
  }, true);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closePop(); });
  document.addEventListener('visibilitychange', () => {
    document.body.classList.toggle('sfb-hidden', document.hidden);
  });

  function boot(tries) {
    const h = host();
    if (!h || !h.cfg() || !api() || !api().bots_state) {
      if (tries < 240) setTimeout(() => boot(tries + 1), 250);
      return;
    }
    injectSettings();
    const s = (h.cfg().settings || {});
    setEnabled(s.experimental_bots === true);
    // Second lines follow the status pushes as they arrive, not on the next tick.
    const pushed = window.__sfAgentStatus;
    if (typeof pushed === 'function' && !pushed.__sfb) {
      const wrapped = function (payload) { pushed(payload); try { tick(); } catch (_) {} };
      wrapped.__sfb = true;
      window.__sfAgentStatus = wrapped;
    }
    document.getElementById('btn-settings') &&
      document.getElementById('btn-settings').addEventListener('click', () => setTimeout(() => { injectSettings(); syncSettings(); }, 0));
    setInterval(tick, 1500);
    setInterval(() => { if (enabled) refresh(); }, 5000);
    // One line in the debug log, so "I see no faces" can be answered from the log.
    setTimeout(() => {
      try {
        api().js_debug('bots', `enabled=${enabled} faces=${document.querySelectorAll('svg.sfb').length} ` +
                               `primary=${st.primary || '-'} bots=${Object.keys(st.roster || {}).filter(isBot).length}`);
      } catch (_) {}
    }, 4000);
  }

  window.SFBots = {
    face, remoteFace, afterSidebar, afterTabs, interceptClose, refresh,
    // for tests and for other modules that want the same faces
    faceSvg, defaultAvatar, SHAPES, PALETTE, STATES,
    enabled: () => enabled,
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => boot(0));
  else boot(0);
})();
