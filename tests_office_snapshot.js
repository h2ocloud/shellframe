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
  res: { label: '示範研究員', bot: { avatar: { shape: 'nope', color: 'nope' } } },
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
  { role: 'b', label: '示範建置員', state: 'awake', activity: 'working', avatar: { shape: 'hex', color: 'amber' } },
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

console.log(fails ? `\n${fails} failed` : '\nALL PASS');
process.exit(fails ? 1 : 0);
