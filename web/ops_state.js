/* The ops board, the main page's half (settings.experimental_ops, default off; web/office_ops.js is the office's half).
 *
 *   window.__sfOpsLevel({level, reason, since})   the backend pushes it when the level changes (no polling); level `alert` puts a red
 *                                                 dot on the sidebar's 辦公室 button, `muted` and the rest do not
 *   ops_state()                                   asked once at load (and after the switch moves) for the starting value
 *   Settings -> Office: the 維運戰情 switch, which writes settings.experimental_ops through the page's own copy (save_settings
 *                                                 replaces the whole dict)
 * The dot is a child of the button, positioned out of the flow, so the entry row's measuring (entry.js) does not see it.
 */
(function (root) {
  'use strict';

  const host = () => root.__sfHost || null;
  const api = () => (root.pywebview && root.pywebview.api) || null;
  const isZh = () => /^zh/i.test((host() && host().lang && host().lang()) || navigator.language || '');
  const t = (en, zh) => (isZh() ? zh : en);
  const cfg = () => (host() && host().cfg ? host().cfg() : null);
  const enabled = () => { const c = cfg(); return !!(c && c.settings && c.settings.experimental_ops === true); };
  const LEVELS = ['ok', 'warn', 'alert', 'none', 'muted'];

  let st = { level: 'none', reason: '', since: null }, tries = 0, timer = 0;

  function norm(s) {
    const o = s && typeof s === 'object' ? s : {};
    return { level: LEVELS.indexOf(o.level) >= 0 && o.on !== false ? o.level : 'none', reason: String(o.reason || '').slice(0, 160), since: typeof o.since === 'number' ? o.since : null };
  }
  function paint() {
    const b = document.getElementById('btn-office');
    if (!b) { clearTimeout(timer); if (tries++ < 120) timer = setTimeout(paint, 500); return; }   // the entry row is built a moment after the page
    const on = st.level === 'alert';
    let d = b.querySelector('.sfe-opsdot');
    b.classList.toggle('has-ops-alert', on);
    if (on) {
      if (!d) { d = document.createElement('span'); d.className = 'sfe-opsdot'; d.setAttribute('role', 'img'); b.appendChild(d); }
      const tip = t('Ops alert', '維運異常') + (st.reason ? ': ' + st.reason : '');
      d.setAttribute('aria-label', tip); d.title = tip;
    } else if (d) d.remove();
  }
  function poke() {                                           // the office, when it is open, learns the new numbers now, not at its next 30 s read
    try { const o = root.SFOffice; if (o && o.isOpen && o.isOpen() && o.host && o.host.objectsChanged) o.host.objectsChanged(); } catch (_) { /* the next read gets it */ }
  }
  root.__sfOpsLevel = function (s) { st = norm(s); paint(); poke(); };
  async function load() {
    const a = api();
    if (!a || !a.ops_state) return;
    try { const r = await a.ops_state(); st = norm(typeof r === 'string' ? JSON.parse(r) : r); } catch (_) { return; }
    paint();
  }

  // ── the switch (Settings -> Office) ──
  function injectSettings() {
    const box = document.getElementById('sx-office');
    if (!box) return;
    let row = document.getElementById('sfo-ops');
    if (!row) {
      row = document.createElement('div');
      row.id = 'sfo-ops';
      row.className = 'sx-row';
      row.innerHTML = '<div class="sx-txt"><div class="sx-label"></div><div class="sx-hint"></div></div>' +
        '<div class="sx-ctl"><button type="button" class="toggle-switch" id="setting-experimental-ops" role="switch" data-guide="office-ops"></button></div>';
      box.appendChild(row);
      row.querySelector('#setting-experimental-ops').addEventListener('click', async function () {
        const c = cfg(), a = api();
        if (!c || !a) return;
        c.settings = c.settings || {};
        c.settings.experimental_ops = !enabled();
        injectSettings();
        try { await a.save_settings(JSON.stringify(c.settings)); } catch (_) { /* the page keeps it; the next save carries it */ }
        await load(); poke();
      });
    }
    row.querySelector('.sx-label').textContent = t('Ops board: a TV for this computer\'s health (experimental)', '維運戰情：看這台電腦健不健康的電視（實驗性）');
    row.querySelector('.sx-hint').textContent = t(
      'A TV on the office wall (and an Ops button) shows CPU, memory, network and disk, turns red and puts a dot on the sidebar\'s 辦公室 button when something is wrong, and sends one system notification per incident. Off by default; measured every 5 minutes, live while its window is open.',
      '辦公室牆上多一台電視（和一顆「維運」鈕），顯示 CPU、記憶體、網路、磁碟；出事時變紅、側欄的「辦公室」鈕冒紅點，每次事件送一則系統通知。預設關閉；平時每 5 分鐘量一次，開著它的視窗才即時量。');
    const sw = row.querySelector('.toggle-switch');
    sw.classList.toggle('on', enabled());
    sw.setAttribute('aria-checked', String(enabled()));
    sw.setAttribute('aria-label', row.querySelector('.sx-label').textContent);
  }

  function boot(n) {
    const H = host();
    if (!H || !H.cfg() || !api()) { if (n < 240) setTimeout(() => boot(n + 1), 250); return; }
    injectSettings();
    load();
    const s = document.getElementById('btn-settings');
    if (s) s.addEventListener('click', () => setTimeout(injectSettings, 0));
  }
  root.SFOps = { level: () => ({ ...st }), paint, load, injectSettings };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => boot(0));
  else boot(0);
})(typeof window !== 'undefined' ? window : globalThis);
