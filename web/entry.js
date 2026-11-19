/* ShellBot entry row — the way in to Bots, the office and the task board; see docs/guide.md and docs/chat-shell.md.
 *
 * A row at the top of the sidebar (terminal view and chat view alike), always there whether or not a feature is switched on:
 *
 *     🤖 Bots     🏢 辦公室     📋 看板 3
 *
 *   - Bots / 辦公室 with the feature ON just open it: Bots shows its section in the sidebar (and starts the 「＋ Bot」 flow when there are
 *     none), 辦公室 opens the office floor. With it OFF the button is drawn dashed and opens an intro card (what it does, a picture,
 *     「打開」 / 「先不要」). 「打開」 turns the switch on the way Settings does (cfg.settings.experimental_* + save_settings) and then
 *     opens the feature; 「先不要」 changes nothing.
 *   - 看板 is web/board.js's button (#btn-taskboard); this file only builds its place. Its count is SFBoard's.
 *   - Labels show when they all fit the sidebar, otherwise each button is an icon with its title (measured here: .sfe-words).
 *     The same measure puts words on the foot's icons (導覽, 給 AI) in #sb-footer-row.
 *
 * The buttons are marked data-guide="entry-bots" / "entry-office" / "taskboard", the points of the tour that follows an update
 * (guide.json tours.entry, web/guide.js).
 */
(function (root) {
  'use strict';
  const host = () => root.__sfHost || null;
  const api = () => (root.pywebview && root.pywebview.api) || null;
  const isZh = () => /^zh/i.test((host() && host().lang && host().lang()) || navigator.language || '');
  const t = (en, zh) => (isZh() ? zh : en);
  const cfg = () => { const h = host(); return h && h.cfg ? h.cfg() : null; };
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const FEATURES = {
    bots: {
      flag: 'experimental_bots', icon: 'bot', pic: 'img/entry-bots.png',
      name: () => 'Bots',
      bullets: () => [
        t('Any tab can become a bot with its own name, face, job and memory.', '任何一個分頁都可以變成 bot：有自己的名字、頭像、執掌和記憶。'),
        t('A sleeping bot wakes up with its memory and picks the work back up.', '休息中的 bot 隨時叫醒，帶著上次的記憶接著做。'),
        t('A primary bot is the first stop for everyday work and hands it out to the others.', '主要 Bot 是日常任務的第一站，負責盤點並把工作派給其他 bot。'),
      ],
      where: () => t('To turn it off later: Settings → Bots.', '之後想關掉，到 設定 → Bots。'),
    },
    office: {
      flag: 'experimental_office', icon: 'office', pic: 'img/entry-office.png',
      name: () => t('Office', '辦公室'),
      bullets: () => [
        t('Every bot is drawn as a person in an office: who is busy and who is waiting on you, at a glance.', '把每個 bot 畫成辦公室裡的一個人，誰在忙、誰在等你，一眼看完。'),
        t('The whiteboard on the wall holds what waits for your decision, and the calendar lays out each schedule.', '牆上的白板收著等你決定的事，行事曆排著各個排程。'),
        t('Click a person to talk to it and to see the tasks in its hands.', '點一個人就能跟它說話，也能看到它手上的任務。'),
      ],
      where: () => t('To turn it off later: Settings → Office.', '之後想關掉，到 設定 → 辦公室。'),
    },
  };

  let rowEl = null, entry = null, card = null, lastFocus = null;
  const btn = {};

  // ── state ──────────────────────────────────────────────────────────────
  function isOn(kind) {
    if (kind === 'bots' && root.SFBots && root.SFBots.enabled) return !!root.SFBots.enabled();
    const c = cfg();
    return !!(c && c.settings && c.settings[FEATURES[kind].flag] === true);
  }

  // ── the row ────────────────────────────────────────────────────────────
  function build() {
    const sb = document.getElementById('sidebar');
    if (!sb || document.getElementById('sb-entry')) return !!document.getElementById('sb-entry');
    entry = document.createElement('div');
    entry.id = 'sb-entry';
    entry.innerHTML = '<div class="sfe-row">' +
      '<button type="button" class="sfe-btn" id="btn-bots" data-guide="entry-bots"><span class="sfe-ico" aria-hidden="true">' + SFIcons.html('bot') + '</span><span class="sfe-label"></span></button>' +
      '<button type="button" class="sfe-btn" id="btn-office" data-guide="entry-office"><span class="sfe-ico" aria-hidden="true">' + SFIcons.html('office') + '</span><span class="sfe-label"></span></button>' +
      '<button type="button" class="sfe-btn" id="btn-taskboard" data-guide="taskboard"><span class="sfe-ico" aria-hidden="true">' + SFIcons.html('clipboard') + '</span><span class="sfe-label"></span><span class="sfb-badge" hidden></span></button>' +
      '</div>';
    sb.insertBefore(entry, sb.firstChild);
    rowEl = entry.firstChild;
    btn.bots = entry.querySelector('#btn-bots');
    btn.office = entry.querySelector('#btn-office');
    btn.bots.addEventListener('click', () => press('bots'));
    btn.office.addEventListener('click', () => press('office'));
    return true;
  }

  function paintOne(kind) {
    const b = btn[kind];
    if (!b) return;
    const on = isOn(kind), name = FEATURES[kind].name();
    const tip = on ? name : t(`${name} (off): tap to see what it does`, `${name}（未開啟）：按一下看它做什麼`);
    b.classList.toggle('off', !on);
    b.title = tip;
    b.setAttribute('aria-label', tip);
    b.querySelector('.sfe-label').textContent = name;
  }

  // The foot's icons get their short word beside them; the full name stays in the title.
  const FOOT_WORDS = { 'btn-guide': () => t('Guide', '導覽'), 'btn-ai-skill': () => t('For AI', '給 AI') };
  function paintFoot() {
    Object.keys(FOOT_WORDS).forEach(id => {
      const b = document.getElementById(id);
      if (!b) return;
      let w = b.querySelector('.sb-word');
      if (!w) {
        w = document.createElement('span');
        w.className = 'sb-word';
        const ico = b.querySelector('.sb-ico');
        b.insertBefore(w, ico ? ico.nextSibling : b.firstChild);
      }
      w.textContent = FOOT_WORDS[id]();
    });
  }

  // Words when every button's label fits; icons with titles when not. The row tries icon + word, then the word alone, and in a
  // narrow sidebar (a row under NARROW px: the sidebar under about 185 px) stays icons only.
  const NARROW = 164;
  // Measured with the buttons' own transitions off (.sfe-measure), or the width read is one the buttons are still moving towards.
  const overflows = row => row.scrollWidth > row.clientWidth + 1;
  function trial(el, row, mode) {
    el.classList.add('sfe-measure', mode);
    const over = overflows(row);
    if (over) { el.classList.remove(mode); void row.offsetWidth; }     // settle the result before the transitions are let back in
    el.classList.remove('sfe-measure');
    return !over;
  }
  function fitFoot() {
    const f = document.getElementById('sb-footer-row');
    if (!f) return;
    f.classList.remove('sfe-words');
    // Keep the footer icon-only while the sidebar is genuinely narrow. The
    // Settings button may shrink to its gear first, which can otherwise make
    // the words appear to fit even though the entry row has already switched
    // to its narrow mode.
    if (f.clientWidth < NARROW + 10) return;
    trial(f, f, 'sfe-words');
  }
  function fitEntry() {
    if (!entry || !rowEl) return;
    entry.classList.remove('sfe-words', 'sfe-text');
    if (rowEl.clientWidth < NARROW) return;
    if (!trial(entry, rowEl, 'sfe-words')) trial(entry, rowEl, 'sfe-text');
  }
  function fit() { fitEntry(); fitFoot(); }
  let fitting = false;
  function refit() {
    if (fitting) return;
    fitting = true;
    requestAnimationFrame(() => { fitting = false; try { fit(); } catch (_) {} });
  }

  function paint() {
    if (!entry && !build()) return;
    paintOne('bots');
    paintOne('office');
    paintFoot();
    fit();
  }

  // ── what a press does ──────────────────────────────────────────────────
  function press(kind) {
    if (isOn(kind)) openFeature(kind);
    else intro(kind, btn[kind]);
  }
  async function openFeature(kind) {
    if (kind === 'office') {
      const o = root.SFOffice;
      if (o && o.open) o.open();
      return;
    }
    const B = root.SFBots;
    if (!B) return;
    const roster = (B.state && B.state().roster) || {};
    const any = Object.keys(roster).some(k => roster[k] && roster[k].bot);
    if (!any && B.newBot) { B.newBot(); return; }
    // The BOTS section and the primary card, brought into view and lit for a moment.
    const sect = document.querySelector('#sidebar .sfb-section');
    const prim = document.querySelector('#sfb-primary .sfb-card');
    [sect, prim].forEach(el => {
      if (!el) return;
      el.classList.remove('sfe-flash'); void el.offsetWidth; el.classList.add('sfe-flash');
      setTimeout(() => el.classList.remove('sfe-flash'), 1600);
    });
    if (sect) { try { sect.scrollIntoView({ block: 'center' }); } catch (_) {} }
  }

  // ── the intro card ─────────────────────────────────────────────────────
  function closeIntro(restore) {
    if (!card) return;
    document.removeEventListener('keydown', onKey, true);
    card.remove();
    card = null;
    if (restore !== false && lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (_) {} }
    lastFocus = null;
  }
  function onKey(e) {
    if (!card) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeIntro(); return; }
    if (e.key === 'Tab') {
      const items = Array.from(card.querySelectorAll('button:not(:disabled)'));
      if (!items.length) return;
      const i = items.indexOf(document.activeElement);
      e.preventDefault();
      items[(i + (e.shiftKey ? -1 : 1) + items.length) % items.length].focus();
    }
  }
  function intro(kind, from) {
    closeIntro(false);
    const F = FEATURES[kind];
    lastFocus = from || document.activeElement;
    const name = F.name();
    card = document.createElement('div');
    card.className = 'sfe-veil';
    card.id = 'sfe-intro';
    card.setAttribute('data-kind', kind);
    card.innerHTML = `<div class="sfe-card" role="dialog" aria-modal="true" aria-labelledby="sfe-title">` +
      `<img class="sfe-pic" src="${esc(F.pic)}" alt="">` +
      `<div class="sfe-main"><div class="sfe-title" id="sfe-title"><span class="sfe-title-ico" aria-hidden="true">${SFIcons.html(F.icon)}</span><span>${esc(name)}</span>` +
      `<span class="sfe-chip">${t('off', '未開啟')}</span></div>` +
      `<ul class="sfe-list">${F.bullets().map(b => `<li>${esc(b)}</li>`).join('')}</ul>` +
      `<div class="sfe-note">${esc(F.where())}</div><div class="sfe-err" hidden></div>` +
      `<div class="sfe-acts"><button type="button" data-act="no">${t('Not now', '先不要')}</button>` +
      `<button type="button" class="primary" data-act="yes">${t('Turn on', '打開')}</button></div></div></div>`;
    card.addEventListener('click', async e => {
      if (e.target === card) { closeIntro(); return; }
      const a = e.target.closest && e.target.closest('[data-act]');
      if (!a) return;
      if (a.getAttribute('data-act') === 'no') { closeIntro(); return; }
      await turnOn(kind);
    });
    document.body.appendChild(card);
    document.addEventListener('keydown', onKey, true);
    const yes = card.querySelector('[data-act="yes"]');
    if (yes) yes.focus();
  }

  // The same save path as the Settings switch: the page's copy of settings, saved whole.
  async function turnOn(kind) {
    const F = FEATURES[kind], c = cfg(), a = api();
    const c0 = card;
    if (!c || !a || !a.save_settings) return;
    const buttons = Array.from(c0.querySelectorAll('button'));
    buttons.forEach(b => { b.disabled = true; });
    c.settings = c.settings || {};
    const before = c.settings[F.flag];
    c.settings[F.flag] = true;
    try {
      await a.save_settings(JSON.stringify(c.settings));
    } catch (_) {
      c.settings[F.flag] = before;
      buttons.forEach(b => { b.disabled = false; });
      const err = c0.querySelector('.sfe-err');
      if (err) { err.hidden = false; err.textContent = t('Could not save the switch. Try again, or use Settings.', '沒存成功，再按一次，或到設定裡打開。'); }
      return;
    }
    closeIntro(false);
    if (kind === 'bots') {
      const B = root.SFBots;
      if (B && B.setEnabled) B.setEnabled(true);
      if (B && B.refresh) { try { await B.refresh(true); } catch (_) {} }
    } else if (root.SFOffice && root.SFOffice.placeButton) {
      root.SFOffice.placeButton();
    }
    paint();
    openFeature(kind);
  }

  // ── boot ───────────────────────────────────────────────────────────────
  function boot(tries) {
    if (!host() || !cfg()) { if (tries < 240) setTimeout(() => boot(tries + 1), 250); return; }
    paint();
    const s = document.getElementById('btn-settings');                       // the language may have changed in Settings
    if (s) s.addEventListener('click', () => setTimeout(paint, 0));
    if (root.MutationObserver) {
      new MutationObserver(paint).observe(document.body, { attributes: true, attributeFilter: ['class'] });   // sfb-on: bots switched on or off
      const ro = root.ResizeObserver ? new root.ResizeObserver(refit) : null;
      const f = document.getElementById('sb-footer-row');
      if (ro) {
        if (rowEl) ro.observe(rowEl);
        if (f) ro.observe(f);
      }
      // What a row holds changes its need for room: the guide's count of new features, the board's count, a label's language.
      const mo = new MutationObserver(refit);
      [rowEl, f].forEach(el => { if (el) mo.observe(el, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['hidden'] }); });
    }
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(refit);
    root.addEventListener('resize', refit);
  }

  // The row exists from the first frame (the page's scripts sit after the sidebar), so web/board.js finds its button.
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => { build(); boot(0); });
  else { build(); boot(0); }
  root.SFEntry = { paint, fit, press, intro, closeIntro, isOn, openFeature, isOpen: () => !!card };
})(typeof window !== 'undefined' ? window : globalThis);
