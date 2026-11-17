/* ShellBot office (settings.experimental_office, off by default) — see docs/ai-skill.md §4.10.
 *
 * Draws the bots (staff, 正職), the AI tabs that are not bots (interns, 實習生) and the subagents a
 * Claude Code tab has launched (part-timers, 工讀生) as one office floor. The floor itself is
 * web/office.html (Canvas2D, no network), loaded in an <iframe> over the whole window so its CSS
 * and DOM cannot clash with this page. This file:
 *
 *   - puts the switch next to the other experimental switches (Settings → General) and, while it is
 *     on, an icon in the sidebar's foot (#sb-footer-row). Off: no button, no iframe, no timer.
 *   - builds the office's snapshot from what the page already holds: the sidebar's dots
 *     (sessions[sid]._dotClass, the same light the tab shows), bots.js's roster and its paired
 *     machines' bots (SFBots.state / peerBots: already polled), plus two cheap calls made only
 *     while the office is open: tasks_overview (one small SQLite read, at most every 9 s) and
 *     office_helpers (list + stat of the open Claude tabs' subagents folders; office_helpers.py).
 *     Nothing here reads a screen or a transcript.
 *   - pushes it every 3 s while the office is open and the window is in front:
 *     iframe.contentWindow.Office.update(snapshot).
 *   - keeps the office's seats, accessories and view mode for this computer: pywebview starts
 *     in private mode, which clears localStorage on every start, so office.html stores them
 *     through SFOffice.host and this file saves them with office_prefs_save (office.json in
 *     the state folder). config.json is not touched.
 *
 * The mapping (build, track, tabState, peerState) is pure and runs in node:
 * tests_office_snapshot.js. The overlay is covered by tests_office_ui.py.
 */
(function (root) {
  'use strict';

  // ── pure: what the page knows → the office's snapshot ──────────────────────
  const DOT_STATE = { 'sig-working': 'working', 'sig-decision': 'decision', 'sig-stuck': 'stuck', 'sig-done': 'done' };
  // A tab's light (index.html's 400 ms loop) as the office's state. 'on attention' is the screen's
  // own "a menu is up" guess: the light shows it the way it shows a question.
  function tabState(dot) {
    const d = String(dot || '');
    if (DOT_STATE[d]) return DOT_STATE[d];
    if (/\battention\b/.test(d)) return 'decision';
    return 'idle';
  }
  // Another machine's bot or tab, from its GET /link/bots row (link_bots.py keeps it).
  function peerState(row) {
    const r = row || {};
    if (r.decision || r.activity === 'decision') return 'decision';
    if (r.blocked || r.activity === 'stuck') return 'stuck';
    if (r.activity === 'working' || r.activity === 'done') return r.activity;
    return 'idle';
  }
  const CLI_NAMES = { claude: 'Claude Code', codex: 'Codex', opencode: 'OpenCode', pi: 'pi', grok: 'Grok Build',
                      cursor: 'Cursor Agent', 'cursor-agent': 'Cursor Agent', agy: 'Antigravity', gemini: 'Gemini CLI' };
  // The program a tab's command runs, past `cd … &&`, `env`, `VAR=…` and a `sf-` launcher prefix.
  function cliName(cmd) {
    let words = String(cmd || '').trim();
    const amp = words.lastIndexOf('&&');
    if (amp >= 0) words = words.slice(amp + 2);
    const parts = words.trim().split(/\s+/).filter(w => w && w !== 'env' && w !== 'exec' && !/^[A-Za-z_][A-Za-z0-9_]*=/.test(w));
    const base = String(parts[0] || '').split(/[\\/]/).pop().replace(/\.(exe|cmd)$/i, '').toLowerCase().replace(/^sf-/, '');
    if (!base) return '';
    const key = Object.keys(CLI_NAMES).find(k => base === k || base.startsWith(k + '-'));
    return key ? CLI_NAMES[key] : base;
  }
  const ROSTER_EPOCH = Date.UTC(2026, 0, 1);
  const iso = ms => new Date(ms).toISOString();
  const clip = (s, n) => { const t = String(s || '').replace(/\s+/g, ' ').trim(); return t.length > n ? t.slice(0, n - 1) + '…' : t; };
  // A face as the office draws it: a shape it knows and a colour as #rrggbb (bots.js's palette keys
  // differ from the prototype's, which would turn every unknown name blue).
  function avatar(av, seed, kit) {
    const k = kit || {};
    const d = k.defaultAvatar ? k.defaultAvatar(seed) : { shape: 'round', color: 'gray' };
    const shape = av && k.shapes && k.shapes.indexOf(av.shape) >= 0 ? av.shape : (av && av.shape && !k.shapes ? av.shape : d.shape);
    const want = av && av.color ? av.color : d.color;
    const hex = c => (/^#[0-9a-f]{6}$/i.test(c || '') ? c : (k.palette || {})[c] || '');
    return { shape, color: hex(want) || hex(d.color) || '#8b90a0' };
  }
  const STATUS_RANK = { doing: 0, waiting: 1, todo: 2 };
  // The open task a person owns: what is being done before what waits, before what is to do; the
  // most recently touched first. `stale` is the store's own verdict (sfctl task stuck).
  function taskFor(owners, tasks, known) {
    const open = ((tasks && tasks.open) || []).filter(x => x && owners.indexOf(String(x.owner || '')) >= 0);
    if (!open.length) return null;
    open.sort((a, b) => (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9) || (b.updated_at || 0) - (a.updated_at || 0));
    const x = open[0];
    const stuck = new Set(((tasks && tasks.stuck) || []).map(s => s && s.id));
    const ref = who => (who === 'user' || known.has(who) ? who : '');
    const waiting = String(x.waiting_on || '');
    const from = String(x.requester || '');
    return { id: '#' + x.id, title: String(x.title || ''), status: x.status, next: String(x.next_step || ''),
             waiting_on: waiting === 'user' || known.has(waiting) ? waiting : (waiting || null),
             from: from && owners.indexOf(from) < 0 ? (ref(from) || null) : null,
             updated_at: iso(Number(x.updated_at || 0) * 1000), stale: stuck.has(x.id) };
  }
  function runsOf(e) {
    const pin = e.pin || {};
    return [(e.cli && e.cli.label) || '', pin.model ? (pin.label || pin.model) : 'CLI 預設'].filter(Boolean).join(' · ');
  }

  /* input:
       botsOn    bots are on (settings.experimental_bots); off = every AI tab is an intern
       roster    bots_state().roster: role → {label, responsibility, bot: {avatar, primary, memory, external}, cli, pin}
       primary   the primary bot's role
       tabs      this computer's tabs in sidebar order: [{sid, label, kind: bot|temp|terminal, role, dot, cmd, line}]
       tasks     tasks_overview(): {open: [...], stuck: [...]}
       peers     SFBots.peerBots(): pid → {name, primary, bots: [/link/bots rows], tabs: {rsid: tab}}
       helpers   office_helpers().helpers: sid → [{id, description, model, agent_type, state, started_at, last_at}]
       seen      sid → first time this page saw the tab (ms); filled in for new ones (an intern's opened_at)
       kit       {palette, shapes, defaultAvatar} (SFBots)
       now       ms
     output: the snapshot office.html's Office.update takes (its §15). */
  function build(inp) {
    const I = inp || {}, now = I.now || Date.now(), seen = I.seen || {}, kit = I.kit || {};
    const roster = I.botsOn ? (I.roster || {}) : {};
    const roles = Object.keys(roster).filter(r => roster[r] && roster[r].bot && !roster[r].bot.external);
    const known = new Set(roles);
    const tabs = (I.tabs || []).map(x => Object.assign({}, x, I.botsOn ? {} : { role: '', kind: x.kind === 'bot' ? 'temp' : x.kind }));
    const tabOf = {};
    for (const x of tabs) if (x.role && known.has(x.role) && !tabOf[x.role]) tabOf[x.role] = x;
    const order = Object.keys(roster);
    const bots = roles.map(role => {
      const e = roster[role], b = e.bot || {}, tab = tabOf[role];
      return { id: role, name: String(e.label || role), avatar: avatar(b.avatar, role, kit), primary: role === I.primary,
               duty: String(e.responsibility || ''), created_at: iso(ROSTER_EPOCH + order.indexOf(role) * 60000),
               state: tab ? tabState(tab.dot) : 'asleep', task: taskFor([role], I.tasks, known),
               runs: runsOf(e), memory: b.memory !== false, sid: tab ? tab.sid : '', line: tab ? clip(tab.line, 120) : '' };
    });
    let k = 0;
    const temps = tabs.filter(x => x.kind === 'temp' && !(x.role && known.has(x.role))).map(x => {
      if (!seen[x.sid]) seen[x.sid] = now + (k++);
      return { id: x.sid, name: String(x.label || x.sid), cli: cliName(x.cmd) || '?', state: tabState(x.dot),
               task: taskFor([String(x.label || ''), x.sid].filter(Boolean), I.tasks, known),
               opened_at: iso(seen[x.sid]), line: clip(x.line, 120) };
    });
    for (const sid of Object.keys(seen)) if (!tabs.some(x => x.sid === sid)) delete seen[sid];
    const peers = Object.keys(I.peers || {}).map(pid => {
      const p = I.peers[pid] || {};
      return {
        id: pid, name: String(p.name || pid.slice(0, 8)),
        bots: (p.bots || []).filter(b => b && b.role && b.state !== 'external').map((b, i) => ({
          id: String(b.role), name: String(b.label || b.role), avatar: avatar(b.avatar, `${p.name || ''}/${b.role}`, kit),
          primary: !!b.primary, duty: String(b.responsibility || ''), created_at: iso(ROSTER_EPOCH + i * 60000),
          state: b.state === 'asleep' ? 'asleep' : peerState(b), runs: String(b.runs_on || ''), memory: b.memory !== false })),
        temps: Object.keys(p.tabs || {}).filter(r => (p.tabs[r] || {}).kind === 'temp').map((r, i) => {
          const tb = p.tabs[r];
          return { id: r, name: String(tb.name || r), cli: CLI_NAMES[tb.cli] || String(tb.cli || '?'), state: peerState(tb),
                   opened_at: iso(ROSTER_EPOCH + i * 1000) };
        }),
      };
    });
    const helpers = [];
    const parentOf = {};
    for (const b of bots) if (b.sid) parentOf[b.sid] = { kind: 'bot', id: b.id, name: b.name };
    for (const x of temps) parentOf[x.id] = { kind: 'temp', id: x.id, name: x.name };
    for (const sid of Object.keys(I.helpers || {})) {
      const par = parentOf[sid];
      if (!par) continue;                          // a helper of a tab the office does not show
      for (const h of I.helpers[sid] || []) {
        if (!h || !h.id) continue;
        helpers.push({ id: `${sid}/${h.id}`, parent: par.id, parent_kind: par.kind, parent_name: par.name,
                       description: String(h.description || ''), model: String(h.model || ''), agent_type: String(h.agent_type || ''),
                       state: h.state === 'stuck' || h.state === 'working' ? h.state : 'idle',
                       started_at: iso(Number(h.started_at || 0) * 1000), last_at: iso(Number(h.last_at || 0) * 1000) });
      }
    }
    for (const b of bots) { delete b.sid; }
    return { bots, temps, peers, helpers, handoffs: [] };
  }

  // What happened, as the office's panel lists it: each person's state changes this page has seen
  // (kept while the app runs; nothing is read to make them). mem: key → {st, events}.
  const EV_KIND = { working: 'work', decision: 'decision', stuck: 'stuck', done: 'done', idle: 'idle', asleep: 'sleep' };
  const EV_WORDS = { working: '在忙', decision: '等你決定', stuck: '卡住了', done: '做完了', idle: '閒下來', asleep: '休息（沒有開分頁）' };
  function track(mem, snap, now) {
    const alive = new Set();
    const one = (key, p, first) => {
      alive.add(key);
      const m = mem[key] || (mem[key] = { st: null, events: [] });
      if (m.st !== p.state) {
        const line = p.line && p.state !== 'asleep' ? '：' + p.line : '';
        m.events.push({ t: now, kind: m.st == null ? 'arrive' : EV_KIND[p.state] || 'note',
                        text: (m.st == null ? first : EV_WORDS[p.state] || p.state) + (m.st == null ? '' : line) });
        if (m.events.length > 30) m.events.splice(0, m.events.length - 30);
        m.st = p.state;
      }
      p.events = m.events.slice();
    };
    for (const b of snap.bots || []) one('b:' + b.id, b, '在辦公室看到他：' + (EV_WORDS[b.state] || b.state));
    for (const x of snap.temps || []) one('t:' + x.id, x, '開了這個分頁（實習生）：' + (EV_WORDS[x.state] || x.state));
    for (const h of snap.helpers || []) one('h:' + h.id, h, `${h.parent_name} 派出去：${clip(h.description, 60)}`);
    for (const key of Object.keys(mem)) if (!alive.has(key)) delete mem[key];
    for (const b of snap.bots || []) delete b.line;
    for (const x of snap.temps || []) delete x.line;
    return snap;
  }

  const pure = { build, track, tabState, peerState, cliName, avatar };
  if (typeof module !== 'undefined' && module.exports) { module.exports = pure; return; }

  // ── the page: switch, button, overlay ────────────────────────────────────
  const host = () => root.__sfHost || null;
  const api = () => (root.pywebview && root.pywebview.api) || null;
  const isZh = () => /^zh/i.test((host() && host().lang && host().lang()) || navigator.language || '');
  const t = (en, zh) => (isZh() ? zh : en);
  const cfg = () => (host() && host().cfg()) || null;
  const enabled = () => { const c = cfg(); return !!(c && c.settings && c.settings.experimental_office === true); };

  let prefs = null, saveTimer = 0;
  const clone = v => (v == null ? v : JSON.parse(JSON.stringify(v)));
  function savePrefs(now) {
    clearTimeout(saveTimer);
    const go = () => { saveTimer = 0; const a = api(); if (a && a.office_prefs_save && prefs) a.office_prefs_save(JSON.stringify(prefs)).catch(() => {}); };
    if (now) go(); else saveTimer = setTimeout(go, 400);
  }
  // What office.html's `store` calls (same origin, synchronous).
  const officeHost = {
    get: k => (prefs && Object.prototype.hasOwnProperty.call(prefs, k) ? clone(prefs[k]) : null),
    set: (k, v) => { prefs = prefs || {}; if (v == null) delete prefs[k]; else prefs[k] = clone(v); savePrefs(false); },
    close: () => close(),
  };

  let overlay = null, frame = null, timer = 0, busy = false, lastPush = null;
  let tasks = { open: [], stuck: [] }, tasksAt = 0, helpers = {};
  const seen = {}, mem = {};
  const TICK_MS = 3000, TASKS_MS = 9000;

  function pageTabs() {
    const H = host(), S = (H && H.sessions()) || {}, B = root.SFBots;
    const order = ((H && H.order && H.order()) || Object.keys(S)).filter(sid => S[sid]);
    for (const sid of Object.keys(S)) if (order.indexOf(sid) < 0) order.push(sid);
    return order.filter(sid => !S[sid].isRemote).map(sid => {
      const s = S[sid] || {};
      let role = '', kind = 'terminal', line = '';
      try { role = (B && B.botOf && B.botOf(sid)) || ''; kind = (B && B.kindOf && B.kindOf(sid)) || 'terminal'; line = (B && B.previewOf && B.previewOf(sid)) || ''; } catch (_) {}
      return { sid, label: s.label || '', kind, role, dot: s._dotClass || '', cmd: s.cmd || '', line };
    });
  }
  function snapshot() {
    const B = root.SFBots || {}, st = (B.state && B.state()) || {};
    const snap = build({
      botsOn: !!(B.enabled && B.enabled()), roster: st.roster || {}, primary: st.primary || '',
      tabs: pageTabs(), tasks, peers: (B.peerBots && B.peerBots()) || {}, helpers, seen,
      kit: { palette: B.PALETTE || {}, shapes: Object.keys(B.SHAPES || {}), defaultAvatar: B.defaultAvatar },
      now: Date.now(),
    });
    return track(mem, snap, Date.now());
  }
  async function refreshData() {
    const a = api();
    if (!a) return;
    if (a.tasks_overview && Date.now() - tasksAt >= TASKS_MS) {
      tasksAt = Date.now();
      try { const r = JSON.parse(await a.tasks_overview()); if (r && r.success) tasks = { open: r.open || [], stuck: r.stuck || [] }; } catch (_) {}
    }
    if (a.office_helpers) {
      try { const r = JSON.parse(await a.office_helpers()); if (r && r.success) helpers = r.helpers || {}; } catch (_) {}
    }
  }
  function push() {
    if (!frame) return false;
    let O = null;
    try { O = frame.contentWindow && frame.contentWindow.Office; } catch (_) { O = null; }
    if (!O || typeof O.update !== 'function') return false;
    lastPush = snapshot();
    try { O.update(lastPush); } catch (e) { try { api().js_debug('office', 'update failed: ' + e); } catch (_) {} }
    return true;
  }
  async function tick() {
    if (!overlay || busy || document.hidden) return;
    busy = true;
    try { await refreshData(); if (overlay) push(); } finally { busy = false; }
  }
  function onKey(e) {
    if (!overlay) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); return; }
    if (!overlay.contains(e.target)) e.stopPropagation();   // keys meant for the office never reach the terminal under it
  }
  function onVisible() { if (!document.hidden && overlay) tick(); }

  async function open() {
    if (!enabled() || overlay) return;
    if (!prefs) {
      try { const r = JSON.parse(await api().office_prefs()); prefs = (r && r.prefs) || {}; } catch (_) { prefs = {}; }
    }
    if (overlay || !enabled()) return;
    overlay = document.createElement('div');
    overlay.id = 'sfo-overlay';
    overlay.tabIndex = -1;
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', t('Office', '辦公室'));
    overlay.innerHTML = `<iframe class="sfo-frame" src="office.html" title="${t('Office', '辦公室')}"></iframe>` +
      `<button class="sfo-close" type="button" aria-label="${t('Close the office (Esc)', '關閉辦公室（Esc）')}" title="${t('Close (Esc)', '關閉（Esc）')}">✕</button>`;
    document.body.appendChild(overlay);
    frame = overlay.querySelector('iframe');
    overlay.querySelector('.sfo-close').addEventListener('click', () => close());
    frame.addEventListener('load', async () => {
      try { frame.contentWindow.focus(); } catch (_) {}
      busy = true;
      try { await refreshData(); } finally { busy = false; }
      push();
    });
    root.addEventListener('keydown', onKey, true);
    document.addEventListener('visibilitychange', onVisible);
    timer = setInterval(tick, TICK_MS);
    try { overlay.focus(); } catch (_) {}
  }
  function close() {
    if (!overlay) return;
    clearInterval(timer); timer = 0;
    root.removeEventListener('keydown', onKey, true);
    document.removeEventListener('visibilitychange', onVisible);
    overlay.remove();                     // the iframe goes with it: no canvas, no timer left behind
    overlay = null; frame = null; busy = false;
    if (saveTimer) savePrefs(true);
    try { const H = host(), s = H && H.sessions()[H.active()]; if (s && s.term) s.term.focus(); } catch (_) {}
  }

  const ICON = '<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5 14.5 5 8 8.5 1.5 5Z" fill="currentColor" opacity=".9"/><path d="M1.5 5v5.5L8 14V8.5Z" fill="currentColor" opacity=".55"/><path d="M14.5 5v5.5L8 14V8.5Z" fill="currentColor" opacity=".35"/></svg>';
  function placeButton() {
    let b = document.getElementById('btn-office');
    if (!enabled()) { if (b) b.remove(); close(); return; }
    const row = document.getElementById('sb-footer-row');
    if (!row) return;
    const name = t('Office (experimental)', '辦公室（實驗性）');
    if (!b) {
      b = document.createElement('button');
      b.id = 'btn-office';
      b.className = 'sb-footer-btn sb-footer-ico';
      b.type = 'button';
      b.innerHTML = `<span class="sb-ico">${ICON}</span><span class="sb-ico-label"></span>`;
      b.addEventListener('click', () => open());
      row.insertBefore(b, document.getElementById('btn-guide') || null);
    }
    b.title = name;
    b.setAttribute('aria-label', name);
    b.querySelector('.sb-ico-label').textContent = name;
  }

  // ── the switch (Settings → General, next to the Loops panel's) ─────────────
  function injectSettings() {
    let sw = document.getElementById('setting-experimental-office');
    if (!sw) {
      const loops = document.getElementById('setting-experimental-loops');
      const cell = loops && loops.parentElement && loops.parentElement.parentElement;
      if (!cell || !cell.parentElement) return;
      const wrap = document.createElement('div');
      wrap.id = 'sfo-setting';
      wrap.innerHTML =
        '<label style="font-size:11px;color:#565f89;display:block;margin-bottom:4px" class="sfo-label"></label>' +
        '<div style="display:flex;align-items:center;gap:6px">' +
        '<button class="toggle-switch" id="setting-experimental-office" data-guide="office"></button>' +
        '<span style="font-size:11px;color:#565f89" class="sfo-hint"></span></div>';
      cell.parentElement.insertBefore(wrap, cell.nextSibling);
      sw = wrap.querySelector('#setting-experimental-office');
      sw.addEventListener('click', async function () {
        this.classList.toggle('on');
        const c = cfg();
        if (!c) return;
        c.settings = c.settings || {};
        c.settings.experimental_office = this.classList.contains('on');
        try { await api().save_settings(JSON.stringify(c.settings)); } catch (_) {}
        placeButton();
      });
    }
    const wrap = document.getElementById('sfo-setting');
    wrap.querySelector('.sfo-label').textContent = t('Office: your bots at their desks (experimental)', '辦公室：bot 坐在自己的桌子前（實驗性）');
    wrap.querySelector('.sfo-hint').textContent = t(
      'A button at the foot of the sidebar opens an office floor: bots (staff) at their desks, AI tabs that are not bots (interns) at a long table, a Claude Code tab\'s subagents (part-timers) beside it; who is busy, waiting on you or stuck. Off by default.',
      '側欄底部多一顆按鈕，打開一層辦公室：bot（正職）坐自己的桌子，不是 bot 的 AI 分頁（實習生）坐長桌，Claude Code 分頁派出去的 subagent（工讀生）站在旁邊；誰在忙、誰在等你、誰卡住，看圖就知道。預設關閉。');
    sw.classList.toggle('on', enabled());
  }

  function boot(tries) {
    const H = host();
    if (!H || !H.cfg() || !api()) {
      if (tries < 240) setTimeout(() => boot(tries + 1), 250);
      return;
    }
    injectSettings();
    placeButton();
    const s = document.getElementById('btn-settings');
    if (s) s.addEventListener('click', () => setTimeout(() => { injectSettings(); placeButton(); }, 0));
  }

  root.SFOffice = Object.assign({ host: officeHost, open, close, isOpen: () => !!overlay, refresh: tick,
                                  lastSnapshot: () => clone(lastPush), placeButton }, pure);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => boot(0));
  else boot(0);
})(typeof window !== 'undefined' ? window : globalThis);
