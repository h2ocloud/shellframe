/* ShellBot 「待你決定」 (Waiting on you): what is waiting on the person, always in reach on the right. See docs/chat-shell.md.
 *
 * Agents wait on the person in two places, and both are listed here so they need not be dug out of long conversations:
 *   1. 分頁在等你: a tab whose screen is up on a menu or a question for the person right now. It is the office's red "!" and the chat
 *      view's decision buttons: this computer's tab whose light is the decision one (the status monitor's push, no polling of
 *      screens: SFOffice.tabState of the tab's _dotClass). Its question and options are what the chat view shows (chat_view's
 *      `decision`, shaped by SFChat.rules.mapDecision), asked once when the tab starts waiting, when the list is opened and after
 *      an answer, never on a timer; a button answers exactly as the chat view's does (chat_answer, only while that question is
 *      still the tab's). The entry goes when the light is no longer the decision one.
 *   2. 等你決定: tasks_overview's `decisions` (owner = the person), every bot's, with who asked. The data is web/board.js's: the same
 *      read as the sidebar button's red count (SFBoard.onData), asked about every 20 s while the window is visible; this file has no
 *      timer that reads Python, only one that reads the page's own state once a second (the tab lights), and none while hidden.
 *      A card opens in place: its full title, next step, the last lines of its history (task_detail), an answer box (required)
 *      and 「也告訴 <asker>」 (task_user_act + one line to the asker: SFOffice.host.board.reply, the board's own 回覆 path),
 *      plus 「跳到那個分頁」 and 「在白板上看」 (SFOffice.openBoard on that card).
 *   3. 隔壁：<電腦> 有 N 件等你, one line per paired computer (link_peer_tasks), which opens that computer's read-only board.
 *   4. 交付物 (web/deliverables.js draws it, below the decisions in the same column): what the bots and tabs handed the person, undecided ones by
 *      person with 打開 / 先放著 / 拿走, and the small 「可能是交付物」 group (收下 / 忽略). Its number is the new ones; a pill in the header jumps to it.
 * The red number is SFBoard.count(): the sidebar button's. A tab waiting is a number of its own beside it (a tab is no card).
 *
 * Where it sits (web/pending.css): chat view, wide window (the conversation's 880px column and the list both fit): a docked column
 * on the right that does not cover the conversation (the chat layer gives up its right edge; the terminal under it is untouched).
 * Narrower, or folded: a thin strip with the count, and a click slides the list over the conversation as a drawer (outside click,
 * Esc close it). Terminal view: a small floating capsule at the pane's top-right that takes no width (the terminal is never
 * resized), a click opens the same drawer. Open or folded is kept per view in the app's config (settings.pending_rail:
 * pywebview clears localStorage at every launch, so it alone never survives a restart), with localStorage as the fast cache.
 * The pure parts (sortDecisions, ageWords) run in node.
 */
(function (root) {
  'use strict';

  // ── pure ────────────────────────────────────────────────────────────────
  const DAY = 86400;
  // Overdue first (the longest overdue first), then due within a day (soonest first), then the rest by when it was asked: the one that
  // has waited longest first (created_at, oldest first), the id breaking ties. Times are the store's seconds. A new array.
  function sortDecisions(rows, now) {
    const n = Number(now) || Date.now() / 1000;
    const bucket = r => { const d = Number(r.due_at) || 0; return d && d < n ? 0 : d && d - n <= DAY ? 1 : 2; };
    return (rows || []).slice().sort((a, b) => {
      const x = bucket(a), y = bucket(b);
      if (x !== y) return x - y;
      if (x < 2) return (Number(a.due_at) - Number(b.due_at)) || (Number(a.id) - Number(b.id));
      return ((Number(a.created_at) || Number(a.updated_at) || 0) - (Number(b.created_at) || Number(b.updated_at) || 0)) || (Number(a.id) - Number(b.id));
    });
  }
  // "3 hours ago", from seconds-ago.
  function ageWords(sec, zh) {
    const s = Math.max(0, Number(sec) || 0), m = Math.floor(s / 60), h = Math.floor(s / 3600), d = Math.floor(s / DAY);
    if (m < 1) return zh ? '剛剛' : 'just now';
    if (m < 60) return zh ? `${m} 分鐘前` : `${m} min ago`;
    if (h < 24) return zh ? `${h} 小時前` : `${h} h ago`;
    return zh ? `${d} 天前` : `${d} d ago`;
  }
  const pure = { sortDecisions, ageWords };
  if (typeof module !== 'undefined' && module.exports) { module.exports = pure; return; }
  const doc = root.document;
  if (!doc) return;

  // ── the page ────────────────────────────────────────────────────────────
  const host = () => root.__sfHost || null;
  const api = () => (root.pywebview && root.pywebview.api) || null;
  const lang = () => (host() && host().lang && host().lang()) || navigator.language || '';
  const isZh = () => /^zh/i.test(lang());
  const t = (en, zh) => (isZh() ? zh : en);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const O = () => root.SFOffice || null;
  const B = () => root.SFBoard || null;
  const SD = () => root.SFDeliv || null;
  const scale = () => parseFloat(getComputedStyle(doc.documentElement).getPropertyValue('--ui-scale')) || 1;
  const PANEL_PX = 300, STRIP_PX = 26, COLUMN_PX = 880, SPARE_PX = 40;   // the docked list, the thin strip, the chat column, room to breathe
  const STRIP_MIN_PX = 420;                                               // a chat layer narrower than this (a huge sidebar, a tiny window) has no width to give: the capsule floats instead

  let wrap = null, cap = null, panel = null, scrim = null, secTabs = null, secCards = null, secPeers = null, secDeliv = null, pillD = null, headN = null, statusEl = null;
  let kind = '', docked = false, stripped = false, drawer = false, mounted = false, armed = false, seen = false;
  const cssSet = {};
  let armAt = 0;
  const ARM_MS = 3000;                                                    // the tab lights settle in the first seconds
  let prefs = { chat: true, term: false };
  try { const p = JSON.parse(localStorage.getItem('sf.pending.v1') || 'null'); if (p && typeof p === 'object') prefs = Object.assign(prefs, { chat: p.chat !== false, term: p.term === true }); } catch (_) { /* no storage */ }
  // Open or folded, per view, also lives in config.json (settings.pending_rail, like the sidebar's sidebar_open): pywebview starts
  // with an empty localStorage every time, so the cache above is only the quick path and the config is what survives a restart.
  let adopted = false;
  const cfgSettings = () => { const c = host() && host().cfg && host().cfg(); return (c && c.settings) || null; };
  const cacheLocal = () => { try { localStorage.setItem('sf.pending.v1', JSON.stringify(prefs)); } catch (_) { /* no storage */ } };
  function savePrefs() {
    cacheLocal();
    adopted = true;                                                   // the person's own choice is newer than whatever the config held
    const s = cfgSettings(), a = api(), cur = s && s.pending_rail;
    if (!s || (cur && cur.chat === prefs.chat && cur.term === prefs.term)) return;
    s.pending_rail = { chat: prefs.chat, term: prefs.term };
    try { if (a && a.save_settings) Promise.resolve(a.save_settings(JSON.stringify(s))).catch(() => { /* kept on the page; the next save carries it */ }); } catch (_) { /* no bridge */ }
  }
  // Once, when the config has been read: what it remembers wins over the (empty after a start) cache.
  function adoptConfig() {
    if (adopted) return;
    const s = cfgSettings();
    if (!s) return;
    adopted = true;
    const p = s.pending_rail;
    if (!p || typeof p !== 'object') return;
    const next = { chat: p.chat !== false, term: p.term === true };
    if (next.chat === prefs.chat && next.term === prefs.term) return;
    prefs = next; cacheLocal();
    if (kind === 'term') setDrawer(prefs.term, false); else { measure(); render(); }
  }

  // state of the list
  const waiting = new Map();          // sid → {sid, since, d: mapped decision | null, asked: ms of the last chat_view, tries, busy, note}
  let openId = null;                  // the expanded card
  const drafts = {};                  // task id → {text, tell}
  const form = { busy: false, msg: '', bad: false };
  const detail = new Map();           // task id → {upd, loading, ok, events}
  let composing = false, dirty = false, lastTotal = 0;
  const last = { tabs: '', cards: '', peers: '', cap: '', head: '', deliv: '', pill: '' };

  // ── what is waiting ───────────────────────────────────────────────────
  const sessions = () => (host() && host().sessions && host().sessions()) || {};
  const tabName = (sid, s) => { const C = root.SFChat; try { return (C && C.label ? C.label(sid, String(s.label || '').trim()) : String(s.label || '')) || sid; } catch (_) { return String(s.label || sid); } };
  function waitingSids() {
    const o = O(), out = [];
    if (!o || !o.tabState) return out;
    const S = sessions();
    for (const sid of Object.keys(S)) if (S[sid] && !S[sid].isRemote && o.tabState(S[sid]._dotClass) === 'decision') out.push(sid);
    return out;
  }
  async function fetchQuestion(sid) {
    const e = waiting.get(sid), C = root.SFChat;
    if (!e || !C || !C.call || !C.rules || !api() || doc.hidden) return;
    e.asked = Date.now(); e.tries++;
    let r = null;
    try { r = JSON.parse(await C.call(sid, 'view', 1, '')); } catch (_) { return; }
    const cur = waiting.get(sid);
    if (!cur || !r || r.success === false) return;
    const d = r.decision;
    cur.d = d && (d.options || []).length && !d.tag ? C.rules.mapDecision(d, lang()) : null;
    render();
  }
  function sync() {
    if (doc.hidden || !mounted) return;
    const want = waitingSids(), now = Date.now();
    let changed = false;
    for (const sid of want) {
      if (!waiting.has(sid)) {
        const st = (sessions()[sid] || {})._serverStatus || {}, at = Number(st.activity_at) > 0 ? Number(st.activity_at) * 1000 : now;
        waiting.set(sid, { sid, since: Math.min(at, now), d: null, asked: 0, tries: 0, busy: false, note: '' });
        changed = true;
        fetchQuestion(sid);
      } else {                                                        // a menu that could not be read yet is asked again, a few times
        const e = waiting.get(sid);
        if (!e.d && e.tries < 4 && now - e.asked > 3000) fetchQuestion(sid);
      }
    }
    for (const sid of [...waiting.keys()]) if (want.indexOf(sid) < 0) { waiting.delete(sid); changed = true; }
    if (!armed && seen && Object.keys(sessions()).length) {            // what was there when the window opened is no news: no pulse for it
      if (!armAt) armAt = now + ARM_MS;
      else if (now >= armAt) { armed = true; lastTotal = redCount() + waiting.size; }
    }
    if (changed) render();
    else if (!doc.hidden) render();                                   // times and the layout; render() skips what did not change
  }
  const refetchAll = () => { for (const e of waiting.values()) if (Date.now() - e.asked > 2500) fetchQuestion(e.sid); };

  // ── mode: chat view docked / strip / drawer, terminal view capsule / drawer ──
  function measure() {
    const k = doc.body.classList.contains('sf-chat-shown') ? 'chat' : 'term';
    const w = wrap ? wrap.getBoundingClientRect().width : 0, sc = scale();
    const wide = k === 'chat' && w >= COLUMN_PX + PANEL_PX * sc + SPARE_PX;       // the column and the list both fit
    if (k !== kind) { kind = k; drawer = k === 'term' ? prefs.term : false; }
    const nowDocked = k === 'chat' && wide && prefs.chat;
    if (nowDocked !== docked) docked = nowDocked;
    if (docked) drawer = false;
    stripped = k === 'chat' && !docked && w >= STRIP_MIN_PX;
    doc.body.classList.toggle('sf-pend-docked', docked);
    doc.body.classList.toggle('sf-pend-strip', stripped);
    for (const [name, px] of [['--pend-w', PANEL_PX * sc], ['--pend-strip-w', STRIP_PX * sc]]) {   // only when it changes: the style attribute is watched
      const v = `${Math.round(px)}px`;
      if (cssSet[name] !== v) { cssSet[name] = v; doc.documentElement.style.setProperty(name, v); }
    }
    return { wide };
  }
  const shown = () => docked || drawer;
  function setDrawer(on, focus) {
    if (docked) return;
    const was = drawer;
    drawer = !!on;
    if (kind === 'term') { prefs.term = drawer; savePrefs(); }
    render();
    if (drawer && !was) { refetchAll(); const b = B(); if (b && b.poll) b.poll(); }
    if (drawer && focus !== false) { try { panel.querySelector('.pend-x').focus({ preventScroll: true }); } catch (_) { /* gone */ } }
    if (!drawer && was && focus !== false) giveFocusBack();
  }
  function giveFocusBack() {
    try {
      const inp = doc.getElementById('chat-input');
      if (kind === 'chat' && inp && inp.getClientRects().length) { inp.focus({ preventScroll: true }); return; }
      const H = host(), s = H && sessions()[H.active()];
      if (s && s.term && kind === 'term') s.term.focus();
    } catch (_) { /* nothing to focus */ }
  }
  function onCap() {
    const b = measure();
    if (kind === 'chat' && b.wide) { prefs.chat = true; savePrefs(); render(); return; }   // wide: the strip docks the list
    setDrawer(!drawer);
  }
  function onFold() {                                                   // « on the docked column: fold it to the strip
    if (docked) { prefs.chat = false; savePrefs(); render(); return; }
    setDrawer(false);
  }

  // ── what is drawn ─────────────────────────────────────────────────────
  function cards() {
    const b = B(), now = Date.now() / 1000;
    const raw = b && b.decisions ? b.decisions() : [];
    const rows = sortDecisions(raw, now);
    const o = O(), keyOf = new Map();
    try { if (o && o.boardRows) for (const r of o.boardRows({ decisions: raw })) keyOf.set(r.id, r.requester_key); } catch (_) { /* no keys: no asker */ }
    return { rows, keyOf, now };
  }
  const askerOf = row => { const o = O(); try { return o && o.host && o.host.board && o.host.board.askerOf ? o.host.board.askerOf(row) : null; } catch (_) { return null; } };
  // Someone asked but cannot be told from here: {name, why} (office.js boardHost.askerMiss), said in the form instead of a silent skip.
  const askerMissOf = row => { const o = O(); try { return o && o.host && o.host.board && o.host.board.askerMiss ? o.host.board.askerMiss(row) : null; } catch (_) { return null; } };
  // A name the store holds (a bot's role, a tab's title) as the person knows it: the bot's label, else as written.
  const personName = raw => {
    const r = String(raw || '');
    if (r === 'user') return t('You', '你');
    const Bots = root.SFBots, st = (Bots && Bots.state && Bots.state()) || {}, e = (st.roster || {})[r];
    return String((e && e.label) || r);
  };
  const askerName = row => {
    const a = askerOf(row);
    if (a && a.name) return a.name;
    const r = String(row.requester || '');
    return r && r !== 'user' ? personName(r) : '';
  };
  const faceOf = sid => { const Bots = root.SFBots; try { return (Bots && Bots.enabled && Bots.enabled() && Bots.face(sid)) || ''; } catch (_) { return ''; } };

  function tabsHtml() {
    const S = sessions();
    const items = [...waiting.values()].filter(e => S[e.sid]);
    if (!items.length) return '';
    const now = Date.now();
    return `<h3>${esc(t('Tabs waiting on you', '分頁在等你'))}<b>${items.length}</b></h3>` + items.map(e => {
      const s = S[e.sid], d = e.d, name = tabName(e.sid, s);
      const q = d ? (d.questionTranslated || !d.translated ? d.question : t('It is asking before it goes on.', '它在繼續之前先問你。'))
                  : t('It is waiting for you in the terminal.', '它在終端機上等你決定。');
      const opts = d ? d.options.map(o => `<button type="button" class="pend-opt${o.tone ? ' tone-' + esc(o.tone) : ''}" data-act="answer" data-sid="${esc(e.sid)}" data-n="${esc(o.n)}" data-id="${esc(d.id)}"` +
        `${e.busy ? ' disabled' : ''} title="${esc(t(`Option ${o.n}: ${o.raw}`, `選項 ${o.n}：${o.raw}`))}">${esc(o.label)}</button>`).join('') : '';
      return `<div class="pend-tab" data-sid="${esc(e.sid)}"><div class="pend-h"><span class="pend-face">${faceOf(e.sid)}</span><b>${esc(name)}</b><span class="pend-ago">${esc(ageWords((now - e.since) / 1000, isZh()))}</span></div>` +
        `<div class="pend-q">${esc(q)}</div>${opts ? `<div class="pend-opts">${opts}</div>` : ''}` +
        (e.note ? `<div class="pend-note bad" role="status">${esc(e.note)}</div>` : '') +
        `<div class="pend-row"><button type="button" class="pend-link" data-act="jump-tab" data-sid="${esc(e.sid)}">${esc(t('Go to the tab', '跳到分頁'))} →</button></div></div>`;
    }).join('');
  }
  function histHtml(id) {
    const h = detail.get(id);
    if (!h || h.loading) return `<li class="none">${esc(t('Reading…', '讀取中…'))}</li>`;
    if (!h.ok) return `<li class="none">${esc(t('The history could not be read.', '讀不到歷程。'))}</li>`;
    const evs = h.events.slice(0, 4);
    if (!evs.length) return `<li class="none">${esc(t('No history yet.', '還沒有紀錄。'))}</li>`;
    return evs.map(e => `<li><time>${esc(ageWords(Date.now() / 1000 - Number(e.ts || 0), isZh()))}</time><span><b>${esc(personName(e.actor))}</b> ${esc(String(e.text || '').slice(0, 160))}</span></li>`).join('');
  }
  function cardsHtml() {
    const b = B(), c = cards();
    if (!b || !b.avail()) return '';
    if (!c.rows.length) return '';
    return `<h3>${esc(t('Decisions waiting on you', '等你決定'))}<b>${c.rows.length}</b></h3>` + c.rows.map(r => {
      const open = openId === r.id, late = Number(r.due_at) && Number(r.due_at) < c.now, soon = !late && Number(r.due_at) && Number(r.due_at) - c.now <= DAY;
      const who = askerName(r), when = ageWords(c.now - (Number(r.created_at) || Number(r.updated_at) || c.now), isZh());
      const tag = late ? `<span class="pend-tag late">${esc(t('Overdue', '逾期'))}</span>` : soon ? `<span class="pend-tag soon">${esc(t('Due soon', '快到期'))}</span>` : '';
      const head = `<button type="button" class="pend-card-h" data-act="toggle" data-id="${esc(r.id)}" aria-expanded="${open}"><span class="pend-id">#${esc(r.id)}</span>` +
        `<span class="pend-t">${esc(r.title || t('(no title)', '（沒有標題）'))}</span><span class="pend-m">${tag}${who ? `<span>${esc(who)}</span>` : ''}<span>${esc(t(`asked ${when}`, `${when}問的`))}</span></span></button>`;
      if (!open) return `<div class="pend-card" data-id="${esc(r.id)}">${head}</div>`;
      const dr = drafts[r.id] || (drafts[r.id] = { text: '', tell: true }), a = askerOf(r), dis = form.busy ? ' disabled' : '';
      const jump = a ? `<button type="button" class="pend-link" data-act="jump-card" data-id="${esc(r.id)}">${esc(t(`Go to ${a.name}'s tab`, `跳到 ${a.name} 的分頁`))} →</button>` : '';
      return `<div class="pend-card open" data-id="${esc(r.id)}">${head}<div class="pend-body-x">` +
        (r.next_step ? `<div class="pend-next"><span>${esc(t('Next step', '下一步'))}</span>${esc(r.next_step)}</div>` : '') +
        `<ol class="pend-hist" aria-label="${esc(t('History', '歷程'))}">${histHtml(r.id)}</ol>` +
        `<form class="pend-form" novalidate data-id="${esc(r.id)}"><label for="pend-ans">${esc(t('Your decision or answer (required). Sending it marks this done.', '你的決定或回答（要寫）。送出就把這件標成做好了。'))}</label>` +
        `<textarea id="pend-ans" rows="3" maxlength="500" aria-required="true" placeholder="${esc(t('Your decision or answer', '你的決定或回答'))}"${dis}>${esc(dr.text)}</textarea>` +
        (a ? `<label class="pend-tell"><input type="checkbox" data-tell${dr.tell ? ' checked' : ''}${dis}> ${esc(t(`Also tell ${a.name}`, `也告訴 ${a.name}`))}</label>`
          : (miss => miss ? `<div class="pend-tell pend-miss" role="note">${esc(t(`Can't tell ${miss.name}: ${miss.why}. The answer stays on the card.`, `沒辦法通知 ${miss.name}：${miss.why}。會記在這張卡上，他看白板才會知道。`))}</div>` : '')(askerMissOf(r))) +
        `<div class="pend-note${form.bad ? ' bad' : ''}" role="status">${esc(form.busy ? t('Sending…', '送出中…') : form.msg)}</div>` +
        `<div class="pend-row"><button type="submit" class="pend-go"${dis}>${esc(t('Send', '送出'))}</button>${jump}` +
        `<button type="button" class="pend-link" data-act="board" data-id="${esc(r.id)}">${esc(t('See it on the board', '在白板上看'))}</button></div></form></div></div>`;
    }).join('');
  }
  function peersHtml() {
    const b = B(), P = b && b.peers ? b.peers() : {}, names = (root.SFBots && root.SFBots.peerBots && root.SFBots.peerBots()) || {};
    const lines = Object.keys(P).map(pid => {
      const p = P[pid] || {}, n = Array.isArray(p.decisions) ? p.decisions.length : 0;
      if (!n || (p.state !== 'ok' && p.state !== 'error')) return '';
      const nm = String((names[pid] && names[pid].name) || pid.slice(0, 8));
      return `<button type="button" class="pend-peer" data-act="peer" data-pid="${esc(pid)}">${esc(t(`Next door: ${nm} has ${n} waiting on its person`, `隔壁：${nm} 有 ${n} 件等你`))} →</button>`;
    }).filter(Boolean);
    return lines.length ? `<h3>${esc(t('Paired computers', '配對的電腦'))}</h3>` + lines.join('') : '';
  }
  const redCount = () => { const b = B(); return b && b.avail() ? b.count() : 0; };

  function put(el, key, html, sig) {
    const k = html + (sig || '');
    if (last[key] === k) return false;
    last[key] = k;
    el.innerHTML = html;
    el.hidden = !html;
    return true;
  }
  function render() {
    if (!mounted) return;
    measure();
    const red = redCount(), nTabs = waiting.size, total = red + nTabs, dNew = SD() ? SD().count() : 0;
    const open = shown();
    panel.classList.toggle('docked', docked);
    panel.classList.toggle('open', open);
    panel.setAttribute('aria-hidden', open ? 'false' : 'true');
    if (!open) panel.setAttribute('inert', ''); else panel.removeAttribute('inert');
    scrim.hidden = !(drawer && !docked);
    doc.body.classList.toggle('sf-pend-open', open);
    // the capsule (terminal) or the strip (chat, not docked): the red count, and a mark for tabs waiting
    const capHtml = `<span class="pend-cap-l">${esc(t('Waiting on you', '待你決定'))}</span>` +
      `<b class="pend-n${red ? ' on' : ''}" data-n="${red}">${red > 99 ? '99+' : red}</b>` + (nTabs ? `<i class="pend-bang" title="${esc(t(`${nTabs} tab(s) waiting`, `${nTabs} 個分頁在等你`))}">!${nTabs > 1 ? nTabs : ''}</i>` : '') +
      (dNew ? `<i class="pend-clip" title="${esc(t(`${dNew} new deliverable(s)`, `${dNew} 件新的交付物`))}">${root.SFDelivDraw ? root.SFDelivDraw.clip(dNew, false) : ''}${dNew > 99 ? '99+' : dNew}</i>` : '');
    put(cap, 'cap', capHtml);
    cap.hidden = docked || open;
    cap.classList.toggle('strip', stripped); cap.classList.toggle('capsule', !stripped); cap.classList.toggle('has', total > 0);
    cap.setAttribute('aria-expanded', String(open));
    cap.title = total ? t(`Waiting on you: ${red} decision(s), ${nTabs} tab(s)`, `待你決定：${red} 件決定、${nTabs} 個分頁`) : t('Nothing is waiting on you', '現在沒有等你決定的事');
    cap.setAttribute('aria-label', cap.title);
    const headHtml = `${esc(t('Waiting on you', '待你決定'))}<b class="pend-n${red ? ' on' : ''}" data-n="${red}">${red > 99 ? '99+' : red}</b>`;
    if (last.head !== headHtml) { last.head = headHtml; headN.innerHTML = headHtml; }
    const showD = !!SD() && (SD().hasRail() || dNew > 0), pillHtml = `${esc(t('Deliverables', '交付物'))}<b class="${dNew ? 'on' : ''}">${dNew > 99 ? '99+' : dNew}</b>`;
    if (last.pill !== pillHtml + showD) { last.pill = pillHtml + showD; pillD.innerHTML = pillHtml; pillD.hidden = !showD; }
    panel.querySelector('.pend-x').textContent = docked ? '»' : '✕';
    panel.querySelector('.pend-x').title = docked ? t('Fold to the edge', '收到邊邊') : t('Close (Esc)', '關閉（Esc）');
    if (armed && lastTotal === 0 && total > 0 && !open && !docked) { cap.classList.remove('pulse'); void cap.offsetWidth; cap.classList.add('pulse'); }   // 0 → something: one pulse, no sound
    lastTotal = total;
    if (!open) return;                                                // nothing inside is drawn while it is folded away
    put(secTabs, 'tabs', tabsHtml());
    const ta = secCards.querySelector('textarea'), focused = !!ta && doc.activeElement === ta, sel = focused ? [ta.selectionStart, ta.selectionEnd] : null;
    if (composing && focused) { dirty = true; }
    else {
      const html = cardsHtml();
      if (put(secCards, 'cards', html, '|' + (openId || '') + '|' + form.busy + form.msg + (detail.get(openId) || {}).ok)) {
        const nt = secCards.querySelector('textarea');
        if (focused && nt) { try { nt.focus({ preventScroll: true }); nt.setSelectionRange(sel[0], sel[1]); } catch (_) { /* gone */ } }
      }
    }
    put(secPeers, 'peers', peersHtml());
    put(secDeliv, 'deliv', SD() ? SD().railHtml() : '', '|' + (SD() ? JSON.stringify(SD().state()) : ''));
    const none = !last.tabs && !secCards.innerHTML && !last.peers && !last.deliv;
    panel.classList.toggle('empty', none);
  }

  // ── actions ───────────────────────────────────────────────────────────
  function say(msg, bad) {
    statusEl.textContent = msg || ''; statusEl.classList.toggle('bad', !!bad); statusEl.hidden = !msg;
    clearTimeout(say.t); if (msg) say.t = setTimeout(() => { statusEl.hidden = true; statusEl.textContent = ''; }, 5000);
  }
  async function answer(btn) {
    const sid = btn.dataset.sid, e = waiting.get(sid), C = root.SFChat;
    if (!e || !C || e.busy) return;
    e.busy = true; e.note = ''; render();
    try {
      const r = JSON.parse(await C.call(sid, 'answer', parseInt(btn.dataset.n, 10), btn.dataset.id));
      if (!r.success) e.note = r.code === 'stale' ? t('That question was already answered or has changed; nothing was sent.', '這題已經回答過或換題了，沒有送出。') : (r.message || t('Not sent.', '沒有送出。'));
      else say(t(`Sent: ${btn.textContent}`, `已送出：${btn.textContent}`));
      if (!r.success && r.code === 'stale') { e.d = null; e.tries = 0; }
    } catch (err) { e.note = String(err); }
    e.busy = false; render();
    if (waiting.has(sid)) fetchQuestion(sid);                         // a menu that stayed (or a new one): read what it is now
  }
  async function loadDetail(id) {
    const o = O(), h = o && o.host && o.host.board, row = cards().rows.find(r => r.id === id);
    if (!h || !row || doc.hidden) return;
    const cur = detail.get(id);
    if (cur && (cur.loading || cur.upd === row.updated_at)) return;
    detail.set(id, { upd: row.updated_at, loading: true });
    render();
    let r = null; try { r = await h.detail(id); } catch (_) { r = null; }
    if (openId !== id && !detail.has(id)) return;
    detail.set(id, { upd: row.updated_at, loading: false, ok: !!(r && r.success && r.task), events: r && r.task ? (r.task.events || []).slice(0, 8) : [] });
    render();
  }
  function toggleCard(id) {
    openId = openId === id ? null : id;
    form.busy = false; form.msg = ''; form.bad = false;
    render();
    if (openId != null) {
      loadDetail(openId);
      const ta = secCards.querySelector('textarea'); if (ta) { try { ta.focus({ preventScroll: true }); } catch (_) { /* gone */ } }
    }
  }
  async function submit(id) {
    const row = cards().rows.find(r => r.id === id), dr = drafts[id], o = O();
    if (!row || !dr || form.busy) return;
    const text = dr.text.trim();
    if (!text) {                                                      // an empty answer is refused here: nothing is sent
      form.msg = t('Write your decision or answer first.', '要先寫下你的決定或回答。'); form.bad = true; render();
      const ta = secCards.querySelector('textarea'); if (ta) { try { ta.focus({ preventScroll: true }); } catch (_) { /* gone */ } }
      return;
    }
    if (!o || !o.host || !o.host.board || !o.host.board.reply) { form.msg = t('Answers cannot be sent from here.', '這裡不能回覆。'); form.bad = true; render(); return; }
    form.busy = true; form.msg = ''; form.bad = false; render();
    let r = null; try { r = await o.host.board.reply(row, text, dr.tell); } catch (err) { r = { success: false, message: String(err) }; }
    form.busy = false;
    if (!r || !r.success) { form.msg = (r && r.message) || t('Not sent. Try again in a moment.', '沒有成功，稍後再試一次。'); form.bad = true; render(); return; }
    form.msg = ''; delete drafts[id]; detail.delete(id); openId = null;
    const miss = r.told === false ? askerMissOf(row) : null;
    // which card it was, and no trailing 「。」: alone after a short word it reads as a dot with something missing after it
    say(r.told === false ? (miss ? t(`Answered #${id}, but could not tell ${miss.name}`, `已回覆 #${id}，但沒辦法通知 ${miss.name}`)
      : t(`Answered #${id}, but could not tell the one who asked`, `已回覆 #${id}，但沒能告訴問的人`))
      : t(`Answered #${id}`, `已回覆 #${id}`), r.told === false);
    render();
    const b = B(); if (b && b.poll) b.poll();                         // the card and the red count follow the store, at once
  }
  function jump(open) {
    const o = O();
    if (!o || !open()) return;
    setDrawer(false, false);
  }
  function onClick(e) {
    const el = e.target.closest('[data-act]');
    if (!el) return;
    const act = el.dataset.act, id = el.dataset.id != null ? Number(el.dataset.id) : null, o = O();
    if (act === 'answer') return answer(el);
    if (act === 'toggle') return toggleCard(id);
    if (act === 'jump-tab') { const H = host(); if (H && H.switchTab) { H.switchTab(el.dataset.sid); setDrawer(false, false); } return; }
    if (act === 'jump-card') { const row = cards().rows.find(r => r.id === id), a = row && askerOf(row); return a ? jump(() => o.host.chat.open(a.key)) : undefined; }
    if (act === 'board') return jump(() => { o.openBoard({ task: id }); return true; });
    if (act === 'peer') return jump(() => { o.openBoard({ peer: el.dataset.pid }); return true; });
  }

  // ── mount ─────────────────────────────────────────────────────────────
  function mount() {
    wrap = doc.getElementById('terminal-wrap');
    if (!wrap || mounted) return mounted;
    scrim = doc.createElement('div'); scrim.id = 'pend-scrim'; scrim.hidden = true;
    cap = doc.createElement('button'); cap.id = 'pend-cap'; cap.type = 'button'; cap.className = 'pend-cap capsule'; cap.setAttribute('data-guide', 'pending');
    panel = doc.createElement('aside'); panel.id = 'pend-panel'; panel.className = 'pend-panel';
    panel.setAttribute('role', 'complementary'); panel.setAttribute('aria-label', t('Waiting on you', '待你決定')); panel.setAttribute('data-guide', 'pending');
    panel.innerHTML = '<header class="pend-head"><span class="pend-title"></span><button type="button" class="pend-pill" id="pend-jump-d" data-guide="deliverables" hidden></button><button type="button" class="pend-x"></button></header>' +
      '<div class="pend-status" role="status" hidden></div><div class="pend-scroll"><section id="pend-tabs" hidden></section><section id="pend-cards" hidden></section><section id="pend-peers" hidden></section><section id="pend-deliv" hidden></section>' +
      '<p class="pend-none">' + esc(t('Nothing is waiting on you.', '現在沒有等你決定的事。')) + '</p></div>';
    wrap.append(scrim, cap, panel);
    headN = panel.querySelector('.pend-title'); statusEl = panel.querySelector('.pend-status');
    secTabs = panel.querySelector('#pend-tabs'); secCards = panel.querySelector('#pend-cards'); secPeers = panel.querySelector('#pend-peers'); secDeliv = panel.querySelector('#pend-deliv'); pillD = panel.querySelector('#pend-jump-d');
    cap.addEventListener('click', onCap);
    panel.querySelector('.pend-x').addEventListener('click', onFold);
    scrim.addEventListener('click', () => setDrawer(false));
    panel.addEventListener('click', onClick);
    panel.addEventListener('click', e => { if (SD() && SD().click(e)) e.stopPropagation(); });   // the deliverables section's own buttons (data-dact)
    pillD.addEventListener('click', () => { if (SD()) SD().unfold(); try { secDeliv.scrollIntoView({ block: 'start' }); } catch (_) { /* no layout */ } });
    if (SD()) SD().setOnChange(() => render());
    panel.addEventListener('input', e => {
      const f = e.target.closest('.pend-form'); if (!f) return;
      const dr = drafts[Number(f.dataset.id)]; if (!dr) return;
      if (e.target.tagName === 'TEXTAREA') dr.text = e.target.value;
    });
    panel.addEventListener('change', e => { const f = e.target.closest('.pend-form'); if (f && e.target.matches('[data-tell]') && drafts[Number(f.dataset.id)]) drafts[Number(f.dataset.id)].tell = e.target.checked; });
    panel.addEventListener('submit', e => { e.preventDefault(); submit(Number(e.target.dataset.id)); });
    panel.addEventListener('keydown', e => {                          // Cmd/Ctrl+Enter sends; a plain Enter is a new line (and never ends an IME choice)
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && e.target.tagName === 'TEXTAREA' && !e.isComposing) { e.preventDefault(); const f = e.target.closest('.pend-form'); if (f) submit(Number(f.dataset.id)); }
    });
    panel.addEventListener('compositionstart', () => { composing = true; });
    panel.addEventListener('compositionend', () => { composing = false; if (dirty) { dirty = false; setTimeout(render, 0); } });
    panel.addEventListener('focusout', () => { setTimeout(() => { if (dirty && !composing) { dirty = false; render(); } }, 0); });
    doc.addEventListener('pointerdown', e => {                        // a press anywhere outside the list and its button (the sidebar too) puts the drawer away
      if (!drawer || docked || panel.contains(e.target) || cap.contains(e.target) || (e.target.closest && e.target.closest('.modal-overlay, #sfo-overlay'))) return;
      setDrawer(false, false);
    }, true);
    root.addEventListener('keydown', e => {                           // Esc is the CLI's interrupt: taken only while the drawer is open over it
      if (e.key !== 'Escape' || e.isComposing || !drawer || docked || doc.getElementById('sfo-overlay') || doc.querySelector('.modal-overlay.open')) return;
      e.preventDefault(); e.stopPropagation(); setDrawer(false);
    }, true);
    mounted = true;
    return true;
  }
  function boot(tries) {
    const b = B(), h = host();
    if (!(b && h && api() && O() && mount())) { if (tries < 240) setTimeout(() => boot(tries + 1), 250); return; }
    measure(); render();
    (function tryAdopt(n) { adoptConfig(); if (!adopted && n < 240) setTimeout(() => tryAdopt(n + 1), 250); })(0);   // the page reads config.json a moment after it loads
    b.onData(() => { seen = true; if (!doc.hidden) render(); });
    new MutationObserver(() => render()).observe(doc.body, { attributes: true, attributeFilter: ['class'] });
    new MutationObserver(() => render()).observe(doc.documentElement, { attributes: true, attributeFilter: ['style'] });   // the interface size
    if (root.ResizeObserver) new ResizeObserver(() => render()).observe(wrap);
    root.addEventListener('resize', () => render());
    const tick = (root.SFQuiet && root.SFQuiet.slow) ? root.SFQuiet.slow(sync, 5000) : sync;   // reads the page's own state: no call to Python while nothing waits
    setInterval(tick, 1000);
    doc.addEventListener('visibilitychange', () => { if (!doc.hidden) sync(); });
    if (root.SFQuiet && root.SFQuiet.onWake) root.SFQuiet.onWake(sync);
    sync();
  }

  root.SFPending = Object.assign({ state: () => ({ kind, docked, stripped, armed, drawer, waiting: [...waiting.keys()], openId }), open: () => setDrawer(true), close: () => setDrawer(false), refresh: render }, pure);
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', () => boot(0));
  else boot(0);
})(typeof window !== 'undefined' ? window : globalThis);
