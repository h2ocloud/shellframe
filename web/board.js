/* ShellBot task board button — see docs/ai-skill.md §4.10 and docs/chat-shell.md.
 *
 * Agents put what they wait on the person for on the task board (`sfctl task add --decision`, docs/ai-skill.md §4.9). This
 * file makes the board reachable from anywhere, in the terminal view and the chat view alike and whether or not the office
 * (settings.experimental_office) is on:
 *
 *   - a button in the entry row at the top of the sidebar (#btn-taskboard, web/entry.js, beside Bots and the office) with a count of the decisions
 *     waiting on the person (tasks_overview's `decisions`; no badge at 0). The count is asked for about every 20 s while the
 *     window is visible and the board is closed; while the board is open web/office.js's own refresh feeds it (SFBoard.note),
 *     which includes the moment after a card was answered. The button is hidden only when the tasks store is unavailable.
 *   - the same read feeds web/pending.js (the 「待你決定」 list on the right): SFBoard.onData(fn) hands it the decisions themselves and, when
 *     computers are paired, each one's decisions (link_peer_tasks, answered from the host's cache) in the same tick. It has no timer of its own.
 *   - a shortcut (Cmd+Shift+B on macOS, Ctrl+Shift+B elsewhere) that opens and closes the board.
 *   - opening is SFOffice.openBoard(): the board sheet over the window without the office floor (office.js, office.html).
 *     With the office already open the shortcut and the button toggle the lobby's whiteboard instead.
 */
(function (root) {
  'use strict';
  const host = () => root.__sfHost || null;
  const api = () => (root.pywebview && root.pywebview.api) || null;
  const isZh = () => /^zh/i.test((host() && host().lang && host().lang()) || navigator.language || '');
  const t = (en, zh) => (isZh() ? zh : en);
  const isMac = () => /Mac|iPhone|iPad/i.test(navigator.platform || navigator.userAgent || '');

  // The shortcut: Shift with Cmd (macOS) or Ctrl (Windows, Linux). A plain Ctrl+letter would take a shell's control key.
  function isKey(e) {
    if (!e || e.altKey || !e.shiftKey || e.isComposing) return false;
    const mod = isMac() ? (e.metaKey && !e.ctrlKey) : (e.ctrlKey && !e.metaKey);
    return mod && (String(e.key || '').toLowerCase() === 'b' || e.code === 'KeyB');
  }
  const keyName = () => (isMac() ? '⌘⇧B' : 'Ctrl+Shift+B');

  let count = 0, avail = true, btn = null, timer = 0, list = [], peers = {}, deliv = null;
  const subs = [];
  const tell = () => subs.forEach(fn => { try { fn(); } catch (_) {} });

  function paint() {
    if (!btn) return;
    btn.hidden = !avail;
    const name = t('Board', '看板'), tip = count > 0
      ? t(`Board: ${count} waiting on you (${keyName()})`, `看板：${count} 件等你決定（${keyName()}）`)
      : t(`Board (${keyName()})`, `看板（${keyName()}）`);
    btn.title = tip;
    btn.setAttribute('aria-label', tip);
    btn.querySelector('.sfe-label').textContent = name;
    const b = btn.querySelector('.sfb-badge');
    b.hidden = count <= 0;
    b.textContent = count > 99 ? '99+' : String(count);
  }
  // The number of decisions waiting on the person (tasks_overview's own list), from whoever just asked the store.
  function note(n, decisions) {
    const v = Math.max(0, Number(n) || 0);
    avail = true;
    if (Array.isArray(decisions)) list = decisions.slice();
    if (v !== count || !btn || btn.hidden) { count = v; paint(); }
    tell();
  }
  // The deliverables' counts and list (tasks_overview's `deliverables`: the same read), from whoever just asked: web/deliverables.js and
  // web/pending.js draw them (the sidebar's paperclips, the 「交付物」 section); null when the store cannot be used.
  function noteDeliv(d) {
    deliv = d && typeof d === 'object' ? d : null;
    tell();
  }
  // The paired computers' task lists (link_peer_tasks's `peers`), from whoever just asked.
  function notePeers(p) {
    peers = p && typeof p === 'object' ? p : {};
    tell();
  }
  const peersPaired = () => { const B = root.SFBots; return !!(B && B.peerBots && Object.keys(B.peerBots() || {}).length); };
  async function poll() {
    const a = api();
    if (!a || !a.tasks_overview) { avail = false; paint(); return; }
    let r = null;
    try { r = JSON.parse(await a.tasks_overview()); } catch (_) { return; }
    if (r && r.success === false) { avail = false; list = []; deliv = null; paint(); tell(); }   // the store cannot be opened: nothing to show
    else if (r && r.success) { deliv = r.deliverables || null; note((r.decisions || []).length, r.decisions || []); }
    if (a.link_peer_tasks && peersPaired()) {                        // the host's cache: it asks the other computers itself, no more often than it needs
      let q = null;
      try { q = JSON.parse(await a.link_peer_tasks()); } catch (_) { return; }
      if (q && q.peers && typeof q.peers === 'object') notePeers(q.peers);
    }
  }
  const O = () => root.SFOffice || null;
  function toggle() {
    const o = O();
    if (!o) return;
    if (!o.isOpen()) o.openBoard();
    else if (o.isBoard()) o.close();
    else o.toggleBoard();
  }
  // The button is web/entry.js's: the third in the row at the top of the sidebar (built before this file runs).
  function place() {
    btn = document.getElementById('btn-taskboard');
    if (!btn) return false;
    btn.addEventListener('click', toggle);
    paint();
    return true;
  }
  function boot(tries) {
    if (!btn && !(api() && host() && place())) { if (tries < 240) setTimeout(() => boot(tries + 1), 250); return; }
    poll();
    timer = setInterval(() => { if (!document.hidden && !(O() && O().isOpen())) poll(); }, 20000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden && !(O() && O().isOpen())) poll(); });
    const s = document.getElementById('btn-settings');                       // the language may have changed in Settings
    if (s) s.addEventListener('click', () => setTimeout(paint, 0));
  }
  // Window capture: before the terminal sees the key, and before office.js's own capture handler (registered later).
  root.addEventListener('keydown', e => {
    if (!isKey(e)) return;
    e.preventDefault(); e.stopPropagation();
    if (!e.repeat) toggle();
  }, true);

  root.SFBoard = { isKey, note, noteDeliv, deliv: () => deliv, notePeers, poll, toggle, count: () => count, keyName, isShown: () => !!btn && !btn.hidden,
                   decisions: () => list.slice(), peers: () => peers, avail: () => avail, onData: fn => { subs.push(fn); } };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => boot(0));
  else boot(0);
})(typeof window !== 'undefined' ? window : globalThis);
