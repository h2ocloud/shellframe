/**
 * IME 去重的純邏輯測試（快，不用瀏覽器）。
 *
 * 端對端那份是 tests_ime_seq.py——它載真實 xterm.js 5.5.0、dispatch 真實
 * composition 事件，驗「xterm 到底送幾次、最後進 PTY 幾次」。這份只驗去重
 * 函式本身的邊界，兩份都要留：純邏輯跑得快，端對端才擋得住 xterm 換版行為變。
 *
 * 函式直接從 web/index.html 抽出來跑，不複製一份會走樣的副本。
 *
 * 跑法：node tests_ime_dedup.js
 */
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'web/index.html'), 'utf8');
const m = html.match(/function _makeImeDedup\(\) \{[\s\S]*?\n  \}\n/);
if (!m) { console.error('FAIL  在 index.html 找不到 _makeImeDedup'); process.exit(1); }

let dropped = [];
global.pywebview = { api: { js_debug: (tag, msg) => dropped.push(JSON.parse(msg)) } };
const _makeImeDedup = new Function(`${m[0]}; return _makeImeDedup;`)();

let fails = 0;
function check(name, ok, detail) {
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${ok ? '' : '  ' + (detail || '')}`);
  if (!ok) fails++;
}
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  // 純 ASCII 完全不經手——終端每個按鍵都走這條路，不能有誤吞的風險
  {
    const d = _makeImeDedup();
    check('ASCII 連送同一個字元不擋', !d.shouldDrop('a') && !d.shouldDrop('a'));
    check('Enter / 控制字元不擋', !d.shouldDrop('\r') && !d.shouldDrop('\r'));
  }

  // 核心：一次 commit 只放行一次
  {
    dropped = [];
    const d = _makeImeDedup();
    d.started(); d.composed('你');
    check('commit 第一次放行', !d.shouldDrop('你'));
    check('同一次 commit 的第二次 → 擋掉', d.shouldDrop('你'));
    check('擋掉的理由是 commit-dup（不是時間窗口）',
          dropped.length === 1 && dropped[0].why === 'commit-dup');
  }

  // 守 Howard 回報的誤吞：新的一次 composition 就是新的字，不能算重複
  {
    const d = _makeImeDedup();
    d.started(); d.composed('好');
    d.shouldDrop('好');                       // 第一次，放行
    d.started(); d.composed('好');            // 使用者又打了一次「好」
    check('新一次 composition 的同字 → 放行（不吃字）', !d.shouldDrop('好'));
  }

  // 沒有 compositionend 資訊時的後備：多字元 400ms
  {
    const d = _makeImeDedup();
    check('無 composition 資訊時多字元第一次放行', !d.shouldDrop('你好'));
    check('無 composition 資訊時多字元 400ms 內重複 → 擋', d.shouldDrop('你好'));
  }
  {
    const d = _makeImeDedup();
    check('無 composition 資訊時單字重複 → 放行（不猜）',
          !d.shouldDrop('哈') && !d.shouldDrop('哈'));
  }

  // 60ms 上限只是保險：超過就當這次 commit 結束
  {
    const d = _makeImeDedup();
    d.started(); d.composed('走');
    d.shouldDrop('走');
    await sleep(80);
    check('超過 60ms 保險上限 → 放行', !d.shouldDrop('走'));
  }

  // commit 內容不同就不是重複
  {
    const d = _makeImeDedup();
    d.started(); d.composed('你');
    check('與 commit 內容不同 → 放行', !d.shouldDrop('你') && !d.shouldDrop('好'));
  }

  console.log(fails ? `\n${fails} FAILED` : '\nALL PASS');
  process.exit(fails ? 1 : 0);
})();
