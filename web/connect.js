/* ShellBot connections — Settings → Connect, the wizard card, the "needs reconnecting" cards.
 *
 * The Python side (connectors/, api_connect.py) owns every fact: which services there are, which
 * way each is connected, which step the person is on, what a check or a test found, and the plain
 * words for it. This file draws what `connect_list`, `connect_describe`, `connect_status` and the
 * `view` of connect_start / connect_step / connect_test hand it, and sends the person's clicks back.
 * It knows no service by name: a service is an icon key and words from its descriptor.
 *
 *   Settings → Connect   one row per service: icon, name, one line on where it stands, a mark when it
 *                        needs the person. While the tab is open it asks connect_status(refresh) every 60 s.
 *   the wizard card      a route to pick, the steps (words, a link, buttons, options, a check, "I'm stuck"),
 *                        the test, and what it came to. Errors say message.text and message.next.
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
    not_started:     ['Not connected', '還沒接上', 'idle', 'Connect', '開始連接'],
    waiting_user:    ['Setting up', '設定中', 'busy', 'Continue', '繼續'],
    verifying:       ['Verifying…', '驗證中…', 'busy', 'View', '查看'],
    connected:       ['Connected', '已接上', 'ok', 'Manage', '管理'],
    failed:          ['Did not connect', '沒接成功', 'bad', 'Try again', '重試'],
    needs_reconnect: ['Needs reconnecting', '需要重新接上', 'bad', 'Reconnect', '重新接上'],
  };
  const stateOf = st => STATES[st && st.state] || STATES.not_started;
  const CLI_STATE = {
    connected: ['Connected', '已接上', 'ok'], missing: ['Not added yet', '還沒加入', 'idle'],
    manual: ['Set up by hand', '要手動設定', 'warn'], unsupported: ['Not supported', '不支援', 'idle'], unknown: ['Not known', '不明', 'idle'],
  };
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
  const L = { order: [], by: {}, clis: [], loaded: false };
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
  };
  const icon = key => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[key] || ICONS.plug}</svg>`;

  // One line on where a connection stands (what a phone or sfctl is told, health.words, in short).
  function lineOf(st, d) {
    const route = routeOf(d, st.route);
    const rn = route ? pick(route.name) : '';
    switch (st.state) {
      case 'waiting_user': {
        const s = st.step;
        return t(`Setting up${s ? `, step ${s.index} of ${s.total}` : ''}`, `設定中${s ? `，第 ${s.index} 步，共 ${s.total} 步` : ''}`);
      }
      case 'verifying': return t('Verifying it works…', '正在驗證通不通…');
      case 'connected': return [t('Connected', '已接上'), rn, st.connected_at ? dateOf(st.connected_at) : '', pick(st.problem && st.problem.text)].filter(Boolean).join(' · ');
      case 'failed': return pick(st.problem && st.problem.text) || t('It did not connect', '沒接成功');
      case 'needs_reconnect': return pick(st.problem && st.problem.text) || t('It stopped working. Connect it again', '用不了了，需要重新接上');
      default: return d ? pick(d.summary) : t('Not connected yet', '還沒接上');
    }
  }
  const routeOf = (d, id) => ((d && d.routes) || []).find(r => r.id === id) || null;

  // ── the list: Settings → Connect ─────────────────────────────────────────
  let panel = null, settingsTimer = 0;
  async function refreshList() {
    const r = await call('connect_list');
    if (r && r.success && Array.isArray(r.services)) {
      L.order = r.services.map(s => s.service);
      L.by = {};
      r.services.forEach(take);
      L.clis = Array.isArray(r.clis) ? r.clis : [];
      L.loaded = true;
    }
    return r;
  }
  function renderList() {
    if (!panel) return;
    const rows = L.order.map(id => {
      const st = L.by[id], d = descDone[id], s = stateOf(st), bad = !!st.attention;
      const sub = bad && st.problem && st.problem.next ? `<small>${esc(pick(st.problem.next))}</small>` : '';
      return `<div class="cn-row${bad ? ' attn' : ''} tone-${s[2]}" data-service="${esc(id)}">` +
        `<div class="cn-ico">${icon(st.icon)}</div>` +
        `<div class="cn-main"><b>${esc(pick(st.name) || id)}${bad ? `<i class="cn-flag">${esc(t('Needs you', '要處理'))}</i>` : ''}</b>` +
        `<span><i class="cn-dot ${s[2]}"></i>${esc(lineOf(st, d))}</span>${sub}</div>` +
        `<button type="button" class="cn-btn${bad ? ' primary' : st.state === 'connected' ? ' ghost' : ''}" data-cn="open" data-service="${esc(id)}">${esc(t(s[3], s[4]))}</button></div>`;
    }).join('');
    panel.innerHTML = `<p class="cn-intro">${esc(t('Services your bots can use. Pick one and ShellBot walks you through connecting it, and checks later that it still works.',
      '讓 bot 能用的外部服務。點一個服務，ShellBot 一步一步帶你接上，之後也會檢查它還通不通。'))}</p>` +
      (rows ? `<div class="cn-list">${rows}</div>` : `<p class="cn-empty">${esc(L.loaded ? t('No services to connect yet.', '目前沒有可以連接的服務。') : t('Reading…', '讀取中…'))}</p>`);
    const tab = doc.querySelector('#settings-tabs-bar [data-tab="connect"]');
    if (tab) tab.textContent = t('Connect', '連接') + (attention().length ? ' ●' : '');
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
    await refreshList();
    renderList();
    renderSide();
    L.order.forEach(id => describe(id).then(() => renderList()));
    clearInterval(settingsTimer);
    refreshStatuses();
    settingsTimer = setInterval(() => { if (panelShown()) refreshStatuses(); else clearInterval(settingsTimer); }, 60000);
  }
  function mountSettingsTab() {
    const bar = $('settings-tabs-bar');
    const modal = doc.querySelector('#modal-settings .modal');
    if (!bar || !modal || $('settings-panel-connect')) return;
    const tab = doc.createElement('button');
    tab.className = 'settings-tab';
    tab.dataset.tab = 'connect';
    bar.insertBefore(tab, bar.querySelector('[data-tab="marketplace"]'));          // after LINE, before Plugins
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
    panel.addEventListener('click', e => {
      const b = e.target.closest('[data-cn="open"]');
      if (b) open(b.dataset.service, {});
    });
    const gear = $('btn-settings');
    if (gear) gear.addEventListener('click', renderList);                          // the label follows the page's language
    renderList();
  }

  // ── the wizard card ──────────────────────────────────────────────────────
  // UI-only state lives here: which route is picked before connect_start, the stuck panel, the test's
  // handle and timer. Where the person is (route, step, results) is the Python side's: `view`.
  let S = null, card = null, lastFocus = null, pollTimer = 0;
  const fresh = (service, from) => ({ open: true, service, desc: null, status: null, view: null, mode: 'load', pick: '', stuck: false,
    prob: '', busy: '', err: null, test: null, from: from || '', checked: {}, opts: {}, optsFor: '', note: '' });
  const routes = () => (S.desc && S.desc.routes) || [];
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
  function resultBox(step) {
    const r = step.result;
    const c = step.check;
    if (!r) {
      if (!c) return '';
      if (S.busy === 'check') return `<div class="cn-msg"><span class="cn-dot busy"></span><span>${esc(t('Checking…', '檢查中…'))}</span></div>`;
      return c.on === 'done' ? `<div class="cn-msg"><span class="cn-dot idle"></span><span>${esc(t('Not checked yet', '還沒檢查'))}</span>${btn('check', t('Check now', '現在檢查'), 'ghost small')}</div>` : '';
    }
    const st = c && c.states && c.states[r.code];
    const text = pick(r.text) || (st && pick(st.text)) || '';
    const hint = st && st.hint ? pick(st.hint) : '';
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
      return `<div class="cn-result ok"><i class="cn-big ok" aria-hidden="true">✓</i><div><h4>${esc(t('Connected', '已接上'))}</h4>` +
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
        (r.recommended ? `<i class="cn-badge">${esc(t('Recommended', '推薦'))}</i>` : '') + '</span>' +
        `<span class="cn-route-blurb">${esc(pick(r.blurb))}</span>` +
        `<span class="cn-chips">${chips.map(c => `<i>${esc(c)}</i>`).join('')}</span>` +
        (cliKeys.length ? `<span class="cn-route-for">${esc(t('For: ', '適用：'))}${esc(cliKeys.map(cliName).join(t(', ', '、')))}</span>` : '') + '</span></button>';
    }).join('');
    return `<h3 class="cn-title" id="cn-title">${esc(t('How do you want to connect it?', '想用哪一種方式接上？'))}</h3>` +
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
    return `<div class="cn-result ok"><i class="cn-big ok" aria-hidden="true">✓</i><div><h4>${esc(t('Connected', '已接上'))}</h4>` +
      `<p>${esc([route ? pick(route.name) : '', st.connected_at ? dateOf(st.connected_at) : ''].filter(Boolean).join(' · '))}</p></div></div>` + warn +
      (S.test ? testBlock(null) : '') + errBox() +
      (clis ? `<div class="cn-sect"><b>${esc(t('Your AIs', '各個 AI'))}</b>${clis}</div>` : '') +
      (scopes ? `<div class="cn-sect"><b>${esc(t('What it may do', '它能做的事'))}</b><div class="cn-chips">${scopes}</div></div>` : '');
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
    if (S.mode === 'connected') {
      return `<div class="cn-foot-l">${btn('disconnect', t('Disconnect', '中斷連接'), 'ghost')}</div><div class="cn-foot-r">` +
        (S.test && S.test.state !== 'done' ? btn('test-stop', t('Stop the test', '停止測試'), 'ghost')
          : btn('test-start', t('Test again', '重新測試'), 'ghost')) +
        btn('reconnect', t('Connect again', '重新接上'), 'ghost') + btn('finish', t('Done', '完成'), 'primary') + '</div>';
    }
    const step = vstep();
    if (!step) return `<div class="cn-foot-l"></div><div class="cn-foot-r">${btn('close', t('Close', '關閉'), 'ghost')}</div>`;
    if (isTest(step)) return testFoot(step);
    const open = httpsOnly(step.url) ? btn('open', t('Open page', '打開頁面') + ' ↗', 'ghost', ` data-url="${esc(step.url)}"`) : '';
    return `<div class="cn-foot-l">${open}</div><div class="cn-foot-r">${S.stuck ? '' : btn('stuck', t("I'm stuck", '卡住了'), 'warn')}${btn('did', t('I did it', '我做好了'), 'primary')}</div>`;
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
    render();
    if (step && step.check && step.check.on === 'enter' && !step.result && !S.checked[step.id]) { S.checked[step.id] = true; return runCheck(); }
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
    if (S === me && vstep() && vstep().id === sid) render();
  }
  async function runAction(id) {
    const s = vstep();
    const a = ((s && s.actions) || []).find(x => x.id === id);
    if (!a) return;
    const me = S;
    const res = await step('act', { action: id, options: S.opts }, 'act:' + id);
    if (S !== me) return;
    if (res && res.success !== false && a.after === 'check' && s.check) return runCheck();
    render();
  }
  async function didIt() {
    const me = S, before = (vstep() || {}).id;
    const res = await step('done', null);
    if (S !== me) return;
    if (res.success !== false && (vstep() || {}).id !== before) { S.checked[before] = true; return enter(); }
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
    const yes = ask ? await ask(t(`Disconnect ${name}? Bots lose access to it; you can connect it again any time.`, `要中斷 ${name} 嗎？bot 會用不了它，之後隨時可以再接上。`),
      { title: t('Disconnect', '中斷連接'), ok: t('Disconnect', '中斷連接'), cancel: t('Keep it', '保留') }) : false;
    const me = S;
    if (!yes || S !== me) return;
    S.busy = 'disconnect'; render();
    const res = await call('connect_disconnect', S.service, '{}');
    if (S !== me) return;
    S.busy = '';
    if (res && res.success === false) { S.err = res.message || null; return render(); }
    close();
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
    const state = S.status.state;
    S.pick = ((routes().find(r => r.recommended) || routes()[0]) || {}).id || '';
    if (o.tag || o.reconnect) S.mode = 'step';
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
    clearTimeout(pollTimer);
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
      return `<div class="cn-sc" role="alert"><div class="cn-sc-text"><b><i class="cn-sc-dot"></i>${esc(s.state === 'failed' ? t(`${name} did not connect`, `${name} 沒接成功`) : t(`${name} needs reconnecting`, `${name} 需要重新接上`))}</b>` +
        `<span>${esc(pick(s.problem && s.problem.text) || t('Bots cannot use it right now.', 'bot 現在用不了它。'))}</span></div>` +
        `<button type="button" class="cn-sc-x" data-cn-dismiss="${key}" aria-label="${esc(t('Hide', '先不要'))}" title="${esc(t('Hide until it changes', '先收起來，狀況有變再提醒'))}">×</button>` +
        `<button type="button" class="cn-btn small primary" data-cn-side="${esc(s.service)}">${esc(s.state === 'failed' ? t('Fix it', '處理') : t('Reconnect', '重新接上'))}</button></div>`;
    }).join('') + (items.length > 2 ? `<div class="cn-sc-more">${esc(t(`${items.length - 2} more in Settings → Connect`, `另外 ${items.length - 2} 項，到 設定 › 連接 看`))}</div>` : '');
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
      `<div class="cm-ask-title">${esc(t('Your call · ', '要你決定 · ') + t(`${name} needs connecting`, `需要接上 ${name}`))}</div>` +
      `<div class="cm-ask-q">${esc(d.reason || t(`This tab has no ${name} tools. Connect it now?`, `這個分頁沒有 ${name} 的工具。要不要現在接上？`))}</div>` +
      `<div class="cm-opts"><button class="cm-opt tone-yes" data-act="connect" data-service="${esc(id)}" data-tag="${esc(d.tag)}" ` +
      `data-n="${esc(go ? go.n : '')}" data-id="${esc(d.id)}"${off}>${esc(t(`Connect ${name} step by step`, `一步一步接上 ${name}`))}</button>` +
      opts.filter(x => x !== go).map(x => `<button class="cm-opt" data-act="answer" data-n="${esc(x.n)}" data-id="${esc(d.id)}"${off}>${esc(x.label)}</button>`).join('') + '</div>' +
      `<div class="cm-ask-note">${esc(t('"Step by step" opens the wizard and answers the bot with option 1, so it waits for you.',
        '「一步一步接上」會打開精靈，同時回覆 bot 選項 1，請它先等你。'))}</div>` +
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
