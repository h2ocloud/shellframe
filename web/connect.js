/* ShellBot connections — Settings → Connect, the wizard card, the "needs reconnecting" cards.
 *
 * The Python side (connectors/, api_connect.py) owns every fact: which services there are, which
 * way each is connected, which step the person is on, what a check or a test found, and the plain
 * words for it. This file draws what `connect_list`, `connect_describe`, `connect_status` and the
 * `view` of connect_start / connect_step / connect_test hand it, and sends the person's clicks back.
 * It knows no service by name: a service is an icon key and words from its descriptor.
 *
 *   Settings → Connect   the catalog ("Connect apps"): a search box, one bubble per category, sections
 *                        (needs you, recommended, this computer's apps, the rest), "N connected ›"; one row per
 *                        service: icon, name, one line, a button that says where it stands. All of it comes
 *                        from connect_list's `catalog` (each service's category, recommended, local_app, can it
 *                        be connected here and why not) and its statuses. While the tab is open it asks
 *                        connect_status(refresh) every 60 s. A row's button opens the wizard below.
 *                        Connections already set up on this computer count (the Python side looks in each AI
 *                        program's own list, in the background: connect_list's `detect`, connect_detect for
 *                        "Look again"): a connected row says through which way (status `via`, the person's own
 *                        setting or not), and the card lists every way with what was found (status `protocols`).
 *   the wizard card      a route to pick, the steps (words, a link, buttons, options, a check, "I'm stuck"),
 *                        the test, and what it came to. Errors say message.text and message.next. It asks as little
 *                        as it can: one way (or the Claude account's connector recommended) starts without the choice;
 *                        a step marked `in_browser` opens its page at once, and the window getting the focus back
 *                        finishes it and starts the test.
 *   the sidebar card     a small card while a service needs reconnecting (connect_list every 60 s, no refresh)
 *   the chat card        [[SF:RED:connect:<service>]] arrives as decision.tag; chat.js asks chatCard() for
 *                        the card, and the button opens the same wizard through connect_signal
 *
 * Words follow the page's language (index.html's `lang`): t(english, chinese). Text that came from
 * Python is {en, zh}; pick() takes the page's language. Nothing here reads, keeps or shows a secret:
 * a pasted secret goes through the Python side's own `secret_set` action, and what comes back is a code.
 */
(function (root) {
  'use strict';
  const doc = root.document;
  if (!doc) return;

  // ── words ────────────────────────────────────────────────────────────────
  const host = () => root.__sfHost || null;
  const api = () => (root.pywebview && root.pywebview.api) || null;
  const isZh = () => {
    const h = host();
    const l = (h && h.lang && h.lang()) || doc.documentElement.lang || (root.navigator && root.navigator.language) || '';
    return /^zh/i.test(l);
  };
  const t = (en, zh) => (isZh() ? zh : en);
  const pick = o => (o == null ? '' : typeof o === 'string' ? o : String((isZh() ? (o.zh || o.en) : (o.en || o.zh)) || ''));
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = id => doc.getElementById(id);
  const httpsOnly = u => /^https:\/\/[^\s]+$/i.test(String(u || ''));
  const dateOf = ts => {
    try { return new Date(Number(ts) * 1000).toLocaleDateString(isZh() ? 'zh-TW' : undefined, { year: 'numeric', month: 'short', day: 'numeric' }); }
    catch (_) { return ''; }
  };

  // The states of a connection (health.status), in words: [English, 中文, tone, button English, button 中文]
  const STATES = {
    not_started:     ['Not connected', '還沒連結', 'idle', 'Connect', '連結'],
    waiting_user:    ['Connecting', '連結中', 'busy', 'Continue', '繼續'],
    verifying:       ['Verifying…', '驗證中…', 'busy', 'View', '查看'],
    connected:       ['Connected', '已連結', 'ok', 'Connected', '已連結'],
    failed:          ['Did not connect', '沒連結成功', 'bad', 'Try again', '重試'],
    needs_reconnect: ['Needs reconnecting', '需要重新連結', 'bad', 'Reconnect', '重新連結'],
  };
  const stateOf = st => STATES[st && st.state] || STATES.not_started;
  const CLI_STATE = {
    connected: ['Connected', '已連結', 'ok'], missing: ['Not added yet', '還沒加入', 'idle'],
    manual: ['Set up by hand', '要手動設定', 'warn'], unsupported: ['Not supported', '不支援', 'idle'], unknown: ['Not known', '不明', 'idle'],
  };
  // The kinds of way (route) a service is reached by, in a few words: what a connected row says it goes through.
  const KIND = {
    claude_connector: ['Claude account connector', 'Claude 帳號連接器'], local_mcp: ['Local MCP', '本機 MCP'],
    api_key: ['API key', 'API 金鑰'], oauth: ['Sign-in', '登入授權'],
  };
  const kindName = k => (KIND[k] ? t(KIND[k][0], KIND[k][1]) : '');
  const yours = () => t(' (your own setting)', '（你自己的設定）');
  const viaName = v => (v && v.kind ? kindName(v.kind) + (v.adopted ? yours() : '') : '');
  // What was found for one way on this computer: [mark, English, 中文, tone]
  const FOUND = {
    checked: ['✅', 'Checked, it works', '測過能通', 'ok'], found: ['🟡', 'Found, not tested', '找到了，還沒測', 'warn'],
    broken: ['⚠', 'Found, not working', '找到但連不上', 'bad'], none: ['⚪', 'Not set up', '沒有設定', 'idle'],
  };
  const WHY = {
    auth: ['it needs signing in or allowing', '要先登入或授權'], failed: ['its check failed', '檢查沒通過'],
    off: ['it is turned off', '被關掉了'], pending: ['waiting to be approved', '還沒被核准'],
    unfinished: ["ShellBot's setup is not finished", 'ShellBot 的設定還沒做完'], tested: ["ShellBot's test passed", 'ShellBot 測過了'],
  };
  const protos = st => (st && Array.isArray(st.protocols) ? st.protocols : []);
  const detectedVia = st => !!(st && st.state === 'connected' && st.via && st.via.detected);   // works a way ShellBot did not set up
  // A way found on this computer that does not count as connected (yet): one short line for the row.
  function foundHint(st) {
    const ps = protos(st).filter(p => p.state === 'found' || p.state === 'broken');
    const p = ps.find(x => x.state === 'found') || ps[0];
    if (!p) return '';
    const f = FOUND[p.state];
    return `${f[0]} ${t(f[1], f[2])}${t(': ', '：')}${kindName(p.kind)}${p.adopted ? yours() : ''}`;
  }
  const NOAPI = () => ({ success: false, code: 'no_api', message: { text: t('This version cannot do that here.', '這個版本在這裡做不到。'), next: '' } });

  // ── the Python side ──────────────────────────────────────────────────────
  async function call(name, ...args) {
    const a = api();
    if (!a || typeof a[name] !== 'function') return NOAPI();
    try {
      const raw = await a[name](...args);
      return typeof raw === 'string' ? JSON.parse(raw || '{}') : (raw || {});
    } catch (_) {
      return { success: false, code: 'error', message: { text: t('That did not go through.', '這個動作沒有成功。'), next: t('Try again in a moment.', '稍後再試一次。') } };
    }
  }
  const json = o => JSON.stringify(o || {});
  function openUrl(url) {
    if (!httpsOnly(url)) return;
    const a = api();
    if (a && typeof a.open_url === 'function') { try { a.open_url(url); return; } catch (_) { /* fall through */ } }
    try { root.open(url, '_blank', 'noopener'); } catch (_) { /* nowhere to open it */ }
  }

  // ── what the page knows about the services ───────────────────────────────
  const L = { order: [], by: {}, clis: [], catalog: {}, platform: '', loaded: false, detect: null };
  const descs = {};                                   // service -> Promise of its descriptor (or null)
  const descDone = {};                                // service -> descriptor, once it arrived
  function describe(id) {
    if (!descs[id]) {
      descs[id] = call('connect_describe', id).then(r => {
        if (!(r && r.success && r.descriptor)) { delete descs[id]; return null; }
        descDone[id] = r.descriptor;
        return r.descriptor;
      });
    }
    return descs[id];
  }
  function take(st) {
    if (!st || !st.service) return;
    L.by[st.service] = st;
    if (!L.order.includes(st.service)) L.order.push(st.service);
  }
  const cliName = key => {
    const c = (L.clis || []).find(x => x && (x.id === key || x.key === key || x.cli === key));
    return (c && pick(c.name || c.label)) || key;
  };
  const attention = () => L.order.map(id => L.by[id]).filter(s => s && s.attention);

  const ICONS = {
    mail: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3.8 7.2l8.2 6.1 8.2-6.1"/>',
    calendar: '<rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.2h7a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    chat: '<path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7a2.5 2.5 0 0 1-2.5 2.5H11l-4.5 3.5V16A2.5 2.5 0 0 1 4 13.5z"/>',
    key: '<circle cx="8" cy="12" r="3.5"/><path d="M11.5 12H21M17 12v3M20 12v2"/>',
    plug: '<path d="M9 3v5M15 3v5M6.5 8h11v3.5a5.5 5.5 0 0 1-11 0zM12 17v4"/>',
    doc: '<path d="M6.5 3.5h7.5l4 4v13h-11.5z"/><path d="M13.5 3.5v4.5h4.5M9 12h6M9 15.5h6"/>',
    note: '<rect x="4.5" y="3.5" width="15" height="17" rx="2.5"/><path d="M4.5 8h15M8.5 12h7M8.5 15.5h4.5"/>',
    message: '<path d="M12 4.5c4.7 0 8.5 3 8.5 6.8s-3.8 6.7-8.5 6.7c-1 0-2-.1-2.9-.4L5 19.5l1.2-3.6c-1.7-1.2-2.7-2.8-2.7-4.6 0-3.8 3.8-6.8 8.5-6.8z"/>',
    checklist: '<path d="M4 6.5l1.6 1.6L8.5 5.2M4 12.5l1.6 1.6 2.9-2.9M4 18.5l1.6 1.6 2.9-2.9M11.5 7h8.5M11.5 13h8.5M11.5 19h8.5"/>',
    search: '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5"/>',
  };
  const icon = key => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[key] || ICONS.plug}</svg>`;

  // One line on where a connection stands (what a phone or sfctl is told, health.words, in short).
  function lineOf(st, d) {
    const route = routeOf(d, st.route);
    const rn = route ? pick(route.name) : '';
    switch (st.state) {
      case 'waiting_user': {
        const s = st.step;
        return t(`Connecting${s ? `, step ${s.index} of ${s.total}` : ''}`, `連結中${s ? `，第 ${s.index} 步，共 ${s.total} 步` : ''}`);
      }
      case 'verifying': return t('Verifying it works…', '正在驗證通不通…');
      case 'connected': return [t('Connected', '已連結'), st.via ? viaName(st.via) : rn, st.connected_at && !detectedVia(st) ? dateOf(st.connected_at) : '',
        pick(st.problem && st.problem.text)].filter(Boolean).join(' · ');
      case 'failed': return pick(st.problem && st.problem.text) || t('It did not connect', '沒連結成功');
      case 'needs_reconnect': return pick(st.problem && st.problem.text) || t('It stopped working. Connect it again', '用不了了，需要重新連結');
      default: return d ? pick(d.summary) : t('Not connected yet', '還沒連結');
    }
  }
  const routeOf = (d, id) => ((d && d.routes) || []).find(r => r.id === id) || null;

  // ── the catalog: Settings → Connect ──────────────────────────────────────
  // UI-only state: the search words, the bubble, which list (home / every service / the connected ones), the
  // overflow bubbles shown, the row whose "why not" is open. Every fact about a service is L's.
  let panel = null, settingsTimer = 0;
  const C = { q: '', cat: 'all', view: 'home', more: false, why: '' };
  const REC_MAX = 6;                                  // rows under "Recommended" before "See all"
  const PILL_MAX = 4;                                 // category bubbles before "More ▾"
  async function refreshList(args) {
    const r = await call('connect_list', json(args));
    if (r && r.success && Array.isArray(r.services)) {
      L.detect = r.detect && typeof r.detect === 'object' ? r.detect : null;
      L.order = r.services.map(s => s.service);
      L.by = {};
      r.services.forEach(take);
      L.clis = Array.isArray(r.clis) ? r.clis : [];
      L.catalog = {};
      (Array.isArray(r.catalog) ? r.catalog : []).forEach(e => { if (e && e.id) L.catalog[e.id] = e; });
      L.platform = String(r.platform || '');
      L.loaded = true;
    }
    return r;
  }
  // A Python side from before the catalog sends no `catalog`: every service is then connectable and has no bubble.
  const entry = id => L.catalog[id] || { id, summary: null, category: null, recommended: false, local_app: false, available: true, why: null };
  const nameOf = id => pick((L.by[id] || {}).name) || id;
  const linked = id => (L.by[id] || {}).state === 'connected';                 // "N connected": the ones that work
  const needy = id => !!(L.by[id] || {}).attention;                            // "N to fix": failed, needs reconnecting
  const elsewhere = id => entry(id).why === 'not_on_platform';             // made for another kind of computer
  const hereName = () => (L.platform === 'mac' ? t('This Mac', '這台 Mac') : L.platform === 'windows' ? t('This PC', '這台電腦') : t('This computer', '這台電腦'));
  const hereBubble = () => (L.platform === 'mac' ? 'Mac' : hereName());
  function categories() {                              // one bubble per category id, the fullest first
    const seen = {};
    L.order.forEach(id => {
      const c = entry(id).category;
      if (!c || !c.id || elsewhere(id)) return;
      (seen[c.id] = seen[c.id] || { id: c.id, name: c.name, n: 0 }).n += 1;
    });
    return Object.values(seen).sort((a, b) => b.n - a.n || String((a.name || {}).en || a.id).localeCompare(String((b.name || {}).en || b.id)));
  }
  function matches(id, q) {
    const e = entry(id), st = L.by[id] || {}, c = e.category || {};
    const hay = [id, (st.name || {}).en, (st.name || {}).zh, (e.summary || {}).en, (e.summary || {}).zh, (c.name || {}).en, (c.name || {}).zh]
      .filter(Boolean).join(' ').toLowerCase();
    return q.toLowerCase().split(/\s+/).filter(Boolean).every(w => hay.includes(w));
  }
  const byName = ids => ids.slice().sort((a, b) => nameOf(a).localeCompare(nameOf(b)));
  function sections() {
    const all = L.order.filter(id => L.by[id]);
    const q = C.q.trim();
    if (q) return [{ key: 'search', title: t('Results', '搜尋結果'), ids: byName(all.filter(id => matches(id, q))),
      empty: t(`Nothing matches “${q}”.`, `找不到符合「${q}」的服務。`) }];
    if (C.view === 'attention') return [{ key: 'attention', title: t('To fix', '要處理'), back: true, ids: byName(all.filter(needy)),
      empty: t('Nothing needs fixing.', '沒有要處理的。') }];
    if (C.view === 'connected') return [{ key: 'connected', title: t('Connected', '已連結'), back: true, ids: byName(all.filter(linked)),
      empty: t('Nothing is connected yet.', '還沒有連結任何服務。') }];
    if (C.view === 'all') return [{ key: 'all', title: t('All services', '所有服務'), back: true, ids: byName(all) }];
    const here = all.filter(id => !elsewhere(id));
    if (C.cat === 'local') return [{ key: 'local', title: hereName(), ids: here.filter(id => entry(id).local_app) }];
    if (C.cat !== 'all') {
      const c = categories().find(x => x.id === C.cat);
      return [{ key: 'cat', title: c ? pick(c.name) : '', ids: here.filter(id => (entry(id).category || {}).id === C.cat) }];
    }
    const shown = new Set(), out = [];
    const add = (key, title, ids, extra) => {
      ids = ids.filter(id => !shown.has(id));
      if (!ids.length) return;
      ids.forEach(id => shown.add(id));
      out.push(Object.assign({ key, title, ids }, extra || {}));
    };
    add('attn', t('Needs you', '需要你處理'), here.filter(id => (L.by[id] || {}).attention));
    add('rec', t('Recommended for you', '為你推薦'), here.filter(id => entry(id).recommended && !entry(id).local_app).slice(0, REC_MAX), { all: true });
    add('local', hereName(), here.filter(id => entry(id).local_app));
    add('rest', out.length ? t('More services', '更多服務') : t('Services', '服務'), here);
    return out;
  }
  function rowHtml(id) {
    const st = L.by[id], e = entry(id), d = descDone[id];
    const off = !e.available && st.state === 'not_started';           // listed, but it cannot be connected here
    const s = stateOf(st), bad = !!st.attention;
    let line = lineOf(st, d), sub = '', button;
    if (off) {
      const p = st.problem || {};
      line = pick(e.summary) || pick(p.text);
      button = `<button type="button" class="cn-btn cg-off-btn" data-cn="why" data-service="${esc(id)}" aria-expanded="${C.why === id}">` +
        `${esc(elsewhere(id) ? t('Not on this computer', '這台不能用') : t('Not yet', '還不能連結'))}</button>`;
      if (C.why === id) sub = `<small class="cg-why">${esc(pick(p.text))}${p.next ? ' ' + esc(pick(p.next)) : ''}</small>`;
    } else {
      if (st.state === 'not_started') line = pick(e.summary) || line;
      if (bad && st.problem && st.problem.next) sub = `<small>${esc(pick(st.problem.next))}</small>`;
      else if (st.state !== 'connected' && foundHint(st)) sub = `<small class="cg-found">${esc(foundHint(st))}</small>`;
      const cls = bad || st.state === 'failed' ? ' primary' : st.state === 'connected' ? ' ghost cg-done' : '';
      const label = st.state === 'connected' ? '✓ ' + t(s[3], s[4]) : t(s[3], s[4]);
      button = `<button type="button" class="cn-btn${cls}" data-cn="open" data-service="${esc(id)}">${esc(label)}</button>`;
    }
    return `<div class="cn-row cg-row${bad ? ' attn' : ''}${off ? ' cg-off' : ''} tone-${s[2]}" data-service="${esc(id)}">` +
      `<div class="cn-ico">${icon(st.icon)}</div>` +
      `<div class="cn-main"><b>${esc(nameOf(id))}${e.beta && !off ? `<i class="cn-badge cg-beta">${esc(t('Beta', '測試版'))}</i>` : ''}` +
      `${bad ? `<i class="cn-flag">${esc(t('Needs you', '要處理'))}</i>` : ''}</b>` +
      `<span>${off || st.state === 'not_started' ? '' : `<i class="cn-dot ${s[2]}"></i>`}${esc(line)}</span>${sub}</div>${button}</div>`;
  }
  function bodyHtml() {
    if (!L.order.length) return `<p class="cn-empty">${esc(L.loaded ? t('No services to connect yet.', '目前沒有可以連結的服務。') : t('Reading…', '讀取中…'))}</p>`;
    return sections().map(sec => {
      const right = sec.back ? `<button type="button" class="cn-link" data-cg="home">‹ ${esc(t('Back', '返回'))}</button>`
        : sec.all ? `<button type="button" class="cn-link" data-cg="all">${esc(t('See all', '查看全部'))}</button>` : '';
      const rows = sec.ids.map(rowHtml).join('');
      return `<section class="cg-sec" data-sec="${esc(sec.key)}"><div class="cg-sec-head"><h4>${esc(sec.title)}</h4>${right}</div>` +
        (rows ? `<div class="cg-grid">${rows}</div>` : `<p class="cn-empty">${esc(sec.empty || t('Nothing here yet.', '這裡還沒有服務。'))}</p>`) + '</section>';
    }).join('');
  }
  function pillsHtml() {
    const searching = !!C.q.trim();
    const pill = (id, label, extra) => `<button type="button" class="cg-pill${!searching && C.view === 'home' && C.cat === id ? ' on' : ''}" ` +
      `data-cg="pill" data-id="${esc(id)}" aria-pressed="${!searching && C.view === 'home' && C.cat === id}"${extra || ''}>${esc(label)}</button>`;
    const cats = categories();
    const head = cats.slice(0, PILL_MAX), rest = cats.slice(PILL_MAX);
    const hasLocal = L.order.some(id => entry(id).local_app && !elsewhere(id));
    const inRest = rest.some(c => c.id === C.cat);
    return pill('all', t('All', '全部')) + (hasLocal ? pill('local', hereBubble()) : '') + head.map(c => pill(c.id, pick(c.name))).join('') +
      (rest.length ? `<button type="button" class="cg-pill cg-more${inRest && !searching ? ' on' : ''}" data-cg="more" aria-expanded="${C.more}">${esc(t('More', '更多'))} ▾</button>` +
        (C.more ? `<div class="cg-more-list">${rest.map(c => pill(c.id, pick(c.name))).join('')}</div>` : '') : '');
  }
  // 「不分享任務給隔壁電腦」: on = a paired computer asking for this one's tasks is refused (frame_link._bots_route)
  function shareOff() { const h = host(), c = h && h.cfg && h.cfg(); return !!(c && c.settings && c.settings.link_share_tasks_off === true); }
  function renderShare() {
    const row = panel && panel.querySelector('.cg-share');
    if (!row) return;
    row.querySelector('.sx-label').textContent = t("Don't share tasks with other computers", '不分享任務給隔壁電腦');
    row.querySelector('.sx-hint').textContent = t("A paired computer can't see this one's task list; its board says this computer doesn't share them.",
      '開啟後，配對的電腦看不到這台的任務清單，它的看板會顯示這台沒有開放。');
    const sw = row.querySelector('.toggle-switch');
    sw.classList.toggle('on', shareOff());
    sw.setAttribute('aria-checked', String(shareOff()));
    sw.setAttribute('aria-label', row.querySelector('.sx-label').textContent);
  }
  async function toggleShare() {
    const h = host(), c = h && h.cfg && h.cfg(), a = api();
    if (!c || !a) return;
    c.settings = c.settings || {};
    c.settings.link_share_tasks_off = !shareOff();           // through the page's own settings copy: save_settings replaces the whole dict
    renderShare();
    try { await a.save_settings(JSON.stringify(c.settings)); } catch (_) { /* the page keeps it; the next save carries it */ }
  }
  function renderList() {
    if (!panel) return;
    if (!panel.querySelector('.cg-body')) {          // the frame once: the search box keeps its focus and words across redraws
      panel.innerHTML = `<div class="cg-head"><h3>${esc(t('Connect apps', '連結應用程式'))}</h3><span class="cg-head-r"><button type="button" class="cg-fix" data-cg="attention" hidden></button>` +
        '<button type="button" class="cg-count" data-cg="connected"></button></span></div>' +
        '<p class="cn-intro"></p><p class="cg-detect" hidden><span></span> <button type="button" class="cn-link" data-cg="detect"></button></p>' +
        `<label class="cg-search">${icon('search')}<input type="search" autocomplete="off" spellcheck="false"></label>` +
        '<div class="cg-pills" role="group"></div><div class="cg-body"></div>' +
        // Paired computers: one switch, in settings.link_share_tasks_off (default off = tasks are shared, as before)
        '<div class="sx-row cg-share"><div class="sx-txt"><span class="sx-label"></span><div class="sx-hint"></div></div>' +
        '<div class="sx-ctl"><button type="button" class="toggle-switch" id="setting-link-share-off" role="switch"></button></div></div>';
      const box = panel.querySelector('.cg-search input');
      box.addEventListener('input', () => { C.q = box.value; C.why = ''; renderList(); });
      box.addEventListener('keydown', e => { if (e.key === 'Escape' && box.value) { e.preventDefault(); e.stopPropagation(); box.value = ''; C.q = ''; renderList(); } });
    }
    const n = L.order.filter(linked).length, k = L.order.filter(needy).length;
    const count = panel.querySelector('.cg-count'), fix = panel.querySelector('.cg-fix');
    count.textContent = t(`${n} connected ›`, `已連結 ${n} 個 ›`);
    count.classList.toggle('on', C.view === 'connected');
    fix.hidden = !k;
    fix.textContent = t(`${k} to fix ›`, `${k} 個要處理 ›`);
    fix.classList.toggle('on', C.view === 'attention');
    const box = panel.querySelector('.cg-search input');
    box.placeholder = t('Search apps', '搜尋應用程式');
    box.setAttribute('aria-label', box.placeholder);
    panel.querySelector('.cg-head h3').textContent = t('Connect apps', '連結應用程式');
    renderShare();
    panel.querySelector('.cn-intro').textContent = t('Services your bots can use. Press Connect and ShellBot walks you through it, and checks later that it still works.',
      '讓 bot 能用的外部服務。按「連結」，ShellBot 一步一步帶你連結，之後也會檢查它還通不通。');
    const det = panel.querySelector('.cg-detect'), D = L.detect;
    det.hidden = !D || (!D.running && !D.at);
    if (D) {
      const at = D.at ? new Date(D.at * 1000).toLocaleTimeString(isZh() ? 'zh-TW' : undefined, { hour: '2-digit', minute: '2-digit' }) : '';
      det.querySelector('span').textContent = D.running ? t('Looking for connections already set up on this computer…', '正在找這台電腦上已經設好的連線…')
        : t(`Connections already set up on this computer are counted (looked at ${at}).`, `這台電腦上已經設好的連線也算在內（${at} 找過）。`);
      const again = det.querySelector('button');
      again.hidden = !!D.running;
      again.textContent = t('Look again', '重新找');
    }
    panel.querySelector('.cg-pills').innerHTML = pillsHtml();
    panel.querySelector('.cg-body').innerHTML = bodyHtml();
    const tab = doc.querySelector('#settings-tabs-bar [data-tab="connect"]');
    if (tab) tab.textContent = t('Connect', '連結') + (attention().length ? ' ●' : '');
  }
  function onCatalog(e) {
    const g = e.target.closest('[data-cg]');
    if (g) {
      const what = g.dataset.cg;
      if (what === 'pill') { C.cat = g.dataset.id; C.view = 'home'; C.more = C.more && categories().slice(PILL_MAX).some(c => c.id === C.cat); }
      else if (what === 'more') C.more = !C.more;
      else if (what === 'connected' || what === 'attention') C.view = C.view === what ? 'home' : what;
      else if (what === 'all') C.view = 'all';
      else if (what === 'home') C.view = 'home';
      else if (what === 'detect') return lookAgain();
      if (what !== 'more') { C.q = ''; const box = panel.querySelector('.cg-search input'); if (box) box.value = ''; }
      C.why = '';
      return renderList();
    }
    const w = e.target.closest('[data-cn="why"]');
    if (w) { C.why = C.why === w.dataset.service ? '' : w.dataset.service; return renderList(); }
    const b = e.target.closest('[data-cn="open"]');
    if (b) open(b.dataset.service, {});
  }
  const panelShown = () => !!(panel && panel.classList.contains('active') && $('modal-settings') && $('modal-settings').classList.contains('open'));
  async function refreshStatuses() {                    // the health of what is connected: connect_status(refresh=true)
    const ids = L.order.filter(id => ['connected', 'needs_reconnect', 'failed'].includes((L.by[id] || {}).state));
    const rs = await Promise.all(ids.map(id => call('connect_status', id, json({ refresh: true }))));
    rs.forEach(r => { if (r && r.success && r.status) take(r.status); });
    renderList();
    renderSide();
  }
  async function showList() {
    renderList();
    await refreshList({ detect: true });                // also: look for what is already set up, when the last look is old
    renderList();
    renderSide();
    watchDetect(true);
    L.order.forEach(id => { if (entry(id).available) describe(id).then(() => renderList()); });
    clearInterval(settingsTimer);
    refreshStatuses();
    settingsTimer = setInterval(() => {
      if (!panelShown()) return clearInterval(settingsTimer);
      refreshList({ detect: true }).then(() => { watchDetect(true); refreshStatuses(); });
    }, 60000);
  }
  // While the Python side looks for connections already set up, ask again every 2.5 s (a minute at most), then
  // redraw: the catalog, the sidebar, and the card when it shows a service.
  let detectTimer = 0, detectTries = 0;
  function watchDetect(fresh) {
    clearTimeout(detectTimer);
    if (fresh) detectTries = 0;
    if (!(L.detect && L.detect.running) || ++detectTries > 24) return;
    detectTimer = setTimeout(async () => {
      await refreshList();
      renderList(); renderSide();
      if (!(L.detect && L.detect.running)) cardFound();
      watchDetect();
    }, 2500);
  }
  async function lookAgain() {
    await call('connect_detect', '{}');
    await refreshList();                                // a quick look may be over already: take the list as it is now
    renderList(); renderSide(); cardFound();
    watchDetect(true);
  }
  function mountSettingsTab() {
    const bar = $('settings-tabs-bar');
    const modal = doc.querySelector('#modal-settings .modal');
    if (!bar || !modal || $('settings-panel-connect')) return;
    const tab = doc.createElement('button');
    tab.className = 'settings-tab';
    tab.dataset.tab = 'connect';
    tab.setAttribute('data-guide', 'connect');
    bar.insertBefore(tab, bar.querySelector('[data-tab="bridge"]'));               // right after the launch presets: the connections, then the two bridges
    panel = doc.createElement('div');
    panel.className = 'settings-tab-panel cn-panel';
    panel.id = 'settings-panel-connect';
    modal.insertBefore(panel, modal.querySelector('.modal-footer'));
    tab.addEventListener('click', () => {
      modal.querySelectorAll('.settings-tab').forEach(x => x.classList.remove('active'));
      modal.querySelectorAll('.settings-tab-panel').forEach(x => x.classList.remove('active'));
      tab.classList.add('active');
      panel.classList.add('active');
      showList();
    });
    panel.addEventListener('click', onCatalog);
    panel.addEventListener('click', e => { if (e.target.closest('#setting-link-share-off')) toggleShare(); });
    const gear = $('btn-settings');
    if (gear) gear.addEventListener('click', renderList);                          // the label follows the page's language
    renderList();
  }

  // ── the wizard card ──────────────────────────────────────────────────────
  // UI-only state lives here: which route is picked before connect_start, the stuck panel, the test's
  // handle and timer. Where the person is (route, step, results) is the Python side's: `view`.
  let S = null, card = null, lastFocus = null, pollTimer = 0, waitTimer = 0;
  const fresh = (service, from) => ({ open: true, service, desc: null, status: null, view: null, mode: 'load', pick: '', stuck: false,
    prob: '', busy: '', err: null, test: null, from: from || '', checked: {}, opts: {}, optsFor: '', note: '', opened: {}, away: 0 });
  const routes = () => (S.desc && S.desc.routes) || [];
  // Start without the choice of a way: there is one way, or the recommended one is the Claude account's connector
  // (a browser and a click: the other ways stay one "Change way" away).
  const quick = () => routes().length === 1 || ((routes().find(r => r.id === S.pick) || {}).kind === 'claude_connector');
  const vstep = () => (S.view && S.view.step) || null;
  const vsteps = () => (S.view && S.view.steps) || [];
  const stepIdx = () => { const s = vstep(); return s ? vsteps().findIndex(x => x.id === s.id) : -1; };
  const isTest = s => !!s && s.kind === 'test';
  const curRoute = () => routeOf(S.desc, (S.view && S.view.route && S.view.route.id) || (S.status && S.status.route));
  const verdictOf = code => {
    const r = curRoute();
    const v = r && r.test && r.test.verdicts && r.test.verdicts[code];
    return v || (S.desc && S.desc.problems && S.desc.problems[code]) || null;
  };

  function absorb(res) {
    if (!res) return;
    if (res.status) { S.status = res.status; take(res.status); }
    if (res.view !== undefined) S.view = res.view;
    S.err = res.success === false ? (res.message || { text: t('That did not work.', '這個沒有成功。'), next: '' }) : null;
  }
  const tone = x => (['ok', 'good', 'pass'].includes(x) ? 'ok' : ['bad', 'error', 'fail'].includes(x) ? 'bad' : 'warn');

  // pieces
  const btn = (act, label, cls, extra) =>
    `<button type="button" class="cn-btn ${cls || ''}" data-act="${act}"${S.busy ? ' disabled' : ''}${extra || ''}>${esc(label)}</button>`;
  const link = (act, label, extra) => `<button type="button" class="cn-link" data-act="${act}"${extra || ''}>${esc(label)}</button>`;
  const errBox = () => {
    const m = S.err || (vstep() && vstep().message);
    if (!m || !(m.text || m.next)) return '';
    return `<div class="cn-msg bad" role="alert"><span class="cn-dot bad"></span><span>${esc(pick(m.text))}` +
      (m.next ? `<small>${esc(pick(m.next))}</small>` : '') + '</span></div>';
  };
  // The check as the description defines it (when it runs, its states) and what it came to. The Python side's
  // `view` puts the check's result (code, tone, words filled in) in step.check; an older side put the definition there.
  const stepDef = id => ((curRoute() || {}).steps || []).find(x => x.id === id) || null;
  function resultBox(step) {
    const def = stepDef(step.id);
    const got = step.check && step.check.code !== undefined && !step.check.on ? step.check : null;
    const r = step.result || got;
    const c = (def && def.check) || (got ? null : step.check);
    if (!r) {
      if (!c) return '';
      if (S.busy === 'check') return `<div class="cn-msg"><span class="cn-dot busy"></span><span>${esc(t('Checking…', '檢查中…'))}</span></div>`;
      return c.on === 'done' ? `<div class="cn-msg"><span class="cn-dot idle"></span><span>${esc(t('Not checked yet', '還沒檢查'))}</span>${btn('check', t('Check now', '現在檢查'), 'ghost small')}</div>` : '';
    }
    const st = c && c.states && c.states[r.code];
    const text = pick(r.text) || (st && pick(st.text)) || '';
    const hint = r.hint ? pick(r.hint) : st && st.hint ? pick(st.hint) : '';
    return `<div class="cn-msg ${tone(r.tone || (st && st.tone))}" data-code="${esc(r.code || '')}"><span class="cn-dot ${tone(r.tone || (st && st.tone))}"></span>` +
      `<span>${esc(text)}${hint ? `<small>${esc(hint)}</small>` : ''}</span>${c ? btn('check', t('Check again', '再檢查一次'), 'ghost small') : ''}</div>`;
  }
  function actionsBox(step) {
    if (!(step.actions || []).length && !(step.options || []).length) return '';
    const opts = (step.options || []).map(o => `<button type="button" class="cn-opt${S.opts[o.id] ? ' on' : ''}" role="checkbox" aria-checked="${!!S.opts[o.id]}" ` +
      `data-act="opt" data-id="${esc(o.id)}"><i class="cn-box"></i><span>${esc(pick(o.label))}</span></button>`).join('');
    const acts = (step.actions || []).map(a => btn('do', S.busy === 'act:' + a.id ? t('Working…', '處理中…') : pick(a.label), 'primary', ` data-id="${esc(a.id)}"`)).join('');
    return `<div class="cn-acts">${opts}<div class="cn-acts-row">${acts}</div></div>`;
  }
  function stuckPanel(step) {
    const items = (step.stuck || []).map(p => {
      const on = S.prob === p.id;
      const links = (p.links || []).map(l => (l.step
        ? link('goto', pick(l.label) + ' ›', ` data-id="${esc(l.step)}"`)
        : link('url', pick(l.label) + ' ↗', ` data-url="${esc(l.url)}"`))).join('');
      return `<div class="cn-prob${on ? ' on' : ''}"><button type="button" class="cn-prob-q" data-act="problem" data-id="${esc(p.id)}" aria-expanded="${on}">` +
        `<i class="cn-caret"></i><span>${esc(pick(p.q))}</span></button>` +
        (on ? `<div class="cn-prob-a"><p>${esc(pick(p.a))}</p>${links ? `<div class="cn-prob-links">${links}</div>` : ''}</div>` : '') + '</div>';
    }).join('');
    const route = curRoute();
    const what = [S.desc ? pick(S.desc.name) : '', route ? pick(route.name) : '', t(`step ${stepIdx() + 1}`, `第 ${stepIdx() + 1} 步`), pick(step.title)].filter(Boolean).join(' · ');
    return `<div class="cn-stuck"><div class="cn-stuck-head">${esc(t('Where are you stuck? Pick one to see what to do.', '卡在哪裡？點一個看怎麼辦'))}</div>${items || ''}` +
      `<div class="cn-stuck-acts">${btn('ask', t('Ask a bot to look', '請 bot 幫我看'), 'primary')}${btn('stuck', t('Back to this step', '回到這一步'), 'ghost')}</div>` +
      `<p class="cn-note">${esc(t('What goes to the bot is only:', '會帶給 bot 的只有：'))} <code>${esc(what)}${S.prob ? ' · ' + esc(t('stuck on one of the problems above', '卡在上面勾的問題')) : ''}</code><br>` +
      `${esc(t('No passwords, keys or codes.', '不會帶密碼、金鑰或任何代碼。'))}</p></div>`;
  }

  // the test: where it is, and what it came to
  function testBlock(step) {
    const tt = S.test;
    const route = curRoute();
    const via = route && route.test && route.test.via ? pick(route.test.via) : '';
    if (!tt) return step && step.intro ? `<p class="cn-lead">${esc(pick(step.intro))}</p>` : '';
    if (tt.state === 'running') {
      return (step && step.intro ? `<p class="cn-lead">${esc(pick(step.intro))}</p>` : '') +
        `<div class="cn-run"><span class="cn-spin" aria-hidden="true"></span><span>${esc(t('Testing it for real…', '正在實際測試…'))}${via ? `<small>${esc(via)}</small>` : ''}</span></div>` +
        `<p class="cn-note">${esc(t('This usually takes under a minute.', '通常不到一分鐘。'))}</p>`;
    }
    if (tt.state === 'needs_user') {
      const v = verdictOf(tt.code) || {};
      return `<div class="cn-warn"><b>${esc(pick(v.text) || t('It is waiting for you', '它在等你處理'))}</b>${v.next ? `<p>${esc(pick(v.next))}</p>` : ''}</div>`;
    }
    if (tt.ok) {
      return `<div class="cn-result ok"><i class="cn-big ok" aria-hidden="true">✓</i><div><h4>${esc(t('Connected', '已連結'))}</h4>` +
        `<p>${esc(t('The test passed', '測試通過了'))}${via ? ' · ' + esc(via) : ''}</p></div></div>`;
    }
    const v = verdictOf(tt.code) || (tt.message ? tt.message : null) || { text: t('The test did not pass', '測試沒有通過'), next: '' };
    const at = tt.failed_step ? vsteps().findIndex(x => x.id === tt.failed_step) : -1;
    return `<div class="cn-result bad"><i class="cn-big bad" aria-hidden="true">✕</i><div><h4>${esc(pick(v.text))}</h4>` +
      (v.next ? `<p>${esc(pick(v.next))}</p>` : '') + (at >= 0 ? `<p class="cn-dim">${esc(t(`It points at step ${at + 1}.`, `看起來是第 ${at + 1} 步沒做好。`))}</p>` : '') + '</div></div>';
  }
  function testFoot(step) {
    const tt = S.test;
    if (!tt) return `<div class="cn-foot-l"></div><div class="cn-foot-r">${btn('test-start', t('Start the test', '開始測試'), 'primary')}</div>`;
    if (tt.state === 'running' || tt.state === 'needs_user') return `<div class="cn-foot-l"></div><div class="cn-foot-r">${btn('test-stop', t('Stop the test', '停止測試'), 'ghost')}</div>`;
    if (tt.ok) return `<div class="cn-foot-l"></div><div class="cn-foot-r">${btn('finish', t('Done', '完成'), 'primary')}</div>`;
    const at = tt.failed_step ? vsteps().findIndex(x => x.id === tt.failed_step) : -1;
    return `<div class="cn-foot-l">${btn('ask', t('Ask a bot to look', '請 bot 幫我看'), 'ghost')}</div><div class="cn-foot-r">` +
      btn('test-start', t('Test again', '重新測試'), at >= 0 ? 'ghost' : 'primary') +
      (at >= 0 ? btn('goto', t(`Back to step ${at + 1}`, `回到第 ${at + 1} 步`), 'primary', ` data-id="${esc(tt.failed_step)}"`) : '') + '</div>';
  }
  async function startTest() {
    clearTimeout(pollTimer);
    S.test = { state: 'running', handle: '', stopped: false };
    S.busy = '';
    render();
    const mine = S.test, me = S;
    const res = await call('connect_test', S.service, '{}');
    if (S !== me || S.test !== mine || mine.stopped) { if (res && res.result && res.result.handle) call('connect_test', S.service, json({ handle: res.result.handle, cancel: true })); return; }
    gotTest(res);
  }
  function gotTest(res) {
    const tt = S.test;
    if (!tt) return;
    if (res.status) { S.status = res.status; take(res.status); }
    const r = res.result;
    if (!res.success || !r) { S.test = { state: 'done', ok: false, code: res.code || 'error', message: res.message }; return render(); }
    tt.state = r.state === 'done' ? 'done' : r.state === 'needs_user' ? 'needs_user' : 'running';
    tt.handle = r.handle || tt.handle;
    tt.ok = !!r.ok; tt.code = r.code || r.phase || ''; tt.failed_step = r.failed_step || '';
    if (tt.state === 'done' && tt.ok && S.status) S.status = Object.assign({}, S.status, { state: 'connected', attention: false, problem: null });
    render();
    if (tt.state !== 'done') { clearTimeout(pollTimer); pollTimer = setTimeout(pollTest, 1500); }
  }
  async function pollTest() {
    const tt = S && S.open && S.test;
    if (!tt || tt.stopped || tt.state === 'done') return;
    const me = S;
    const res = await call('connect_test', S.service, json({ handle: tt.handle }));
    if (S !== me || S.test !== tt || tt.stopped) return;
    gotTest(res);
  }
  async function stopTest() {
    const tt = S.test;
    clearTimeout(pollTimer);
    if (!tt) return;
    tt.stopped = true;
    S.test = null;
    render();
    if (tt.handle) await call('connect_test', S.service, json({ handle: tt.handle, cancel: true }));
  }

  // What was found on this computer for one way, as a chip on its card in the choice of a way.
  function foundChip(id) {
    const p = protos(S.status).find(x => x.id === id);
    if (!p || p.state === 'none') return '';
    const f = FOUND[p.state] || FOUND.none;
    return `<span class="cn-route-found tone-${f[3]}" data-found="${esc(p.state)}">${f[0]} ${esc(t(f[1], f[2]))}${p.adopted ? esc(yours()) : ''}</span>`;
  }
  // Every way to connect the service, with what was found for it on this computer and where ShellBot's own steps are.
  function protocolsBox(st) {
    const ps = protos(st);
    if (!ps.length) return '';
    const rows = ps.map(p => {
      const r = routeOf(S.desc, p.id) || {};
      const f = FOUND[p.state] || FOUND.none;
      const inUse = st.state === 'connected' && st.via && st.via.route === p.id;
      const where = (p.places || []).map(x => cliName(x.cli) + (x.entry ? t(': ', '：') + x.entry : '') + (x.adopted ? yours() : '')).join(t('; ', '；'));
      const why = p.why && WHY[p.why] && p.state !== 'checked' ? t(WHY[p.why][0], WHY[p.why][1]) : '';
      const su = p.setup && p.setup.state !== 'connected' ? p.setup : null;
      const setup = !su ? '' : su.step ? t(`ShellBot's setup: step ${su.step.index} of ${su.step.total}`, `ShellBot 的設定：做到第 ${su.step.index} 步（共 ${su.step.total} 步）`)
        : t(`ShellBot's setup: ${stateOf(su)[0].toLowerCase()}`, `ShellBot 的設定：${stateOf(su)[1]}`);
      const go = su && !inUse && st.state === 'connected' ? link('resume', t('Continue it', '接著做') + ' ›', ` data-route="${esc(p.id)}"`) : '';
      return `<div class="cn-proto tone-${f[3]}${inUse ? ' on' : ''}" data-route="${esc(p.id)}" data-found="${esc(p.state)}">` +
        `<span class="cn-proto-mark" aria-hidden="true">${f[0]}</span>` +
        `<span class="cn-proto-main"><b>${esc(kindName(p.kind) || pick(r.name))}${inUse ? `<i class="cn-badge">${esc(t('In use', '使用中'))}</i>` : ''}</b>` +
        `<small>${esc([pick(r.name), where, why, setup].filter(Boolean).join(' · '))}</small>${go}</span>` +
        `<span class="cn-proto-state">${esc(t(f[1], f[2]))}</span></div>`;
    }).join('');
    return `<div class="cn-sect cn-protos"><b>${esc(t('Ways to connect it', '連線方式'))}</b>${rows}</div>`;
  }

  // route choice
  function routeBody() {
    const list = routes();
    const cards = list.map(r => {
      const on = S.pick === r.id;
      const cliKeys = Object.keys(r.clis || {}).filter(k => (r.clis[k] || {}).via && r.clis[k].via !== 'none');
      const chips = [t(`${(r.steps || []).length} steps`, `${(r.steps || []).length} 步`), r.minutes ? t(`about ${r.minutes} min`, `約 ${r.minutes} 分鐘`) : '']
        .concat((r.needs || []).map(pick)).filter(Boolean);
      return `<button type="button" class="cn-route${on ? ' on' : ''}" role="radio" aria-checked="${on}" data-act="pick" data-route="${esc(r.id)}">` +
        `<span class="cn-radio"></span><span class="cn-route-main"><span class="cn-route-head"><b>${esc(pick(r.name))}</b>` +
        (r.recommended ? `<i class="cn-badge">${esc(t('Recommended', '推薦'))}</i>` : '') +
        (r.beta ? `<i class="cn-badge cg-beta">${esc(t('Beta', '測試版'))}</i>` : '') + '</span>' +
        `<span class="cn-route-blurb">${esc(pick(r.blurb))}</span>` + foundChip(r.id) +
        `<span class="cn-chips">${chips.map(c => `<i>${esc(c)}</i>`).join('')}</span>` +
        (cliKeys.length ? `<span class="cn-route-for">${esc(t('For: ', '適用：'))}${esc(cliKeys.map(cliName).join(t(', ', '、')))}</span>` : '') + '</span></button>';
    }).join('');
    return `<h3 class="cn-title" id="cn-title">${esc(t('How do you want to connect it?', '想用哪一種方式連結？'))}</h3>` +
      `<div class="cn-routes" role="radiogroup">${cards}</div>`;
  }
  function stepBody() {
    const step = vstep();
    if (!step) return `<p class="cn-lead">${esc(t('Reading…', '讀取中…'))}</p>`;
    const head = `<h3 class="cn-title" id="cn-title">${esc(pick(step.title))}</h3>`;
    if (isTest(step)) return head + errBox() + testBlock(step);
    if (S.stuck) return head + stuckPanel(step);
    const how = (step.how || []).length ? `<ol class="cn-how">${step.how.map(l => `<li>${esc(pick(l))}</li>`).join('')}</ol>` : '';
    const see = step.see ? `<div class="cn-see"><b>${esc(t('What you should see', '看到這個就對了'))}</b><p>${esc(pick(step.see))}</p></div>` : '';
    const note = step.note ? `<div class="cn-fyi"><b>${esc(t('Good to know', '先知道'))}</b><p>${esc(pick(step.note))}</p></div>` : '';
    return head + how + actionsBox(step) + resultBox(step) + errBox() + see + note;
  }
  function connectedBody() {
    const st = S.status || {};
    if (detectedVia(st)) return foundBody(st);
    const route = routeOf(S.desc, st.route);
    const clis = Object.keys(st.clis || {}).map(k => {
      const c = CLI_STATE[st.clis[k]] || CLI_STATE.unknown;
      return `<div class="cn-kv"><span>${esc(cliName(k))}</span><span><i class="cn-dot ${c[2]}"></i>${esc(t(c[0], c[1]))}</span></div>`;
    }).join('');
    const scopes = (st.scopes || []).map(id => {
      const sc = ((S.desc && S.desc.scopes) || []).find(x => x.id === id);
      return `<i>${esc(sc ? pick(sc.label) : id)}</i>`;
    }).join('');
    const warn = st.problem ? `<div class="cn-msg warn"><span class="cn-dot warn"></span><span>${esc(pick(st.problem.text))}${st.problem.next ? `<small>${esc(pick(st.problem.next))}</small>` : ''}</span></div>` : '';
    return `<div class="cn-result ok"><i class="cn-big ok" aria-hidden="true">✓</i><div><h4>${esc(t('Connected', '已連結'))}</h4>` +
      `<p>${esc([st.via ? viaName(st.via) : route ? pick(route.name) : '', st.connected_at ? dateOf(st.connected_at) : ''].filter(Boolean).join(' · '))}</p></div></div>` + warn +
      (S.test ? testBlock(null) : '') + errBox() +
      (clis ? `<div class="cn-sect"><b>${esc(t('Your AIs', '各個 AI'))}</b>${clis}</div>` : '') +
      (scopes ? `<div class="cn-sect"><b>${esc(t('What it may do', '它能做的事'))}</b><div class="cn-chips">${scopes}</div></div>` : '') +
      protocolsBox(st);
  }
  // Connected through a way found on this computer (ShellBot did not set it up): which one, whose setting, every way.
  function foundBody(st) {
    const v = st.via, where = cliName(v.cli);
    const note = v.adopted
      ? t(`ShellBot found "${v.entry}" in ${where} and uses it as it is: it never changes or removes it. What it may do is up to your setting; it may not be read-only.`,
        `ShellBot 在 ${where} 裡找到「${v.entry}」，照原樣使用：不會改它，也不會刪它。它能做哪些事由你的設定決定，不一定是唯讀。`)
      : t(`${where} reports it as connected. It was not set up in ShellBot.`, `${where} 回報它已經連上；不是在 ShellBot 裡設定的。`) +
        (v.kind === 'claude_connector' ? t(' Only Claude tabs can use it.', '只有 Claude 分頁用得到。') : '');
    return `<div class="cn-result ok"><i class="cn-big ok" aria-hidden="true">✓</i><div><h4>${esc(t('Connected', '已連結'))}</h4>` +
      `<p>${esc(viaName(v))}</p></div></div>` +
      `<div class="cn-fyi"><b>${esc(v.adopted ? t('Your own setting', '你自己的設定') : t('Found on this computer', '在這台電腦上找到的'))}</b><p>${esc(note)}</p></div>` +
      errBox() + protocolsBox(st);
  }

  function progress() {
    if (S.mode !== 'step') return '';
    const steps = vsteps(), cur = stepIdx();
    const back = `<button type="button" class="cn-back" data-act="back" aria-label="${esc(t('Back', '上一步'))}" title="${esc(t('Back', '上一步'))}"${S.busy || (cur <= 0 && routes().length <= 1) ? ' disabled' : ''}>‹</button>`;
    const passed = !!(S.test && S.test.ok);
    const segs = steps.map((s, i) => {
      const cls = /fail|bad/.test(String(s.state || '')) ? 'bad' : (passed || i < cur) ? 'done' : i === cur ? 'now' : '';
      return `<i class="${cls}"${i < cur ? ` data-act="goto" data-id="${esc(s.id)}"` : ''}></i>`;
    }).join('');
    const route = curRoute();
    const more = routes().length > 1 && !(S.test && S.test.ok) ? ` · ${link('reroute', t('Change way', '換方式'))}` : '';
    return `<div class="cn-prog">${back}<div class="cn-segs">${segs}</div><span class="cn-prog-label">${esc(t(`Step ${cur + 1} of ${steps.length}`, `第 ${cur + 1} 步，共 ${steps.length} 步`))}` +
      `${route ? ' · ' + esc(pick(route.name)) : ''}${more}</span></div>`;
  }
  function foot() {
    if (S.mode === 'route') {
      return `<div class="cn-foot-l"></div><div class="cn-foot-r">${btn('close', t('Cancel', '取消'), 'ghost')}${btn('route-go', t('Next', '下一步'), 'primary')}</div>`;
    }
    if (S.mode === 'connected' && detectedVia(S.status)) {      // nothing of ShellBot's to test or remove on this way
      return `<div class="cn-foot-l">${S.status.route ? btn('disconnect', t("Clear ShellBot's setup", '清掉 ShellBot 的設定'), 'ghost') : ''}</div>` +
        `<div class="cn-foot-r">${btn('detect', S.busy === 'detect' ? t('Looking…', '找中…') : t('Look again', '重新找'), 'ghost')}${btn('finish', t('Done', '完成'), 'primary')}</div>`;
    }
    if (S.mode === 'connected') {
      return `<div class="cn-foot-l">${btn('disconnect', t('Disconnect', '中斷連結'), 'ghost')}</div><div class="cn-foot-r">` +
        (S.test && S.test.state !== 'done' ? btn('test-stop', t('Stop the test', '停止測試'), 'ghost')
          : btn('test-start', t('Test again', '重新測試'), 'ghost')) +
        btn('reconnect', t('Connect again', '重新連結'), 'ghost') + btn('finish', t('Done', '完成'), 'primary') + '</div>';
    }
    const step = vstep();
    if (!step) return `<div class="cn-foot-l"></div><div class="cn-foot-r">${btn('close', t('Close', '關閉'), 'ghost')}</div>`;
    if (isTest(step)) return testFoot(step);
    const open = httpsOnly(step.url) ? btn('open', (step.in_browser ? t('Open it again', '再打開一次') : t('Open page', '打開頁面')) + ' ↗', 'ghost', ` data-url="${esc(step.url)}"`) : '';
    const did = step.done_label ? pick(step.done_label) : step.in_browser ? t('Done, test it', '連好了，測試') : t('I did it', '我做好了');
    return `<div class="cn-foot-l">${open}</div><div class="cn-foot-r">${S.stuck ? '' : btn('stuck', t("I'm stuck", '卡住了'), 'warn')}${btn('did', did, 'primary')}</div>`;
  }

  const keyOf = el => [el.dataset.act, el.dataset.id, el.dataset.route].join('|');
  function render() {
    if (!card || !S || !S.open) return;
    const focusKey = card.contains(doc.activeElement) && doc.activeElement.dataset && doc.activeElement.dataset.act ? keyOf(doc.activeElement) : '';
    const name = pick((S.desc && S.desc.name) || (S.status && S.status.name)) || S.service;
    const st = S.status;
    const sub = st && S.mode !== 'route' ? t(stateOf(st)[0], stateOf(st)[1]) : '';
    let body;
    if (S.mode === 'load') body = `<p class="cn-lead">${esc(t('Reading…', '讀取中…'))}</p>${errBox()}`;
    else if (S.mode === 'route') body = routeBody() + errBox();
    else if (S.mode === 'connected') body = connectedBody();
    else body = stepBody();
    card.dataset.mode = S.mode === 'step' ? (isTest(vstep()) ? 'test' : S.stuck ? 'stuck' : 'step') : S.mode;
    card.innerHTML = `<div class="cn-head"><span class="cn-head-ico">${icon(st && st.icon)}</span><h2>${esc(name)}</h2><span class="cn-sub">${esc(sub)}</span>` +
      `<button type="button" class="cn-x" data-act="close" aria-label="${esc(t('Close', '關閉'))}" title="${esc(t('Close', '關閉'))}">×</button></div>` +
      progress() + `<div class="cn-body">${body}</div>` +
      (S.mode === 'load' ? '' : `<div class="cn-foot">${foot()}</div>`);
    if (focusKey) {
      const again = Array.from(card.querySelectorAll('[data-act]')).find(x => keyOf(x) === focusKey && !x.disabled);
      if (again) again.focus();
    }
  }

  // doing
  function enter() {
    S.stuck = false; S.prob = '';
    const step = vstep();
    if (step && S.optsFor !== step.id) {                 // a step's tick boxes start where its descriptor says
      S.opts = {}; (step.options || []).forEach(o => { S.opts[o.id] = !!o.default; });
      S.optsFor = step.id;
    }
    if (!isTest(step) && S.test && S.mode === 'step') { clearTimeout(pollTimer); S.test = null; }
    if (step && step.in_browser && httpsOnly(step.url) && !S.opened[step.id]) {    // done in the browser: open it now
      S.opened[step.id] = true;
      S.away = Date.now();
      openUrl(step.url);
    }
    render();
    follow();
    if (step && step.check && step.check.on === 'enter' && !step.result && !S.checked[step.id]) { S.checked[step.id] = true; return runCheck(); }
  }
  // A result the description marks "poll": an action that started something the person finishes elsewhere (a sign-in in
  // the browser). Ask again with the action it names, quietly, until the result stops asking; the step moves on by itself.
  function pollOf(step) {
    const r = step && step.result, def = r && stepDef(step.id);
    const a = def && (def.actions || []).find(x => x.id === r.action);
    return (a && a.results && a.results[r.code] && a.results[r.code].poll) || '';
  }
  function follow() {
    clearTimeout(waitTimer);
    const me = S, s = me && me.open && me.mode === 'step' ? vstep() : null, id = pollOf(s);
    if (!id) return;
    const sid = s.id;
    waitTimer = setTimeout(async () => {
      if (S !== me || !S.open || !vstep() || vstep().id !== sid) return;
      if (S.busy || S.stuck) return follow();
      const res = await call('connect_step', S.service, 'act', json({ action: id, options: S.opts }));
      if (S !== me || !S.open) return;
      absorb(res);
      if (res && res.success !== false && vstep() && vstep().id !== sid) { S.checked[sid] = true; return enter(); }
      render();
      follow();
    }, 2500);
  }
  // Back from the browser: the step that was done there counts as done, and the test runs (didIt starts it).
  function cameBack() {
    const st = S && S.open && S.mode === 'step' ? vstep() : null;
    if (!st || !st.in_browser || !S.away || S.busy || S.stuck || Date.now() - S.away < 1500) return;
    S.away = 0;
    didIt();
  }
  async function step(op, args, busy) {
    const me = S;
    S.busy = busy || op;
    render();
    const res = await call('connect_step', S.service, op, json(args));
    if (S !== me) return Object.assign({}, res, { success: false, closed: true });       // closed meanwhile: nothing to draw
    S.busy = '';
    absorb(res);
    return res;
  }
  async function runCheck() {
    const me = S, sid = (vstep() || {}).id;
    await step('check', null, 'check');
    if (S !== me) return;
    if (vstep() && vstep().id !== sid) { S.checked[sid] = true; return enter(); }   // it passed and the next step is up
    render();
  }
  async function runAction(id) {
    const s = vstep();
    const a = ((s && s.actions) || []).find(x => x.id === id);
    if (!a) return;
    const me = S;
    const res = await step('act', { action: id, options: S.opts }, 'act:' + id);
    if (S !== me) return;
    if (res && res.success !== false && a.after === 'check' && s.check) { await runCheck(); return follow(); }
    render();
    follow();
  }
  async function didIt() {
    const me = S, was = vstep() || {}, before = was.id;
    S.away = 0;
    const res = await step('done', null);
    if (S !== me) return;
    if (res.success !== false && (vstep() || {}).id !== before) {
      S.checked[before] = true;
      enter();
      if (was.in_browser && isTest(vstep()) && !S.test) startTest();      // back from the browser: test it at once
      return;
    }
    render();
  }
  async function goBack() {
    if (stepIdx() <= 0) {                                 // before the first step is the choice of a way
      if (routes().length > 1) { S.pick = (S.view && S.view.route && S.view.route.id) || S.pick; S.mode = 'route'; S.err = null; return render(); }
      return;
    }
    const me = S;
    await step('back', null);
    if (S === me) enter();
  }
  async function gotoStep(id) {                           // the Python side has `back`, so go back until the step is on screen
    const me = S, target = vsteps().findIndex(x => x.id === id);
    const now = await call('connect_status', S.service, '{}');   // a failed test may already have moved it there: start from where it is
    if (S !== me) return;
    if (now && now.success && now.view) { S.status = now.status || S.status; S.view = now.view; }
    for (let guard = 0; target >= 0 && S === me && stepIdx() > target && guard < 20; guard++) {
      const res = await step('back', null);
      if (res.success === false) break;
    }
    if (S !== me) return;
    S.test = null;
    enter();
  }
  async function pickRoute() {
    if (!S.pick) return;
    if (S.pick === (S.status && S.status.route) && vstep()) { S.mode = 'step'; return enter(); }    // the way it is already on: nothing to restart
    const me = S;
    S.busy = 'route'; render();
    const has = S.status && S.status.route && S.view && S.view.step;
    const res = has && S.pick !== S.status.route ? await call('connect_step', S.service, 'route', json({ route: S.pick }))
      : await call('connect_start', S.service, json({ route: S.pick }));
    if (S !== me) return;
    S.busy = '';
    absorb(res);
    if (res.success === false || !vstep()) return render();
    S.mode = 'step'; S.test = null;
    enter();
  }
  async function reconnect() {
    const me = S;
    S.busy = 'reconnect'; render();
    const res = await call('connect_start', S.service, json({ reconnect: true }));
    if (S !== me) return;
    S.busy = '';
    absorb(res);
    S.test = null;
    if (res.success === false || !vstep()) return render();
    S.mode = 'step';
    enter();
  }
  async function askBot() {
    const me = S, from = S.from || (host() && host().active && host().active()) || '';
    S.busy = 'ask'; render();
    const res = await call('connect_step', S.service, 'stuck', json({ problem: S.prob, from_sid: from }));
    if (S !== me) return;
    S.busy = '';
    const sid = res && (res.sid || (res.details && res.details.sid));
    if (res && res.success !== false && sid) {
      close();
      try { const h = host(); if (h && h.switchTab) h.switchTab(sid); } catch (_) { /* the tab opens anyway */ }
      return;
    }
    S.err = (res && res.message) || { text: t('Could not reach a bot.', '找不到可以問的 bot。'), next: t('Open a tab and tell its AI where you are stuck.', '開一個分頁，把卡住的地方告訴它的 AI。') };
    render();
  }
  async function disconnect() {
    const name = pick((S.desc && S.desc.name) || '') || S.service;
    const ask = root.SFDialog && root.SFDialog.confirm;
    const own = protos(S.status).flatMap(p => (p.places || []).filter(x => x.adopted).map(x => x.entry)).filter((x, i, a) => x && a.indexOf(x) === i);
    const keep = own.length ? t(` Your own setting (${own.join(', ')}) stays as it is: ShellBot never removes it.`, `你自己的設定（${own.join('、')}）會照原樣留著：ShellBot 不會刪它。`) : '';
    const text = detectedVia(S.status)
      ? t(`Clear ShellBot's own setup for ${name}? Only what ShellBot added goes; it keeps working the way it does now.`, `要清掉 ShellBot 為 ${name} 做的設定嗎？只拿掉 ShellBot 加的東西，現在能用的照樣能用。`)
      : t(`Disconnect ${name}? Bots lose access to it; you can connect it again any time.`, `要中斷 ${name} 嗎？bot 會用不了它，之後隨時可以再連結。`);
    const yes = ask ? await ask(text + keep,
      { title: t('Disconnect', '中斷連結'), ok: detectedVia(S.status) ? t('Clear it', '清掉') : t('Disconnect', '中斷連結'), cancel: t('Keep it', '保留') }) : false;
    const me = S;
    if (!yes || S !== me) return;
    S.busy = 'disconnect'; render();
    const res = await call('connect_disconnect', S.service, '{}');
    if (S !== me) return;
    S.busy = '';
    if (res && res.success === false) { S.err = res.message || null; return render(); }
    close();
  }

  // "Continue it": go on with ShellBot's own steps on a way that is not the one in use.
  async function resume(routeId) {
    const me = S;
    S.busy = 'resume'; render();
    const res = await call('connect_start', S.service, json({ route: routeId }));
    if (S !== me) return;
    S.busy = '';
    absorb(res);
    if (res.success === false || !vstep()) return render();
    S.mode = 'step'; S.test = null;
    enter();
  }
  // "Look again" on the card: the Python side looks in the background; ask until it is done, then redraw this service.
  async function cardLookAgain() {
    const me = S;
    S.busy = 'detect'; render();
    await call('connect_detect', '{}');
    await refreshList();
    for (let i = 0; i < 40 && S === me && L.detect && L.detect.running; i++) {
      await new Promise(ok => setTimeout(ok, 1500));
      await refreshList();
    }
    if (S !== me) return;
    S.busy = '';
    renderList(); renderSide();
    const now = await call('connect_status', S.service, '{}');
    if (S !== me) return;
    absorb(now);
    if (S.status && S.status.state !== 'connected') return open(S.service, {});
    render();
  }
  // A look finished while the card shows a service: take its new status (the card is not moved off a step).
  async function cardFound() {
    if (!S || !S.open || !['connected', 'route'].includes(S.mode) || S.busy) return;
    const me = S, now = await call('connect_status', S.service, '{}');
    if (S !== me || !now || !now.success) return;
    S.status = now.status; take(now.status);
    if (S.mode === 'route' && S.status.state === 'connected') S.mode = 'connected';
    render();
  }

  async function onClick(e) {
    const el = e.target.closest('[data-act]');
    if (!el || !S || !S.open || el.disabled) return;
    e.preventDefault();
    const act = el.dataset.act;
    if (act === 'close' || act === 'finish') return close();
    if (act === 'pick') { S.pick = el.dataset.route; return render(); }
    if (act === 'route-go') return pickRoute();
    if (act === 'reroute') { S.pick = (S.view && S.view.route && S.view.route.id) || S.pick; S.mode = 'route'; S.err = null; return render(); }
    if (act === 'back') return goBack();
    if (act === 'goto') return gotoStep(el.dataset.id);
    if (act === 'open' || act === 'url') return openUrl(el.dataset.url);
    if (act === 'stuck') { S.stuck = !S.stuck; S.err = null; return render(); }
    if (act === 'problem') { S.prob = S.prob === el.dataset.id ? '' : el.dataset.id; return render(); }
    if (act === 'check') return runCheck();
    if (act === 'did') return didIt();
    if (act === 'opt') { S.opts[el.dataset.id] = !S.opts[el.dataset.id]; return render(); }
    if (act === 'do') return runAction(el.dataset.id);
    if (act === 'ask') return askBot();
    if (act === 'test-start') return startTest();
    if (act === 'test-stop') return stopTest();
    if (act === 'reconnect') return reconnect();
    if (act === 'disconnect') return disconnect();
    if (act === 'resume') return resume(el.dataset.route);
    if (act === 'detect') return cardLookAgain();
  }
  function onKey(e) {
    if (!S || !S.open) return;
    if (e.key === 'Escape') {
      if (root.SFDialog && root.SFDialog.isOpen && root.SFDialog.isOpen()) return;      // its own Esc
      e.preventDefault(); e.stopPropagation(); close();
    } else if (e.key === 'Tab' && card) {                // the keyboard stays inside the card
      const f = Array.from(card.querySelectorAll('button:not([disabled])'));
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (!card.contains(doc.activeElement)) { e.preventDefault(); first.focus(); }
      else if (e.shiftKey && doc.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && doc.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }
  function mount() {
    if (card) return true;
    if (!doc.body) return false;
    const el = doc.createElement('div');
    el.id = 'modal-connect';
    el.className = 'modal-overlay cn-overlay';
    el.innerHTML = '<div class="cn-card" id="cn-card" role="dialog" aria-modal="true" aria-labelledby="cn-title" tabindex="-1"></div>';
    doc.body.appendChild(el);
    card = el.querySelector('#cn-card');
    el.addEventListener('click', onClick);
    doc.addEventListener('keydown', onKey, true);
    root.addEventListener('focus', cameBack);
    doc.addEventListener('visibilitychange', () => { if (!doc.hidden) cameBack(); });
    return true;
  }

  // Open the wizard for a service. o.tag: the [[SF:RED:connect:…]] tag (connect_signal); o.reconnect;
  // o.from_sid: the tab a "stuck" message may go to first.
  async function open(service, o) {
    o = o || {};
    if (!service || !mount()) return false;
    clearTimeout(pollTimer);
    S = fresh(service, o.from_sid);
    lastFocus = doc.activeElement;
    card.parentNode.classList.add('open');
    render();
    card.focus();
    const mine = S;
    const [d, first] = await Promise.all([describe(service), o.tag ? call('connect_signal', o.tag)
      : o.reconnect ? call('connect_start', service, json({ reconnect: true })) : call('connect_status', service, '{}')]);
    if (S !== mine || !S.open) return true;
    S.desc = d;
    absorb(first);
    if (first && first.service && first.service !== service) S.service = first.service;
    if (!d || !S.status) { S.mode = 'load'; if (!S.err) S.err = { text: t('This service is not available here.', '這個服務在這裡不能用。'), next: '' }; return render(), true; }
    if (!routes().length) {                             // listed before it can be connected: say why, open no step
      S.mode = 'load';
      S.err = S.err || (d.unavailable ? { text: d.unavailable.text, next: d.unavailable.next || '' } : { text: t('This service cannot be connected yet.', '這個服務還不能連結。'), next: '' });
      return render(), true;
    }
    const state = S.status.state;
    S.pick = ((routes().find(r => r.recommended) || routes()[0]) || {}).id || '';
    if (o.tag || o.reconnect) S.mode = 'step';
    else if (state === 'not_started' && quick()) {        // nothing to choose: start the way at once
      const r = await call('connect_start', service, json({ route: S.pick }));
      if (S !== mine || !S.open) return true;
      absorb(r); S.mode = 'step';
    }
    else if (state === 'not_started') S.mode = 'route';
    else if (state === 'connected') S.mode = 'connected';
    else if (state === 'needs_reconnect') { const r = await call('connect_start', service, json({ reconnect: true })); absorb(r); S.mode = 'step'; }
    else { const r = await call('connect_start', service, '{}'); absorb(r); S.mode = 'step'; }
    if (S.mode === 'step' && !vstep()) { S.mode = routes().length ? 'route' : 'load'; }
    if (S.status && S.status.route && S.mode === 'route') S.pick = S.status.route;
    S.mode === 'step' ? enter() : render();
    return true;
  }
  function close() {
    clearTimeout(pollTimer); clearTimeout(waitTimer);
    if (!S || !S.open) return;
    const tt = S.test, svc = S.service;
    S.open = false;
    card.parentNode.classList.remove('open');
    if (tt && tt.handle && tt.state !== 'done') call('connect_test', svc, json({ handle: tt.handle, cancel: true }));   // a test nobody watches is stopped
    try { if (lastFocus && lastFocus.focus) lastFocus.focus(); } catch (_) { /* gone */ }
    S = null;
    refreshList().then(() => { renderList(); renderSide(); });
  }

  // ── the sidebar card ─────────────────────────────────────────────────────
  const dismissed = new Set();                           // "<service>:<state>" the person closed until it changes
  let sideEl = null, sideTimer = 0;
  function renderSide() {
    const list = $('sidebar-sessions');
    if (!list || !list.parentNode) return;
    if (!sideEl || !sideEl.isConnected) {
      sideEl = doc.createElement('div');
      sideEl.id = 'cn-side';
      list.parentNode.insertBefore(sideEl, $('sfb-primary') || $('sfb-tools') || list);
      sideEl.addEventListener('click', e => {
        const x = e.target.closest('[data-cn-dismiss]');
        if (x) { dismissed.add(x.dataset.cnDismiss); renderSide(); return; }
        const go = e.target.closest('[data-cn-side]');
        if (go) open(go.dataset.cnSide, { reconnect: (L.by[go.dataset.cnSide] || {}).state === 'needs_reconnect' });
      });
    }
    const now = new Set(attention().map(s => s.service));
    dismissed.forEach(k => { if (!now.has(k.split(':')[0])) dismissed.delete(k); });      // it recovered: the next trouble is news again
    const items = attention().filter(s => !dismissed.has(s.service + ':' + s.state));
    const html = items.slice(0, 2).map(s => {
      const name = pick(s.name) || s.service;
      const key = esc(s.service + ':' + s.state);
      return `<div class="cn-sc" role="alert"><div class="cn-sc-text"><b><i class="cn-sc-dot"></i>${esc(s.state === 'failed' ? t(`${name} did not connect`, `${name} 沒連結成功`) : t(`${name} needs reconnecting`, `${name} 需要重新連結`))}</b>` +
        `<span>${esc(pick(s.problem && s.problem.text) || t('Bots cannot use it right now.', 'bot 現在用不了它。'))}</span></div>` +
        `<button type="button" class="cn-sc-x" data-cn-dismiss="${key}" aria-label="${esc(t('Hide', '先不要'))}" title="${esc(t('Hide until it changes', '先收起來，狀況有變再提醒'))}">×</button>` +
        `<button type="button" class="cn-btn small primary" data-cn-side="${esc(s.service)}">${esc(s.state === 'failed' ? t('Fix it', '處理') : t('Reconnect', '重新連結'))}</button></div>`;
    }).join('') + (items.length > 2 ? `<div class="cn-sc-more">${esc(t(`${items.length - 2} more in Settings → Connect`, `另外 ${items.length - 2} 項，到 設定 › 連結 看`))}</div>` : '');
    if (sideEl.dataset.html !== html) { sideEl.innerHTML = html; sideEl.dataset.html = html; }
    sideEl.hidden = !html;
  }
  async function sidePoll() {
    if (doc.hidden || (S && S.open)) return;
    await refreshList();
    renderSide();
    if (panelShown()) renderList();
  }

  // ── the chat card ────────────────────────────────────────────────────────
  // chat.js asks for the card of a [[SF:RED:connect:<service>]] question (decision.tag "connect:<service>").
  // '' when this is not one, or the page does not know the service: the question stays a plain question.
  const tagOf = d => {
    const m = d && d.kind === 'red' && /^(?:connect:)?([A-Za-z0-9_-]{1,40})$/.exec(String(d.tag || ''));
    return m && String(d.tag).startsWith('connect:') ? m[1] : (m && L.by[m[1]] ? m[1] : '');
  };
  function chatCard(d, o) {
    o = o || {};
    const id = tagOf(d);
    if (!id) return '';
    const st = L.by[id];
    if (!st) { if (!L.loaded) refreshList(); return ''; }
    const name = pick(st.name) || id;
    const opts = o.options || [];
    const go = opts.find(x => Number(x.n) === 1) || opts[0] || null;     // option 1 connects, whatever order the bot printed them in
    const off = o.answered ? ' disabled' : '';
    return `<div class="cm-ask-card">${o.face || ''}<div class="cm-ask-body">` +
      `<div class="cm-ask-title">${esc(t('Your call · ', '要你決定 · ') + t(`${name} needs connecting`, `需要連結 ${name}`))}</div>` +
      `<div class="cm-ask-q">${esc(d.reason || t(`This tab has no ${name} tools. Connect it now?`, `這個分頁沒有 ${name} 的工具。要不要現在連結？`))}</div>` +
      `<div class="cm-opts"><button class="cm-opt tone-yes" data-act="connect" data-service="${esc(id)}" data-tag="${esc(d.tag)}" ` +
      `data-n="${esc(go ? go.n : '')}" data-id="${esc(d.id)}"${off}>${esc(t(`Connect ${name} step by step`, `一步一步連結 ${name}`))}</button>` +
      opts.filter(x => x !== go).map(x => `<button class="cm-opt" data-act="answer" data-n="${esc(x.n)}" data-id="${esc(d.id)}"${off}>${esc(x.label)}</button>`).join('') + '</div>' +
      `<div class="cm-ask-note">${esc(t('"Step by step" opens the wizard and answers the bot with option 1, so it waits for you.',
        '「一步一步連結」會打開精靈，同時回覆 bot 選項 1，請它先等你。'))}</div>` +
      (o.answered ? `<div class="cm-ask-note">${esc(o.answered)}</div>` : '') + '</div></div>';
  }
  const openFromChat = (service, o) => open(service, { tag: (o && o.tag) || 'connect:' + service, from_sid: o && o.from_sid });

  // ── start ────────────────────────────────────────────────────────────────
  root.SFConnect = { open, close, openFromChat, chatCard, tagOf, refresh: refreshList, poll: sidePoll, state: () => S, list: () => L };
  let tries = 0;
  function boot() {
    const a = api();
    if (!a || typeof a.connect_list !== 'function') {         // the Python side may not be ready yet, or be an older one
      if (++tries < 60) setTimeout(boot, 500);
      return;
    }
    mountSettingsTab();
    refreshList().then(() => { renderList(); renderSide(); });
    clearInterval(sideTimer);
    sideTimer = setInterval(sidePoll, 60000);
  }
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', boot);
  else boot();
})(typeof window !== 'undefined' ? window : null);
