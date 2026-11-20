/* The office's ops board: the TV on the lobby wall and its detail window (office_ops.py is the other half).
 *
 * Loaded by office.html (the iframe), after office_objects.js. Nothing here samples anything: the backend's object `ops` says
 * (in the office objects' summary) its level, reason and a few numbers for the TV's face, and `office_object_view` answers the
 * detail window's sections and widgets, already in the page's language and masked. This file draws them.
 *
 *   OfficeOps.create(env) -> ctl      env: { tr, doc, host, kick, beforeOpen, changed, rm, now }
 *     host: { view(id) -> {success, refresh_ms, title, banner, sections}, live(id, on), action(id, itemId, actionId, args) }
 *   ctl.setObject(o)        the `ops` declaration with its summary fields (level, reason, since, muted_until, updated, face), or null
 *   ctl.active()            the TV and the button exist: the switch is on and the registry has an `ops` object
 *   ctl.draw(x, g)          the TV on the wall: g = { P, poly, wq, ellipse, COL, FONT, alpha, T, q } (office.html passes them)
 *   ctl.open() / close() / toggle() / esc() / isOpen()
 *   ctl.timing              { hb, minRefresh }  the heartbeat (30 s) and the shortest refresh the window accepts; a test shortens them
 *   ctl.drawPeer(x, g, o)   a paired computer's small TV on its room's back wall (g as for draw; o = what the page kept of its
 *                           ops_peer answer: {state, level, reason, face, updated, ...}); a peer that is off / older / out of
 *                           reach / without data is drawn grey with the reason in words
 *   ctl.openPeer(pid, name) / peerOpen()   the same detail window for that computer: titled with its name, read through
 *                           host.peerView(pid, sort) and kept live with host.peerLive(pid, on); the rules are shown, not
 *                           changeable (and say where to change them), there is no mute, the top five's order can be switched
 *
 * The detail window draws eight kinds of widget (gauge, bars, stack, spark, strip, table, stat, rules); a kind it does not know is
 * left out, a missing field is a blank, never an error. While it is open the backend is told so (live on, a heartbeat every 30 s,
 * live off at close and when the page goes away); a refresh patches only the sections that changed and never one whose field has the
 * focus, so a number being typed is not lost.
 */
(function (root) {
  'use strict';

  const LEVELS = new Set(['ok', 'warn', 'alert', 'none', 'muted']);
  const TONES = new Set(['', 'ok', 'warn', 'bad', 'info', 'dim']);
  const CELLS = new Set(['ok', 'warn', 'alert', 'none']);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = v => (typeof v === 'number' && isFinite(v) ? v : null);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const toneOf = t => (TONES.has(t) ? t : '');
  const arr = v => (Array.isArray(v) ? v : []);

  // ── what a number looks like ─────────────────────────────────────────────
  function fmtBytes(b) {
    b = num(b); if (b == null) return '–';
    const G = 1024 ** 3, M = 1024 ** 2;
    return b >= G ? (b / G).toFixed(b >= 10 * G ? 0 : 1) + ' GB' : b >= M ? Math.round(b / M) + ' MB' : b >= 1024 ? Math.round(b / 1024) + ' KB' : Math.round(b) + ' B';
  }
  function fmtRate(b) {
    b = num(b); if (b == null) return '–';
    return b >= 1e9 ? (b / 1e9).toFixed(1) + ' GB/s' : b >= 1e6 ? (b / 1e6).toFixed(1) + ' MB/s' : b >= 1e3 ? Math.round(b / 1e3) + ' KB/s' : Math.round(b) + ' B/s';
  }
  const fmtNum = v => { v = num(v); return v == null ? '–' : Math.abs(v) >= 100 ? String(Math.round(v)) : String(Math.round(v * 10) / 10); };
  const hhmm = sec => { const d = new Date(sec * 1000), p = n => String(n).padStart(2, '0'); return p(d.getHours()) + ':' + p(d.getMinutes()); };
  const mdhm = sec => { const d = new Date(sec * 1000); return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + hhmm(sec); };

  // ── the TV's face (design size FW × FH, drawn into the wall's quad by an affine transform) ──
  const FW = 300, FH = 127;
  const PAL = {
    ok: { bg: '#0d121c', line: 'rgba(192,202,245,.28)', frame: '#262a3c' },
    warn: { bg: '#15130f', line: 'rgba(224,175,104,.7)', frame: '#2f2a22' },
    alert: { bg: '#1a0d12', line: 'rgba(247,118,142,.9)', frame: '#3a1a22' },
    muted: { bg: '#12141c', line: 'rgba(192,202,245,.2)', frame: '#23262f' },
    none: { bg: '#12141c', line: 'rgba(192,202,245,.2)', frame: '#23262f' },
  };
  function tvState(o) {
    const lv = o && LEVELS.has(o.level) ? o.level : 'none', f = o && o.face && typeof o.face === 'object' ? o.face : null;
    return { level: lv, reason: String((o && o.reason) || ''), face: f, updated: num(o && o.updated), muted_until: num(o && o.muted_until), since: num(o && o.since) };
  }
  function fitText(x, s, maxW, px, weight, FONT, min) {              // the font shrinks to fit, then the text is cut with an ellipsis
    let p = px; s = String(s);
    x.font = `${weight} ${p}px ${FONT}`;
    while (p > (min || 11) && x.measureText(s).width > maxW) { p -= 1; x.font = `${weight} ${p}px ${FONT}`; }
    if (x.measureText(s).width > maxW) { while (s.length > 1 && x.measureText(s + '…').width > maxW) s = s.slice(0, -1); s += '…'; }
    return s;
  }
  function ring(x, g, cx, cy, r, pct, col, label, dim) {
    const t0 = .75 * Math.PI, t1 = 2.25 * Math.PI, v = pct == null ? 0 : clamp(pct, 0, 100) / 100;
    x.lineWidth = 7; x.lineCap = 'round'; x.strokeStyle = 'rgba(192,202,245,.14)'; x.beginPath(); x.arc(cx, cy, r, t0, t1); x.stroke();
    if (pct != null) { x.strokeStyle = dim ? '#6b7399' : col; x.beginPath(); x.arc(cx, cy, r, t0, t0 + (t1 - t0) * Math.max(v, .015)); x.stroke(); }
    x.fillStyle = dim ? '#9aa5ce' : '#e8ecff'; x.font = `800 15px ${g.FONT}`; x.textAlign = 'center'; x.textBaseline = 'middle';
    x.fillText(pct == null ? '–' : Math.round(pct) + '%', cx, cy + 1);
    x.fillStyle = 'rgba(192,202,245,.78)'; x.font = `600 12px ${g.FONT}`; x.fillText(label, cx, cy + r + 14);
  }
  function triangle(x, cx, cy, s, col) {                              // the warning sign, drawn (no glyph)
    x.fillStyle = col; x.beginPath(); x.moveTo(cx, cy - s); x.lineTo(cx + s * 1.1, cy + s * .8); x.lineTo(cx - s * 1.1, cy + s * .8); x.closePath(); x.fill();
    x.fillStyle = '#1a0d12'; x.fillRect(cx - s * .1, cy - s * .35, s * .2, s * .6); x.fillRect(cx - s * .1, cy + s * .42, s * .2, s * .2);
  }
  // The temperatures on the face: "CPU 46° · GPU 43°". A reading that is missing is left out; with neither the line is not drawn. A number over its
  // line (the rule's number: 90 °C for the CPU and 85 °C for the GPU unless the person changed it) is drawn in the warning colour. `align`: 'l' from X, 'r' to X, 'c' around X.
  const TEMP_LINE = { cpu: 90, gpu: 85 };                              // the rules' defaults, for a face that does not carry its own lines (an older computer)
  function tempParts(f) {
    const out = [];
    if (!f) return out;
    const c = num(f.cpu_temp), g = num(f.gpu_temp), cl = num(f.cpu_temp_line) == null ? TEMP_LINE.cpu : f.cpu_temp_line, gl = num(f.gpu_temp_line) == null ? TEMP_LINE.gpu : f.gpu_temp_line;
    if (c != null) out.push({ k: 'CPU', v: Math.round(c) + '°', hot: c > cl });         // over the line the person set in the rule
    if (g != null) out.push({ k: 'GPU', v: Math.round(g) + '°', hot: g > gl });
    return out;
  }
  function drawTemps(x, parts, X, Y, px, maxW, align, FONT, dim) {
    if (!parts.length) return;
    let p = px;
    const w = () => { x.font = `700 ${p}px ${FONT}`; return parts.reduce((a, q, i) => a + x.measureText(q.k + ' ' + q.v).width + (i ? x.measureText(' · ').width : 0), 0); };
    while (p > 8 && w() > maxW) p -= 1;
    let cx = align === 'r' ? X - w() : align === 'c' ? X - w() / 2 : X;
    x.textAlign = 'left'; x.textBaseline = 'alphabetic';
    parts.forEach((q, i) => {
      if (i) { x.fillStyle = 'rgba(192,202,245,.4)'; x.fillText(' · ', cx, Y); cx += x.measureText(' · ').width; }
      x.fillStyle = dim ? '#8d94b8' : 'rgba(192,202,245,.62)'; x.fillText(q.k + ' ', cx, Y); cx += x.measureText(q.k + ' ').width;
      x.fillStyle = q.hot ? '#ff6b86' : dim ? '#9aa5ce' : '#e8ecff'; x.fillText(q.v, cx, Y); cx += x.measureText(q.v).width;
    });
  }
  function drawTempStack(x, parts, X, Y0, Y1, px, maxW, FONT, dim) {       // one reading to a line, in the compact small TV's fourth column
    if (!parts.length) return;
    x.textAlign = 'left'; x.textBaseline = 'middle';
    parts.forEach((q, i) => {
      const cy = Y0 + (Y1 - Y0) * (i + .5) / parts.length, lab = q.k + ' ';
      let p = px; x.font = `700 ${p}px ${FONT}`;
      while (p > 8 && x.measureText(lab + q.v).width > maxW) { p -= 1; x.font = `700 ${p}px ${FONT}`; }
      x.fillStyle = dim ? '#8d94b8' : 'rgba(192,202,245,.62)'; x.fillText(lab, X, cy);
      x.fillStyle = q.hot ? '#ff6b86' : dim ? '#9aa5ce' : '#e8ecff'; x.fillText(q.v, X + x.measureText(lab).width, cy);
    });
  }
  function ageWords(tr, now, updated) {
    if (updated == null) return '';
    const m = Math.max(0, Math.floor((now - updated) / 60));
    return m < 1 ? tr('Updated just now', '剛剛更新') : m < 60 ? tr(`Updated ${m} min ago`, `${m} 分鐘前更新`) : tr(`Updated ${Math.floor(m / 60)} hr ago`, `${Math.floor(m / 60)} 小時前更新`);
  }
  // The face in design coordinates, FW wide and H high (H follows the quad's own shape: the 3D wall is about 127, the flat view's much lower).
  // st: tvState; g: { FONT, T, rm, tr, now }
  function drawFace(x, st, g, H) {
    const tr = g.tr, FONT = g.FONT, lv = st.level, pal = PAL[lv], pulse = g.rm ? 1 : .6 + .4 * Math.sin(g.T * 3.2), f = st.face;
    x.fillStyle = pal.bg; x.fillRect(0, 0, FW, H);
    x.textBaseline = 'middle';
    if (lv === 'none') {
      x.fillStyle = '#8d94b8'; x.textAlign = 'center'; x.font = `700 ${Math.min(22, H * .3)}px ${FONT}`; x.fillText(tr('No data', '沒有資料'), FW / 2, H * .42);
      x.fillStyle = 'rgba(192,202,245,.5)'; x.font = `500 ${Math.min(13, H * .17)}px ${FONT}`; x.fillText(tr('Ops is not measured here', '這台電腦沒有在量維運'), FW / 2, H * .68);
      return;
    }
    const bad = lv === 'alert', compact = H < 80;
    const reason = st.reason || (bad ? tr('Something is wrong', '出了問題') : tr('Needs a look', '需要留意'));
    if (compact && (bad || lv === 'warn')) {                         // the flat view: a short, wide screen: the banner is all of it
      x.fillStyle = bad ? `rgba(224,60,84,${.82 + .18 * pulse})` : 'rgba(224,175,104,.94)'; x.fillRect(0, 0, FW, H);
      triangle(x, 20, H / 2, H * .24, bad ? '#fff' : '#2b2113');
      x.fillStyle = bad ? '#fff' : '#2b2113'; x.textAlign = 'left';
      x.fillText(fitText(x, reason, FW - 50, H * .5, 800, FONT, 11), 40, H / 2 + 1);
      return;
    }
    let y0;
    if (bad || lv === 'warn') {
      const h = bad ? 46 : 40;
      x.fillStyle = bad ? `rgba(224,60,84,${.82 + .18 * pulse})` : 'rgba(224,175,104,.94)'; x.fillRect(0, 0, FW, h);
      triangle(x, 19, h / 2, bad ? 9 : 8, bad ? '#fff' : '#2b2113');
      x.fillStyle = bad ? '#fff' : '#2b2113'; x.textAlign = 'left';
      x.fillText(fitText(x, reason, FW - 44, bad ? 24 : 20, 800, FONT, 13), 36, h / 2 + 1); y0 = h;
    } else {
      const muted = lv === 'muted', hh = compact ? H * .4 : 24;
      x.fillStyle = muted ? '#6b7399' : '#9ece6a'; x.beginPath(); x.arc(15, hh / 2, compact ? hh * .2 : 5, 0, 6.3); x.fill();
      x.fillStyle = '#d7defa'; x.font = `700 ${compact ? hh * .5 : 15}px ${FONT}`; x.textAlign = 'left'; x.fillText(tr('Ops', '維運'), 27, hh / 2);
      x.fillStyle = muted ? '#e0af68' : 'rgba(192,202,245,.62)'; x.font = `600 ${compact ? hh * .42 : 13}px ${FONT}`; x.textAlign = 'right';
      x.fillText(muted ? (st.muted_until ? tr('Muted until ', '靜音到 ') + hhmm(st.muted_until) : tr('Muted', '已靜音')) : tr('All normal', '一切正常'), FW - 10, hh / 2);
      if (compact) drawTemps(x, tempParts(f), 138, hh / 2 + hh * .15, hh * .42, 110, 'c', FONT, muted);      // the short screen has no foot: the temperatures sit in the header
      y0 = hh;
    }
    const dim = lv === 'muted';
    const cpu = f ? num(f.cpu) : null, mem = f ? num(f.mem) : null, pr = f ? f.pressure : null;
    const cpuCol = cpu != null && cpu > 90 ? '#ff6b86' : cpu != null && cpu > 70 ? '#e0af68' : '#7aa2f7';
    const memCol = pr === 'critical' || (mem != null && mem > 92) ? '#ff6b86' : pr === 'warn' || (mem != null && mem > 80) ? '#e0af68' : '#9ece6a';
    const net = !f ? '–' : f.online === false ? tr('Offline', '離線') : '↓ ' + fmtRate(f.net_in);
    if (compact) {                                                   // three small readings in a row, each with a bar under it
      const fs = Math.max(9, (H - y0) * .34), by = H - (H - y0) * .2;
      [[tr('CPU', 'CPU') + ' ' + (cpu == null ? '–' : Math.round(cpu) + '%'), cpu, cpuCol, 12], [tr('Memory', '記憶體') + ' ' + (mem == null ? '–' : Math.round(mem) + '%'), mem, memCol, 112], [net, null, '#7dcfff', 212]].forEach(([txt, v, col, X]) => {
        x.fillStyle = dim ? '#9aa5ce' : '#e8ecff'; x.font = `700 ${fs}px ${FONT}`; x.textAlign = 'left'; x.textBaseline = 'middle';
        x.fillText(fitText(x, txt, 88, fs, 700, FONT, 9), X, y0 + (H - y0) * .38);
        x.fillStyle = 'rgba(192,202,245,.14)'; x.fillRect(X, by, 80, 3);
        if (v != null) { x.fillStyle = dim ? '#6b7399' : col; x.fillRect(X, by, 80 * clamp(v, 0, 100) / 100, 3); }
      });
      return;
    }
    // The body between the header and the foot: two rings with their labels, and under the memory ring its last readings when there is room (not under a banner)
    const sp = f ? arr(f.spark).filter(v => num(v) != null).slice(-12) : [], avail = H - 27 - y0, withSpark = sp.length > 1 && avail >= 75;
    const r = withSpark ? 20 : Math.min(20, (avail - 20) / 2), cy = y0 + 4 + (avail - (2 * r + (withSpark ? 31 : 20))) / 2 + r;
    ring(x, g, 48, cy, r, cpu, cpuCol, 'CPU', dim);
    ring(x, g, 140, cy, r, mem, memCol, tr('Memory', '記憶體'), dim);
    if (withSpark) {
      x.strokeStyle = dim ? '#6b7399' : memCol; x.lineWidth = 2; x.lineJoin = 'round'; x.beginPath();
      sp.forEach((v, i) => { const px = 110 + i * 60 / (sp.length - 1), py = cy + r + 31 - clamp(v, 0, 100) / 100 * 8; i ? x.lineTo(px, py) : x.moveTo(px, py); }); x.stroke();
    }
    // the network: the two rates as numbers (a line of the memory would be a lie), a red word when it is offline
    const nx = 196;
    x.textAlign = 'left';
    if (f && f.online === false) {
      x.fillStyle = '#ff6b86'; x.font = `800 16px ${FONT}`; x.fillText(tr('Offline', '離線'), nx, cy - 6);
    } else {
      x.fillStyle = dim ? '#9aa5ce' : '#e8ecff'; x.font = `700 14px ${FONT}`; x.fillText('↓ ' + (f ? fmtRate(f.net_in) : '–'), nx, cy - 9);
      x.fillStyle = dim ? '#8d94b8' : 'rgba(232,236,255,.8)'; x.font = `600 13px ${FONT}`; x.fillText('↑ ' + (f ? fmtRate(f.net_out) : '–'), nx, cy + 9);
    }
    x.fillStyle = 'rgba(192,202,245,.78)'; x.font = `600 12px ${FONT}`; x.fillText(tr('Network', '網路'), nx, cy + r + 14);
    // the foot, all at the left: the lower right of the 3D wall is behind the filing cabinet
    const disk = f ? num(f.disk_free_pct) : null, age = bad ? tr('Click for details', '點一下看詳情') : ageWords(tr, g.now, st.updated);
    x.fillStyle = 'rgba(192,202,245,.55)'; x.font = `500 12px ${FONT}`; x.textBaseline = 'alphabetic'; x.textAlign = 'left';
    x.fillText(fitText(x, [disk == null ? '' : tr(`Disk ${Math.round(disk)}% free`, `磁碟剩 ${Math.round(disk)}%`), age].filter(Boolean).join('  ·  '), 176, 12, 500, FONT, 9), 10, H - 5);
    drawTemps(x, tempParts(f), 10, bad || lv === 'warn' ? H - 16 : H - 19, bad || lv === 'warn' ? 11 : 12, 98, 'l', FONT, dim);       // a line above the foot, at the left (under the rings, clear of the memory's last readings)
  }
  // ── a paired computer's small TV: the same colours, three bars and the disk line; grey with the reason in words when there is nothing to show ──
  const GRAY = { off: ['Ops is off there', '那台沒打開維運戰情'], old: ['Older version there', '那台版本較舊'], offline: ['Cannot reach it', '連不上'],
    none: ['No data yet', '還沒有資料'], denied: ['Not shared with you', '那台沒有開放給這台看'], pending: ['Reading…', '讀取中…'] };
  const PEER_STATES = new Set(['ok', 'off', 'old', 'offline', 'none', 'denied', 'error', 'pending']);
  function peerNorm(o) {
    const raw = o && typeof o === 'object' ? o : null;
    let state = raw ? (PEER_STATES.has(raw.state) ? raw.state : 'error') : 'pending';
    const lv = raw && raw.level === 'off' ? 'off' : raw && LEVELS.has(raw.level) ? raw.level : 'none';
    if (state === 'ok' && (lv === 'off' || lv === 'none')) state = lv;
    if (state === 'error') state = 'offline';
    const t = tvState(state === 'ok' ? raw : null);
    return Object.assign(t, { mini: true, gray: state === 'ok' ? '' : state, level: state === 'ok' ? t.level : 'none' });
  }
  function drawMini(x, st, g, H) {
    const tr = g.tr, FONT = g.FONT, lv = st.level, pal = PAL[lv], f = st.face, bad = lv === 'alert', warn = lv === 'warn', dim = lv === 'muted';
    const pulse = g.rm ? 1 : .6 + .4 * Math.sin(g.T * 3.2), compact = H < 80, hh = Math.round(H * (bad || warn ? .3 : .24));
    x.fillStyle = pal.bg; x.fillRect(0, 0, FW, H); x.textBaseline = 'middle'; x.textAlign = 'left';
    if (st.gray) {
      x.fillStyle = '#6b7399'; x.beginPath(); x.arc(15, hh / 2 + 2, 5, 0, 6.3); x.fill();
      x.fillStyle = '#9aa5ce'; x.font = `700 ${Math.round(H * .15)}px ${FONT}`; x.fillText(tr('Ops', '維運'), 28, hh / 2 + 2);
      const w = GRAY[st.gray] || GRAY.none; x.fillStyle = '#c0caf5'; x.textAlign = 'center';
      x.fillText(fitText(x, tr(w[0], w[1]), FW - 24, Math.round(H * .24), 800, FONT, 12), FW / 2, H * (compact ? .6 : .62));
      return;
    }
    const reason = st.reason || (bad ? tr('Something is wrong', '出了問題') : tr('Needs a look', '需要留意'));
    if (compact && (bad || warn)) {                                   // the flat view's low, wide screen: the banner is all of it
      x.fillStyle = bad ? `rgba(224,60,84,${.82 + .18 * pulse})` : 'rgba(224,175,104,.94)'; x.fillRect(0, 0, FW, H);
      triangle(x, 20, H / 2, H * .24, bad ? '#fff' : '#2b2113');
      x.fillStyle = bad ? '#fff' : '#2b2113';
      x.fillText(fitText(x, reason, FW - 50, H * .5, 800, FONT, 11), 40, H / 2 + 1);
      return;
    }
    const cpu = f ? num(f.cpu) : null, mem = f ? num(f.mem) : null, pr = f ? f.pressure : null, ni = f ? num(f.net_in) : null, off = !!(f && f.online === false);
    const cpuCol = cpu != null && cpu > 90 ? '#ff6b86' : cpu != null && cpu > 70 ? '#e0af68' : '#7aa2f7';
    const memCol = pr === 'critical' || (mem != null && mem > 92) ? '#ff6b86' : pr === 'warn' || (mem != null && mem > 80) ? '#e0af68' : '#9ece6a';
    const rows = [[tr('CPU', 'CPU'), cpu, cpuCol, cpu == null ? '–' : Math.round(cpu) + '%'],
      [tr('Memory', '記憶體'), mem, memCol, mem == null ? '–' : Math.round(mem) + '%'],
      [tr('Network', '網路'), ni == null ? null : clamp(Math.log10(1 + ni) / 8, 0, 1) * 100, off ? '#ff6b86' : '#7dcfff', off ? tr('Offline', '離線') : '↓ ' + fmtRate(ni)]];
    if (compact) {                                                    // three readings in a row, each with a bar under it
      const tp = tempParts(f), step = tp.length ? 68 : 100, fs = Math.max(10, H * .3) * (tp.length ? .8 : 1), by = H - H * .22;     // with temperatures the three narrow and a fourth column takes them
      rows.forEach(([lab, v, col, txt], i) => {
        const X = 10 + i * step;
        x.textAlign = 'left'; x.textBaseline = 'middle'; x.fillStyle = dim ? '#9aa5ce' : '#e8ecff'; x.font = `700 ${fs}px ${FONT}`;
        x.fillText(fitText(x, i < 2 ? lab + ' ' + txt : txt, step - 10, fs, 700, FONT, 8), X, H * .36);
        x.fillStyle = 'rgba(192,202,245,.14)'; x.fillRect(X, by, step - 14, 3);
        if (v != null) { x.fillStyle = dim ? '#6b7399' : col; x.fillRect(X, by, (step - 14) * clamp(v, 0, 100) / 100, 3); }
      });
      drawTempStack(x, tp, 10 + 3 * step + 4, H * .12, H * .78, Math.max(9, H * .24), FW - 10 - (10 + 3 * step + 4), FONT, dim);
      return;
    }
    if (bad || warn) {
      x.fillStyle = bad ? `rgba(224,60,84,${.82 + .18 * pulse})` : 'rgba(224,175,104,.94)'; x.fillRect(0, 0, FW, hh);
      triangle(x, 19, hh / 2, hh * .26, bad ? '#fff' : '#2b2113');
      x.fillStyle = bad ? '#fff' : '#2b2113'; x.fillText(fitText(x, reason, FW - 46, Math.round(hh * .56), 800, FONT, 12), 36, hh / 2 + 1);
    } else {
      x.fillStyle = dim ? '#6b7399' : '#9ece6a'; x.beginPath(); x.arc(15, hh / 2, 5, 0, 6.3); x.fill();
      x.fillStyle = '#d7defa'; x.font = `700 ${Math.round(hh * .62)}px ${FONT}`; x.fillText(tr('Ops', '維運'), 28, hh / 2 + 1);
      x.fillStyle = dim ? '#e0af68' : 'rgba(192,202,245,.7)'; x.font = `600 ${Math.round(hh * .5)}px ${FONT}`; x.textAlign = 'right';
      x.fillText(dim ? tr('Muted', '已靜音') : tr('All normal', '一切正常'), FW - 10, hh / 2 + 1);
    }
    const top = hh + 7, rh = (H - hh - 7 - 20) / 3, fs = Math.max(11, Math.round(rh * .62));
    rows.forEach(([lab, v, col, txt], i) => {
      const cy = top + rh * (i + .5);
      x.textAlign = 'left'; x.fillStyle = dim ? '#8d94b8' : 'rgba(232,236,255,.85)'; x.font = `700 ${fs}px ${FONT}`; x.fillText(fitText(x, lab, 68, fs, 700, FONT, 10), 10, cy);
      x.fillStyle = 'rgba(192,202,245,.14)'; x.fillRect(82, cy - 4, 124, 8);
      if (v != null) { x.fillStyle = dim ? '#6b7399' : col; x.fillRect(82, cy - 4, 124 * clamp(v, 0, 100) / 100, 8); }
      x.textAlign = 'right'; x.fillStyle = dim ? '#9aa5ce' : '#e8ecff'; x.font = `800 ${fs}px ${FONT}`; x.fillText(fitText(x, txt, 86, fs, 800, FONT, 10), FW - 8, cy);
    });
    const disk = f ? num(f.disk_free_pct) : null;
    x.textAlign = 'left'; x.textBaseline = 'alphabetic'; x.fillStyle = 'rgba(192,202,245,.62)'; x.font = `600 ${Math.round(H * .11)}px ${FONT}`;
    x.fillText(disk == null ? '' : tr(`Disk ${Math.round(disk)}% free`, `磁碟剩 ${Math.round(disk)}%`), 10, H - 5);
    drawTemps(x, tempParts(f), FW - 8, H - 5, Math.round(H * .11), 150, 'r', FONT, dim);
  }
  // The TV on the wall: the glow (an alert only, and only pulsing when motion is allowed), the frame, the face, the red mark.
  function draw(x, g, st) {
    const q = g.q, bad = st.level === 'alert', pal = PAL[st.level], pulse = g.rm ? 1 : .7 + .3 * Math.sin(g.T * 3.2);
    const O = g.P(q[0], q[1], q[7]), ex = g.P(q[0] + 1, q[1], q[7]), ez = g.P(q[0], q[1], q[7] - 1), sx = (q[2] - q[0]) / FW;
    const wLen = Math.hypot(ex.x - O.x, ex.y - O.y) * (q[2] - q[0]), hLen = Math.hypot(ez.x - O.x, ez.y - O.y) * (q[7] - q[6]);
    const fh = clamp(FW * hLen / Math.max(1, wLen), 40, FH), sz = (q[7] - q[6]) / fh;      // the face takes the quad's own shape: the flat view's screen is low and wide
    if (bad) {
      const c = g.P((q[0] + q[2]) / 2, q[1], (q[6] + q[7]) / 2), ro = st.mini ? 44 : 82, gr = x.createRadialGradient(c.x, c.y, 4, c.x, c.y, ro);
      gr.addColorStop(0, `rgba(247,90,120,${.4 * pulse})`); gr.addColorStop(1, 'rgba(247,90,120,0)');
      x.fillStyle = gr; x.fillRect(c.x - ro - 14, c.y - ro + 6, 2 * ro + 28, 2 * ro - 12);
    }
    g.poly(x, g.wq(q[0] - .07, q[1], q[2] + .07, q[3], 0, 1, q[6] - 1.6, q[7] + 1.6), pal.frame, bad ? g.alpha(g.COL.red, .9) : pal.line, bad ? 1.6 : 1);
    const quad = g.wq(q[0], q[1], q[2], q[3], 0, 1, q[6], q[7]);
    x.save(); x.beginPath(); x.moveTo(quad[0].x, quad[0].y); for (const p of quad.slice(1)) x.lineTo(p.x, p.y); x.closePath(); x.clip();
    x.transform((ex.x - O.x) * sx, (ex.y - O.y) * sx, (ez.x - O.x) * sz, (ez.y - O.y) * sz, O.x, O.y);
    try { (st.mini ? drawMini : drawFace)(x, st, g, fh); } finally { x.restore(); }
    g.poly(x, g.wq(q[0], q[1], q[2], q[3], 0, 1, q[7] - 6, q[7]), 'rgba(255,255,255,.035)');
    if (bad) {
      const b = g.P(q[2], q[1], q[7] + 2); g.ellipse(x, b.x, b.y, 7.4, 7.4, g.COL.red, '#e6e9f5', 1.4);
      x.font = `800 10px ${g.FONT}`; x.fillStyle = '#fff'; x.textAlign = 'center'; x.textBaseline = 'middle'; x.fillText('!', b.x, b.y + .5);
    }
  }

  // ── the detail window's widgets (each returns markup; every string through esc) ──
  function wGauge(w) {
    const v = clamp(num(w.value) == null ? 0 : w.value, 0, 100), R = 34, C = 2 * Math.PI * R, A = C * .75;
    return `<div class="op-gauge op-t-${toneOf(w.tone) || 'info'}"><svg viewBox="0 0 100 100" role="img" aria-label="${esc(w.label)} ${esc(w.text || Math.round(v) + '%')}">` +
      `<circle class="trk" cx="50" cy="50" r="${R}" stroke-dasharray="${A.toFixed(2)} ${C.toFixed(2)}" transform="rotate(135 50 50)"/>` +
      `<circle class="val" cx="50" cy="50" r="${R}" stroke-dasharray="${(A * v / 100).toFixed(2)} ${C.toFixed(2)}" transform="rotate(135 50 50)"/>` +
      `<text x="50" y="52" text-anchor="middle" dominant-baseline="middle">${esc(w.text != null ? w.text : Math.round(v) + '%')}</text></svg>` +
      (w.label ? `<span class="op-cap">${esc(w.label)}</span>` : '') + '</div>';
  }
  function wBars(w) {
    const items = arr(w.items).slice(0, 64), labels = items.length <= 20;
    return (w.label ? `<div class="op-cap">${esc(w.label)}</div>` : '') + `<div class="op-bars${labels ? '' : ' nolab'}">` + items.map(i => {
      const v = clamp(num(i && i.value) == null ? 0 : i.value, 0, 100), g = i && (i.group === 'p' || i.group === 'e') ? i.group : 'x';
      return `<div class="op-bar g-${g}" title="${esc((i && i.label) || '')} ${Math.round(v)}%"><u><i style="height:${Math.max(2, v)}%"></i></u>${labels ? `<span>${esc((i && i.label) || '')}</span>` : ''}</div>`;
    }).join('') + '</div>';
  }
  function wStack(w) {
    const parts = arr(w.parts).filter(p => p && num(p.value) != null && p.value > 0), sum = parts.reduce((a, p) => a + p.value, 0), total = Math.max(num(w.total) || 0, sum) || 1;
    return (w.label ? `<div class="op-cap">${esc(w.label)}</div>` : '') +
      `<div class="op-stack" role="img" aria-label="${esc(w.label || '')}">${parts.map(p => `<i class="op-c-${toneOf(p.tone) || 'x'}" style="flex:${(p.value / total * 100).toFixed(3)} 0 0" title="${esc(p.label)} ${esc(fmtBytes(p.value))}"></i>`).join('')}<i class="rest" style="flex:${(Math.max(0, total - sum) / total * 100).toFixed(3)} 0 0"></i></div>` +
      `<div class="op-legend">${parts.map(p => `<span><i class="op-c-${toneOf(p.tone) || 'x'}"></i>${esc(p.label)} <b>${esc(fmtBytes(p.value))}</b></span>`).join('')}</div>`;
  }
  function wSpark(w) {
    const series = arr(w.series).filter(s => s && Array.isArray(s.values)), all = series.flatMap(s => s.values.filter(v => num(v) != null));
    const max = num(w.max) > 0 ? w.max : Math.max(1, ...all) * 1.1, VW = 300, VH = 64, n = Math.max(2, ...series.map(s => s.values.length));
    const line = s => {
      let d = '', pen = false;
      s.values.forEach((v, i) => { const X = i * VW / (n - 1); if (num(v) == null) { pen = false; return; } const Y = VH - 3 - clamp(v / max, 0, 1) * (VH - 8); d += (pen ? 'L' : 'M') + X.toFixed(1) + ' ' + Y.toFixed(1); pen = true; });
      return d;
    };
    const unit = w.unit ? ' ' + w.unit : '';
    return (w.label ? `<div class="op-cap">${esc(w.label)}</div>` : '') +
      `<svg class="op-spark" viewBox="0 0 ${VW} ${VH}" preserveAspectRatio="none" role="img" aria-label="${esc(w.label || '')}"><path class="gl" d="M0 ${VH - 3}H${VW}M0 ${VH / 2}H${VW}"/>` +
      series.map(s => `<path class="ln op-t-${toneOf(s.tone) || 'info'}" d="${line(s)}"/>`).join('') + '</svg>' +
      `<div class="op-legend">${series.map(s => { const last = [...s.values].reverse().find(v => num(v) != null);
        return `<span><i class="op-c-${toneOf(s.tone) || 'info'}"></i>${esc(s.label || '')} <b>${last == null ? '–' : esc(fmtNum(last) + unit)}</b></span>`; }).join('')}</div>`;
  }
  function wStrip(w, T) {
    const cells = arr(w.cells), start = num(w.start), step = num(w.step_s) || 0;
    const t = i => (start == null ? '' : (step * cells.length > 43200 ? mdhm : hhmm)(start + i * step));
    const word = c => ({ ok: T('Normal', '正常'), warn: T('Warning', '注意'), alert: T('Alert', '異常'), none: T('No data', '沒資料') }[c] || '');
    return (w.label ? `<div class="op-cap">${esc(w.label)}</div>` : '') +
      `<div class="op-strip" style="grid-template-columns:repeat(${Math.max(1, cells.length)},minmax(0,1fr))" role="img" aria-label="${esc(w.label || '')}">${cells.map((c, i) => { const k = CELLS.has(c) ? c : 'none'; return `<i class="c-${k}" title="${esc(t(i))} ${esc(word(k))}"></i>`; }).join('')}</div>` +
      (start != null && cells.length ? `<div class="op-axis"><span>${esc(t(0))}</span><span>${esc(T('Now', '現在'))}</span></div>` : '');
  }
  function wSwitch(sw, T) {
    const opts = arr(sw.options).filter(o => o && typeof o.id === 'string'), act = String(sw.action || ''), cur = String(sw.value == null ? '' : sw.value);
    if (!act || opts.length < 2) return '';
    return `<div class="op-switch"><span class="op-seg" role="group" aria-label="${esc(T('Order', '排序'))}">${opts.map(o => `<button type="button" class="op-segb${String(o.id) === cur ? ' on' : ''}" data-op="switch" data-act="${esc(act)}" data-val="${esc(o.id)}" aria-pressed="${String(o.id) === cur}">${esc(o.label || o.id)}</button>`).join('')}</span>` +
      (sw.note ? `<span class="op-snote">${esc(sw.note)}</span>` : '') + '</div>';
  }
  function wTable(w, T) {
    const cols = arr(w.columns), rows = arr(w.rows).slice(0, 30);
    const cell = (c, j) => { const s = String(c == null ? '' : c), m = j > 0 && /^\s*(\d+(?:\.\d+)?)\s*%\s*$/.exec(s); return m ? `<td><span class="op-pc"><i style="width:${clamp(+m[1], 0, 100)}%"></i></span>${esc(s)}</td>` : `<td>${esc(s)}</td>`; };
    return (w.switch && typeof w.switch === 'object' ? wSwitch(w.switch, T || ((en) => en)) : '') + `<table class="op-table"><thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${arr(r).map(cell).join('')}</tr>`).join('')}</tbody></table>`;
  }
  const wStat = w => `<div class="op-stat op-t-${toneOf(w.tone)}">${w.label ? `<span class="op-cap">${esc(w.label)}</span>` : ''}<b>${esc(w.value)}</b>${w.sub ? `<em>${esc(w.sub)}</em>` : ''}</div>`;
  function wRules(w, T) {
    const items = arr(w.items).filter(i => i && typeof i.id === 'string');
    const lvWord = l => (l === 'alert' ? T('Alert', '異常') : l === 'warn' ? T('Warning', '注意') : ''), ro = w.readonly === true, dis = ro ? ' disabled' : '';
    return (ro ? `<p class="op-ro">${esc(T('Change these on that computer.', '要改請到那台電腦。'))}</p>` : '') + `<div class="op-rules${ro ? ' ro' : ''}">${items.map(i => {
      const hasV = num(i.value) != null, lab = String(i.label || i.id);
      return `<div class="op-rule${i.on ? '' : ' off'}" data-rule="${esc(i.id)}">` +
        `<button type="button" class="op-sw${i.on ? ' on' : ''}" role="switch" aria-checked="${!!i.on}"${dis} data-op="rule-on" aria-label="${esc(lab)}"></button>` +
        `<span class="op-rl">${esc(lab)}</span>` +
        (hasV ? `<span class="op-rv"><input type="number" class="op-num" data-op="rule-val"${dis} value="${esc(i.value)}"${num(i.min) != null ? ` min="${esc(i.min)}"` : ''}${num(i.max) != null ? ` max="${esc(i.max)}"` : ''}${num(i.step) != null ? ` step="${esc(i.step)}"` : ''} aria-label="${esc(lab)}${i.unit ? ' (' + esc(i.unit) + ')' : ''}">${i.unit ? `<em>${esc(i.unit)}</em>` : ''}</span>` : '') +
        (lvWord(i.level) ? `<span class="op-lv op-t-${i.level === 'alert' ? 'bad' : 'warn'}">${esc(lvWord(i.level))}</span>` : '') + '</div>';
    }).join('')}</div>` + (ro ? '' : `<button type="button" class="oo-btn op-reset" data-op="reset">${esc(T('Back to the defaults', '回到預設規則'))}</button>`);
  }
  const KINDS = { gauge: wGauge, bars: wBars, stack: wStack, spark: wSpark, strip: wStrip, table: wTable, stat: wStat, rules: wRules };
  const widgetHtml = (w, T) => (w && typeof w === 'object' && Object.prototype.hasOwnProperty.call(KINDS, w.kind) ? `<div class="op-w op-w-${w.kind}">${KINDS[w.kind](w, T)}</div>` : '');

  // ── the controller ───────────────────────────────────────────────────────
  function create(env) {
    const E = env || {}, tr = E.tr || (en => en), doc = E.doc || document, host = E.host || null, kick = E.kick || (() => {});
    const nowS = E.now || (() => Date.now() / 1000), ICON = root.SFIcons ? n => root.SFIcons.html(n) : () => '';
    const timing = { hb: 30000, minRefresh: 500 };
    let obj = null, veil = null, S = null, msgTimer = 0;
    const ID = 'ops';
    const $ = sel => veil.querySelector(sel);
    const call = (fn, ...a) => { try { const r = fn && fn(...a); return r && r.then ? r.catch(() => null) : Promise.resolve(r); } catch (_) { return Promise.resolve(null); } };

    function paintButton() {
      const b = doc.getElementById('opsBtn');
      if (!b) return;
      b.hidden = !obj;
      const alert = !!obj && obj.level === 'alert';
      if (alert) b.dataset.alert = '1'; else delete b.dataset.alert;
      b.classList.toggle('on', !!S);
    }
    function setObject(o) {
      obj = o && o.id === ID ? o : null;
      paintButton();
      if (S && !obj) close();
      else if (S && S.view) { S.bsig = ''; paint(S.view); }       // the banner's buttons follow the object (muted or not) at once
      kick();
    }
    const active = () => !!obj;
    const state = () => { const s = tvState(obj); return { active: !!obj, level: s.level, reason: s.reason, open: !!S, hasFace: !!s.face }; };

    // ── the window ──
    function ensureVeil() {
      if (veil) return veil;
      veil = doc.createElement('div');
      veil.id = 'opspop'; veil.className = 'oo-veil op-veil'; veil.hidden = true;
      veil.innerHTML =
        '<section class="oo-box op-box" role="dialog" aria-modal="true" aria-labelledby="opTitle">' +
        `<header class="oo-head"><span class="oo-ic" aria-hidden="true">${ICON('computer')}</span><div class="oo-t"><h2 id="opTitle"></h2><p id="opSub"></p></div>` +
        '<span class="op-live" id="opLive"><i></i><span></span></span>' +
        `<button class="oo-x" type="button" id="opClose" title="${esc(tr('Close (Esc)', '關起來（Esc）'))}" aria-label="${esc(tr('Close', '關起來'))}">${ICON('close')}</button></header>` +
        '<div class="op-body" id="opBody"><div class="op-inner" id="opInner"></div></div>' +
        '<footer class="oo-foot"><span class="oo-note" id="opNote"></span><span class="oo-msg" id="opMsg" role="status" aria-live="polite"></span></footer></section>';
      doc.body.appendChild(veil);
      veil.addEventListener('click', onClick);
      veil.addEventListener('change', onChange);
      veil.addEventListener('keydown', onKey);
      return veil;
    }
    // The window is this computer's, or (`target` = {pid, name}) a paired computer's: the same shell, read through the peer calls.
    const liveCall = (tg, on) => call(tg ? host && host.peerLive : host && host.live, ...(tg ? [tg.pid, on] : [ID, on]));
    function open(target) {
      if (!obj) return false;
      const tg = target && target.pid ? { pid: String(target.pid), name: String(target.name || target.pid) } : null;
      if (S && (S.target ? S.target.pid : '') === (tg ? tg.pid : '')) return true;
      if (S) close();
      if (E.beforeOpen) E.beforeOpen();
      ensureVeil();
      S = { seq: 0, view: null, sigs: [], bsig: '', grid: '', hb: 0, rt: 0, ok: true, ms: 2000, busy: false, target: tg, sort: '' };
      veil.hidden = false;
      $('#opTitle').textContent = tg ? tr('Ops board · ', '維運戰情 · ') + tg.name : tr('Ops board', '維運戰情');
      $('#opSub').textContent = tg ? tr('A paired computer (read only)', '隔壁電腦（唯讀）') : tr('This computer', '這台電腦');
      $('#opInner').innerHTML = `<div class="op-hint">${esc(tr('Reading…', '讀取中…'))}</div>`;
      $('#opMsg').textContent = '';
      paintLive(); paintNote(); paintButton();
      liveCall(tg, true);
      S.hb = setInterval(() => { if (!doc.hidden) liveCall(tg, true); }, timing.hb);
      doc.addEventListener('visibilitychange', onVisible);
      root.addEventListener('pagehide', onHide);
      refresh();
      kick();
      setTimeout(() => { try { $('#opClose').focus({ preventScroll: true }); } catch (_) {} }, 0);
      return true;
    }
    function close() {
      if (!S) return;
      clearInterval(S.hb); clearTimeout(S.rt); clearTimeout(msgTimer);
      const tg = S.target;
      S = null;
      doc.removeEventListener('visibilitychange', onVisible);
      root.removeEventListener('pagehide', onHide);
      liveCall(tg, false);
      if (veil) { veil.hidden = true; $('#opInner').innerHTML = ''; }
      paintButton(); kick();
      if (E.onClose) E.onClose();
    }
    const toggle = () => (S && !S.target ? close() : open(null));
    const esc2 = () => { if (!S) return false; close(); return true; };
    function onHide() { if (S) liveCall(S.target, false); }
    function onVisible() { if (!S || doc.hidden) return; liveCall(S.target, true); refresh(); }

    function paintLive() {
      const l = $('#opLive'); if (!l) return;
      l.classList.toggle('stale', !S.ok);
      l.querySelector('span').textContent = S.ok ? tr('Live', '即時') : tr('Offline', '連不上');
    }
    function paintNote() {
      const s = Math.max(1, Math.round((S.ms || 2000) / 1000));
      $('#opNote').textContent = S.target ? tr(`Live, every ${s} s. That computer measures live only while this window is open.`, `即時，每 ${s} 秒更新。開著這個視窗，那台才即時量。`)
        : tr(`Live, every ${s} s. It is measured live only while this window is open; otherwise one reading every 5 minutes.`,
          `即時，每 ${s} 秒更新。開著這個視窗才即時量；關掉後每 5 分鐘記一筆。`);
    }
    function say(text, bad) {
      const m = $('#opMsg'); if (!m) return;
      m.textContent = text || ''; m.classList.toggle('bad', !!bad);
      clearTimeout(msgTimer);
      if (text) msgTimer = setTimeout(() => { if (m) m.textContent = ''; }, 6000);
    }
    async function refresh() {
      if (!S) return;
      const my = ++S.seq;
      let r = null;
      const tg = S.target;
      try { r = await (tg ? (host && host.peerView ? host.peerView(tg.pid, S.sort) : null) : (host && host.view ? host.view(ID) : null)); } catch (_) { r = null; }
      if (!S || my !== S.seq) return;
      if (r && r.success) {
        S.ok = true; S.view = r; S.ms = clamp(num(r.refresh_ms) || 2000, timing.minRefresh, 60000);
        paint(r);
      } else {
        S.ok = false;
        if (!S.view) showError(tg && r && r.code && GRAY[r.code] ? tr(GRAY[r.code][0], GRAY[r.code][1]) : (r && r.message) || tr('The ops board is not available right now.', '維運戰情現在打不開。'));
      }
      paintLive(); paintNote();
      schedule();
    }
    function schedule() {                                                 // the next read: the window's own pace, a slow retry after a failure, nothing while the page is hidden
      if (!S) return;
      clearTimeout(S.rt);
      S.rt = setTimeout(() => { if (!S) return; if (doc.hidden) schedule(); else refresh(); }, S.ok ? S.ms : Math.max(timing.minRefresh, 5000));
    }
    function showError(msg) {
      S.view = null; S.grid = ''; S.sigs = []; S.bsig = '';
      $('#opInner').innerHTML = `<div class="op-hint"><b>${esc(msg)}</b><button type="button" class="oo-btn" data-op="retry">${esc(tr('Try again', '再試一次'))}</button></div>`;
    }

    function ruleLabel(v, id) {
      for (const s of arr(v.sections)) for (const w of arr(s && s.widgets)) if (w && w.kind === 'rules') for (const i of arr(w.items)) if (i && i.id === id) return String(i.label || '');
      return '';
    }
    function bannerHtml(v) {
      const b0 = v.banner && typeof v.banner === 'object' ? v.banner : null;
      const peer = !!(S && S.target), o = peer ? null : obj;               // a paired computer's banner says what it says: this computer's own level and mute are not its
      const b = b0 || { level: o && LEVELS.has(o.level) ? o.level : 'none', text: o && o.reason ? o.reason : '', since: null, rule: '' };   // no banner from the backend: the buttons still have to be there
      const raw = LEVELS.has(b.level) ? b.level : 'none', muted = raw === 'muted' || !!(o && (o.level === 'muted' || (num(o.muted_until) != null && o.muted_until > nowS())));
      const lv = muted ? 'muted' : raw;                           // a muted alert is shown quiet: the text still says why (and until when)
      const sub = [num(b.since) != null ? tr(`Since ${hhmm(b.since)}`, `${hhmm(b.since)} 起`) : '',
        b.rule && ruleLabel(v, b.rule) ? tr('Rule: ', '規則：') + ruleLabel(v, b.rule) : ''].filter(Boolean).join('  ·  ');
      const ic = ICON(lv === 'ok' ? 'check-circle' : lv === 'alert' || lv === 'warn' ? 'warn' : 'clock');
      return `<div class="op-banner b-${lv}" role="${lv === 'alert' ? 'alert' : 'status'}"><span class="op-bi" aria-hidden="true">${ic}</span>` +
        `<div class="op-bt"><b>${esc(b.text)}</b>${sub ? `<span>${esc(sub)}</span>` : ''}</div>` +
        `<div class="op-ba">${peer ? '' : raw === 'none' && !muted ? '' : muted ? `<button type="button" class="oo-btn" data-op="unmute">${esc(tr('Unmute', '取消靜音'))}</button>` : `<button type="button" class="oo-btn" data-op="mute">${esc(tr('Mute for 1 hour', '靜音 1 小時'))}</button>`}` +
        (peer ? '' : `<button type="button" class="oo-btn" data-op="rules">${esc(tr('Adjust rules', '調整規則'))}</button>`) + '</div></div>';
    }
    // Patch only what changed: the banner, then each section by its markup. A section with a field in focus is left alone this round.
    function paint(v) {
      const inner = $('#opInner'), secs = arr(v.sections).filter(s => s && typeof s === 'object').map((s, i) => ({ id: String(s.id || 'sec' + i), title: String(s.title || ''), html: arr(s.widgets).map(w => widgetHtml(w, tr)).join('') })).filter(s => s.html);
      const gridSig = secs.map(s => s.id).join('|'), top = $('#opBody').scrollTop;
      if (!inner.querySelector('.op-grid') || gridSig !== S.grid) {
        const n = secs.length;
        inner.innerHTML = '<div class="op-ban" id="opBan"></div><div class="op-grid">' + secs.map((s, i) => {
          const span = i < 3 ? 2 : (n - 3) % 2 === 1 && i === n - 1 ? 6 : 3;
          return `<section class="op-sec op-s${span}" data-sec="${esc(s.id)}"><h3>${esc(s.title)}</h3><div class="op-sb"></div></section>`;
        }).join('') + '</div>';
        S.grid = gridSig; S.sigs = []; S.bsig = '';
      }
      const bh = bannerHtml(v);
      if (bh !== S.bsig) { S.bsig = bh; $('#opBan').innerHTML = bh; }
      const focus = doc.activeElement, force = S.force; S.force = false;
      secs.forEach((s, i) => {
        const el = inner.querySelector(`.op-sec[data-sec="${CSS.escape(s.id)}"] .op-sb`), sig = s.title + '\u0001' + s.html;
        if (!el || (S.sigs[i] === sig && !force)) return;
        const here = focus && el.contains(focus) ? focus : null;
        if (here && !force && here.tagName === 'INPUT' && here.value !== here.defaultValue) return;     // a number typed and not sent yet: left alone
        const keep = here && here.closest('.op-rule') ? [here.closest('.op-rule').dataset.rule, here.dataset.op] : null;
        const keepSeg = here && here.dataset.op === 'switch' ? here.dataset.val : null;
        S.sigs[i] = sig; el.innerHTML = s.html; el.parentNode.querySelector('h3').textContent = s.title;
        if (keep) { const again = [...el.querySelectorAll('.op-rule')].find(r => r.dataset.rule === keep[0]); const c = again && again.querySelector(`[data-op="${keep[1]}"]`); if (c) try { c.focus({ preventScroll: true }); } catch (_) {} }
        if (keepSeg != null) { const c = [...el.querySelectorAll('[data-op="switch"]')].find(x => x.dataset.val === keepSeg); if (c) try { c.focus({ preventScroll: true }); } catch (_) {} }   // the control the person used keeps the focus
      });
      $('#opBody').scrollTop = top;
    }

    // ── events ──
    async function act(aid, args) {
      if (!S || S.busy || !host || !host.action) return null;
      S.busy = true;
      let r = null;
      try { r = await host.action(ID, '', aid, args || {}); } catch (_) { r = null; }
      if (S) S.busy = false;
      if (!S) return r;
      const ok = !!(r && r.success !== false && r.ok !== false);
      say((r && r.message) || (ok ? tr('Done.', '好了。') : tr('That did not work.', '這個沒成功。')), !ok);
      if (E.changed) { try { E.changed(); } catch (_) {} }
      S.force = true;                                             // the sections show what the backend now says (a refused change springs back), whoever has the focus
      refresh();
      return ok;
    }
    function ruleRow(el) { return el.closest('.op-rule'); }
    function sendRule(row) {
      const id = row.dataset.rule, sw = row.querySelector('.op-sw'), inp = row.querySelector('.op-num');
      const args = { id, on: sw.classList.contains('on') };
      if (inp) {
        let v = parseFloat(inp.value);
        if (!isFinite(v)) v = parseFloat(inp.defaultValue);
        const lo = inp.min !== '' ? +inp.min : -Infinity, hi = inp.max !== '' ? +inp.max : Infinity;
        v = clamp(v, lo, hi); inp.value = String(v); args.value = v;
      }
      return act('set_rule', args);
    }
    function onClick(ev) {
      if (!S) return;
      if (ev.target === veil || ev.target.closest('#opClose')) { close(); return; }
      const b = ev.target.closest('[data-op]'); if (!b) return;
      const op = b.dataset.op;
      if (op === 'mute') act('mute', { minutes: 60 });
      else if (op === 'unmute') act('unmute', {});
      else if (op === 'retry') refresh();
      else if (op === 'reset') act('reset_rules', {});
      else if (op === 'rules') {
        const sec = veil.querySelector('.op-sec[data-sec="rules"]') || veil.querySelector('.op-rule');
        if (sec) { sec.scrollIntoView({ block: 'center', behavior: 'smooth' }); sec.classList.remove('flash'); void sec.offsetWidth; sec.classList.add('flash'); const i = sec.querySelector('.op-sw, .op-num'); if (i) try { i.focus({ preventScroll: true }); } catch (_) {} }
      } else if (op === 'switch') {
        if (S.target) { if (b.dataset.val && b.dataset.val !== S.sort) { S.sort = b.dataset.val; S.force = true; refresh(); } }     // a paired computer: its answer in that order, nothing saved there
        else if (b.dataset.act && b.dataset.val) act(b.dataset.act, { by: b.dataset.val });
      } else if (op === 'rule-on') {
        if (S.target || b.disabled) return;
        const row = ruleRow(b); b.classList.toggle('on'); b.setAttribute('aria-checked', String(b.classList.contains('on'))); row.classList.toggle('off', !b.classList.contains('on'));
        sendRule(row);
      }
    }
    function onChange(ev) {
      if (!S || S.target || !ev.target.matches || !ev.target.matches('[data-op="rule-val"]')) return;
      sendRule(ruleRow(ev.target));
    }
    function onKey(ev) {
      if (!S) return;
      if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); close(); return; }
      if (!S.target && ev.key === 'Enter' && ev.target.matches && ev.target.matches('[data-op="rule-val"]') && !ev.isComposing && ev.keyCode !== 229) { ev.preventDefault(); sendRule(ruleRow(ev.target)); }
    }
    const btn = doc.getElementById('opsBtn');
    if (btn) btn.addEventListener('click', () => toggle());

    const peerOpen = () => (S && S.target ? S.target.pid : null);
    const openPeer = (pid, name) => (peerOpen() === String(pid) ? (close(), true) : open({ pid, name }));
    return { setObject, active, state, draw: (x, g) => draw(x, Object.assign({ tr, now: nowS(), rm: !!E.rm }, g), tvState(obj)),
             drawPeer: (x, g, o) => draw(x, Object.assign({ tr, now: nowS(), rm: !!E.rm }, g), peerNorm(o)), openPeer, peerOpen,
             open: () => open(null), close, toggle: () => (S && !S.target ? close() : open(null)), esc: esc2, isOpen: () => !!S, timing, find: () => obj };
  }

  // A stand-in for the page's host when office.html is opened alone: the same shape the backend answers (demo numbers only).
  function demoView(T, level) {
    const lv = level || 'alert', t0 = Math.floor(Date.now() / 1000), T_ = T || ((en) => en);
    const cells = (n, bad) => Array.from({ length: n }, (_, i) => (i >= n - bad ? 'alert' : i >= n - bad - 3 ? 'warn' : 'ok'));
    return { success: true, refresh_ms: 2000, title: T_('Ops board', '維運戰情'),
      banner: lv === 'ok' ? { level: 'ok', text: T_('All normal', '一切正常'), since: null, rule: '' } : { level: lv, text: T_('Memory pressure high for 12 minutes', '記憶體壓力高 12 分鐘'), since: t0 - 720, rule: 'mem_pressure_high' },
      sections: [
        { id: 'cpu', title: 'CPU', widgets: [{ kind: 'gauge', label: T_('All cores', '全部核心'), value: 41, text: '41%', tone: 'info' },
          { kind: 'bars', label: T_('Each core', '每顆核心'), items: [40, 34, 28, 20, 15, 12, 8, 5, 4, 3].map((v, i) => ({ label: (i < 6 ? 'P' + (i + 1) : 'E' + (i - 5)), value: v, group: i < 6 ? 'p' : 'e' })) },
          { kind: 'spark', label: T_('Last 60 seconds', '最近 60 秒'), unit: '%', max: 100, series: [{ label: 'CPU', values: [30, 33, 41, null, 38, 44, 41], tone: 'info' }] }] },
        { id: 'mem', title: T_('Memory', '記憶體'), widgets: [{ kind: 'stack', label: T_('Used 93%', '已用 93%'), total: 17179869184, parts: [{ label: 'App', value: 7.4e9, tone: 'info' }, { label: T_('Wired', '固定'), value: 2.6e9, tone: 'warn' }, { label: T_('Compressed', '壓縮'), value: 3.4e9, tone: 'dim' }, { label: T_('Cache', '快取'), value: 1.4e9, tone: '' }] },
          { kind: 'strip', label: T_('Pressure (last 30 minutes)', '壓力（最近 30 分鐘）'), start: t0 - 1800, step_s: 30, cells: cells(60, lv === 'ok' ? 0 : 24) },
          { kind: 'stat', label: T_('Swap', '交換區'), value: '3.2 GB', sub: T_('+1.1 GB in 30 minutes', '30 分鐘 +1.1 GB'), tone: 'warn' }] },
        { id: 'net', title: T_('Network and disk', '網路與磁碟'), widgets: [{ kind: 'spark', label: T_('Down / up', '下載／上傳'), unit: 'MB/s', series: [{ label: T_('Down', '下載'), values: [0.8, 1.2, 1.9, 1.1, 1.4], tone: 'info' }, { label: T_('Up', '上傳'), values: [0.2, 0.3, 0.2, 0.4, 0.3], tone: 'dim' }] },
          { kind: 'stat', label: T_('Disk free', '磁碟剩餘'), value: '38%', sub: '182 GB', tone: 'ok' }] },
        { id: 'temp', title: T_('Temperature', '溫度'), widgets: [{ kind: 'stat', label: T_('CPU temperature', 'CPU 溫度'), value: '46.0°C', sub: 'acpitz', tone: 'ok' },
          { kind: 'stat', label: 'GPU · NVIDIA GB10', value: '43°C', sub: T_('Shared memory · Power 11.0 W', '共用記憶體 · 功耗 11.0 W'), tone: 'ok' },
          { kind: 'gauge', label: T_('GPU utilization', 'GPU 使用率'), value: 12, text: '12%', tone: 'info' },
          { kind: 'spark', label: T_('Last 30 minutes', '最近 30 分鐘'), unit: '°C', series: [{ label: 'CPU', values: [44, 45, 45, 46, 47, 46, 46], tone: 'info' }, { label: 'GPU', values: [41, 42, 43, 43, 44, 43, 43], tone: 'warn' }] }] },
        { id: 'top', title: T_('Top 5 by use', '吃資源前 5 名'), widgets: [{ kind: 'table', columns: [T_('Program', '程式'), 'CPU', T_('Memory', '記憶體')], switch: { action: 'top_sort', value: 'auto', options: [{ id: 'auto', label: T_('Auto', '自動') }, { id: 'cpu', label: 'CPU' }, { id: 'mem', label: T_('Memory', '記憶體') }], note: T_('Now ranked by CPU', '現在照 CPU 排') }, rows: [['示範瀏覽器', '24%', '3.1 GB'], ['示範建置程序', '19%', '2.4 GB'], ['示範編輯器', '7%', '1.9 GB']] }] },
        { id: 'day', title: T_('Last 24 hours', '最近 24 小時'), widgets: [{ kind: 'strip', label: T_('Memory pressure (every 5 minutes)', '記憶體壓力（每 5 分鐘）'), start: t0 - 86400, step_s: 300, cells: cells(288, lv === 'ok' ? 0 : 3) }] },
        { id: 'rules', title: T_('What counts as wrong', '什麼算異常'), widgets: [{ kind: 'rules', items: [
          { id: 'mem_pressure_high', label: T_('Memory pressure "high" for', '記憶體壓力「高」連續'), on: true, value: 10, unit: T_('min', '分鐘'), level: 'alert', min: 1, max: 120, step: 1 },
          { id: 'cpu_high', label: T_('CPU above 90% for', 'CPU 超過 90% 連續'), on: true, value: 5, unit: T_('min', '分鐘'), level: 'warn', min: 1, max: 60, step: 1 },
          { id: 'net_down', label: T_('Network down for', '網路中斷超過'), on: false, value: 2, unit: T_('min', '分鐘'), level: 'warn', min: 1, max: 30, step: 1 }] }] }] };
  }
  const demoHost = T => ({ view: async () => demoView(T, 'alert'), live: async () => ({ success: true }), action: async () => ({ ok: true, message: '' }),
    peerView: async (pid, sort) => { const v = demoView(T, 'ok'); for (const s of v.sections) for (const w of s.widgets) { if (w.kind === 'rules') w.readonly = true; if (w.kind === 'table' && w.switch && sort) w.switch.value = sort; } return v; },
    peerLive: async () => ({ success: true }) });
  const demoObject = (T, level) => Object.assign({ id: 'ops', title: T('Ops', '維運'), furniture: 'tv', place: 'lobby:wall', button: false, badge: level === 'alert' ? 1 : 0, filters: [], sorts: [], actions: [],
    hint: '', level: level || 'ok', reason: level === 'alert' || level === 'warn' ? T('Memory pressure high for 12 minutes', '記憶體壓力高 12 分鐘') : '', since: null, muted_until: level === 'muted' ? Math.floor(Date.now() / 1000) + 2400 : null, updated: Math.floor(Date.now() / 1000) - 20,
    face: level === 'none' ? null : { cpu: 23.5, mem: level === 'alert' ? 93 : 61, pressure: level === 'alert' ? 'critical' : 'normal', net_in: 1.2e6, net_out: 3e5, online: true, disk_free_pct: 38, spark: [55, 57, 60, 61, 64, 70, 78, 93], cpu_temp: 46, gpu_temp: 43, gpu_util: 12 } });

  root.OfficeOps = { create, demoView, demoHost, demoObject, tvState, peerNorm, tempParts, GRAY, fmtBytes, fmtRate, KINDS: Object.keys(KINDS) };
})(typeof window !== 'undefined' ? window : globalThis);
