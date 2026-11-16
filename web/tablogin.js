/* The chat view's sign-in card (tab_login.py, api_chat.chat_login): when a tab's Claude sign-in expired,
 * it takes a person who knows neither the product nor the CLI through signing in again, with buttons
 * only. chat_view's `login` says the step: expired → menu → browser / code → done; afterwards it offers
 * to send the last message again. Words follow the page's language. */
(function (root) {
  'use strict';
  const host = () => root.__sfHost || null;
  const isZh = () => /^zh/i.test(((host() && host().lang && host().lang()) || navigator.language || ''));
  const T = (en, zh) => (isZh() ? zh : en);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const api = () => (root.pywebview && root.pywebview.api) || null;
  const after = {};               // sid -> {text}: signed in, offer the last message again

  const btn = (act, label, primary) =>
    `<button class="cm-opt${primary ? ' tone-primary' : ''}" data-act="${act}">${esc(label)}</button>`;

  function lastWords(turns) {
    for (let i = (turns || []).length - 1; i >= 0; i--) {
      const t = turns[i];
      if (t && t.kind === 'user_msg' && String(t.text || '').trim()) return String(t.text).trim();
    }
    return '';
  }

  function card(sid, data, face) {
    const lg = (data && data.login) || {};
    const step = lg.step || '';
    let title = '', q = '', opts = '', extra = '';
    if (!step && after[sid]) {
      const w = after[sid].text;
      title = T('Signed in', '登入好了');
      q = w ? T('Send your last message again?', '要把剛才那則再送一次嗎？') : T('You can go on.', '可以繼續了。');
      extra = w ? `<div class="cm-ask-ctx"><code>${esc(w.length > 80 ? w.slice(0, 80) + '…' : w)}</code></div>` : '';
      opts = (w ? btn('login-resend', T('Send it again', '重送'), true) : '') + btn('login-close', w ? T('No need', '不用') : T('OK', '好'));
    } else if (step === 'expired') {
      title = T('Sign in again', '要重新登入');
      q = T("This tab's Claude sign-in has expired. Sign in again and it goes on; no commands to type.",
            '這個分頁的 Claude 登入過期了。重新登入後就能繼續，不用打任何指令。');
      opts = btn('login-start', T('Sign in', '重新登入'), true);
    } else if (step === 'starting') {
      title = T('Opening the sign-in…', '正在打開登入…');
      opts = btn('login-cancel', T('Cancel', '取消'));
    } else if (step === 'menu') {
      title = T('How do you sign in?', '用哪種帳號登入？');
      q = T('Most people pick the first one.', '大多數人選第一個。');
      opts = btn('login-subscription', T('Claude subscription (Pro, Max, Team)', 'Claude 訂閱帳號（Pro、Max、Team）'), true) +
             btn('login-console', T('API account (Console)', 'API 帳號（Console）')) + btn('login-cancel', T('Cancel', '取消'));
    } else if (step === 'browser' || step === 'code') {
      title = T('Finish signing in in the browser', '在瀏覽器完成登入');
      q = step === 'code'
        ? T('Sign in on the page, then paste the code it shows here.', '在登入頁登入後，把它顯示的代碼貼到這裡。')
        : T('The sign-in page should be open in your browser; if not, open it here. If the page then shows a code, paste it here.',
            '登入頁應該已經在瀏覽器打開了；沒有的話按「打開登入頁」。登入後如果頁面給你一組代碼，貼到下面。');
      extra = `<div class="cm-ask-ctx" style="display:flex;gap:6px;align-items:center">` +
        `<input class="cm-login-code" type="text" autocomplete="off" spellcheck="false" placeholder="${esc(T('Code from the page', '登入頁給的代碼'))}" ` +
        `style="flex:1;min-width:0;padding:6px 8px;border-radius:6px;border:1px solid #3b4261;background:#1a1b26;color:#c0caf5">` +
        btn('login-code', T('Send code', '送出代碼'), step === 'code') + `</div>`;
      opts = (lg.url ? `<button class="cm-opt tone-primary" data-act="login-open" data-url="${esc(lg.url)}">${esc(T('Open the sign-in page', '打開登入頁'))}</button>` : '') +
             btn('login-cancel', T('Cancel', '取消'));
    } else if (step === 'done') {
      title = T('Signed in', '登入好了');
      q = T('Press Continue to go back to the conversation.', '按「繼續」回到對話。');
      opts = btn('login-finish', T('Continue', '繼續'), true);
    } else {
      return '';
    }
    return `<div class="cm-ask-card cm-login-card" data-step="${esc(step || 'after')}">${face}<div class="cm-ask-body">` +
      `<div class="cm-ask-title">${esc(title)}</div>` + (q ? `<div class="cm-ask-q">${esc(q)}</div>` : '') + extra +
      `<div class="cm-opts">${opts}</div></div></div>`;
  }

  // A button on the card. `done(login)` redraws the view with the step it is at now.
  async function click(el, sid, data, done) {
    const act = el.dataset.act.replace(/^login-/, '');
    const a = api();
    if (!a) return;
    if (act === 'open') { try { a.open_url(el.dataset.url); } catch (_) {} return; }
    if (act === 'close') { delete after[sid]; return done(); }
    if (act === 'resend') {
      const w = (after[sid] || {}).text;
      delete after[sid];
      if (w) { try { await a.chat_send(sid, w, false); } catch (_) {} }
      return done();
    }
    let code = '';
    if (act === 'code') {
      const box = el.closest('.cm-ask-body') && el.closest('.cm-ask-body').querySelector('.cm-login-code');
      code = box ? box.value.trim() : '';
      if (!code) { if (box) box.focus(); return; }
    }
    el.closest('.cm-ask-body').querySelectorAll('button').forEach(b => { b.disabled = true; });
    let r = {};
    try { r = JSON.parse(await a.chat_login(sid, act, code)); } catch (_) { r = {}; }
    if (act === 'finish') after[sid] = { text: lastWords(data && data.turns) };
    done(r.login);
  }

  root.SFLogin = { card, click, _after: after };
})(typeof window !== 'undefined' ? window : globalThis);
