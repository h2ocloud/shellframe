/**
 * 右鍵的決策：有選取就只複製，沒選取就貼上，兩者絕不在同一次點擊裡合體。
 *
 * 歷史：0.38.2 為了「複製時順便貼上」把 macOS 沒選取時的右鍵貼上整個拿掉，
 * 結果用右鍵貼上的人貼不了。上一版的測試只比對原始碼字串，連方向寫反都照過。
 * 這份把真正的決策函式從 web/index.html 抽出來跑，驗行為。
 *
 * 跑法：node tests_right_click.js
 */
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'web/index.html'), 'utf8');
const mq = html.match(/const RIGHT_CLICK_QUIET_MS = \d+;/);
const mf = html.match(/function _rightClickAction\(sel, now, lastCopyAt\) \{[\s\S]*?\n  \}\n/);
if (!mq || !mf) { console.error('FAIL  在 index.html 找不到右鍵決策函式'); process.exit(1); }
const act = new Function(`${mq[0]}; ${mf[0]}; return { act: _rightClickAction, quiet: RIGHT_CLICK_QUIET_MS };`)();
const { quiet } = act;
const decide = act.act;

let fails = 0;
function check(name, ok, detail) {
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${ok ? '' : '  ' + (detail || '')}`);
  if (!ok) fails++;
}

const T = 1_000_000;
check('a selection is copied', decide('some text', T, 0) === 'copy');
check('a selection is copied even right after another copy', decide('x', T, T - 10) === 'copy');
check('nothing selected, nothing copied lately → paste (the whole point of right-click paste)',
  decide('', T, 0) === 'paste');
check('nothing selected, last copy long ago → paste', decide('', T, T - 60_000) === 'paste');
check('right after a copy, a selection-less click is that same click, not a paste',
  decide('', T, T - 50) === 'none');
check('just inside the quiet window → none', decide('', T, T - (quiet - 1)) === 'none');
check('just outside the quiet window → paste', decide('', T, T - quiet) === 'paste');
check('a copy then a deliberate paste a moment later works (select, right-click, move, right-click)',
  decide('', T, T - 3_000) === 'paste');
check('the quiet window is short enough not to feel like a dead click', quiet <= 2_000, String(quiet));

// 處理器本身：沒有任何平台判斷把貼上擋掉
const h = html.match(/\$wrap\.addEventListener\('contextmenu'[\s\S]*?\n  \}\);/);
check('live right-click handler found', !!h);
check('no platform check can swallow the paste', !!h && !/IS_MAC|IS_WIN/.test(h[0]));
check('handler asks the decision function and pastes only on "paste"',
  !!h && /_rightClickAction\(sel, now, _lastRightClickCopyAt\)/.test(h[0])
  && /action === 'paste'[\s\S]*_rightClickPaste\(\)/.test(h[0]));
check('every click leaves one decision line in the debug log (never the text)',
  !!h && /_logRightClick\(action, sel,/.test(h[0])
  && /sel \? sel\.length : 0/.test(html));

console.log(fails ? `\n${fails} FAILED` : '\nALL PASS');
process.exit(fails ? 1 : 0);
