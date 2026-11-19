/* ShellBot deliverables in the main window: the paperclips and the 「交付物」 section of the right-hand 「待你決定」 list.
 * See docs/ai-skill.md §4.11, deliverables.py (the store and the security rules) and office_deliverables.py (the popup's object).
 *
 * What a bot or a tab hands the person (sfctl deliver add) sits on its owner's desk in the office. The same facts are here, outside
 * the office, from the same read as 「待你決定」: tasks_overview's `deliverables` (web/board.js hands it over; this file has no timer and
 * never reads Python except for the person's own buttons):
 *   - a paperclip with the number of new ones on the avatar of each bot / tab in the sidebar, on a tab in the top bar (grey and empty when
 *     only kept or seen ones are left on the desk; none when there is nothing). Candidates (what auto-pickup only guessed) never count.
 *   - the section: undecided ones by person with 打開 / 先放著 / 拿走, and the small 「可能是交付物」 group with 收下 / 忽略 (SFDeliv.rail*).
 * An action is the backend's `deliverables_act` (the file is opened by the app, this page never gets a path); afterwards the read is
 * asked again at once (SFBoard.poll). A file that looks like it holds secrets asks first (the backend answers `confirm`).
 * The same small drawings as the office's desks (web/deliv_draw.js) are the pictures.
 */
(function (root) {
  'use strict';
  const doc = root.document;
  const host = () => root.__sfHost || null;
  const api = () => (root.pywebview && root.pywebview.api) || null;
  const lang = () => (host() && host().lang && host().lang()) || navigator.language || '';
  const isZh = () => /^zh/i.test(lang());
  const t = (en, zh) => (isZh() ? zh : en);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const D = () => (root.SFBoard && root.SFBoard.deliv && root.SFBoard.deliv()) || null;
  const Bots = () => root.SFBots || null;

  // ── who a sidebar row or a tab is, in the keys the backend counts by ──
  const botOn = () => { const B = Bots(); return !!(B && B.enabled && B.enabled()); };
  function keyOfSid(sid) {
    const B = Bots();
    let role = '';
    try { role = botOn() && B.botOf ? B.botOf(sid) || '' : ''; } catch (_) { role = ''; }
    return role ? 'b:' + role : 't:' + sid;
  }
  const infoOf = key => { const d = D(); return (d && d.by_owner && d.by_owner[key]) || null; };
  const newOf = key => { const i = infoOf(key); return i ? Number(i.new) || 0 : 0; };
  const total = () => { const d = D(); return d ? Number(d.new) || 0 : 0; };

  // ── the paperclip ──
  function clipHtml(i, small) {
    const n = Number(i.new) || 0;
    const tip = n ? t(`${n} new deliverable(s) on this desk`, `桌上有 ${n} 件新的交付物`) : t(`${i.count} deliverable(s) kept on this desk`, `桌上還放著 ${i.count} 件交付物`);
    return `<span class="sfd-clip${n ? ' on' : ' grey'}${small ? ' sm' : ''}" data-sig="${n}|${i.count}|${small ? 1 : 0}" title="${esc(tip)}" role="img" aria-label="${esc(tip)}">` +
      root.SFDelivDraw.clip(n, !n) + (n ? `<b>${n > 9 ? '9+' : n}</b>` : '') + '</span>';
  }
  function put(host_, i, small) {
    const cur = host_.querySelector(':scope > .sfd-clip');
    if (!i || !(Number(i.count) > 0)) { if (cur) cur.remove(); return; }
    const sig = `${Number(i.new) || 0}|${i.count}|${small ? 1 : 0}`;
    if (cur && cur.dataset.sig === sig && cur.title) return;                   // nothing changed: no DOM write (the observer below watches this tree)
    if (cur) cur.remove();
    host_.insertAdjacentHTML('beforeend', clipHtml(i, small));
  }
  function apply() {
    const S = (host() && host().sessions && host().sessions()) || {};
    doc.querySelectorAll('#sidebar-sessions .sb-item[data-section="local"][data-sid] .busy-dot').forEach(dot => {
      const sid = dot.closest('.sb-item').dataset.sid;
      if (S[sid] && S[sid].isRemote) return;
      put(dot, infoOf(keyOfSid(sid)), false);
    });
    doc.querySelectorAll('.sfb-sleeper[data-sfb-role] .sfb-avatar').forEach(av => put(av, infoOf('b:' + av.closest('.sfb-sleeper').dataset.sfbRole), false));
    const B = Bots(), prim = B && B.state && (B.state() || {}).primary;
    const card = doc.querySelector('#sfb-primary .sfb-card .sfb-avatar');
    if (card && prim) put(card, infoOf('b:' + prim), false);
    doc.querySelectorAll('#tabs .tab[data-sid]').forEach(tab => {
      const sid = tab.dataset.sid, i = infoOf(keyOfSid(sid));
      const cur = tab.querySelector(':scope > .sfd-clip');
      if (!i || !(Number(i.count) > 0)) { if (cur) cur.remove(); return; }
      const sig = `${Number(i.new) || 0}|${i.count}|1`;
      if (cur && cur.dataset.sig === sig) return;
      if (cur) cur.remove();
      const close = tab.querySelector('.close-tab');
      const wrap = doc.createElement('span');
      wrap.innerHTML = clipHtml(i, true);
      tab.insertBefore(wrap.firstChild, close || null);
    });
  }
  let raf = 0;
  const later = () => { if (raf) return; raf = root.requestAnimationFrame ? root.requestAnimationFrame(() => { raf = 0; try { apply(); } catch (_) { /* a sidebar without it is still a sidebar */ } }) : setTimeout(() => { raf = 0; apply(); }, 16); };

  // ── the 「交付物」 section of the right-hand list ──
  const ui = { open: true, openId: null, busy: false, msg: '', bad: false, confirm: null, candOpen: true };
  try { const p = JSON.parse(localStorage.getItem('sf.deliv.rail.v1') || 'null'); if (p && typeof p.open === 'boolean') ui.open = p.open; } catch (_) { /* no storage */ }
  const keep = () => { try { localStorage.setItem('sf.deliv.rail.v1', JSON.stringify({ open: ui.open })); } catch (_) { /* no storage */ } };
  const kb = n => (n < 1024 ? `${n} B` : n < 1048576 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1048576).toFixed(1)} MB`);
  function when(ts) {
    const d = new Date(ts * 1000), now = new Date(), days = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()) - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
    const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    return days <= 0 ? hm : days === 1 ? t(`yesterday ${hm}`, `昨天 ${hm}`) : `${d.getMonth() + 1}/${d.getDate()}`;
  }
  const pic = type => { const u = root.SFDelivDraw ? root.SFDelivDraw.thumb(type, 64) : ''; return u ? `<img src="${esc(u)}" alt="">` : ''; };
  const railItems = () => { const d = D(); return d && Array.isArray(d.rail) ? d.rail : []; };
  const railCands = () => { const d = D(); return d && Array.isArray(d.candidates) ? d.candidates : []; };
  const railCount = () => total();
  const hasRail = () => railItems().length > 0 || railCands().length > 0;

  function rowHtml(r) {
    const open = ui.openId === r.id, cand = r.state === 'candidate';
    const stat = cand ? '' : (r.fresh ? `<span class="sfd-st new">${esc(t('New', '新'))}</span>` : `<span class="sfd-st">${esc(t('Seen', '看過'))}</span>`);
    const sub = [r.type === 'url' ? '' : kb(r.size), when(r.created_at)].filter(Boolean).join(' · ');
    let body = '';
    if (open && !cand) {
      body = (r.note ? `<div class="sfd-note">${esc(r.note)}</div>` : '') +
        (ui.confirm && ui.confirm.id === r.id ? `<div class="sfd-confirm" role="alert">${esc(ui.confirm.text)} <button type="button" class="sfd-b" data-dact="confirm" data-id="${r.id}">${esc(ui.confirm.label)}</button><button type="button" class="sfd-b" data-dact="cancel">${esc(t('Cancel', '取消'))}</button></div>` : '') +
        `<div class="sfd-acts"><button type="button" class="sfd-b pri" data-dact="open" data-id="${r.id}"${ui.busy ? ' disabled' : ''}>${esc(t('Open', '打開'))}</button>` +
        `<button type="button" class="sfd-b" data-dact="keep" data-id="${r.id}"${ui.busy ? ' disabled' : ''}>${esc(t('Keep', '先放著'))}</button>` +
        `<button type="button" class="sfd-b" data-dact="take" data-id="${r.id}"${ui.busy ? ' disabled' : ''}>${esc(t('Take', '拿走'))}</button></div>`;
    }
    if (cand) {
      body = `<div class="sfd-acts"><button type="button" class="sfd-b pri" data-dact="accept" data-id="${r.id}"${ui.busy ? ' disabled' : ''}>${esc(t('Accept', '收下'))}</button>` +
        `<button type="button" class="sfd-b" data-dact="dismiss" data-id="${r.id}"${ui.busy ? ' disabled' : ''}>${esc(t('Ignore', '忽略'))}</button></div>`;
    }
    const flag = r.flag === 'secret' ? `<span class="sfd-st warn" title="${esc(t('Looks like it holds a key: not previewed, opening asks first', '看起來含金鑰：不預覽，打開前會再問'))}">${esc(t('key?', '金鑰?'))}</span>` : '';
    return `<div class="sfd-row${open ? ' open' : ''}${cand ? ' cand' : ''}" data-id="${r.id}">` +
      `<button type="button" class="sfd-h" data-dact="toggle" data-id="${r.id}" aria-expanded="${open}"${cand ? ' tabindex="-1"' : ''}><span class="sfd-pic" aria-hidden="true">${pic(r.type)}</span>` +
      `<span class="sfd-t"><b>${esc(r.title)}</b><small>${esc(sub)}</small></span>${flag}${stat}</button>${body}</div>`;
  }
  function railHtml() {
    const items = railItems(), cands = railCands();
    if (!items.length && !cands.length) return '';
    const n = railCount();
    const by = new Map();
    for (const r of items) { if (!by.has(r.owner)) by.set(r.owner, { label: r.label, rows: [] }); by.get(r.owner).rows.push(r); }
    const d = D(), name = key => { const i = (d.by_owner || {})[key]; return (i && i.label) || ''; };
    let html = `<h3 class="sfd-h3"><button type="button" class="sfd-fold" data-dact="fold" aria-expanded="${ui.open}"><i aria-hidden="true">${ui.open ? '▾' : '▸'}</i>${esc(t('Deliverables', '交付物'))}</button>` +
      `<b class="sfd-n${n ? ' on' : ''}">${n ? esc(t(`${n} new`, `${n} 新`)) : items.length}</b></h3>`;
    if (!ui.open) return html;
    for (const [key, g] of by) {
      const nn = g.rows.filter(r => r.fresh).length;
      html += `<div class="sfd-grp"><div class="sfd-gh"><span>${esc(name(key) || g.label || key)}</span>${key.startsWith('x:') ? `<em>${esc(t('closed tab', '已關閉的分頁'))}</em>` : key.startsWith('t:') ? `<em>${esc(t('tab', '分頁'))}</em>` : ''}${nn ? `<b>${esc(t(`${nn} new`, `${nn} 新`))}</b>` : ''}</div>${g.rows.map(rowHtml).join('')}</div>`;
    }
    const more = d.rail_more | 0;
    if (more) html += `<div class="sfd-more">${esc(t(`${more} more: open 「Deliverables」 in the office`, `還有 ${more} 件：到辦公室的「交付物」看`))}</div>`;
    if (cands.length) {
      html += `<div class="sfd-grp cand"><div class="sfd-gh"><button type="button" class="sfd-fold" data-dact="candfold" aria-expanded="${ui.candOpen}"><i aria-hidden="true">${ui.candOpen ? '▾' : '▸'}</i>${esc(t('Maybe deliverables', '可能是交付物'))}</button><b class="plain">${d.candidates_total || cands.length}</b></div>` +
        (ui.candOpen ? `<p class="sfd-hint">${esc(t('Guessed from replies: not on any desk and not counted until you accept.', '從回覆裡猜的：收下才上桌，沒收下不會算進數字。'))}</p>` + cands.map(rowHtml).join('') : '') + '</div>';
    }
    if (ui.msg) html += `<div class="sfd-msg${ui.bad ? ' bad' : ''}" role="status">${esc(ui.msg)}</div>`;
    return html;
  }

  // ── actions ──
  let onChange = () => {};
  const poll = () => { const b = root.SFBoard; if (b && b.poll) b.poll(); };
  async function act(id, action, args) {
    const a = api();
    if (!a || !a.deliverables_act || ui.busy) return;
    ui.busy = true; ui.msg = ''; onChange();
    let r = null;
    try { r = JSON.parse(await a.deliverables_act(id, action, JSON.stringify(args || {}), isZh() ? 'zh' : 'en')); } catch (_) { r = null; }
    ui.busy = false;
    if (!r || r.success === false || r.ok === false) { ui.msg = (r && r.message) || t('That did not work.', '這個沒成功。'); ui.bad = true; ui.confirm = null; onChange(); poll(); return; }
    if (r.confirm) { ui.confirm = { id, text: r.confirm.text, label: r.confirm.label || t('OK', '好'), action, args: r.confirm.args || {} }; onChange(); return; }
    ui.confirm = null; ui.bad = false; ui.msg = action === 'open' ? '' : (r.message || '');
    if (action !== 'open') ui.openId = null;
    onChange(); poll();
    if (ui.msg) setTimeout(() => { ui.msg = ''; onChange(); }, 4000);
  }
  // true when the click was this section's
  function click(ev) {
    const el = ev.target.closest && ev.target.closest('[data-dact]');
    if (!el) return false;
    const a = el.dataset.dact, id = el.dataset.id != null ? Number(el.dataset.id) : null;
    if (a === 'fold') { ui.open = !ui.open; keep(); onChange(); return true; }
    if (a === 'candfold') { ui.candOpen = !ui.candOpen; onChange(); return true; }
    if (a === 'toggle') { if (railCands().some(r => r.id === id)) return true; ui.openId = ui.openId === id ? null : id; ui.confirm = null; onChange(); return true; }
    if (a === 'cancel') { ui.confirm = null; onChange(); return true; }
    if (a === 'confirm') { const c = ui.confirm; if (c) act(c.id, c.action, c.args); return true; }
    if (['open', 'keep', 'take', 'accept', 'dismiss'].indexOf(a) >= 0) { act(id, a); return true; }
    return false;
  }

  // ── boot ──
  function boot(tries) {
    const b = root.SFBoard;
    if (!(b && b.onData && root.SFDelivDraw)) { if (tries < 240) setTimeout(() => boot(tries + 1), 250); return; }
    b.onData(later);
    const side = doc.getElementById('sidebar-sessions'), tabs = doc.getElementById('tabs');
    const obs = new MutationObserver(later);
    for (const el of [side && side.parentNode, tabs]) if (el) obs.observe(el, { childList: true, subtree: true });
    later();
  }
  root.SFDeliv = { total, newOf, infoOf, keyOfSid, railHtml, hasRail, click, count: railCount, apply,
                   unfold: () => { if (!ui.open) { ui.open = true; keep(); onChange(); } },
                   setOnChange: fn => { onChange = fn || (() => {}); }, isOpen: () => ui.open, state: () => ({ open: ui.open, openId: ui.openId, msg: ui.msg }) };
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', () => boot(0)); else boot(0);
})(typeof window !== 'undefined' ? window : globalThis);
