/**
 * 側欄最寬能顯示多少：視窗決定，存檔的寬度不動（只限制畫面上看到的）。
 *
 * 量到的：Windows 125% 縮放、視窗縮到最小（800px = 640 CSS px），側欄拉到最寬（存 448~480），
 * 終端機或對話畫面只剩約 150px，對話的標題和輸入框縮成幾個字。上限是這支純函式算的：
 * 視窗寬 − 右側面板 − 兩條拖曳把手 − 主欄最小寬度，再除以介面大小（側欄有 zoom），
 * 最小不低於拖曳能拉到的最窄（140）。真的版面（Chromium 與 WebKit）在 tests_sidebar_limit.py。
 *
 * 跑法：node tests_sidebar_limit.js
 */
const path = require('path');
const { sidebarCap, SIDEBAR_MIN, MAIN_MIN } = require(path.join(__dirname, 'web/layout.js'));

let fails = 0;
function check(name, ok, detail) {
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${ok ? '' : '  ' + (detail || '')}`);
  if (!ok) fails++;
}

check('the main column keeps a size a terminal or a chat can use', MAIN_MIN >= 360 && MAIN_MIN <= 480, String(MAIN_MIN));
check('the narrowest sidebar is what a drag can make', SIDEBAR_MIN === 140, String(SIDEBAR_MIN));

// 回報的情況：800px 視窗 @125% = 640 CSS px，兩條把手之一在（5px），右面板關著
const reported = sidebarCap(640, 0, 5, 1);
check('the reported window: the sidebar may be 640 − 5 − main, not 448', reported === 640 - 5 - MAIN_MIN && reported < 448, String(reported));
check('a roomy window caps nothing a drag can reach (480)', sidebarCap(1600, 0, 5, 1) > 480);
check('the default width (220) fits the smallest window at 125%', sidebarCap(640, 0, 5, 1) >= 220, String(sidebarCap(640, 0, 5, 1)));

// 側欄有 zoom（介面大小）：結果是它自己的單位，畫出來的寬度 = cap × scale
const large = sidebarCap(640, 0, 5, 1.18);
check('Large interface size: the cap is in the sidebar\'s own (zoomed) pixels', large === Math.floor((640 - 5 - MAIN_MIN) / 1.18), String(large));
check('…so what is drawn still leaves the main column its minimum', large * 1.18 <= 640 - 5 - MAIN_MIN, String(large * 1.18));

// 地板
check('a window too narrow for both: the sidebar keeps the narrowest a drag makes', sidebarCap(500, 0, 5, 1) === SIDEBAR_MIN);
check('…and so does a zero or negative room', sidebarCap(100, 300, 10, 1) === SIDEBAR_MIN);

// 右面板開著：它的寬度和第二條把手也要扣
const board = sidebarCap(1100, 280, 10, 1);
check('the right panel open takes its width and handle from the room', board === 1100 - 280 - 10 - MAIN_MIN, String(board));

// 壞輸入不會讓版面壞掉
check('a missing interface size counts as 1', sidebarCap(640, 0, 5, 0) === sidebarCap(640, 0, 5, 1) && sidebarCap(640, 0, 5, NaN) === sidebarCap(640, 0, 5, 1));

// 性質：視窗越寬越大；面板、把手、介面大小越大越小；只要沒落到地板，主欄一定拿得到最小寬度
let mono = true, room = true;
for (const scale of [0.85, 1, 1.18]) {
  for (const boardPx of [0, 200, 280, 560]) {
    let prev = -1;
    for (let total = 500; total <= 2400; total += 50) {
      const c = sidebarCap(total, boardPx, 10, scale);
      if (c < prev) mono = false;
      prev = c;
      if (c > SIDEBAR_MIN && total - boardPx - 10 - c * scale < MAIN_MIN - 1e-9) room = false;
    }
  }
}
check('a wider window never allows less', mono);
check('above the floor, the main column always keeps its minimum', room);

console.log(fails ? `\n${fails} FAILED` : '\nALL PASS');
process.exit(fails ? 1 : 0);
