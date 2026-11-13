/* The model chip on an AI tab (docs/tab-model.md; the Python side is api_tab_model.py).
 *
 * A small label at the top of the tab, in the chat view's header, the terminal's header in the chat
 * style, and the tab bar in the native CLI style. It says which model the tab runs ("unknown" when
 * ShellBot cannot read it: it does not guess). Clicking it lists the models of that CLI (the list
 * is agent_model's table, from tab_model_info) and its thinking levels; picking one makes ShellBot
 * type the CLI's own switch into the tab and read back what the CLI said.
 *
 *   a tab in a turn         the switch waits for it: the chip says so, with a Cancel
 *   text in the input box   asked first (wait for it to be sent / clear it); never written over
 *   a box ShellBot cannot read (Codex, Windows)   asked first
 *   a failure               shown with its reason, never as a success
 *   a CLI that cannot switch inside a tab   says it has to be started again; for a remembered bot
 *                           the existing "save and switch now" (bots_switch, now) is offered
 *   a remembered bot        after a switch: "keep it as this bot's model?", not saved unless yes
 *
 * chat.js draws chipHtml(sid) in its headers and hands the click to open(); the tab bar's chip is
 * drawn here. index.html is size-budgeted, so all of it lives in this file.
 */
(function (root) {
  'use strict';
  const doc = root && root.document;
  if (!doc) return;
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const host = () => root.__sfHost || null;
  const api = () => (root.pywebview && root.pywebview.api) || null;
  const zh = () => /^zh/i.test(String((host() && host().lang && host().lang()) || ''));
  const T = (en, cn) => (zh() ? cn : en);
  const REASON = {
    working: ['it is in the middle of a turn', '它正在跑'], decision: ['it is waiting on you', '它在等你決定'],
    draft: ['there is text in its input box', '輸入框裡有還沒送出的字'], telegram: ['a Telegram message is in flight', '它正在處理 Telegram 的訊息'],
    model: ['it is switching', '它正在換'], queued: ['messages ahead of it are waiting', '前面還有訊息在排'],
  };

  const info = {};        // sid -> tab_model_info
  const sigs = {};        // sid -> what the page last drew from it
  const ui = {};          // sid -> { open, model, custom, effort, card, watch, asked, note }
  let pop = null;
  const U = sid => ui[sid] || (ui[sid] = { open: false, model: undefined, custom: '', effort: '', card: null, watch: '', asked: {}, note: '' });

  // ── the chip ───────────────────────────────────────────────────────────
  function chipState(sid) {
    const i = info[sid];
    if (!i || !i.ai) return null;
    const j = i.job;
    if (j && j.state === 'waiting') return { cls: 'wait', text: T('Switching when it is done', '等它做完再換') };
    if (j && j.state === 'switching') return { cls: 'busy', text: T('Switching…', '換模型中…') };
    if (j && j.state === 'failed' && j.code !== 'cancelled') return { cls: 'bad', text: '⚠ ' + T('Not switched', '沒換成') };
    if (j && j.state === 'unconfirmed') return { cls: 'warn', text: T('Sent, not confirmed', '已送出，未確認') };
    const c = i.current;
    if (!c) return { cls: 'unknown', text: T('Model: unknown', '模型：未知') };
    return { cls: '', text: c.name + (c.effort ? ' · ' + c.effort : '') };
  }
  function chipHtml(sid) {
    const s = (host() && host().sessions && host().sessions()[sid]) || null;
    if (!s || s.isRemote) return '';
    const st = chipState(sid);
    if (!st) return '';
    const i = info[sid];
    const title = T("Change this tab's model", '換這個分頁的模型') + (i.current ? ` (${i.cli_name})` : '');
    return `<button class="tm-chip${st.cls ? ' tm-' + st.cls : ''}" data-act="model" title="${esc(title)}"><span>${esc(st.text)}</span><i>▾</i></button>`;
  }

  // ── the pop-up ─────────────────────────────────────────────────────────
  function jobHtml(sid) {
    const i = info[sid], j = i.job, u = U(sid);
    if (!j) return '';
    if (j.state === 'waiting') {
      const r = REASON[j.reason];
      return `<div class="tm-job tm-wait">${esc(T('Waiting for it to finish', '等它做完再換'))}${r ? esc(' (' + T(r[0], r[1]) + ')') : ''}` +
        `<button data-tm="cancel">${esc(T('Cancel', '取消'))}</button></div>`;
    }
    if (j.state === 'switching') return `<div class="tm-job tm-busy">${esc(T('Switching…', '換模型中…'))}</div>`;
    const cls = j.state === 'ok' ? 'ok' : (j.state === 'unconfirmed' ? 'warn' : 'bad');
    let h = `<div class="tm-job tm-${cls}">${esc(j.message || '')}<button data-tm="dismiss">${esc(T('OK', '知道了'))}</button></div>`;
    if (j.state === 'ok' && i.bot && i.bot.can_save && j.model != null && !u.asked[j.id]) {
      h += `<div class="tm-ask">${esc(T(`Also keep this as ${i.bot.label}'s model?`, `也存成「${i.bot.label}」的預設模型？`))}` +
        `<span><button data-tm="bot-yes">${esc(T('Keep it', '存起來'))}</button><button class="tm-quiet" data-tm="bot-no">${esc(T('No', '不用'))}</button></span></div>` +
        `<div class="tm-hint">${esc(T('Only the next time it wakes up; this tab already runs it.', '下次叫醒才用；這個分頁現在已經是了。'))}</div>`;
    }
    return h;
  }
  function cardHtml(sid) {
    const c = U(sid).card;
    if (!c) return '';
    if (c.kind === 'draft') {
      return `<div class="tm-card"><div>${esc(c.message)}</div><q>${esc(c.text)}</q>` +
        `<div class="tm-row"><button data-tm="draft-wait">${esc(T('Switch after I send it', '等我送出再換'))}</button>` +
        `<button data-tm="draft-clear">${esc(T('Clear it and switch', '清掉再換'))}</button><button class="tm-quiet" data-tm="card-no">${esc(T('Cancel', '取消'))}</button></div></div>`;
    }
    if (c.kind === 'unreadable') {
      return `<div class="tm-card"><div>${esc(c.message)}</div><div class="tm-row"><button data-tm="go-anyway">${esc(T('Switch anyway', '直接換'))}</button>` +
        `<button class="tm-quiet" data-tm="card-no">${esc(T('Cancel', '取消'))}</button></div></div>`;
    }
    return `<div class="tm-card tm-bad"><div>${esc(c.message)}</div><div class="tm-row"><button class="tm-quiet" data-tm="card-no">${esc(T('OK', '知道了'))}</button></div></div>`;
  }
  function modelButtons(i, u, attr) {
    const cur = i.current_key;
    return i.models.map(m => `<button class="tm-opt${u.model === m.key && !u.custom ? ' on' : ''}${cur === m.key && m.key ? ' cur' : ''}" data-tm="${attr}" data-key="${esc(m.key)}">` +
      `${esc(m.label)}${cur === m.key && m.key ? ' ✓' : ''}</button>`).join('');
  }
  function pickerHtml(sid) {
    const i = info[sid], u = U(sid);
    let h = '';
    if (i.models.length) h += `<div class="tm-opts">${modelButtons(i, u, 'model')}</div>`;
    if (i.custom) {
      h += `<input class="tm-custom" data-tm="custom" value="${esc(u.custom)}" placeholder="${esc(i.models.length
        ? T('Other: type a model name', '其他：打模型名稱') : T('Type the model name (part of it is fine)', '打模型名稱（打一部分就好）'))}">`;
    }
    if (i.efforts.length) {
      h += `<div class="tm-lab">${esc(T('Thinking', '思考強度'))}</div><div class="tm-seg">` +
        i.efforts.map(e => `<button class="${u.effort === e.key ? 'on' : ''}${i.current && i.current.effort === e.key ? ' cur' : ''}" data-tm="effort" data-key="${esc(e.key)}">${esc(e.label)}</button>`).join('') + '</div>';
    }
    const can = u.model !== undefined || u.custom.trim() || u.effort;
    if (i.persists) h += `<div class="tm-hint">${esc(T(`${i.cli_name} keeps what you pick as its own default for new tabs.`, `${i.cli_name} 自己會把你選的存成新分頁的預設。`))}</div>`;
    if (i.unverified) h += `<div class="tm-hint">${esc(T('Not checked on a live tab yet: ShellBot reads the result and says if it could not confirm.', '這家還沒在真的分頁上驗過：換完會讀結果，讀不到會照實說。'))}</div>`;
    return h + `<div class="tm-row"><button class="tm-go" data-tm="go"${can ? '' : ' disabled'}>${esc(T('Switch', '換'))}</button></div>`;
  }
  function restartHtml(sid) {
    const i = info[sid], u = U(sid);
    let h = `<div class="tm-note">${esc(T('This one has to be restarted to change.', '這家要重開才能換。'))}</div>`;
    if (i.restart_note) h += `<div class="tm-hint">${esc(i.restart_note)}</div>`;
    if (i.bot && i.models.length) {
      h += `<div class="tm-opts">${modelButtons(i, u, 'model')}</div><div class="tm-row"><button class="tm-go" data-tm="restart"${u.model === undefined ? ' disabled' : ''}>` +
        `${esc(T('Save and switch now (restarts the tab)', '儲存並現在就換（重開分頁）'))}</button></div>`;
    }
    return h;
  }
  function popHtml(sid) {
    const i = info[sid], u = U(sid);
    return `<div class="tm-h"><b>${esc(i.cli_name)}</b><span>${esc(T('Model', '模型'))}</span><button class="tm-x" data-tm="close" title="${esc(T('Close', '關閉'))}">×</button></div>` +
      jobHtml(sid) + cardHtml(sid) + (u.note ? `<div class="tm-hint">${esc(u.note)}</div>` : '') + (i.in_session ? pickerHtml(sid) : restartHtml(sid));
  }
  function anchorRect() {
    for (const sel of ['#chat-head .tm-chip', '#term-head .tm-chip', '#tm-slot .tm-chip']) {
      const el = doc.querySelector(sel);
      if (el && el.getClientRects().length) return el.getBoundingClientRect();
    }
    return { left: 12, bottom: 48, right: 120 };
  }
  function renderPop(sid) {
    if (!pop) {
      pop = doc.createElement('div');
      pop.id = 'tm-pop';
      pop.addEventListener('click', onPopClick);
      pop.addEventListener('input', e => { const el = e.target.closest('[data-tm="custom"]'); if (el) { U(activeSid()).custom = el.value; syncGo(); } });
      pop.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.matches('[data-tm="custom"]')) { e.preventDefault(); go(activeSid()); } if (e.key === 'Escape') close(); });
      doc.body.appendChild(pop);
    }
    const typing = doc.activeElement && pop.contains(doc.activeElement) && doc.activeElement.matches('[data-tm="custom"]');
    const keep = typing ? [doc.activeElement.selectionStart, doc.activeElement.selectionEnd] : null;
    pop.innerHTML = popHtml(sid);
    const r = anchorRect();
    pop.style.left = Math.max(8, Math.min(r.left, (root.innerWidth || 800) - 330)) + 'px';
    pop.style.top = (r.bottom + 6) + 'px';
    pop.hidden = false;
    if (typing) { const el = pop.querySelector('[data-tm="custom"]'); el.focus(); try { el.setSelectionRange(keep[0], keep[1]); } catch (_) {} }
  }
  function syncGo() {
    const b = pop && pop.querySelector('[data-tm="go"]'), u = U(activeSid());
    if (b) b.disabled = !(u.model !== undefined || u.custom.trim() || u.effort);
  }
  const activeSid = () => (host() && host().active && host().active()) || '';
  function open(sid, anchor) {
    const u = U(sid);
    if (u.open) return close();
    if (!info[sid]) return;
    u.open = true;
    renderPop(sid);
  }
  function close() {
    const sid = activeSid();
    if (ui[sid]) ui[sid].open = false;
    if (pop) pop.hidden = true;
  }
  async function call(name, ...args) {
    const a = api();
    if (!a || !a[name]) return {};
    try { return JSON.parse(await a[name](...args)); } catch (_) { return {}; }
  }
  async function go(sid, extra) {
    const i = info[sid], u = U(sid);
    const custom = u.custom.trim();
    const body = Object.assign({ model: custom || (u.model !== undefined ? u.model : null), effort: u.effort || null, custom: !!custom }, extra || {});
    const r = await call('tab_model_set', sid, JSON.stringify(body));
    if (r.success) {
      u.card = null; u.model = undefined; u.custom = ''; u.effort = ''; u.note = ''; u.watch = Date.now();
      u.lastBody = body;
    } else if (r.code === 'draft') { u.card = { kind: 'draft', text: r.draft || '', message: r.message }; u.lastBody = body; }
    else if (r.code === 'unreadable') { u.card = { kind: 'unreadable', message: r.message }; u.lastBody = body; }
    else u.card = { kind: 'error', message: r.message || T('Could not switch', '沒辦法換') };
    await refresh(sid, true);
    renderPop(sid);
  }
  async function onPopClick(e) {
    const el = e.target.closest('[data-tm]');
    if (!el) return;
    const sid = activeSid(), u = U(sid), act = el.dataset.tm;
    if (act === 'close') return close();
    if (act === 'model') { u.model = el.dataset.key; u.custom = ''; return renderPop(sid); }
    if (act === 'effort') { u.effort = u.effort === el.dataset.key ? '' : el.dataset.key; return renderPop(sid); }
    if (act === 'go') return go(sid);
    if (act === 'draft-wait') return go(sid, Object.assign({}, u.lastBody, { draft: 'wait' }));
    if (act === 'draft-clear') return go(sid, Object.assign({}, u.lastBody, { draft: 'clear' }));
    if (act === 'go-anyway') return go(sid, Object.assign({}, u.lastBody, { unreadable_ok: true }));
    if (act === 'card-no') { u.card = null; return renderPop(sid); }
    if (act === 'cancel') { await call('tab_model_cancel', sid); await refresh(sid, true); return renderPop(sid); }
    if (act === 'dismiss') { await call('tab_model_dismiss', sid); await refresh(sid, true); return renderPop(sid); }
    const j = (info[sid] || {}).job;
    if (act === 'bot-no' && j) { u.asked[j.id] = true; return renderPop(sid); }
    if (act === 'bot-yes' && j) {
      u.asked[j.id] = true;
      const r = await call('tab_model_save_bot', sid, j.model || '');
      u.note = r.success ? T("Kept: it will wake up on this model next time.", '存好了：下次叫醒會用這個模型。') : (r.message || T('Could not keep it', '沒存成'));
      return renderPop(sid);
    }
    if (act === 'restart') {
      const i = info[sid];
      const label = (i.models.find(m => m.key === u.model) || {}).label || u.model;
      const ok = await SFDialog.confirm(T(`Save ${label} as ${i.bot.label}'s model and restart the tab now? It writes its memory first.`,
        `把「${i.bot.label}」的模型存成 ${label}，並現在重開分頁？它會先寫好記憶。`), { title: T('Switch now', '現在就換') });
      if (!ok) return;
      const r = await call('bots_switch', i.bot.role, '', u.model, 'now');
      u.note = r.success ? T('Switching: the tab closes and wakes up again.', '正在換：分頁會關掉再叫醒。') : (r.message || T('Could not switch', '沒辦法換'));
      u.model = undefined;
      return renderPop(sid);
    }
  }

  // ── what the page knows ────────────────────────────────────────────────
  async function refresh(sid, force) {
    const r = await call('tab_model_info', sid);
    if (!r || !r.success) { if (info[sid]) { delete info[sid]; changed(sid); } return; }
    const prev = info[sid];
    info[sid] = r;
    const sig = JSON.stringify([r.ai, r.current, r.job && [r.job.id, r.job.state, r.job.reason], r.in_session, r.bot && r.bot.role, r.models.length]);
    const u = U(sid);
    // a switch this page started has ended: show how it went where the person will see it
    const j = r.job, was = prev && prev.job;
    if (u.watch && Date.now() - u.watch < 120000 && j && ['ok', 'failed', 'unconfirmed'].includes(j.state) && (!was || was.id !== j.id || was.state !== j.state) && j.code !== 'cancelled') {
      const ask = j.state === 'ok' && r.bot && r.bot.can_save && j.model != null;
      if (j.state !== 'ok' || ask) { u.open = true; }
      u.watch = '';
    }
    if (sig !== sigs[sid] || force) { sigs[sid] = sig; changed(sid); }
  }
  function changed(sid) {
    paintBar();
    if (root.SFChat) { try { root.SFChat.apply(false); root.SFChat.relabel(); } catch (_) {} }
    if (sid === activeSid() && U(sid).open && info[sid]) renderPop(sid);
  }
  function paintBar() {
    const bar = doc.getElementById('tab-bar');
    if (!bar) return;
    let slot = doc.getElementById('tm-slot');
    if (!slot) {
      slot = doc.createElement('span');
      slot.id = 'tm-slot';
      const sw = doc.getElementById('chat-switch');
      const spacer = bar.querySelector('.tab-spacer');
      bar.insertBefore(slot, sw && sw.parentNode === bar ? sw : (spacer ? spacer.nextSibling : null));
      slot.addEventListener('click', e => { if (e.target.closest('.tm-chip')) { e.preventDefault(); open(activeSid()); } });
    }
    const html = activeSid() ? chipHtml(activeSid()) : '';
    if (slot.dataset.key !== html) { slot.innerHTML = html; slot.dataset.key = html; }
  }
  function tick() {
    if (doc.hidden) return;
    if (root.SFQuiet && root.SFQuiet.still()) return;     // nobody looking (web/quiet.js): asked again on wake
    const sid = activeSid();
    if (sid) refresh(sid, false);
    else paintBar();
  }
  doc.addEventListener('mousedown', e => {
    if (pop && !pop.hidden && !pop.contains(e.target) && !e.target.closest('.tm-chip')) close();
  });

  root.SFTabModel = { chipHtml, open, close, refresh, info: sid => info[sid] || null };
  const boot = () => { setInterval(tick, 1000); tick(); if (root.SFQuiet) root.SFQuiet.onWake(tick); };
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', boot); else boot();
})(typeof window !== 'undefined' ? window : null);
