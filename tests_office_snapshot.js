/**
 * The office's snapshot from what the page holds (web/office.js build / track), in node.
 *
 *   - a tab's light → the office's state; a bot with no open tab is asleep
 *   - only the roster's own bots (not an external one, not a plain role); none while bots are off
 *   - an AI tab that is no bot is an intern; a terminal is not shown
 *   - faces as #rrggbb, created_at in roster order, a bot's open task with the store's stale verdict
 *   - another machine's bots and interns from its /link/bots rows
 *   - part-timers under the bot or intern whose tab launched them; a tab the office does not show
 *     brings none
 *   - the panel's events are the state changes seen, nothing invented
 *   - the task board (decisions, stuck, open: each task once, owners as the office's people) and the tabs'
 *     loops (a /loop or cron on a tab's status; a stale one dropped) and this computer's schedules (no command kept)
 *   - the panel's conversation (chatShape, with web/chat.js's rules): who said what, tool calls as one
 *     line, agent text escaped, a question's options with their own numbers, a question it cannot read
 *     sends the person to the terminal, the queue in plain words
 * Demo data only. Run: node tests_office_snapshot.js
 */
const path = require('path');
const O = require(path.join(__dirname, 'web/office.js'));

let fails = 0;
function check(name, ok, detail) {
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${ok ? '' : '  ' + JSON.stringify(detail === undefined ? '' : detail)}`);
  if (!ok) fails++;
}

const kit = { palette: { purple: '#A15BF0', amber: '#F0A934', gray: '#8B90A0' }, shapes: ['round', 'star', 'egg', 'hex'],
              defaultAvatar: () => ({ shape: 'round', color: 'gray' }) };
const roster = {
  chief: { label: '示範秘書長', responsibility: '統籌', bot: { avatar: { shape: 'star', color: 'purple' }, primary: true }, cli: { label: 'Claude Code' }, pin: {} },
  eng: { label: '示範工程師', responsibility: '寫程式', bot: { avatar: { shape: 'egg', color: '#123456' }, memory: false }, cli: { label: 'Codex' }, pin: { model: 'demo-model' } },
  res: { label: '示範研究員', cmd: 'sf-pi-demo --x', bot: { avatar: { shape: 'nope', color: 'nope' } } },
  ext: { label: '示範外部', bot: { external: { kind: 'webhook' } } },
  plain: { label: '示範角色' },
};
const tabs = [
  { sid: 's1', label: '示範秘書長', kind: 'bot', role: 'chief', dot: '', cmd: 'claude', line: '' },
  { sid: 's2', label: '示範工程師', kind: 'bot', role: 'eng', dot: 'sig-working', cmd: 'codex', line: '示範：跑測試' },
  { sid: 's3', label: '示範分頁', kind: 'temp', role: '', dot: 'on attention', cmd: 'cd /tmp && env A=1 sf-claude --x', line: '' },
  { sid: 's4', label: '示範終端機', kind: 'terminal', role: '', dot: '', cmd: 'zsh', line: '' },
];
const now = Date.UTC(2026, 10, 17, 6, 0, 0);
const tasks = { open: [
  { id: 1, title: '示範待辦', status: 'todo', owner: 'eng', requester: 'eng', updated_at: 100 },
  { id: 2, title: '示範進行中', status: 'doing', owner: 'eng', requester: 'chief', waiting_on: '客戶回覆', next_step: '示範下一步', updated_at: 50 },
  { id: 3, title: '示範分頁的事', status: 'waiting', owner: '示範分頁', requester: 'sfctl', waiting_on: 'user', updated_at: 70 },
], stuck: [{ id: 2 }] };
const peers = { p1: { name: '示範電腦', bots: [
  { role: 'b', label: '示範建置員', state: 'awake', activity: 'working', primary: true, avatar: { shape: 'hex', color: 'amber' } },
  { role: 'c', label: '示範睡覺', state: 'asleep' }, { role: 'd', state: 'external' }],
  tabs: { r1: { kind: 'temp', name: '示範隔壁分頁', cli: 'codex', decision: { id: 'x' } }, r2: { kind: 'terminal' } } } };
const helpers = { s2: [{ id: 'a1', description: '示範：查一下', model: 'demo', state: 'working', started_at: 1000, last_at: 1100 }],
                  s3: [{ id: 'b1', description: '示範：卡住', state: 'stuck', started_at: 900, last_at: 950 }],
                  s4: [{ id: 'c1', description: '終端機不會有', state: 'working' }], s9: [{ id: 'd1' }] };
const seen = {};
const snap = O.build({ botsOn: true, roster, primary: 'chief', tabs, tasks, peers, helpers, seen, kit, now });
const B = Object.fromEntries(snap.bots.map(b => [b.id, b]));

console.log('states');
check('a tab light maps to the office state', ['sig-working', 'sig-decision', 'sig-stuck', 'sig-done', '', 'on attention', 'weird']
  .map(O.tabState).join() === 'working,decision,stuck,done,idle,decision,idle');
check('another machine: a question, a block, work, rest', [{ decision: {} }, { blocked: 'x' }, { activity: 'working' }, { activity: 'done' }, {}]
  .map(O.peerState).join() === 'decision,stuck,working,done,idle');

console.log('bots');
check('only the roster\'s own bots, in roster order', snap.bots.map(b => b.id).join() === 'chief,eng,res', snap.bots.map(b => b.id));
check('states: idle tab, working tab, no tab = asleep', [B.chief.state, B.eng.state, B.res.state].join() === 'idle,working,asleep');
check('primary, duty, runs, memory', B.chief.primary && !B.eng.primary && B.chief.duty === '統籌' && B.eng.runs === 'Codex · demo-model'
  && B.chief.runs === 'Claude Code · CLI 預設' && B.eng.memory === false && B.chief.memory === true, [B.chief, B.eng]);
check('the AI each bot runs on, for zoning by AI: the roster\'s CLI name, else its command\'s, else none',
  B.chief.cli === 'Claude Code' && B.eng.cli === 'Codex' && B.res.cli === 'pi', [B.chief.cli, B.eng.cli, B.res.cli]);
check('faces: palette key → hex, #hex kept, unknown → the default', B.chief.avatar.color === '#A15BF0' && B.eng.avatar.color === '#123456'
  && B.eng.avatar.shape === 'egg' && B.res.avatar.shape === 'round' && B.res.avatar.color === '#8B90A0', [B.chief.avatar, B.eng.avatar, B.res.avatar]);
check('created_at follows the roster order', B.chief.created_at < B.eng.created_at && B.eng.created_at < B.res.created_at
  && !isNaN(Date.parse(B.chief.created_at)));
const t = B.eng.task || {};
check('a bot\'s task: doing before todo, stale from the store, from its requester, free-text waiting kept',
  t.id === '#2' && t.status === 'doing' && t.stale === true && t.from === 'chief' && t.waiting_on === '客戶回覆'
  && t.next === '示範下一步' && t.updated_at === new Date(50000).toISOString(), t);
check('a bot with no open task has none', B.chief.task === null && B.res.task === null);

console.log('interns');
check('the AI tab that is no bot is an intern; the terminal is not', snap.temps.map(x => x.id).join() === 's3', snap.temps);
const x = snap.temps[0];
check('…named by its tab, its CLI past cd/env/sf-, its state from the light', x.name === '示範分頁' && x.cli === 'Claude Code' && x.state === 'decision', x);
check('…its task by its tab name; a requester that is nobody here is left out', x.task && x.task.id === '#3' && x.task.from === null && x.task.waiting_on === 'user', x.task);
check('…opened_at is when this page first saw it, kept on the next build', seen.s3 === now && x.opened_at === new Date(now).toISOString());
O.build({ botsOn: true, roster, tabs: tabs.filter(q => q.sid !== 's3'), seen, kit, now: now + 5000 });
check('…and forgotten when the tab is gone', !('s3' in seen));

console.log('bots off');
const off = O.build({ botsOn: false, roster, tabs, seen: {}, kit, now });
check('no bots; every AI tab is an intern', off.bots.length === 0 && off.temps.map(q => q.id).join() === 's1,s2,s3', off.temps.map(q => q.id));

console.log('peers');
const P = snap.peers[0];
check('a paired machine: its bots but the external one, asleep kept', P.id === 'p1' && P.name === '示範電腦'
  && P.bots.map(b => `${b.id}:${b.state}`).join() === 'b:working,c:asleep', P.bots);
check('…its interns, a question kept', P.temps.length === 1 && P.temps[0].state === 'decision' && P.temps[0].cli === 'Codex', P.temps);

check('…the primary bot says so (exactly the row that is primary)', P.bots[0].primary === true && P.bots[1].primary === false, P.bots.map(b => b.primary));
check('…no board and no state when link_peer_tasks has not answered for it', !('board' in P) && !('boardState' in P));
const pt = { p1: { state: 'ok', at: 1700000000, decisions: [{ id: 31, title: '示範決定', kind: 'decision', owner: 'user', requester: 'b', waiting_on: 'user', status: 'waiting' }],
  stuck: [{ id: 12, idle_s: 90000 }], open: [
    { id: 12, title: '示範卡住', status: 'doing', owner: 'b', requester: 'user', updated_at: 50 },
    { id: 31, title: '示範決定', status: 'waiting', owner: 'user', requester: 'b', waiting_on: 'user', updated_at: 60 },
    { id: 13, title: '示範隔壁實習生的事', status: 'doing', owner: '示範隔壁分頁', requester: 'user', updated_at: 70 },
    { id: 14, title: '示範沒人認得', status: 'todo', owner: '不認識的人', requester: 'b', updated_at: 80 }] } };
const snap2 = O.build({ botsOn: true, roster, primary: 'chief', tabs, tasks, peers, peerTasks: pt, helpers, seen: {}, kit, now });
const P2 = snap2.peers[0];
check('a peer with link_peer_tasks: its board (decisions, stuck, open; each task once), state and when', P2.boardState === 'ok' && P2.boardAt === 1700000000
  && P2.board.decisions.map(x => x.id).join() === '31' && P2.board.stuck.map(x => x.id).join() === '12' && P2.board.open.map(x => x.id).join() === '13,14'
  && P2.board.all.length === 4, P2.board);
check('…its owners are that computer\'s people (a bot p:, an intern q:, user stays, an unknown name is empty)',
  P2.board.all.map(x => x.owner_key).join() === 'user,p:p1/b,q:p1/r1,' && P2.board.decisions[0].requester_key === 'p:p1/b' && P2.board.decisions[0].waiting_key === 'user', P2.board.all.map(x => x.owner_key));
check('…the stuck row borrowed its text from the open list, and the local board is untouched', P2.board.stuck[0].title === '示範卡住' && snap2.board.all.every(x => x.id !== 31), snap2.board);
const states = ['pending', 'old', 'denied', 'error', 'bogus'].map(state => O.build({ botsOn: true, roster, tabs, peers, peerTasks: { p1: { state, at: 0, decisions: [], stuck: [], open: [] } }, seen: {}, kit, now }).peers[0].boardState);
check('…pending / old / denied / error pass through, an unknown state is an error', states.join() === 'pending,old,denied,error,error', states);
const none = O.build({ botsOn: true, roster, tabs, peers, peerTasks: { other: pt.p1 }, seen: {}, kit, now }).peers[0];
check('…a peer the answer does not list has no board', !('board' in none) && !('boardState' in none));

console.log('part-timers');
const H = Object.fromEntries(snap.helpers.map(h => [h.id, h]));
check('under the bot whose tab launched them, and under an intern', H['s2/a1'] && H['s2/a1'].parent === 'eng' && H['s2/a1'].parent_kind === 'bot'
  && H['s3/b1'] && H['s3/b1'].parent === 's3' && H['s3/b1'].parent_kind === 'temp', snap.helpers);
check('…none for a terminal or a tab the office does not know', !H['s4/c1'] && !Object.keys(H).some(k => k.startsWith('s9/')));
check('…times as ISO, state kept, parent named', H['s2/a1'].started_at === new Date(1000000).toISOString() && H['s3/b1'].state === 'stuck'
  && H['s2/a1'].parent_name === '示範工程師');

console.log('events');
const mem = {};
const s1 = O.track(mem, O.build({ botsOn: true, roster, primary: 'chief', tabs, tasks, helpers, seen: {}, kit, now }), now);
const e1 = s1.bots.find(b => b.id === 'eng').events;
check('first sight is one event', e1.length === 1 && e1[0].kind === 'arrive', e1);
const tabs2 = tabs.map(q => q.sid === 's2' ? Object.assign({}, q, { dot: 'sig-decision', line: '示範：選 A 還是 B' }) : q);
const s2 = O.track(mem, O.build({ botsOn: true, roster, primary: 'chief', tabs: tabs2, tasks, seen: {}, kit, now: now + 1000 }), now + 1000);
const e2 = s2.bots.find(b => b.id === 'eng').events;
check('a state change adds one, with the tab\'s latest line', e2.length === 2 && e2[1].kind === 'decision' && e2[1].text === '等你決定：示範：選 A 還是 B', e2);
const s3 = O.track(mem, O.build({ botsOn: true, roster, primary: 'chief', tabs: tabs2, tasks, seen: {}, kit, now: now + 2000 }), now + 2000);
check('no change, no event', s3.bots.find(b => b.id === 'eng').events.length === 2);
check('a part-timer gone from the snapshot is forgotten', !Object.keys(mem).some(k => k.startsWith('h:')));
check('the latest line is not sent on its own', !('line' in s3.bots[0]) && !('line' in s3.temps[0]));

console.log('the conversation (chatShape)');
const R = require(path.join(__dirname, 'web/chat.js'));
const turns = [
  { kind: 'user_msg', text: '示範：幫我跑一次測試', ts: 1 },
  { kind: 'tool_call', tool: 'Bash', target: 'npm test', ts: 2 },
  { kind: 'tool_call', tool: 'Read', target: '/tmp/demo/a.txt', ts: 3 },
  { kind: 'assistant_text', text: '示範：**全部通過**，見 <script>alert(1)</script> https://example.com/x', ts: 4 },
  { kind: 'error', text: '示範錯誤', ts: 5 }, { kind: 'error', text: '示範錯誤', ts: 6 },
];
const c1 = O.chatShape({ rev: 'r1', provider: 'claude', readable: true, activity: 'idle', turns, decision: null, queue: { queued: [], given_up: [] } }, R, 'zh-TW', '示範工程師', now);
check('turns become items in order: me, tools, bot, error', c1.items.map(i => i.who).join() === 'me,tools,bot,error', c1.items);
check('the tool calls are one line', c1.items[1].html === '查了 1 個檔案、跑了 1 個指令', c1.items[1]);
check('agent text is escaped (no tag survives) and markdown applied', !/<script/i.test(c1.items[2].html) && /&lt;script&gt;/.test(c1.items[2].html) && /<strong>全部通過<\/strong>/.test(c1.items[2].html), c1.items[2].html);
check('…a link is a data-url, not an href that leaves the page', /data-url="https:\/\/example\.com\/x"/.test(c1.items[2].html) && !/href="http/.test(c1.items[2].html));
check('the same error twice is one item, counted', /×2/.test(c1.items[3].html), c1.items[3]);
check('no question, no queue', c1.ask === null && c1.queue === '' && c1.rev === 'r1');
const many = []; for (let i = 0; i < 50; i++) many.push({ kind: 'user_msg', text: '示範 ' + i, ts: i + 10 }, { kind: 'assistant_text', text: '回覆 ' + i, ts: i + 10.5 });
const c2 = O.chatShape({ turns: many }, R, 'zh-TW', 'x', now);
check('only the last 30 items are drawn, keys unique', c2.items.length === 30 && new Set(c2.items.map(i => i.k)).size === 30 && /回覆 49/.test(c2.items[29].html));
const dec = { id: 'q1', reason: 'Do you want to proceed?', options: [{ n: 1, text: 'Yes' }, { n: 2, text: 'No' }] };
const c3 = O.chatShape({ activity: 'decision', turns: [{ kind: 'tool_call', tool: 'Bash', target: 'rm -rf /tmp/demo', ts: 1 }], decision: dec }, R, 'zh-TW', 'x', now);
check('a question in plain words, each option with its own number and tone', c3.ask && c3.ask.id === 'q1' && c3.ask.question === '要讓它繼續嗎？' &&
      c3.ask.options.map(o => `${o.n}:${o.label}:${o.tone}`).join() === '1:允許:yes,2:不允許:no', c3.ask);
check('…with what it is about (the pending command, escaped)', /rm -rf \/tmp\/demo/.test(c3.ask.context), c3.ask.context);
const c4 = O.chatShape({ activity: 'decision', turns: [], decision: null }, R, 'zh-TW', 'x', now);
check('waiting with no readable options: the terminal, nothing answered from here', c4.ask && c4.ask.term === true);
const c5 = O.chatShape({ activity: 'decision', turns: [], decision: { id: 'q2', tag: 'connect:gmail', reason: 'x', options: [{ n: 1, text: 'Yes' }] } }, R, 'zh-TW', 'x', now);
check('a connection question (tagged) goes to the terminal too', c5.ask && c5.ask.term === true);
const c6 = O.chatShape({ turns: [], queue: { queued: ['a'], given_up: [{ id: 'b', why: '示範原因' }], busy: { reason: 'working', since: now / 1000 - 180 } } }, R, 'zh-TW', '示範工程師', now);
check('the queue in plain words: waiting, why, how long; and one given up with its reason',
      /還在排隊/.test(c6.queue) && /示範工程師還在做上一步（已經 3 分鐘）/.test(c6.queue) && /1 則沒送出（示範原因）/.test(c6.queue), c6.queue);
check('in English too', /waiting to go in/.test(O.chatShape({ turns: [], queue: { queued: ['a'] } }, R, 'en', 'Demo', now).queue));
const c7 = O.chatShape({ turns: [{ kind: 'user_msg', text: '<b>示範</b>', ts: 1 }], login: { step: 'menu' } }, R, 'zh-TW', 'x', now);
check('the person\'s own text is escaped too; a sign-in under way is flagged', c7.items[0].html === '&lt;b&gt;示範&lt;/b&gt;' && c7.login === true, c7);

console.log('the task board and the loops (boardOf, loopOf, build)');
const bt = O.build({ botsOn: true, roster, primary: 'chief', tabs, now, kit, seen: {}, tasks: {
  decisions: [{ id: 9, title: '示範決定', kind: 'decision', status: 'waiting', owner: 'user', requester: 'eng', waiting_on: 'user', updated_at: 5 }],
  stuck: [{ id: 2, idle_s: 90000 }, { id: 3, title: '示範分頁卡住', status: 'waiting', owner: '示範分頁', idle_s: 8000, updated_at: 1 }],
  open: [{ id: 1, title: '示範待辦', status: 'todo', owner: 'eng', requester: 'chief', updated_at: 100, due_at: 1800000000 },
         { id: 2, title: '示範進行中', status: 'doing', owner: '示範工程師', requester: 'chief', waiting_on: 'user', next_step: '示範下一步', updated_at: 50 },
         { id: 3, title: '示範分頁卡住', status: 'waiting', owner: '示範分頁', updated_at: 1 },
         { id: 9, title: '示範決定', kind: 'decision', status: 'waiting', owner: 'user', updated_at: 5 }] } });
const BD = bt.board;
check('the board is decisions, stuck, open: each task once, in that order', BD.decisions.map(x => x.id).join() === '9' && BD.stuck.map(x => x.id).join() === '2,3'
  && BD.open.map(x => x.id).join() === '1' && BD.all.map(x => x.id).join() === '9,2,3,1', BD);
check('a decision and a stuck row are marked; a stuck row with only {id, idle_s} borrows its text from the open list',
  BD.decisions[0].decision === true && BD.stuck[0].stuck === true && BD.stuck[0].title === '示範進行中' && BD.stuck[0].idle_s === 90000 && BD.stuck[0].next_step === '示範下一步', BD.stuck[0]);
check('an owner is a person of the office: by role, by the bot\'s label, by an intern\'s tab title; the person is "user"',
  BD.open[0].owner_key === 'b:eng' && BD.stuck[0].owner_key === 'b:eng' && BD.stuck[1].owner_key === 't:s3' && BD.decisions[0].owner_key === 'user', [BD.open[0].owner_key, BD.stuck[0].owner_key, BD.stuck[1].owner_key]);
check('…waiting-on and requester likewise; a name nobody has is empty', BD.stuck[0].waiting_key === 'user' && BD.decisions[0].requester_key === 'b:eng' && O.personKey('客戶回覆', { roles: new Set(), labels: {}, temps: {} }) === '');
check('times stay in seconds (the store\'s), ids are numbers', BD.open[0].due_at === 1800000000 && typeof BD.open[0].id === 'number');
const bo = O.build({ botsOn: true, roster, primary: 'chief', tabs, now, kit, seen: {}, tasks: null }).board;
check('no tasks at all: an empty board, not a crash', bo.all.length === 0 && bo.decisions.length === 0);
const sec = Math.floor(now / 1000);
check('loopOf: a /loop keeps its wake, interval, rounds and a clipped summary',
  JSON.stringify(O.loopOf({ kind: 'loop', status: 'sleeping', next_ts: sec + 600, delay: 1200, rounds: 4, reason: '示範'.repeat(60), crons: [] }, now)).length > 50
  && O.loopOf({ kind: 'loop', status: 'sleeping', next_ts: sec + 600, delay: 1200, rounds: 4, reason: '示範'.repeat(60) }, now).reason.length <= 90);
check('…one whose wake passed over half an hour ago is no loop (the status behind it can be stale)', O.loopOf({ kind: 'loop', status: 'sleeping', next_ts: sec - 3600 }, now) === null
  && O.loopOf({ kind: 'loop', status: 'running', next_ts: sec - 60 }, now).status === 'running');
check('…a cron has no wake and stays; nothing, or an unknown kind, is null', O.loopOf({ kind: 'cron', crons: [{ desc: '示範', schedule: '*/30 * * * *' }] }, now).status === 'scheduled'
  && O.loopOf(null, now) === null && O.loopOf({ kind: 'weird' }, now) === null);
const tabsL = tabs.map(q => q.sid === 's2' ? Object.assign({}, q, { loop: { kind: 'loop', status: 'sleeping', next_ts: sec + 900, delay: 900, reason: '示範輪詢' } })
  : q.sid === 's3' ? Object.assign({}, q, { loop: { kind: 'cron', crons: [{ desc: '示範排程', schedule: '0 9 * * *' }] } })
  : q.sid === 's4' ? Object.assign({}, q, { loop: { kind: 'loop', status: 'sleeping', next_ts: sec + 60 } }) : q);
const sl = O.build({ botsOn: true, roster, primary: 'chief', tabs: tabsL, tasks: null, kit, seen: {}, now });
check('a bot and an intern with a loop carry it; the snapshot lists them with who they are', sl.bots.find(b => b.id === 'eng').loop.delay === 900 && sl.temps[0].loop.kind === 'cron'
  && sl.bots.find(b => b.id === 'chief').loop === null && sl.loops.map(x => x.key).join() === 'b:eng,t:s3,', sl.loops.map(x => x.key));
check('…a tab no one sits at (the terminal) is listed without a person', sl.loops[2].name === '示範終端機' && sl.loops[2].key === '');
const ss = O.schedShape({ schedules: [{ id: 'a', title: '示範', when: { en: 'Daily 09:00', zh: '每天 09:00' }, enabled: true, cmd: '/bin/secret --token x', members: [1, 2] }], configured: true, launchd_ok: true });
check('schedShape keeps title, when, on/off and drops the command', ss.ok && ss.rows[0].title === '示範' && ss.rows[0].when.zh === '每天 09:00' && ss.rows[0].enabled === true
  && ss.rows[0].count === 2 && !('cmd' in ss.rows[0]) && !JSON.stringify(ss).includes('secret'), ss);
check('…not supported / not configured / failed are said, not an empty list', O.schedShape({ schedules: [], unsupported: true }).unsupported === true
  && O.schedShape({ schedules: [], configured: false }).configured === false && O.schedShape({ schedules: [], error: 'x' }).ok === false);
const sx = O.schedShape({ schedules: [
  { id: 'a', title: 'A', when: { en: 'x', zh: 'x' }, enabled: false, owner: '  ops  ', toggle: false, state: 'armed', cmd: '/bin/secret',
    slots: { interval: 600, calendar: [{ Hour: 9, Minute: 30, Weekday: 1, Bogus: 5, Day: 'x' }, null, { Month: -1, Hour: 99999 }], partial: false } },
  { id: 'b', title: 'B', when: 'raw', enabled: true, slots: { interval: -5, calendar: Array.from({ length: 500 }, () => ({ Hour: 1 })) } },
  { id: 'c', title: 'C', when: 'raw', enabled: true }] });
check('schedShape passes owner, toggle, state and slots through, clipped to what the calendar uses',
  sx.rows[0].owner === 'ops' && sx.rows[0].toggle === false && sx.rows[0].state === 'armed' && sx.rows[0].slots.interval === 600
  && JSON.stringify(sx.rows[0].slots.calendar) === '[{"Weekday":1,"Hour":9,"Minute":30},{},{}]' && !JSON.stringify(sx).includes('secret'), JSON.stringify(sx.rows[0]));
check('…a row without them: no owner, a switch, no slots; a long calendar is cut and a bad interval dropped',
  sx.rows[2].owner === '' && sx.rows[2].toggle === true && sx.rows[2].slots.interval === null && sx.rows[2].slots.calendar.length === 0
  && sx.rows[1].slots.calendar.length === 200 && sx.rows[1].slots.interval === null);

console.log(fails ? `\n${fails} failed` : '\nALL PASS');
process.exit(fails ? 1 : 0);
