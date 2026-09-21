/**
 * 上滑歷史 overlay 的背景色/反白清洗（純邏輯，快，不用瀏覽器）。
 *
 * 回報：跨機（Frame Link）session 的歷史軌跡出現灰色底塊——抓回來的內容帶了
 * claude TUI 的背景色/反白，或串流漏掉 49 重置讓灰底一路漏染。overlay 只是拿來
 * 回看，背景色塊只會變醜。這裡驗 `_stripBgReverseSgr`：拿掉背景與反白、留前景與
 * 粗斜底線；本機來源本來就沒背景碼，等於 no-op。
 *
 * 函式直接從 web/index.html 抽出來跑，不複製一份會走樣的副本。
 *
 * 跑法：node tests_history_bg_strip.js
 */
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'web/index.html'), 'utf8');
const m = html.match(/function _stripBgReverseSgr\(s\) \{[\s\S]*?\n    \}\n/);
if (!m) { console.error('FAIL  在 index.html 找不到 _stripBgReverseSgr'); process.exit(1); }
const _stripBgReverseSgr = new Function(`${m[0]}; return _stripBgReverseSgr;`)();

const E = '\x1b[';
let fails = 0;
function check(name, ok, detail) {
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${ok ? '' : '  ' + JSON.stringify(detail || '')}`);
  if (!ok) fails++;
}

// 256 色背景（灰底塊的典型來源）整段拿掉，前景保留
check('48;5;N 背景被拿掉、前景保留',
  _stripBgReverseSgr(`${E}38;5;114;48;5;240m文字${E}0m`) === `${E}38;5;114m文字${E}0m`);

// truecolor 背景（48;2;r;g;b）連 4 個參數一起吃掉
check('48;2;r;g;b 背景被拿掉',
  _stripBgReverseSgr(`${E}48;2;80;80;80m塊${E}0m`) === `${E}m塊${E}0m`);

// 反白(7) 會被當灰塊，拿掉
check('reverse(7) 被拿掉',
  _stripBgReverseSgr(`${E}7m反白${E}27m`) === `${E}m反白${E}m`);

// 具名背景 40-47 / 亮背景 100-107 拿掉
check('具名背景 41 拿掉、粗體 1 留著',
  _stripBgReverseSgr(`${E}1;41mX${E}0m`) === `${E}1mX${E}0m`);
check('亮背景 100 拿掉',
  _stripBgReverseSgr(`${E}100mX${E}0m`) === `${E}mX${E}0m`);

// 前景（含亮前景 90-97、truecolor 38;2）完全不動
check('前景 38;2 不動',
  _stripBgReverseSgr(`${E}38;2;255;0;0m紅${E}0m`) === `${E}38;2;255;0;0m紅${E}0m`);
check('亮前景 92 不動',
  _stripBgReverseSgr(`${E}92m綠${E}0m`) === `${E}92m綠${E}0m`);

// 混合：前景+背景+粗體 → 只掉背景
check('混合序列只掉背景',
  _stripBgReverseSgr(`${E}1;38;5;39;48;5;236m混${E}0m`) === `${E}1;38;5;39m混${E}0m`);

// 沒有 ANSI 的純文字原樣返回
check('純文字原樣',
  _stripBgReverseSgr('沒有跳脫序列的中文') === '沒有跳脫序列的中文');

// 重置 0 一定留著（不然樣式會漏染）
check('重置 0 保留',
  _stripBgReverseSgr(`${E}48;5;240mX${E}0mY`) === `${E}mX${E}0mY`);

console.log(fails ? `\n${fails} FAILED` : '\nALL PASS');
process.exit(fails ? 1 : 0);
