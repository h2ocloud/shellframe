/**
 * 右鍵的決策：有選取就只複製；沒選取只開選單，貼上要使用者在選單裡點了才貼。
 * 右鍵本身絕不自己貼上，也絕不在同一次點擊裡又複製又貼上。
 *
 * 歷史：0.38.2 為了「複製時順便貼上」把 macOS 沒選取時的右鍵貼上整個拿掉；0.38.3 又放回來，
 * 規則是「沒選取就貼上，複製後 1.5 秒內不貼」。結果 TUI 一重畫，xterm 的選取就不見
 * （換 screen buffer 時會清掉），選好字再按右鍵複製的人，被當成「沒選字」而貼上，終端機輸入框
 * 多出一段沒打算送的字，後面從對話畫面送出的訊息就排在它後面。這一版沒選取只開選單。
 * 這份把真正的決策函式和處理器從 web/index.html 抽出來跑，驗行為；整個流程（選字、重畫、右鍵、
 * 選單、貼上）在 tests_right_click_menu.py，用真的 xterm 驗。
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
check('nothing selected, nothing copied lately → the menu, not a paste',
  decide('', T, 0) === 'menu');
check('nothing selected, last copy long ago → the menu', decide('', T, T - 60_000) === 'menu');
check('right after a copy, a selection-less click is that same click, not a menu',
  decide('', T, T - 50) === 'none');
check('just inside the quiet window → none', decide('', T, T - (quiet - 1)) === 'none');
check('just outside the quiet window → the menu', decide('', T, T - quiet) === 'menu');
check('a copy then a deliberate second right-click a moment later opens the menu',
  decide('', T, T - 3_000) === 'menu');
check('the quiet window is short enough not to feel like a dead click', quiet <= 2_000, String(quiet));

// 沒有任何輸入組合會讓決策函式回「貼上」：貼上不是右鍵的決定
const seen = new Set();
for (const sel of ['', ' ', 'x', 'long\nselection']) {
  for (const ago of [0, 1, 50, quiet - 1, quiet, quiet + 1, 3_000, 60_000, T]) {
    for (const lastCopy of [0, T - ago]) seen.add(decide(sel, T, lastCopy));
  }
}
check('no input makes the decision a paste (a paste is the menu\'s)',
  !seen.has('paste') && [...seen].every(a => ['copy', 'menu', 'none'].includes(a)), [...seen].join(','));

// 處理器本身：沒有任何平台判斷把選單擋掉，也沒有一條路直接貼上
const h = html.match(/\$wrap\.addEventListener\('contextmenu'[\s\S]*?\n  \}\);/);
check('live right-click handler found', !!h);
const plat = h ? (h[0].match(/IS_MAC|IS_WIN/g) || []) : ['?'];
check('no platform check can swallow the menu (only the shortcut hint\'s label differs)',
  !!h && plat.length <= 1 && (plat.length === 0 || /hint: IS_MAC \?/.test(h[0])), plat.join(','));
check('handler asks the decision function and opens the menu only on "menu"',
  !!h && /_rightClickAction\(sel, now, _lastRightClickCopyAt\)/.test(h[0])
  && /action === 'menu'[\s\S]*SFMenu\.open\(/.test(h[0]));
// 唯一呼叫 _rightClickPaste 的地方是選單項目的 onSelect
const calls = h ? (h[0].match(/_rightClickPaste\(/g) || []) : [];
check('the handler pastes only as the menu item\'s own action',
  !!h && calls.length === 1 && /onSelect: \(\) => \{[^}]*_rightClickPaste\(\)/.test(h[0]), String(calls.length));
check('nothing else in the page calls the paste on a right-click',
  (html.match(/(?<!function )_rightClickPaste\(\)/g) || []).length === 1);   // the definition is not a call
check('every click leaves one decision line in the debug log (never the text)',
  !!h && /_logRightClick\(action, sel,/.test(h[0])
  && /sel \? sel\.length : 0/.test(html));
check('the paste picked from the menu is logged too',
  !!h && /_logRightClick\('menu-paste'/.test(h[0]));

console.log(fails ? `\n${fails} FAILED` : '\nALL PASS');
process.exit(fails ? 1 : 0);
