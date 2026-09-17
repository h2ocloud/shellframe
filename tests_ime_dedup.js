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
const mGuard = html.match(/function _makeImeInputGuard\(sid\) \{[\s\S]*?\n  \}\n/);
if (!mGuard) { console.error('FAIL  在 index.html 找不到 _makeImeInputGuard'); process.exit(1); }
const mLeak = html.match(/const IME_LEAK_RE = [^\n]+/);
if (!mLeak) { console.error('FAIL  在 index.html 找不到 IME_LEAK_RE'); process.exit(1); }

let dropped = [];
global.pywebview = { api: { js_debug: (tag, msg) => dropped.push(JSON.parse(msg)) } };
const _makeImeDedup = new Function(`${m[0]}; return _makeImeDedup;`)();
// 守門用到 IME_LEAK_RE + _imeComposeStart + _imeComposing（外層全域），注入後
// 抽出來，驗它把「本機那套保護」正確套到（遠端 pane 之前完全沒有）。
const _makeImeInputGuard = new Function(
  `${mLeak[0]}; const _imeComposeStart = {}; let _imeComposing = false;` +
  `${m[0]}${mGuard[0]}; return _makeImeInputGuard;`)();

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

  // 守住回報過的誤吞：新的一次 composition 就是新的字，不能算重複
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
    // v0.35.17 改：原本這裡是「無 composition 資訊時單字重複一律放行（不猜）」。
    // 那個保守選擇的代價在 Windows 上現形——WebView2 有不發 compositionend 的
    // 情形，於是單字完全沒有任何規則擋得住，每個字都重複（回報：中文輸入文字
    // 會持續重複）。
    // 現在仍然不猜，只是把界線畫在量到的數字上：雙送實測 ≤0.8ms，最快的真實
    // 連打實測 93ms，門檻取 30ms——距離雙送 37 倍、距離真實連打 3 倍。
    const d = _makeImeDedup();
    check('單字在 30ms 內重複 → 擋（那個間隔人做不到）',
          !d.shouldDrop('哈') && d.shouldDrop('哈'));
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

  // ── 遠端 pane 的 IME 守門（_makeImeInputGuard）：遠端以前是裸送，
  //    這裡驗它把去重 + 漏字擋 + Enter 放行都套上（2026-09-07 回報）──
  {
    const g = _makeImeInputGuard('rmt:peer:s1');
    // 一次 commit（compositionstart → compositionend('還是要用')）只放行一次，
    // 第二次同內容擋掉——這正是「還是要用還是要用」重複的修法。
    g.start(); g.end('還是要用');
    check('遠端守門：commit 第一次放行', g.keep('還是要用') === true);
    check('遠端守門：同一次 commit 第二次 → 擋', g.keep('還是要用') === false);
  }
  {
    const g = _makeImeInputGuard('rmt:peer:s1');
    g.start();                                  // 組字中
    check('遠端守門：組字中漏出的純注音 → 擋', g.keep('ㄨㄛ') === false);
    check('遠端守門：組字中漏出的空白（叫候選）→ 擋', g.keep(' ') === false);
    check('遠端守門：組字中漏出的選字數字 → 擋', g.keep('3') === false);
    check('遠端守門：組字中真的漢字 commit → 放行', g.keep('我') === true);
    g.end('我');
  }
  {
    const g = _makeImeInputGuard('rmt:peer:s1');
    check('遠端守門：Enter 永遠放行', g.keep('\r') === true);
    check('遠端守門：純 ASCII 打字不擋', g.keep('o') && g.keep('p') && g.keep('e'));
    // 非組字狀態下的空白不該被當漏字擋掉（英文句子要能打空白）
    check('遠端守門：非組字時空白照送', g.keep(' ') === true);
  }

  // ── compositionend 沒帶回 commit 內容時，單字也不能重複（v0.35.17）────────
  // 主路徑靠 compositionend 記下「這次 commit 是什麼」；Windows 的 WebView2 有
  // 不發那個事件的情形，於是只剩「length > 1 且 400ms 內」那條後備——單字完全
  // 沒有規則擋得住，每個字都重複。回報「中文輸入文字會持續重複」就是這個。
  const spin = (ms) => { const t = Date.now(); while (Date.now() - t < ms) {} };
  {
    const d = _makeImeDedup();   // 沒有 started()／composed()＝收不到組字事件
    check('缺 compositionend：單字第一次放行', d.shouldDrop('好') === false);
    check('缺 compositionend：同一個字 30ms 內再來＝雙送，要擋',
          d.shouldDrop('好') === true);
  }
  {
    const d = _makeImeDedup();
    check('過了窗口的連打是真的輸入', d.shouldDrop('好') === false);
    spin(40);
    check('30ms 之後的同一個字不能吃掉（實測最快連打 93ms）',
          d.shouldDrop('好') === false);
  }
  {
    const d = _makeImeDedup();
    check('純 ASCII 完全不經手',
          d.shouldDrop('a') === false && d.shouldDrop('a') === false);
  }
  {
    const d = _makeImeDedup();
    d.started(); d.composed('哈');
    check('正常路徑：這次 commit 的第一次放行', d.shouldDrop('哈') === false);
    check('正常路徑：同一次 commit 的第二次要擋', d.shouldDrop('哈') === true);
    spin(40);
    d.started(); d.composed('哈');
    check('正常路徑：新的一次 commit＝新的字，要放行',
          d.shouldDrop('哈') === false);
  }

  console.log(fails ? `\n${fails} FAILED` : '\nALL PASS');
  process.exit(fails ? 1 : 0);
})();
