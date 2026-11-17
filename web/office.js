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
 *     The floor itself reads no screen and no transcript.
 *   - gives the panel of the person being followed their conversation and a message box
 *     (SFOffice.host.chat): the chat view's own routes for that one tab (chat_view every 2 s while
 *     the panel's Conversation tab is showing and the window is in front, chat_send, chat_answer),
 *     shaped by chatShape with web/chat.js's rules. A paired computer's bot or intern with a tab is
 *     reached through that computer's POST /link/chat (link_chat), as the chat view reaches a remote
 *     tab. A bot with no tab (here or there) and a part-timer get no box.
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
               runs: runsOf(e), memory: b.memory !== false, cli: String((e.cli && e.cli.label) || cliName(e.cmd) || ''),
               doing: tab ? clip(tab.line, 120) : '', sid: tab ? tab.sid : '', line: tab ? clip(tab.line, 120) : '' };
    });
    let k = 0;
    const temps = tabs.filter(x => x.kind === 'temp' && !(x.role && known.has(x.role))).map(x => {
      if (!seen[x.sid]) seen[x.sid] = now + (k++);
      return { id: x.sid, name: String(x.label || x.sid), cli: cliName(x.cmd) || '?', state: tabState(x.dot),
               task: taskFor([String(x.label || ''), x.sid].filter(Boolean), I.tasks, known),
               opened_at: iso(seen[x.sid]), doing: clip(x.line, 120), line: clip(x.line, 120) };
    });
    for (const sid of Object.keys(seen)) if (!tabs.some(x => x.sid === sid)) delete seen[sid];
    const peers = Object.keys(I.peers || {}).map(pid => {
      const p = I.peers[pid] || {};
      return {
        id: pid, name: String(p.name || pid.slice(0, 8)),
        bots: (p.bots || []).filter(b => b && b.role && b.state !== 'external').map((b, i) => ({
          id: String(b.role), name: String(b.label || b.role), avatar: avatar(b.avatar, `${p.name || ''}/${b.role}`, kit),
          primary: !!b.primary, duty: String(b.responsibility || ''), created_at: iso(ROSTER_EPOCH + i * 60000),
          state: b.state === 'asleep' ? 'asleep' : peerState(b), runs: String(b.runs_on || ''), memory: b.memory !== false,
          doing: b.state === 'asleep' ? '' : clip(b.latest, 120) })),
        temps: Object.keys(p.tabs || {}).filter(r => (p.tabs[r] || {}).kind === 'temp').map((r, i) => {
          const tb = p.tabs[r];
          return { id: r, name: String(tb.name || r), cli: CLI_NAMES[tb.cli] || String(tb.cli || '?'), state: peerState(tb),
                   doing: clip(tb.latest, 120), opened_at: iso(ROSTER_EPOCH + i * 1000) };
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

  // ── pure: one person's conversation, for the office's panel ─────────────────
  // r is chat_view's answer for the tab (the chat view's own call: the phone's `conversation` turns and
  // `_link_decision`), R is web/chat.js's rules (SFChat.rules; pure, and what node's require gives).
  // Agent text reaches HTML only through R.md / R.escHtml, as in the chat view.
  const CHAT_LIMIT = 100, CHAT_SHOW = 30;       // turns asked for (tool calls are turns too); items drawn
  const baseName = p => String(p || '').split(/[\\/]/).pop();
  // Messages of the person's still waiting to go in, or given up, in plain words (chat_view's `queue`).
  function queueWords(q, name, zh, now) {
    const T = (en, cn) => (zh ? cn : en), out = [];
    const waiting = ((q && q.queued) || []).length, gone = (q && q.given_up) || [];
    if (waiting) {
      const b = q.busy || {}, mins = b.since ? Math.floor((now / 1000 - b.since) / 60) : 0;
      const why = {
        working: T(`${name} is still on the previous step${mins >= 1 ? ` (${mins} min so far)` : ''}`, `${name}還在做上一步${mins >= 1 ? `（已經 ${mins} 分鐘）` : ''}`),
        telegram: T('a message from Telegram is still being handled', '還在處理從 Telegram 來的那一則'),
        decision: T('it is waiting for your answer first', '它在等你先回答問題'),
        model: T('it is switching models', '它在換模型'),
        draft: T('there is unsent text in its terminal', '它的終端機裡有還沒送出的字'),
      }[b.reason] || '';
      out.push(T(`${waiting === 1 ? 'Your message is' : waiting + ' messages are'} waiting to go in${why ? ': ' + why : ''}.`,
                 `${waiting === 1 ? '你的訊息' : waiting + ' 則訊息'}還在排隊${why ? '：' + why : ''}，好了就會送進去。`));
    }
    if (gone.length) {
      const last = gone[gone.length - 1] || {};
      out.push(T(`${gone.length === 1 ? 'One message was' : gone.length + ' messages were'} not sent${last.why ? ` (${last.why})` : ''}: send it again if it still matters.`,
                 `${gone.length} 則沒送出${last.why ? `（${last.why}）` : ''}，還需要的話再傳一次。`));
    }
    return out.join(' ');
  }
  /* output: {rev, provider, readable, why, activity, items: [{k, who, html}], ask, queue, login}
       who   me | bot | peer | tools | note | error
       ask   null; {term: true} (waiting in the terminal, options not readable here);
             or {id, question, context, options: [{n, label, tone, raw}], asIs} (answered with chat_answer) */
  function chatShape(r, R, lang, name, now) {
    const zh = R.isZh(lang), T = (en, cn) => (zh ? cn : en), e = R.escHtml;
    const all = R.buildTimeline((r && r.turns) || [], { foldRed: true });
    const start = Math.max(0, all.length - CHAT_SHOW);
    const items = all.slice(start).map((it, i) => {
      const k = `${it.type}:${it.ts || 0}:${start + i}`;
      switch (it.type) {
        case 'user': return { k, who: 'me', html: e(it.text) };
        case 'bot': return { k, who: 'bot', html: R.md(it.text, zh) };
        case 'peer': return { k, who: 'peer', html: `<b>${e(it.who || T('Another agent', '另一個 agent'))}</b>` +
          (it.blocked ? `<em>${e(T(' (chain limit: read as data only)', '（超過轉交層數，只當資料讀）'))}</em>` : '') + `<br>${e(it.text)}` };
        case 'tools': return { k, who: 'tools', html: e(R.toolSummary(it.calls, lang)) };
        case 'file': return { k, who: 'note', html: e(T('Attached ', '附上 ') + baseName(it.path)) };
        case 'compact': return { k, who: 'note', html: e(T('The earlier conversation was compacted', '前面的對話已經壓縮')) };
        case 'memory': return { k, who: 'note', html: e(T('Saved its memory', '存了一次記憶')) };
        case 'error': return { k, who: 'error', html: e(it.text) + (it.times > 1 ? ` ×${it.times}` : '') };
        default: return null;
      }
    }).filter(Boolean);
    const d = (r && r.decision) || null;
    let ask = null;
    if (d && !d.tag && (d.options || []).length) {
      const m = R.mapDecision(d, lang);
      ask = { id: m.id, question: m.questionTranslated || !m.translated ? m.question : T('It is asking before it goes on.', '它在繼續之前先問你。'),
              context: R.askContext(d, m, r.activity === 'decision' ? R.pendingCall(r.turns) : null, lang),
              options: m.options.map(o => ({ n: o.n, label: o.label, tone: o.tone || '', raw: o.raw })), asIs: !m.translated };
    } else if ((r && r.activity === 'decision') || (d && d.tag)) {
      ask = { term: true };
    }
    return { rev: (r && r.rev) || '', provider: (r && r.provider) || '', readable: !!(r && r.readable), why: (r && r.why) || '',
             activity: (r && r.activity) || '', items, ask, queue: queueWords(r && r.queue, name, zh, now || Date.now()),
             login: !!(r && r.login && r.login.step) };
  }

  const pure = { build, track, tabState, peerState, cliName, avatar, chatShape, queueWords, CHAT_LIMIT };
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
  // The tab a person in the office is: 'b:<role>' → the bot's tab on this computer (the first in the sidebar's
  // order, as build() picks), 't:<sid>' → that tab. '' for anyone without one here (asleep, part-timer, a
  // paired computer's). Looked up at every call: build() leaves sids out of the snapshot, and a relaunch
  // can give a bot a new tab.
  function sidOf(key) {
    const k = String(key || ''), H = host(), S = (H && H.sessions()) || {};
    if (k.startsWith('t:')) { const sid = k.slice(2); return S[sid] && !S[sid].isRemote ? sid : ''; }
    if (k.startsWith('b:')) {
      const B = root.SFBots;
      if (!(B && B.enabled && B.enabled())) return '';
      const hit = pageTabs().find(x => x.role === k.slice(2));
      return hit ? hit.sid : '';
    }
    return '';
  }
  // 'p:<pid>/<role>' → a paired computer's bot's tab there, 'q:<pid>/<rsid>' → one of its interns: {peer, rsid},
  // from the rows bots.js already polls (SFBots.peerBots). null for one asleep (no tab) or not listed.
  function peerOf(key) {
    const m = /^([pq]):([^/]+)\/(.+)$/.exec(String(key || ''));
    if (!m) return null;
    const B = root.SFBots, P = (B && B.peerBots && B.peerBots()) || {}, p = P[m[2]];
    if (!p) return null;
    if (m[1] === 'q') return (p.tabs || {})[m[3]] ? { peer: m[2], rsid: m[3] } : null;
    const b = (p.bots || []).find(x => x && String(x.role) === m[3]);
    return b && b.sid && b.state !== 'asleep' ? { peer: m[2], rsid: String(b.sid), label: String(b.label || b.role) } : null;
  }
  const targetOf = key => { const sid = sidOf(key); return sid ? { sid } : peerOf(key); };
  // The chat view's calls, on this computer's tab or a paired one's (its POST /link/chat, arguments by name,
  // as chat.js sends them for a remote tab).
  const REMOTE_ARGS = { view: ['limit', 'rev'], send: ['text', 'clear_draft'], answer: ['n', 'qid'] };
  function callOn(tg, action, ...args) {
    if (tg.sid) return chatOf().call(tg.sid, action, ...args);
    const named = {};
    (REMOTE_ARGS[action] || []).forEach((n, i) => { named[n] = args[i]; });
    return api().link_chat(tg.peer, tg.rsid, action, JSON.stringify(named));
  }
  const lang = () => (host() && host().lang && host().lang()) || 'en';
  const chatOf = () => { const C = root.SFChat; return C && C.call && C.rules ? C : null; };
  // What the panel's conversation calls: the chat view's own routes (chat_view / chat_send / chat_answer),
  // for one person's tab. Answers are plain objects; nothing throws.
  const chatHost = {
    async view(key, rev, name) {
      const tg = targetOf(key), C = chatOf();
      if (!tg) return { ok: false, why: 'no_tab' };
      if (!C) return { ok: false, why: 'no_chat' };
      let r = null;
      try { r = JSON.parse(await callOn(tg, 'view', CHAT_LIMIT, rev || '')); } catch (_) { r = null; }
      if (!r || r.success === false) return { ok: false, why: (r && r.code) || 'error' };   // old_peer, unreachable…
      // what it is doing: this computer's status monitor; a paired one's tab, the activity its chat_view gives
      const st = tg.sid ? ((host().sessions() || {})[tg.sid] || {})._serverStatus : (r.unchanged ? null : { state: r.activity });
      const doing = st ? C.rules.plainDoing(st, lang()) : null;
      if (r.unchanged) return { ok: true, unchanged: true, rev: r.rev, doing };
      return Object.assign({ ok: true, doing, remote: !tg.sid }, chatShape(r, C.rules, lang(), name || '', Date.now()));
    },
    async send(key, text, clearDraft) {
      const tg = targetOf(key);
      if (!tg || !chatOf()) return { success: false, code: 'no_tab' };
      try { return JSON.parse(await callOn(tg, 'send', String(text || ''), !!clearDraft)); }
      catch (e) { return { success: false, message: String(e) }; }
    },
    async answer(key, n, qid) {
      const tg = targetOf(key);
      if (!tg || !chatOf()) return { success: false, code: 'no_tab' };
      try { return JSON.parse(await callOn(tg, 'answer', n, qid)); }
      catch (e) { return { success: false, message: String(e) }; }
    },
    has: key => !!targetOf(key),
    // The tab itself, in the main window: the office closes and the sidebar switches to it (a paired
    // computer's tab opens as the sidebar's remote row opens it).
    open(key) {
      const tg = targetOf(key), H = host();
      if (!tg || !H) return false;
      if (tg.sid) {
        if (!H.switchTab) return false;
        close();
        H.switchTab(tg.sid);
        return true;
      }
      if (!H.openRemote || !H.openRemote(tg.peer, tg.rsid, tg.label)) return false;   // not reachable or not ours to control
      close();
      return true;
    },
    openUrl(url) { if (/^https?:\/\//i.test(String(url || ''))) { try { api().open_url(String(url)); } catch (_) {} } },
    // A picture (or a file copied in Finder) pasted into the panel's box: saved as the terminal's paste saves it
    // (save_image / get_clipboard_files); for a paired computer's tab the file is sent there first (link_send_file:
    // that computer's own path comes back), since a path on this computer means nothing there. [{path, name}] or
    // {ok: false, message}.
    async attach(key, dataUrl, name) {
      const tg = targetOf(key), a = api();
      if (!tg || !a) return { ok: false, message: '' };
      let paths = [];
      try {
        if (dataUrl) { const p = await a.save_image(dataUrl); if (p && !/^ERROR/.test(String(p))) paths = [String(p)]; }
        else paths = JSON.parse((await a.get_clipboard_files()) || '[]');
      } catch (e) { return { ok: false, message: String(e) }; }
      if (!paths.length) return { ok: false, message: '' };
      if (tg.sid) return { ok: true, files: paths.map(p => ({ path: p, name: name || p.split(/[\\/]/).pop() })) };
      const out = [];
      for (const p of paths) {
        let r = null;
        try { r = JSON.parse(await a.link_send_file(tg.peer, p)); } catch (_) { r = null; }
        if (!r || !r.success || !r.saved) return { ok: false, message: (r && r.message) || '' , remote: true };
        out.push({ path: String(r.saved), name: name || p.split(/[\\/]/).pop() });
      }
      return { ok: true, files: out };
    },
    // An intern asked to leave: its tab is closed, as its ✕ does (here: the sidebar's close; a paired computer's:
    // that computer closes it, as the sidebar's remote ✕ does). Only an intern (a tab that is no bot); the office
    // asks first in its own panel (the page's own confirm would sit under the office).
    async dismiss(key) {
      const k = String(key || ''), H = host();
      if (k.startsWith('t:')) {
        const sid = sidOf(k);
        if (!sid || !H || !H.closeTab) return { ok: false };
        try { await H.closeTab(sid); return { ok: true }; } catch (e) { return { ok: false, message: String(e) }; }
      }
      const m = /^q:([^/]+)\/(.+)$/.exec(k);
      if (!m || !peerOf(k)) return { ok: false };
      try { const r = JSON.parse(await api().link_remote_close(m[1], m[2])); return { ok: r && r.success !== false, message: (r && r.message) || '' }; }
      catch (e) { return { ok: false, message: String(e) }; }
    },
  };
  // What office.html's `store` calls (same origin, synchronous), and its conversation panel (promises).
  const officeHost = {
    get: k => (prefs && Object.prototype.hasOwnProperty.call(prefs, k) ? clone(prefs[k]) : null),
    set: (k, v) => { prefs = prefs || {}; if (v == null) delete prefs[k]; else prefs[k] = clone(v); savePrefs(false); },
    close: () => close(),
    chat: chatHost,
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
