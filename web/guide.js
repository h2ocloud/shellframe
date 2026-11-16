/* The guide: a first-run tour and a help list, drawn from guide.json (guide.py, api_guide.py).
 *
 * Python decides what there is to say: each feature's {en, zh} title and body, whether it needs a
 * setting that is off (`on`), which settings tab shows it (`open`), the tours, and the person's state
 * (`tour`: "pending" on a first launch, `seen`: the version they last looked at). This file finds each
 * feature on the page by `data-guide="<id>"`, which tests_guide.py keeps in step with guide.json.
 *
 *   the tour      a first launch only (state.tour "pending"): one spotlight per feature of
 *                 tours.first_run that is on the page; one missing from it is skipped. Ends "done".
 *   the help list the "Guide" button: every feature, "new" since the version last seen, "off" when it
 *                 needs a setting; "show me" opens its settings tab and points at it.
 *
 * Words follow the page's language (index.html's `lang`), as schedules.js does.
 */
(function () {
  'use strict';

  const host = () => window.__sfHost || null;
  const isZh = () => {
    const h = host();
    const l = (h && h.lang && h.lang()) || navigator.language || '';
    return /^zh/i.test(l);
  };
  const t = (en, zh) => (isZh() ? zh : en);
  const pick = (o) => (o ? (isZh() ? (o.zh || o.en) : (o.en || o.zh)) || '' : '');
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const rich = (s) => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>');
  const api = () => (window.pywebview && window.pywebview.api) || null;

  let data = null;            // guide_data(): {features, tours, state}
  let layer = null;           // the tour's or the spotlight's elements while one is shown
  let openedSettings = false; // the tour opened Settings itself: it closes it again

  const vt = (v) => (String(v || '').match(/\d+/g) || ['0']).slice(0, 3).map(Number);
  const newer = (a, b) => {
    const x = vt(a), y = vt(b);
    for (let i = 0; i < 3; i++) { if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0); }
    return false;
  };
  const isNew = (f) => !!(data && data.state.seen && newer(f.since, data.state.seen));
  const byId = (id) => (data ? data.features.find(f => f.id === id) : null);

  // ── where a feature is on the page ─────────────────────────────────────
  function visible(el) {
    if (!el || !el.getClientRects().length) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
  }
  function anchor(id) {
    return Array.from(document.querySelectorAll(`[data-guide="${id}"]`)).find(visible) || null;
  }
  function settingsOpen() {
    const m = document.getElementById('modal-settings');
    return !!(m && m.classList.contains('open'));
  }
  // `open` is "settings:<tab>": open Settings on that tab (the tour remembers it opened it).
  function reveal(f) {
    const m = /^settings:([a-z]+)$/.exec(f.open || '');
    if (!m) return;
    if (!settingsOpen()) {
      const b = document.getElementById('btn-settings');
      if (b) { b.click(); openedSettings = true; }
    }
    const tab = document.querySelector(`#modal-settings .settings-tab[data-tab="${m[1]}"]`);
    if (tab && !tab.classList.contains('active')) tab.click();
  }
  function unreveal() {
    if (openedSettings && settingsOpen()) {
      const c = document.getElementById('btn-close-settings');
      if (c) c.click();
    }
    openedSettings = false;
  }

  // ── the spotlight ──────────────────────────────────────────────────────
  function clear() {
    if (!layer) return;
    layer.root.remove();
    window.removeEventListener('resize', layer.place);
    document.removeEventListener('keydown', layer.keys, true);
    layer = null;
  }

  // One card pointing at `el` (or in the middle when there is none). `buttons`: [{label, run, primary}].
  function show(f, el, step, buttons, onEsc) {
    clear();
    const root = document.createElement('div');
    root.className = 'sfg-layer';
    root.innerHTML = `<div class="sfg-spot${el ? '' : ' none'}"></div>` +
      `<div class="sfg-card" role="dialog" aria-label="${esc(pick(f.title))}">` +
      `<div class="sfg-title">${esc(pick(f.title))}${f.on === false ? `<span class="sfg-chip off">${t('off', '未開啟')}</span>` : ''}</div>` +
      `<div class="sfg-body">${rich(pick(f.body))}</div>` +
      (f.on === false ? `<div class="sfg-hint">${t('Turn it on with its switch here.', '用這裡的開關打開。')}</div>` : '') +
      `<div class="sfg-foot"><span class="sfg-step">${esc(step || '')}</span><span class="sfg-acts"></span></div></div>`;
    const acts = root.querySelector('.sfg-acts');
    buttons.forEach(b => {
      const btn = document.createElement('button');
      btn.className = 'sfg-btn' + (b.primary ? ' primary' : '');
      btn.textContent = b.label;
      btn.addEventListener('click', (e) => { e.stopPropagation(); b.run(); });
      acts.appendChild(btn);
    });
    document.body.appendChild(root);
    const spot = root.querySelector('.sfg-spot'), card = root.querySelector('.sfg-card');
    const place = () => {
      const vw = window.innerWidth, vh = window.innerHeight, pad = 6, gap = 12, m = 12;
      const cw = Math.min(340, vw - 2 * m);
      card.style.width = cw + 'px';
      if (!el || !visible(el)) {
        card.style.left = Math.max(m, (vw - cw) / 2) + 'px';
        card.style.top = Math.max(m, (vh - card.offsetHeight) / 2) + 'px';
        return;
      }
      const r = el.getBoundingClientRect();
      Object.assign(spot.style, { left: (r.left - pad) + 'px', top: (r.top - pad) + 'px',
                                  width: (r.width + 2 * pad) + 'px', height: (r.height + 2 * pad) + 'px' });
      const ch = card.offsetHeight;
      let top = r.bottom + pad + gap;
      if (top + ch > vh - m) top = r.top - pad - gap - ch;          // no room below: above it
      if (top < m) top = Math.min(vh - m - ch, Math.max(m, r.top));  // no room either way: beside it
      let left = r.left + r.width / 2 - cw / 2;
      if (top === Math.min(vh - m - ch, Math.max(m, r.top)) && r.right + gap + cw < vw - m) left = r.right + gap;
      card.style.left = Math.max(m, Math.min(left, vw - cw - m)) + 'px';
      card.style.top = top + 'px';
    };
    const keys = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onEsc(); }
      else if (e.key === 'ArrowRight' || e.key === 'Enter') { const p = buttons.find(b => b.primary); if (p) { e.preventDefault(); e.stopPropagation(); p.run(); } }
      else if (e.key === 'ArrowLeft') { const b = buttons.find(x => x.back); if (b) { e.preventDefault(); e.stopPropagation(); b.run(); } }
    };
    layer = { root, place, keys };
    place();
    window.addEventListener('resize', place);
    document.addEventListener('keydown', keys, true);
    const primary = acts.querySelector('.sfg-btn.primary');
    if (primary) primary.focus();
  }

  // ── the tour ───────────────────────────────────────────────────────────
  function mark(args) {
    const a = api();
    if (!a || !a.guide_mark) return Promise.resolve(null);
    return a.guide_mark(args.tour || '', !!args.seen).then(r => {
      try { const o = JSON.parse(r); if (o && o.state && data) data.state = o.state; } catch (e) { /* keep ours */ }
      badge();
      return r;
    }).catch(() => null);
  }

  function tour(name) {
    if (!data) return;
    const h = host();
    const sb = document.getElementById('sidebar');                 // a first launch starts with it folded
    if (sb && sb.classList.contains('collapsed') && !tour._opening) {
      const b = document.getElementById('btn-sidebar');
      if (b) { b.click(); tour._opening = true; setTimeout(() => { tour._opening = false; tour(name); }, 350); return; }
    }
    // With no tab yet the tab bar (and its +) is hidden and the launcher is what opens a tab: its step
    // points at the launcher; the other steps put it away, and the end brings it back.
    const noTab = () => { const s = h && h.sessions ? h.sessions() : null; return !!s && !Object.keys(s).length; };
    const launcher = (open) => {
      const m = document.getElementById('modal-new');
      if (!m || m.classList.contains('open') === open) return;
      if (open) { const b = document.getElementById('btn-new-tab'); if (b) b.click(); }
      else if (h && h.closeModal) h.closeModal();
    };
    // The steps on this page: in Settings (opened when its turn comes), or on screen now. A button a
    // first launch does not have yet (a tab's ⟳, the chat switch with no tab) is left out.
    const steps = ((data.tours && data.tours[name]) || []).map(byId)
      .filter(f => f && (f.open || anchor(f.id) || (f.id === 'new-tab' && noTab())));
    let i = 0;
    const end = () => {
      clear(); unreveal();
      mark({ tour: 'done', seen: true });
      if (noTab()) launcher(true);
    };
    const go = (dir) => {
      for (; i >= 0 && i < steps.length; i += dir) {
        const f = steps[i];
        if (noTab()) launcher(f.id === 'new-tab');
        if (f.open) reveal(f); else unreveal();
        const el = anchor(f.id);
        if (!el) continue;
        const at = i, last = at === steps.length - 1;
        show(f, el, `${at + 1} / ${steps.length}`, [
          { label: t('Skip', '略過'), run: end },
          ...(at > 0 ? [{ label: t('Back', '上一步'), back: true, run: () => { i = at - 1; go(-1); } }] : []),
          { label: last ? t('Done', '完成') : t('Next', '下一步'), primary: true,
            run: () => { if (last) end(); else { i = at + 1; go(1); } } },
        ], end);
        return;
      }
      if (dir < 0 && steps.length) { i = 0; go(1); return; }   // nothing before it: back to the first
      end();
    };
    go(1);
  }

  // One feature, from the help list: open where it is and point at it.
  function point(id) {
    const f = byId(id);
    if (!f) return;
    closeHelp();
    reveal(f);
    setTimeout(() => {
      const done = () => { clear(); unreveal(); };
      show(f, anchor(f.id), '', [{ label: t('Got it', '知道了'), primary: true, run: done }], done);
    }, f.open ? 120 : 0);
  }

  // ── the help list ──────────────────────────────────────────────────────
  let help = null;
  function closeHelp() { if (help) { help.remove(); help = null; } }
  function openHelp() {
    if (!data) { load().then(() => data && openHelp()); return; }
    closeHelp();
    const fresh = data.features.filter(isNew);
    const row = (f) => `<div class="sfg-row" data-id="${esc(f.id)}">` +
      `<div class="sfg-row-head"><span class="sfg-row-title">${esc(pick(f.title))}</span>` +
      (isNew(f) ? `<span class="sfg-chip new">${t('new', '新')}</span>` : '') +
      (f.on === false ? `<span class="sfg-chip off">${t('off · Settings', '未開啟・在設定裡')}</span>` : '') +
      `<button class="sfg-btn sfg-show" data-show="${esc(f.id)}">${t('Show me', '指給我看')}</button></div>` +
      `<div class="sfg-row-body">${rich(pick(f.body))}</div></div>`;
    help = document.createElement('div');
    help.className = 'sfg-help-veil';
    help.innerHTML = `<div class="sfg-help" role="dialog" aria-label="${t('Guide', '導覽')}">` +
      `<div class="sfg-help-head"><span>${t('Guide', '導覽')}</span>` +
      `<button class="sfg-btn primary" data-act="tour">${t('Take the tour', '看一次導覽')}</button>` +
      `<button class="sfg-x" data-act="close" aria-label="${t('Close', '關閉')}">×</button></div>` +
      (fresh.length ? `<div class="sfg-sec">${t('New since you last looked', '上次看過之後的新功能')}</div>` + fresh.map(row).join('') : '') +
      `<div class="sfg-sec">${t('Everything', '全部功能')}</div>` +
      data.features.filter(f => !isNew(f)).map(row).join('') + `</div>`;
    help.addEventListener('click', (e) => {
      const s = e.target.closest('[data-show]');
      if (s) { point(s.getAttribute('data-show')); return; }
      const a = e.target.closest('[data-act]');
      if (a && a.getAttribute('data-act') === 'tour') { closeHelp(); tour('first_run'); return; }
      if ((a && a.getAttribute('data-act') === 'close') || e.target === help) closeHelp();
    });
    document.body.appendChild(help);
    mark({ seen: true });
  }

  // How many features are new since the version last looked at, on the Guide button.
  function badge() {
    const b = document.getElementById('btn-guide');
    if (!b || !data) return;
    const n = data.features.filter(isNew).length;
    let chip = b.querySelector('.sfg-badge');
    if (!n) { if (chip) chip.remove(); return; }
    if (!chip) { chip = document.createElement('span'); chip.className = 'sfg-badge'; b.appendChild(chip); }
    chip.textContent = String(n);
    chip.title = t(`${n} new since you last looked`, `有 ${n} 個新功能`);
  }

  function load() {
    const a = api();
    if (!a || !a.guide_data) return Promise.resolve(null);
    return a.guide_data().then(r => {
      const o = JSON.parse(r);
      if (o && o.success) data = o;
      badge();
      return data;
    }).catch(() => null);
  }

  // Once the page is up (the API is there and the first restore had a moment), load the guide;
  // a first launch gets the tour.
  function boot() {
    const ready = () => api() && host();
    const start = () => load().then(d => {
      if (d && d.state && d.state.tour === 'pending') setTimeout(() => tour('first_run'), 400);
    });
    let tries = 0;
    const wait = () => {
      if (ready()) { setTimeout(start, 1500); return; }
      if (++tries < 120) setTimeout(wait, 250);
    };
    wait();
  }

  window.SFGuide = { boot, tour, openHelp, point, reload: load, _anchor: anchor };
  document.addEventListener('click', (e) => {
    if (e.target.closest && e.target.closest('#btn-guide')) { e.preventDefault(); openHelp(); }
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
