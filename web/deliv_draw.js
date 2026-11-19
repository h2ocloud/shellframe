/* The deliverables' small objects: one drawing per kind of file, shared by the office desk (web/office.html), the pop-up's list and the
 * legend (the same drawing, as a small picture), and the right-hand list in the main window. Isometric, drawn with the primitives it is
 * given, so on a desk it follows the office's camera and flat/3D morph, and as an icon it draws on a small flat canvas of its own.
 *
 *   SFDelivDraw.prims(P)             {P, poly, box, ellipse, wq, fquad, EDGE} built on a projection P(x, y, z) -> {x, y}
 *   SFDelivDraw.kinds(prims)         {pdf, html, zip, md, img, pptx, xlsx, docx, video, folder, file}: draw(ctx, spot{x,y,w,h,z})
 *   SFDelivDraw.kindOf(type)         a deliverable's `type` (md, pdf, url, folder, …) -> which drawing (url -> html, unknown -> file)
 *   SFDelivDraw.info(tr)             [{id, name, exts}] for a legend
 *   SFDelivDraw.thumb(type, size)    a PNG data URL of the object (cached)
 *   SFDelivDraw.pile(ctx, prims, spot, d, o)   the pile of papers on a desk; returns where it is on the screen
 *   SFDelivDraw.clip(n, o)           the paperclip (an inline SVG string) with its count
 * A pile is drawn from the owner's summary only: how many papers, the newest three types, how many nobody opened yet. No file is read.
 */
(function (root) {
  'use strict';
  const lerp = (a, b, t) => a + (b - a) * t;
  const EDGE = 'rgba(192,202,245,.10)';
  const RED = '#f7768e';

  function prims(P) {
    const poly = (c, pts, fill, stroke, lw) => {
      c.beginPath(); pts.forEach((p, i) => i ? c.lineTo(p.x, p.y) : c.moveTo(p.x, p.y)); c.closePath();
      if (fill) { c.fillStyle = fill; c.fill(); } if (stroke) { c.strokeStyle = stroke; c.lineWidth = lw || 1; c.stroke(); }
    };
    const box = (c, x, y, w, h, z0, H, cols, edge) => {
      const A = P(x, y, z0 + H), B = P(x + w, y, z0 + H), C = P(x + w, y + h, z0 + H), D = P(x, y + h, z0 + H);
      const B0 = P(x + w, y, z0), C0 = P(x + w, y + h, z0), D0 = P(x, y + h, z0);
      poly(c, [D, C, C0, D0], cols.l); poly(c, [C, B, B0, C0], cols.r); poly(c, [A, B, C, D], cols.t);
      if (edge) { c.strokeStyle = edge; c.lineWidth = 1; c.beginPath(); c.moveTo(D.x, D.y); c.lineTo(C.x, C.y); c.lineTo(B.x, B.y); c.moveTo(C.x, C.y); c.lineTo(C0.x, C0.y); c.stroke(); }
    };
    const ellipse = (c, x, y, rx, ry, fill, stroke, lw) => { c.beginPath(); c.ellipse(x, y, Math.max(.1, rx), Math.max(.1, ry), 0, 0, 6.2832); if (fill) { c.fillStyle = fill; c.fill(); } if (stroke) { c.strokeStyle = stroke; c.lineWidth = lw || 1; c.stroke(); } };
    const wq = (x0, y0, x1, y1, u1, u2, z1, z2) => [P(lerp(x0, x1, u1), lerp(y0, y1, u1), z2), P(lerp(x0, x1, u2), lerp(y0, y1, u2), z2), P(lerp(x0, x1, u2), lerp(y0, y1, u2), z1), P(lerp(x0, x1, u1), lerp(y0, y1, u1), z1)];
    const fquad = (x0, y0, x1, y1, z) => [P(x0, y0, z), P(x1, y0, z), P(x1, y1, z), P(x0, y1, z)];
    return { P, poly, box, ellipse, wq, fquad, EDGE };
  }

  // spot s = {x, y, w, h, z}: the tile it stands on (grid units) and the height of what it stands on
  function kinds(g) {
    const { P, poly, box, ellipse, wq, fquad, EDGE } = g;
    return {

  pdf: { draw(x, s) { const z = s.z, w = s.w, h = s.h;
    box(x, s.x + .02, s.y + .02, w - .06, h - .05, z, 2.6, { t: '#f5f7fc', l: '#cbd0e3', r: '#aab0c8' }, EDGE);
    const zt = z + 2.7; poly(x, fquad(s.x + .07, s.y + .06, s.x + w * .55, s.y + .1, zt), '#e0475b');
    for (let i = 0; i < 4; i++) poly(x, fquad(s.x + .07, s.y + .15 + i * .06, s.x + w - .13 - (i === 3 ? .12 : 0), s.y + .19 + i * .06, zt), '#a9b0cb');
    poly(x, fquad(s.x + .04, s.y + .04, s.x + .09, s.y + .06, zt + .1), '#59607e');                                       // 釘書針
    box(x, s.x + w - .06, s.y + .07, .1, .13, z + .5, 1.9, { t: '#ff7186', l: '#c93c50', r: '#a22f3f' }); } },           // 紅色角標
  html: { draw(x, s) { const z = s.z, y0 = s.y + s.h * .62;
    box(x, s.x + .05, y0 - .03, s.w - .1, .06, z, 1.2, { t: '#4a4f6e', l: '#3a3f5c', r: '#2f3350' });                       // 底座
    poly(x, wq(s.x + .02, y0, s.x + s.w - .02, y0, 0, 1, z + 1, z + 17), '#2a2f48');                                    // 框
    poly(x, wq(s.x + .02, y0, s.x + s.w - .02, y0, .05, .95, z + 2.2, z + 15.8), '#eef2ff');                              // 網頁
    poly(x, wq(s.x + .02, y0, s.x + s.w - .02, y0, .05, .95, z + 12.6, z + 15.8), '#7aa2f7');                             // 瀏覽器列
    [.1, .17, .24].forEach((u, i) => poly(x, wq(s.x + .02, y0, s.x + s.w - .02, y0, u, u + .045, z + 13.6, z + 14.8), ['#f7768e', '#e0af68', '#9ece6a'][i]));
    poly(x, wq(s.x + .02, y0, s.x + s.w - .02, y0, .08, .52, z + 8.2, z + 11.4), '#b9c3ea'); poly(x, wq(s.x + .02, y0, s.x + s.w - .02, y0, .58, .9, z + 3.2, z + 11.4), '#9ece6a');
    poly(x, wq(s.x + .02, y0, s.x + s.w - .02, y0, .08, .5, z + 3.2, z + 6.4), '#d3d9f2'); } },
  zip: { draw(x, s) { const z = s.z, w = s.w - .06, h = s.h - .06, X = s.x + .03, Y = s.y + .03, Hh = 8;
    box(x, X, Y, w, h, z, Hh, { t: '#d3a46a', l: '#b9874f', r: '#9a6e3e' }, 'rgba(60,40,20,.3)');
    poly(x, fquad(X + w * .42, Y, X + w * .58, Y + h, z + Hh + .05), '#f3e3b8');                                          // 膠帶（頂）
    poly(x, wq(X, Y + h, X + w, Y + h, .42, .58, z + Hh * .55, z + Hh), '#f3e3b8');                                      // 膠帶（正面）
    poly(x, wq(X, Y + h, X + w, Y + h, .1, .34, z + 1.2, z + 3.6), '#fffdf2'); poly(x, wq(X, Y + h, X + w, Y + h, .13, .3, z + 2.1, z + 2.5), '#b9874f'); } },
  md: { draw(x, s) { const z = s.z, w = s.w - .05, h = s.h - .04;
    box(x, s.x + .02, s.y + .02, w, h, z, 3, { t: '#fff6c6', l: '#d6c88a', r: '#bba96d' }, EDGE);
    for (let i = 0; i < 4; i++) poly(x, fquad(s.x + .08, s.y + .14 + i * .055, s.x + w - .04 - (i === 3 ? .1 : 0), s.y + .175 + i * .055, z + 3.1), '#c7b66e');
    poly(x, fquad(s.x + .02, s.y + .02, s.x + w + .02, s.y + .1, z + 3.12), '#e0af68');                                   // 上緣
    for (let i = 0; i < 5; i++) { const p = P(s.x + .08 + i * (w - .12) / 4, s.y + .06, z + 3.3); ellipse(x, p.x, p.y, 1.05, 1.05, '#59607e'); } } },   // 螺旋
  img: { draw(x, s) { const z = s.z, c = { x: s.x + s.w / 2, y: s.y + s.h / 2 }, r = .21, ang = -.22, cs = Math.cos(ang), sn = Math.sin(ang);
    const R = (u, v, zz) => P(c.x + (u * cs - v * sn) * r * 1.9, c.y + (u * sn + v * cs) * r * 1.9, zz), Q = (u0, v0, u1, v1, zz) => [R(u0, v0, zz), R(u1, v0, zz), R(u1, v1, zz), R(u0, v1, zz)];
    poly(x, Q(-.52, -.5, .52, .6, z + .2), 'rgba(0,0,0,.28)');
    poly(x, Q(-.5, -.52, .5, .58, z + 1.4), '#f8f9ff', 'rgba(60,70,110,.35)', .8);                                         // 白框
    poly(x, Q(-.4, -.42, .4, .28, z + 1.5), '#7dcfff');                                                                    // 照片：天空
    poly(x, [R(-.4, .28, z + 1.6), R(-.4, .05, z + 1.6), R(-.12, -.08, z + 1.6), R(.12, .12, z + 1.6), R(.4, -.04, z + 1.6), R(.4, .28, z + 1.6)], '#73c37a');   // 山丘
    const sun = R(.18, -.26, z + 1.7); ellipse(x, sun.x, sun.y, 1.8, 1.1, '#ffe08a'); } },
  pptx: { draw(x, s) { const z = s.z, y0 = s.y + s.h * .55, x0 = s.x + .03, x1 = s.x + s.w - .03, cx = (x0 + x1) / 2;
    x.strokeStyle = '#8a6246'; x.lineWidth = 1.7; x.lineCap = 'round';
    for (const [a, b] of [[x0 + .05, y0 + .26], [x1 - .05, y0 + .26], [cx, y0 - .24]]) { const p0 = P(a, b, z), p1 = P(cx, y0, z + 9); x.beginPath(); x.moveTo(p0.x, p0.y); x.lineTo(p1.x, p1.y); x.stroke(); }
    x.lineCap = 'butt'; poly(x, wq(x0, y0, x1, y0, 0, 1, z + 8, z + 18.5), '#e9edf8', '#8a6246', 1.4);                    // 畫板
    poly(x, wq(x0, y0, x1, y0, .07, .93, z + 13.2, z + 17.4), '#ff9e64');                                                  // 標題色塊
    [[.12, 4.2, 9.4], [.34, 4.2, 11.8], [.56, 4.2, 8.2]].forEach(([u, a, b]) => poly(x, wq(x0, y0, x1, y0, u, u + .16, z + 9.2, z + 9.2 + (b - a) * .5), '#7aa2f7'));
    poly(x, wq(x0, y0, x1, y0, .78, .92, z + 9.2, z + 12), '#bb9af7'); } },
  xlsx: { draw(x, s) { const z = s.z, X = s.x + .02, Y = s.y + .01, w = s.w - .05, h = s.h - .03;
    box(x, X, Y, w, h, z, 1.8, { t: '#8a6246', l: '#6b4a33', r: '#573b29' }, EDGE);
    poly(x, fquad(X + .035, Y + .07, X + w - .035, Y + h - .03, z + 1.9), '#f4f7ee');
    for (let i = 1; i < 4; i++) { poly(x, fquad(X + .035, Y + .07 + i * (h - .1) / 4 - .005, X + w - .035, Y + .07 + i * (h - .1) / 4 + .008, z + 2), '#7ccf8b'); poly(x, fquad(X + .035 + i * (w - .07) / 4 - .005, Y + .07, X + .035 + i * (w - .07) / 4 + .008, Y + h - .03, z + 2), '#7ccf8b'); }
    poly(x, fquad(X + .035, Y + .07, X + w - .035, Y + .07 + (h - .1) / 4, z + 2.05), '#4caf6a');                          // 表頭列
    box(x, X + w * .3, Y - .015, w * .4, .08, z + 1.7, 1.3, { t: '#d6dbeb', l: '#9aa1bd', r: '#80879f' }); } },          // 夾子
  docx: { draw(x, s) { const z = s.z, X = s.x + .01, Y = s.y + .08, w = s.w - .03, h = s.h - .14;
    box(x, X + .04, Y - .03, w - .08, h * .6, z + 1, 1.2, { t: '#fbfcff', l: '#c9cee0', r: '#aab0c8' });                      // 露出的信紙
    poly(x, fquad(X + .09, Y + .0, X + w - .13, Y + .018, z + 2.25), '#7aa2f7'); poly(x, fquad(X + .09, Y + .05, X + w - .2, Y + .068, z + 2.25), '#aeb5cf');
    box(x, X, Y + h * .22, w, h * .78, z, 2.2, { t: '#e8d9b4', l: '#c7b58a', r: '#a8966d' }, EDGE);                          // 信封
    const a = P(X, Y + h * .22, z + 2.25), b = P(X + w, Y + h * .22, z + 2.25), m = P(X + w / 2, Y + h * .6, z + 2.25);
    poly(x, [a, b, m], '#f3e6c4', 'rgba(90,70,40,.35)', .8); } },                                                         // 封口
  video: { draw(x, s) { const z = s.z, c = { x: s.x + s.w / 2, y: s.y + s.h / 2 }, p = P(c.x, c.y, z), p2 = P(c.x, c.y, z + 3.4), rx = 14, ry = 7;
    ellipse(x, p.x + 1, p.y + 1.5, rx, ry, 'rgba(0,0,0,.28)');
    ellipse(x, p.x, p.y, rx, ry, '#2a2d40'); x.fillStyle = '#2a2d40'; x.fillRect(p.x - rx, p2.y, rx * 2, p.y - p2.y); ellipse(x, p2.x, p2.y, rx, ry, '#41465f', '#7c84ad', 1);
    for (let i = 0; i < 5; i++) { const t = i / 5 * 6.2832 - .4; ellipse(x, p2.x + Math.cos(t) * rx * .6, p2.y + Math.sin(t) * ry * .6, 2.6, 1.4, '#16161e'); }
    ellipse(x, p2.x, p2.y, 2.8, 1.5, '#e0af68'); x.fillStyle = '#16161e'; x.beginPath(); x.moveTo(p2.x + rx * .85, p2.y + 1); x.quadraticCurveTo(p2.x + rx * 1.3, p2.y + ry * 1.7, p2.x + rx * 1.8, p2.y + ry * 1.5); x.lineTo(p2.x + rx * 1.8, p2.y + ry * 1.5 + 3); x.quadraticCurveTo(p2.x + rx * 1.3, p2.y + ry * 1.7 + 3, p2.x + rx * .82, p2.y + 3); x.fill(); } },
  folder: { draw(x, s) { const z = s.z, X = s.x + .01, Y = s.y + .02, w = s.w - .03, h = s.h - .05;
    box(x, X, Y, w, h, z, 1.5, { t: '#e3c07a', l: '#b9955a', r: '#9c7b46' }, EDGE);
    box(x, X + w * .08, Y - .045, w * .34, .065, z + .6, 1.3, { t: '#f0d28f', l: '#c9a566', r: '#a98a54' });                    // 標籤耳朵
    poly(x, fquad(X + .05, Y + .06, X + w - .05, Y + h - .04, z + 1.6), '#efd493'); poly(x, fquad(X + w * .55, Y + .09, X + w - .09, Y + .13, z + 1.65), '#b9955a'); } },
  file: { draw(x, s) { box(x, s.x + .03, s.y + .03, s.w - .08, s.h - .07, s.z, 2.2, { t: '#e6e9f5', l: '#b8bdd6', r: '#9aa0bb' }, EDGE); } },

    };
  }
  const NAMES = {'pdf':['Stapled report','釘好的報告','pdf'],'html':['Little web page','小網頁','html · http(s) 連結'],'zip':['Taped box','封好膠帶的紙箱','zip · tar · gz · 7z'],'md':['Spiral notepad','便條本','md · txt'],'img':['Polaroid','拍立得','png · jpg · gif · webp'],'pptx':['Small easel','小畫架','pptx · key'],'xlsx':['Clipboard sheet','夾板＋方格紙','xlsx · xls · csv'],'docx':['Letter in an envelope','信封裡的信','docx · doc'],'video':['Film reel','影片盤','mp4 · mov · webm'],'folder':['Manila folder','資料夾','folder'],'file':['Plain paper','其他檔案','other']};
  const ORDER = ['pdf', 'html', 'zip', 'md', 'img', 'pptx', 'xlsx', 'docx', 'video', 'folder', 'file'];
  const kindOf = t => (t === 'url' ? 'html' : NAMES[t] ? t : 'file');
  const info = tr => ORDER.map(id => ({ id, name: tr(NAMES[id][0], NAMES[id][1]), exts: NAMES[id][2] }));

  // ── icons: the same drawing on a small flat canvas ──
  const isoP = (x, y, z = 0) => ({ x: (x - y) * 32, y: (x + y) * 16 - z });
  let flat = null;
  const cache = new Map();
  function thumb(type, size, zoom) {
    const k = kindOf(type), key = k + '|' + (size || 64) + '|' + (zoom || 1);
    if (cache.has(key)) return cache.get(key);
    let url = '';
    try {
      const s = size || 64, cvs = document.createElement('canvas'); cvs.width = cvs.height = s;
      const x = cvs.getContext('2d');
      flat = flat || kinds(prims(isoP));
      x.translate(s / 2, s * .62); x.scale(s / 92 * (zoom || 1), s / 92 * (zoom || 1));
      const o = isoP(.5, .5, 0); x.translate(-o.x, -o.y);
      flat[k].draw(x, { x: .06, y: .06, w: .88, h: .88, z: 0 });
      url = cvs.toDataURL();
    } catch (_) { url = ''; }
    cache.set(key, url);
    return url;
  }

  // ── the pile on a desk ──
  // spot {x, y, z}: its corner tile and the desk's height; d {count, types: [newest first], fresh}; o {T, RM, COL, FONT, band}
  // Returns {x, y, r, pile: {x, y}}: the screen point of the envelope (or the pile's top) and a radius to hit-test, in CSS pixels.
  function pile(x, g, spot, d, o) {
    const { P, poly, box, ellipse, wq } = g, K = kinds(g), opt = o || {};
    const n = Math.max(1, d.count | 0), Hs = Math.min(2.2 + n * 1.25, 15), z = spot.z, X = spot.x, Y = spot.y, w = .46, h = .4;
    const c0 = P(X + w / 2, Y + h / 2, z);
    ellipse(x, c0.x, c0.y + 1, 17, 8, 'rgba(0,0,0,.22)');
    box(x, X, Y, w, h, z, Hs, { t: '#f4f6fb', l: '#d3d8ea', r: '#b3b9d2' }, 'rgba(60,70,110,.3)');
    for (let k = 1; k * 1.55 < Hs; k++) { const zz = z + k * 1.55; poly(x, wq(X, Y + h, X + w, Y + h, 0, 1, zz, zz + .32), '#98a0bf'); poly(x, wq(X + w, Y, X + w, Y + h, 0, 1, zz, zz + .32), '#8890b0'); }
    if (opt.band) poly(x, wq(X, Y + h, X + w, Y + h, 0, 1, z + Hs * .4, z + Hs * .4 + 1.1), opt.band);
    const top = z + Hs, types = (d.types || []).slice(0, 3);
    types.forEach((t, i) => {
      const tl = [{ x: X + .03, y: Y + .02, w: w - .06, h: h - .04, z: top }, { x: X - .5, y: Y - .06, w: .44, h: .46, z }, { x: X + .12, y: Y + h + .06, w: .42, h: .44, z }][i];
      K[kindOf(t)].draw(x, tl);
    });
    const c = P(X + w / 2, Y + h / 2, top + 8), fresh = d.fresh | 0;
    let env = null;
    if (fresh > 0) {                                                       // something nobody opened: an envelope, a star and the red number
      const bob = opt.RM ? 0 : Math.sin((opt.T || 0) * 2.6 + X) * 1.6, ex = c.x, ey = c.y - 22 + bob;
      x.save(); x.shadowColor = 'rgba(255,224,138,.9)'; x.shadowBlur = 10; ellipse(x, ex, ey + 1, 12, 9, 'rgba(255,224,138,.22)'); x.restore();
      x.fillStyle = '#fff8e6'; x.strokeStyle = '#9a6e3e'; x.lineWidth = 1.2; x.beginPath(); x.roundRect(ex - 9, ey - 6, 18, 12, 2); x.fill(); x.stroke();
      x.beginPath(); x.moveTo(ex - 9, ey - 6); x.lineTo(ex, ey + 1); x.lineTo(ex + 9, ey - 6); x.stroke();
      x.fillStyle = RED; x.beginPath(); x.arc(ex, ey + 1.4, 2, 0, 6.2832); x.fill();
      const sx = ex - 13, sy = ey - 10; x.fillStyle = '#ffe08a'; x.beginPath(); x.moveTo(sx, sy - 4.5); x.lineTo(sx + 1.2, sy - 1.2); x.lineTo(sx + 4.5, sy); x.lineTo(sx + 1.2, sy + 1.2); x.lineTo(sx, sy + 4.5); x.lineTo(sx - 1.2, sy + 1.2); x.lineTo(sx - 4.5, sy); x.lineTo(sx - 1.2, sy - 1.2); x.closePath(); x.fill();
      const bx = ex + 10, by = ey - 8; ellipse(x, bx, by, 7.4, 7.4, (opt.COL && opt.COL.red) || RED, '#f4f6ff', 1.6);
      x.font = `800 ${fresh > 9 ? 8 : 9.5}px ${opt.FONT || 'system-ui,sans-serif'}`; x.fillStyle = '#fff'; x.textAlign = 'center'; x.textBaseline = 'middle'; x.fillText(fresh > 9 ? '9+' : String(fresh), bx, by + .6);
      env = { x: ex, y: ey };
    }
    // where it is on the screen (CSS pixels of the canvas), for the pointer
    const m = x.getTransform(), dpr = x.canvas && x.canvas.clientWidth ? x.canvas.width / x.canvas.clientWidth : 1, sc = Math.hypot(m.a, m.b) / dpr;
    const at = m.transformPoint(new DOMPoint(c.x, c.y + 6));
    return { x: at.x / dpr, y: at.y / dpr, r: 20 * sc, fresh };
  }

  // the paperclip (blue with its count; grey and empty when only kept ones are left)
  function clip(n, grey) {
    const fill = grey ? '#7c84ad' : '#7aa2f7';
    return `<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true" focusable="false"><path d="M10.9 5.4 6.2 10.1a1.5 1.5 0 0 0 2.1 2.1l5-5a3 3 0 0 0-4.2-4.2L4 8.1a4.5 4.5 0 0 0 6.4 6.4l4-4" fill="none" stroke="${fill}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  }

  root.SFDelivDraw = { prims, kinds, kindOf, info, thumb, pile, clip, ORDER };
})(typeof window !== 'undefined' ? window : globalThis);
