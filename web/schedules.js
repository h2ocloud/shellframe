/* Loops panel — the LaunchAgent schedule section.
 *
 * Python decides what a schedule IS (sched_view.py: its kind, the text for its
 * cadence or state in English and Traditional Chinese, its warnings, one-offs
 * grouped); this file only draws the rows it is handed. It holds no state and
 * calls nothing: index.html fetches `schedules_list`, keeps the payload, and asks
 * `SFSched.section(payload, loaded)` for the HTML of the "Schedules" block (and the
 * "Resident" block under it, for KeepAlive jobs), plus how many schedules are on (for
 * the header and the overview line).
 *
 * Words follow the page's language (index.html's `lang`): t(english, chinese).
 * A text that came from Python is {en, zh}; pick() takes the page's language.
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

  // What a member of a group of one-offs says about itself.
  const MEMBER = { ran: ['ran', '已執行'], armed: ['armed', '已排'], unarmed: ['not loaded', '未載入'] };

  function notesHtml(s) {
    return (s.notes || []).map(n =>
      `<div class="sched-note" title="${esc(pick(n.tip))}">⚠ ${esc(pick(n.text))}</div>`).join('');
  }

  function memberHtml(m, showDate) {
    const when = (showDate && m.dates && m.dates.length ? m.dates.join(', ') + ' ' : '') + pick(m.times);
    const st = MEMBER[m.state] || ['', ''];
    return `<div class="sched-member st-${esc(m.state)}" title="${esc(m.cmd)}">` +
      `<span class="sched-mt">${esc(when)}</span>` +
      `<span class="sched-mn">${esc(m.title)}</span>` +
      `<span class="sched-ms">${esc(t(st[0], st[1]))}</span></div>`;
  }

  function rowHtml(s, i) {
    const warn = (s.notes || []).some(n => n.level === 'warn');
    const bad = s.state === 'failing';
    const cls = 'sched-row k-' + esc(s.kind) + (s.active ? '' : ' off') + (warn ? ' warn' : '') + (bad ? ' bad' : '') +
      (s.state ? ' st-' + esc(s.state) : '');
    // A daemon that keeps failing already says so in its note; the chip is for the rest.
    const fail = (s.enabled && s.last_exit && !bad)
      ? `<span class="sched-fail" title="${esc(t('The last run exited with status ' + s.last_exit,
          '上次執行失敗（exit ' + s.last_exit + '）'))}">${esc(t('last run failed', '上次失敗'))}</span>` : '';
    const running = s.running ? `<span class="sched-run">${esc(t('running', '執行中'))}</span>` : '';
    const members = s.group && s.members
      ? `<div class="sched-members">${s.members.map(m => memberHtml(m, (s.dates || []).length > 1)).join('')}</div>`
      : (s.cmd ? `<div class="sched-cmd" title="${esc(s.cmd)}">${esc(s.cmd)}</div>` : '');
    // A one-off is armed by asking the agent, not by a switch: the switch would
    // load it for whatever date its plist still holds. The gap keeps Edit in line.
    const toggle = s.toggle === false ? '<span class="sched-noswitch"></span>' :
      `<button class="toggle-switch sched-toggle${s.enabled ? ' on' : ''}" data-id="${esc(s.id)}" ` +
      `title="${esc(s.enabled ? t('Click to turn off', '點擊停用') : t('Click to turn on', '點擊啟用'))}"></button>`;
    return `<div class="${cls}" data-id="${esc(s.id)}">` +
      `<span class="sched-num">${i + 1}</span>` +
      `<div class="sched-info">` +
        `<div class="sched-head">` +
          `<span class="sched-title" title="${esc(s.title)}">${esc(s.title)}</span>` +
          (s.group && s.members ? `<span class="sched-n">×${s.members.length}</span>` : '') +
          `<button class="sched-edit" data-id="${esc(s.id)}" title="${esc(t(
            "Bring this schedule's details into the current conversation, so you can ask your AI to change it",
            '把這個排程的資訊帶進目前對話，讓你直接請 AI 調整'))}">${esc(t('Edit', '編輯'))}</button>` +
          toggle +
        `</div>` +
        `<div class="sched-line"><span class="sched-freq" title="${esc(pick(s.tip))}">${esc(pick(s.when))}</span>` +
          `${running}${fail}</div>` +
        notesHtml(s) +
        (s.detail ? `<div class="sched-detail">${esc(pick(s.detail))}</div>` : '') +
        members +
      `</div>` +
    `</div>`;
  }

  const msg = (html, level) => `<div class="sched-msg${level ? ' ' + level : ''}">${html}</div>`;

  const header = (en, zh, count) => `<div class="loop-group">${esc(t(en, zh))}` +
    (count ? `<span class="loop-group-n">${esc(count)}</span>` : '') + `</div>`;

  // The "Schedules" block, and under it the "Resident" block (KeepAlive jobs). `data`
  // is the parsed `schedules_list` payload; `loaded` is false until the first answer.
  // `on` and `total` count the timed schedules only: a resident job is not on a schedule.
  function section(data, loaded) {
    data = data || {};
    const list = Array.isArray(data.schedules) ? data.schedules : [];
    const timers = list.filter(s => s.kind !== 'daemon');
    const daemons = list.filter(s => s.kind === 'daemon');
    const on = timers.filter(s => s.active).length;
    let body = '';
    let resident = '';
    if (!loaded) {
      body = `<div class="feed-empty">${esc(t('Loading…', '載入中…'))}</div>`;
    } else if (data.unsupported) {
      body = msg(esc(t('This platform is not supported yet: the schedule list reads macOS launchd only.',
                       '此平台尚未支援：排程清單目前只讀 macOS 的 launchd。')), 'warn');
    } else if (data.error) {
      body = msg(esc(t('Could not read the schedules: ', '讀取排程失敗：') + data.error), 'bad');
    } else {
      if (data.config_error) body += msg(esc(data.config_error), 'bad');
      if (data.launchd_ok === false) {
        body += msg(esc(t("Could not ask launchd what is loaded, so the on/off state below may be wrong.",
                          '讀不到 launchd 目前載入了哪些排程，下面的開關狀態可能不準。')), 'warn');
      }
      if (data.configured === false) {
        body += msg(t(
          'Nothing is listed because no LaunchAgent label prefix is set. Add yours (for example "demo.") under ' +
          '"prefixes" in <code>schedules.json</code> in ShellBot\'s config folder, or ask your AI agent to do it ' +
          '(<code>sfctl skill</code> explains the file).',
          '還沒設定要列出哪些 LaunchAgent，所以這裡是空的。請在 ShellBot 設定資料夾的 <code>schedules.json</code> ' +
          '的 "prefixes" 填上你自己的 label 前綴（例如 "demo."），或請 AI 代勞（<code>sfctl skill</code> 有說明這個檔案）。'));
      } else if (!list.length) {
        body += `<div class="feed-empty">${esc(t('No schedules found', '沒有偵測到排程'))}</div>`;
      }
      body += timers.map(rowHtml).join('');
      if (daemons.length) {
        const up = daemons.filter(s => s.running).length;
        resident = header('Resident', '常駐', `${up}/${daemons.length} ${t('running', '執行中')}`) +
          daemons.map(rowHtml).join('');
      }
    }
    // Only resident jobs and nothing to say about timers: no empty "Schedules" header.
    const quiet = loaded && !body && daemons.length && !timers.length;
    const head = quiet ? '' : header('Schedules', '排程',
      loaded && timers.length ? `${on}/${timers.length} ${t('on', '開')}` : '');
    return { html: head + body + resident, on, total: timers.length };
  }

  window.SFSched = { section, rowHtml };
})();
