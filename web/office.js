/* ShellBot office (settings.experimental_office, off by default) — see docs/ai-skill.md §4.10.
 *
 * The same overlay also opens the whiteboard on its own (SFOffice.openBoard, "board-only": office.html draws no floor, only
 * the board sheet, and closing the sheet closes the overlay). That does not need the office switch: web/board.js puts a board
 * button (with the count of decisions waiting on the person) in the sidebar's foot of both views and a keyboard shortcut.
 *
 * Draws the bots (staff, 正職), the AI tabs that are not bots (interns, 實習生) and the subagents a
 * Claude Code tab has launched (part-timers, 工讀生) as one office floor. The floor itself is
 * web/office.html (Canvas2D, no network), loaded in an <iframe> over the whole window so its CSS
 * and DOM cannot clash with this page. This file:
 *
 *   - puts the switch next to the other experimental switches (Settings → General) and, while it is
 *     on, an icon in the sidebar's foot (#sb-footer-row). Off: no office button, no iframe, no timer (the board button is board.js's).
 *   - builds the office's snapshot from what the page already holds: the sidebar's dots
 *     (sessions[sid]._dotClass, the same light the tab shows), bots.js's roster and its paired
 *     machines' bots (SFBots.state / peerBots: already polled), plus two cheap calls made only
 *     while the office is open: tasks_overview (one small SQLite read, at most every 9 s) and
 *     office_helpers (list + stat of the open Claude tabs' subagents folders; office_helpers.py).
 *     The floor itself reads no screen and no transcript.
 *     Each tab's `loop` (a /loop or cron routine: agent_status.detect_schedules puts it on the tab's
 *     status whether or not the Loops panel is on) comes from the sessions the page already holds.
 *     The lobby's task board and calendar views ask SFOffice.host.board for what they open: task_detail
 *     (a task's history, only when its card is opened), task_user_act (the person's 「我做好了」 / 「留言」 on that card)
 *     and schedules_list (this computer's schedules, only while the calendar view is showing, at most once a minute:
 *     office.html paces it). The office writes little itself: task_user_act, and setEnabled (schedule_set_enabled)
 *     switching one of this computer's schedules on or off; every other schedule change is a message to the schedule's
 *     owner agent (board.request, the chat panel's send).
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
  // A loop or cron routine a tab runs, as agent_status.detect_schedules leaves it on the tab's status
  // (`loop`: kind loop|cron, status, next_ts in seconds, delay in seconds, reason, rounds, crons). null for
  // none, and for a /loop whose next wake passed more than LOOP_GRACE_S ago: the status behind it can be
  // up to 25 s old, so the page does not trust "sleeping" forever.
  const LOOP_GRACE_S = 1800;
  function loopOf(raw, now) {
    const r = raw && typeof raw === 'object' ? raw : null;
    if (!r || (r.kind !== 'loop' && r.kind !== 'cron')) return null;
    const next = Number(r.next_ts) > 0 ? Number(r.next_ts) : 0;
    if (r.kind === 'loop' && (!next || next + LOOP_GRACE_S < (now || Date.now()) / 1000)) return null;
    const crons = (Array.isArray(r.crons) ? r.crons : []).slice(0, 5).map(c => ({ desc: clip(c && c.desc, 60), schedule: clip(c && c.schedule, 40) }));
    return { kind: r.kind, status: r.status === 'running' ? 'running' : r.kind === 'cron' ? 'scheduled' : 'sleeping', next_ts: next,
             delay: Number(r.delay) > 0 ? Number(r.delay) : 0, reason: clip(r.reason, 90), rounds: Number(r.rounds) || 0, crons };
  }
  // A person's name for a task's owner / waiting-on / requester → the office's key ('user', 'b:<role>', 't:<sid>'),
  // by role, by the bot's label, or by the title or sid of a tab that is no bot. '' when nobody here has that name.
  function personKey(name, who) {
    const n = String(name || '');
    if (!n) return '';
    if (n === 'user') return 'user';
    if (who.roles.has(n)) return 'b:' + n;
    if (who.labels[n]) return 'b:' + who.labels[n];
    return who.temps[n] ? 't:' + who.temps[n] : '';
  }
  const TASK_KEYS = ['id', 'title', 'status', 'kind', 'owner', 'requester', 'waiting_on', 'next_step', 'due_at', 'updated_at', 'idle_s'];
  // The task board: the person's decisions (kind decision, owned by `user`), the stuck ones, then every other open
  // task. Each task is in ONE list, the first that has it (decision, stuck, open), so a stuck decision is not three
  // rows; `all` is that order and is what the sticky notes follow. A stuck row from an older overview with only
  // {id, idle_s} borrows its text from the open list. Times stay in seconds (the store's), as `age` needs them.
  function boardOf(tasks, who, now) {
    const T = tasks || {}, W = who || { roles: new Set(), labels: {}, temps: {} };
    const byId = new Map(((T.open) || []).filter(x => x && x.id != null).map(x => [x.id, x]));
    const seen = new Set();
    const row = (x, extra) => {
      const o = {};
      for (const k of TASK_KEYS) o[k] = x[k] == null ? '' : x[k];
      o.id = Number(x.id); o.title = clip(x.title, 200); o.next_step = clip(x.next_step, 200);
      o.due_at = Number(x.due_at) || 0; o.updated_at = Number(x.updated_at) || 0; o.idle_s = Number(x.idle_s) || 0;
      o.owner_key = personKey(x.owner, W); o.waiting_key = personKey(x.waiting_on, W); o.requester_key = personKey(x.requester, W);
      return Object.assign(o, extra);
    };
    const take = (list, extra) => list.filter(x => x && x.id != null && !seen.has(x.id)).map(x => { seen.add(x.id); return row(Object.assign({}, byId.get(x.id) || {}, x), extra); });
    const decisions = take(T.decisions || [], { decision: true });
    const stuck = take(T.stuck || [], { stuck: true });
    const open = take(T.open || [], {});
    return { decisions, stuck, open, all: decisions.concat(stuck, open) };
  }
  // The computer's own schedules (api_schedules.schedules_list) as the office lists them: a title, when, on or off, and
  // what the calendar needs to place and change them: `slots` (sched_view's numbers: an interval, calendar entries; bounded),
  // `owner` (the bot that looks after it, from schedules.json), `toggle` (false: no on/off switch for this one). The
  // commands are left out: the office shows when something runs, not what.
  function slotsOf(s) {
    const o = s && typeof s === 'object' ? s : {}, iv = Number(o.interval);
    const cal = (Array.isArray(o.calendar) ? o.calendar : []).slice(0, 200).map(c => {
      const r = {};
      for (const k of ['Month', 'Day', 'Weekday', 'Hour', 'Minute']) { const v = c && c[k]; if (Number.isInteger(v) && v >= 0 && v <= 366) r[k] = v; }
      return r;
    });
    return { interval: iv > 0 ? Math.min(iv, 31536000) : null, calendar: cal, partial: !!o.partial };
  }
  function schedShape(raw) {
    const r = raw && typeof raw === 'object' ? raw : {};
    const rows = (Array.isArray(r.schedules) ? r.schedules : []).map(x => ({
      id: String(x.id || ''), title: String(x.title || x.id || ''), when: x.when && typeof x.when === 'object' ? { en: String(x.when.en || ''), zh: String(x.when.zh || '') } : { en: String(x.when || ''), zh: String(x.when || '') },
      enabled: !!x.enabled, running: !!x.running, kind: String(x.kind || ''), count: Array.isArray(x.members) ? x.members.length : 0,
      owner: clip(x.owner, 60), toggle: x.toggle !== false, state: clip(x.state, 12), slots: slotsOf(x.slots) }));
    return { ok: !r.error, unsupported: !!r.unsupported, configured: r.configured !== false, launchd_ok: r.launchd_ok !== false, rows, at: Date.now() };
  }
  function runsOf(e) {
    const pin = e.pin || {};
    return [(e.cli && e.cli.label) || '', pin.model ? (pin.label || pin.model) : 'CLI 預設'].filter(Boolean).join(' · ');
  }

  // A paired computer's task board (link_peer_tasks: its decisions, stuck and open tasks, rows shaped like the local ones) as
  // the office's board: the same lists boardOf makes, owners as that computer's people ('p:<pid>/<role>' for a bot,
  // 'q:<pid>/<tab>' for an intern; 'user' is whoever sits at that computer), plus `boardState` (ok | pending | old | denied |
  // error) and `boardAt` (seconds; when the rows were last read fresh). Nothing for a computer the call did not answer for.
  const PEER_STATES = ['ok', 'pending', 'old', 'denied', 'error'];
  function peerBoardOf(pid, p, pt, now) {
    if (!pt || typeof pt !== 'object') return {};
    const who = { roles: new Set(), labels: {}, temps: {} };
    for (const b of p.bots || []) if (b && b.role && b.state !== 'external') { who.roles.add(String(b.role)); if (b.label) who.labels[String(b.label)] = String(b.role); }
    for (const r of Object.keys(p.tabs || {})) { const tb = p.tabs[r]; if (tb && tb.kind === 'temp') { who.temps[String(tb.name || r)] = r; who.temps[r] = r; } }
    const b = boardOf({ open: pt.open, stuck: pt.stuck, decisions: pt.decisions }, who, now);
    const key = k => (k.startsWith('b:') ? `p:${pid}/${k.slice(2)}` : k.startsWith('t:') ? `q:${pid}/${k.slice(2)}` : k);
    for (const r of b.all) { r.owner_key = key(r.owner_key); r.waiting_key = key(r.waiting_key); r.requester_key = key(r.requester_key); }
    return { board: b, boardState: PEER_STATES.indexOf(pt.state) >= 0 ? pt.state : 'error', boardAt: Number(pt.at) > 0 ? Number(pt.at) : 0 };
  }

  /* input:
       botsOn    bots are on (settings.experimental_bots); off = every AI tab is an intern
       roster    bots_state().roster: role → {label, responsibility, bot: {avatar, primary, memory, external}, cli, pin}
       primary   the primary bot's role
       tabs      this computer's tabs in sidebar order: [{sid, label, kind: bot|temp|terminal, role, dot, cmd, line, loop}]
                 (loop: the tab's status `loop`, a /loop or cron routine it runs, or null)
       tasks     tasks_overview(): {decisions: [...], open: [...], stuck: [...]}
       peers     SFBots.peerBots(): pid → {name, primary, bots: [/link/bots rows], tabs: {rsid: tab}}
       peerTasks link_peer_tasks's `peers`: pid → {state, at, decisions, stuck, open}; a peer listed there carries board, boardState, boardAt
       helpers   office_helpers().helpers: sid → [{id, description, model, agent_type, state, started_at, last_at}]
       seen      sid → first time this page saw the tab (ms); filled in for new ones (an intern's opened_at)
       kit       {palette, shapes, defaultAvatar} (SFBots)
       now       ms
     output: the snapshot office.html's Office.update takes (its §15), with `board` (the task board: decisions, stuck,
     open, all; boardOf) and `loops` (every tab with a loop or cron, who it is and when it wakes next); a bot or an
     intern with one carries `loop` too. */
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
      return Object.assign({
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
      }, peerBoardOf(pid, p, (I.peerTasks || {})[pid], now));
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
    const loops = [];
    const loopOn = (tab, key, name) => {
      const lp = loopOf(tab && tab.loop, now);
      if (lp) loops.push(Object.assign({ key, name }, lp));
      return lp;
    };
    for (const b of bots) { const tab = tabOf[b.id]; if (tab) b.loop = loopOn(tab, 'b:' + b.id, b.name); }
    for (const x of temps) { const tab = tabs.find(q => q.sid === x.id); x.loop = loopOn(tab, 't:' + x.id, x.name); }
    for (const tab of tabs) if (tab.loop && !tab.role && tab.kind !== 'temp') loopOn(tab, '', String(tab.label || tab.sid));   // a tab no one sits at
    const who = { roles: known, labels: {}, temps: {} };
    for (const r of roles) if (roster[r].label) who.labels[String(roster[r].label)] = r;
    for (const x of tabs) if (x.kind === 'temp' && !(x.role && known.has(x.role))) { who.temps[String(x.label || '')] = x.sid; who.temps[x.sid] = x.sid; }
    for (const b of bots) { delete b.sid; }
    return { bots, temps, peers, helpers, handoffs: [], board: boardOf(I.tasks, who, now), loops };
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

  const pure = { build, track, tabState, peerState, cliName, avatar, chatShape, queueWords, CHAT_LIMIT, loopOf, boardOf, personKey, schedShape };
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
  // The task board's and the schedule calendar's calls (office.html asks only while that view is showing).
  // detail: one task and its events newest first (task_detail); schedules: this computer's own schedules (schedules_list,
  // asked at most once a minute by the view), shaped by schedShape. The calendar changes only two things itself:
  // setEnabled flips one of this computer's schedules on or off (schedule_set_enabled: the answer says what launchd did).
  // Anything else is a request to the schedule's owner agent: request() puts a message in that agent's tab as the person
  // typed it (the chat panel's send; never clears the person's unsent draft), canRequest says whether it has a tab to reach.
  const boardHost = {
    async detail(id) {
      try { return JSON.parse(await api().task_detail(String(id))); }
      catch (e) { return { success: false, message: String(e) }; }
    },
    // The person's own hand on a task (task_user_act): "done" or "note". After a success the next tick asks the store again
    // at once (not in 9 s), so the board shows it within a second or two. No optimistic change: the answer is the store's.
    async act(id, action, text) {
      try {
        const r = JSON.parse(await api().task_user_act(String(id), String(action), String(text || '')));
        if (r && r.success) { tasksAt = 0; setTimeout(() => { tick(); }, 250); }
        return r;
      } catch (e) { return { success: false, message: String(e) }; }
    },
    async schedules() {
      try { return schedShape(JSON.parse(await api().schedules_list())); }
      catch (e) { return { ok: false, unsupported: false, configured: true, launchd_ok: true, rows: [], at: Date.now() }; }
    },
    async setEnabled(label, on) {
      try { return JSON.parse(await api().schedule_set_enabled(String(label), !!on)); }
      catch (e) { return { success: false, message: String(e) }; }
    },
    canRequest: key => chatHost.has(key),
    request: (key, text) => chatHost.send(key, text, false),
  };
  // What office.html's `store` calls (same origin, synchronous), and its conversation panel (promises).
  const officeHost = {
    get: k => (prefs && Object.prototype.hasOwnProperty.call(prefs, k) ? clone(prefs[k]) : null),
    set: (k, v) => { prefs = prefs || {}; if (v == null) delete prefs[k]; else prefs[k] = clone(v); savePrefs(false); },
    close: () => close(),
    // True while the overlay is the whiteboard alone (SFOffice.openBoard): office.html draws no floor and no office-only helper is asked.
    boardOnly: () => boardMode,
    // Whether a key is the board shortcut (web/board.js): office.html asks, as the focus is in its frame while it is open.
    isBoardKey: e => !!(root.SFBoard && root.SFBoard.isKey && root.SFBoard.isKey(e)),
    // Settings' Interface size (small / medium / large: index.html's --ui-scale); the office's words follow it
    uiScale: () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ui-scale')) || 1,
    chat: chatHost,
    board: boardHost,
  };

  let overlay = null, frame = null, timer = 0, busy = false, lastPush = null, boardMode = false;
  let tasks = { open: [], stuck: [], decisions: [] }, tasksAt = 0, helpers = {}, peerTasks = {}, peerTasksAt = 0;
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
      return { sid, label: s.label || '', kind, role, dot: s._dotClass || '', cmd: s.cmd || '', line, loop: (s._serverStatus || {}).loop || null };
    });
  }
  function snapshot() {
    const B = root.SFBots || {}, st = (B.state && B.state()) || {};
    const snap = build({
      botsOn: !!(B.enabled && B.enabled()), roster: st.roster || {}, primary: st.primary || '',
      tabs: pageTabs(), tasks, peers: (B.peerBots && B.peerBots()) || {}, peerTasks, helpers, seen,
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
      try {
        const r = JSON.parse(await a.tasks_overview());
        if (r && r.success) { tasks = { open: r.open || [], stuck: r.stuck || [], decisions: r.decisions || [] }; if (root.SFBoard) root.SFBoard.note(tasks.decisions.length); }
      } catch (_) {}
    }
    // The paired computers' boards (the calls an older app does not have are skipped: no peer boards, no error). Same cadence
    // as the local tasks, never on the 3 s tick; the answer is the host's own (it caches what it read from the other computers).
    if (a.link_peer_tasks && Date.now() - peerTasksAt >= TASKS_MS) {
      peerTasksAt = Date.now();
      try { const r = JSON.parse(await a.link_peer_tasks()); if (r && r.peers && typeof r.peers === 'object') peerTasks = r.peers; } catch (_) {}
    }
    if (a.office_helpers && !boardMode) {          // the floor's part-timers: not asked for by the board alone (and refused with the switch off)
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

  // open(): the office (needs the switch). open({board: true}): the whiteboard alone, whatever the switch says.
  async function open(opts) {
    const bo = !!(opts && opts.board);
    if ((!bo && !enabled()) || overlay) return;
    if (!prefs && api().office_prefs) {      // also for the board alone: a set() would otherwise save a lone key over the person's seats
      try { const r = JSON.parse(await api().office_prefs()); prefs = (r && r.prefs) || {}; } catch (_) { prefs = {}; }
    }
    if (overlay || (!bo && !enabled())) return;
    boardMode = bo;
    tasksAt = 0; peerTasksAt = 0; peerTasks = {};   // a freshly opened office asks again, not on the last open's answer
    overlay = document.createElement('div');
    overlay.id = 'sfo-overlay';
    overlay.tabIndex = -1;
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    const nm = bo ? t('Board', '看板') : t('Office', '辦公室');
    overlay.setAttribute('aria-label', nm);
    if (bo) overlay.classList.add('sfo-board');
    overlay.innerHTML = `<iframe class="sfo-frame" src="office.html" title="${nm}"></iframe>` +
      (bo ? '' : `<button class="sfo-close" type="button" aria-label="${t('Close the office (Esc)', '關閉辦公室（Esc）')}" title="${t('Close (Esc)', '關閉（Esc）')}">✕</button>`);
    document.body.appendChild(overlay);
    frame = overlay.querySelector('iframe');
    const x = overlay.querySelector('.sfo-close');
    if (x) x.addEventListener('click', () => close());
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
    overlay = null; frame = null; busy = false; boardMode = false;
    if (saveTimer) savePrefs(true);
    try { const H = host(), s = H && H.sessions()[H.active()]; if (s && s.term) s.term.focus(); } catch (_) {}
  }

  const ICON = '<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5 14.5 5 8 8.5 1.5 5Z" fill="currentColor" opacity=".9"/><path d="M1.5 5v5.5L8 14V8.5Z" fill="currentColor" opacity=".55"/><path d="M14.5 5v5.5L8 14V8.5Z" fill="currentColor" opacity=".35"/></svg>';
  function placeButton() {
    let b = document.getElementById('btn-office');
    if (!enabled()) { if (b) b.remove(); if (!boardMode) close(); return; }
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

  // ── the switch (Settings → Office) ──────────────────────────────────────────
  function injectSettings() {
    let sw = document.getElementById('setting-experimental-office');
    if (!sw) {
      const box = document.getElementById('sx-office');
      if (!box) return;
      const wrap = document.createElement('div');
      wrap.id = 'sfo-setting';
      wrap.className = 'sx-row';
      wrap.innerHTML =
        '<div class="sx-txt"><div class="sx-label sfo-label"></div><div class="sx-hint sfo-hint"></div></div>' +
        '<div class="sx-ctl"><button class="toggle-switch" id="setting-experimental-office" data-guide="office"></button></div>';
      box.appendChild(wrap);
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

  // While the office is open its lobby whiteboard is what the board shortcut opens or closes (office.html's Office.board).
  function toggleBoard() { try { frame.contentWindow.Office.board(); } catch (_) {} }
  root.SFOffice = Object.assign({ host: officeHost, open, close, isOpen: () => !!overlay, isBoard: () => !!overlay && boardMode,
                                  openBoard: () => open({ board: true }), toggleBoard, refresh: tick,
                                  lastSnapshot: () => clone(lastPush), placeButton }, pure);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => boot(0));
  else boot(0);
})(typeof window !== 'undefined' ? window : globalThis);
