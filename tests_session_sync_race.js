// 兩輪 reconcile 疊在一起不能把每個 pane 造第二次（v0.35.9）。
//
// 實測到的白畫面：debug log 裡每一個 sid 的 `js:ime-init` 在 2.5 秒內出現兩輪
// ——第一輪 12:24:52.5→54.8、第二輪 12:24:55.0→56.5。也就是 syncSessionsFromBackend
// 跑了兩輪重疊的對帳，每一輪都替所有 sid 建了一次 pane。第二輪的 term 取代了
// sessions[sid]，第一輪那個帶著使用者內容的 DOM 節點變成孤兒，而新的 pane 沒有
// 人重播畫面給它 → 終端整片空白。
//
// 根因有兩處：
//   1. `localSids` 是進函式時的快照，而建立迴圈裡每一步都 await（list_sessions
//      一次，再對每個新 sid 各一次 reconnectSession）——快照跑幾百毫秒就過期。
//   2. 沒有「同一時間只跑一輪」，四個觸發源（1.5 秒定時、bridge session 數變化、
//      Python 直推、開分頁之後）任意兩個撞在一起就重疊。
// 另外原本也沒有版本守門：晚到的舊回應會把新 label / order 蓋回舊值。
//
// 這支把 web/index.html 裡真正的那三支函式挖出來，用受控的回應順序驗這些。
//
// 跑法：node tests_session_sync_race.js
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'web/index.html'), 'utf8');

function grab(startMarker, endMarker) {
  const start = html.indexOf(startMarker);
  if (start < 0) throw new Error('index.html 找不到起點：' + startMarker);
  const end = html.indexOf(endMarker, start);
  if (end < 0) throw new Error('index.html 找不到終點：' + endMarker);
  return html.slice(start, end);
}

// 單一進行中／版本守門／對帳本體，原封不動搬過來
const SYNC = grab('  // 同一時間只跑一輪 reconcile。',
                  '  // Expose for Python to nudge');
// reconnectSession 的冪等外殼也是出貨的程式碼——測試不要自己寫一份簡化版，
// 那會驗到測試自己而不是實作。實際建 pane 的 _reconnectSession 由測試替身提供。
const RECONNECT_GUARD = grab('  // 正在建立中的 sid。',
                             '  async function _reconnectSession(');

let passed = 0, failed = 0;
function check(name, ok, detail) {
  if (ok) { passed++; console.log('  [PASS] ' + name); }
  else { failed++; console.log('  [FAIL] ' + name + '  ' + (detail || '')); }
}

function makeCtx(backendRounds) {
  const pending = [];
  const reconnects = [];
  const ctx = {
    _uiCreatingSession: 0,
    _syncDeferred: false,
    sessions: {},
    sessionOrder: [],
    activeId: null,
    _localOrderPushedAt: 0,
    isRemoteSid: () => false,
    renderTabs: () => {},
    switchTab: (sid) => { ctx.activeId = sid; },
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    console,
    window: {},
    JSON,
    Date,
    pywebview: { api: {
      list_sessions: () => new Promise(resolve => pending.push(resolve)),
    }},
    Map,
    // 建 pane 的替身：故意在寫入 sessions[sid] 之前 await，這正是真實函式的
    // 形狀（它在註冊前有好幾個 await），也是「只檢查 sessions[sid] 不夠」的原因。
    _reconnectSession: async (sid, cmd, label) => {
      reconnects.push(sid);
      await new Promise(r => setTimeout(r, 5));
      ctx.sessions[sid] = { cmd, label: label || cmd.split(/\s/)[0],
                            term: {}, pane: {} };
      ctx.sessionOrder.push(sid);
      return ctx.sessions[sid];
    },
  };
  vm.createContext(ctx);
  vm.runInContext(RECONNECT_GUARD + '\n' + SYNC, ctx);
  return { ctx, pending, reconnects };
}

const BACKEND = ['s1', 's2', 's3'].map(sid => ({
  sid, cmd: 'claude', label: 'new ' + sid, bridge_enabled: true,
}));

(async () => {
  // ── 1. 兩輪重疊：每個 sid 只能被建一次 ──
  {
    const { ctx, pending, reconnects } = makeCtx();
    const first = ctx.syncSessionsFromBackend();
    const second = ctx.syncSessionsFromBackend();
    check('進行中再被觸發不會開第二輪（共用同一個 promise）',
          pending.length === 1, 'list_sessions 被呼叫 ' + pending.length + ' 次');
    pending[0](JSON.stringify(BACKEND));
    await first; await second;
    // pending 的補跑
    await new Promise(r => setTimeout(r, 30));
    if (pending[1]) pending[1](JSON.stringify(BACKEND));
    await new Promise(r => setTimeout(r, 30));
    const dupes = reconnects.filter((s, i) => reconnects.indexOf(s) !== i);
    check('每個 sid 只建一個 pane（白畫面的直接成因）',
          dupes.length === 0, '重複建立：' + JSON.stringify(dupes));
    check('三個 sid 都建起來了',
          Object.keys(ctx.sessions).sort().join(',') === 's1,s2,s3',
          JSON.stringify(Object.keys(ctx.sessions)));
  }

  // ── 2. 就算兩輪真的並行（模擬舊行為的入口），冪等守門也擋得住 ──
  {
    const { ctx, pending, reconnects } = makeCtx();
    // 直接繞過單一進行中，兩輪同時打對帳本體
    const a = ctx._reconcileSessionsOnce();
    const b = ctx._reconcileSessionsOnce();
    check('繞過 single-flight 時會有兩份 list_sessions 在飛',
          pending.length === 2, String(pending.length));
    pending[0](JSON.stringify(BACKEND));
    pending[1](JSON.stringify(BACKEND));
    await a; await b;
    await new Promise(r => setTimeout(r, 40));
    const dupes = reconnects.filter((s, i) => reconnects.indexOf(s) !== i);
    check('第二層防線：reconnectSession 冪等，仍然不會造第二個 pane',
          dupes.length === 0, '重複建立：' + JSON.stringify(dupes));
  }

  // ── 3. 晚到的舊回應不能把新 label 蓋回去 ──
  {
    const { ctx, pending } = makeCtx();
    ctx.sessions.s1 = { cmd: 'codex', label: 'initial', term: {}, pane: {} };
    ctx.sessionOrder = ['s1'];
    ctx.activeId = 's1';

    const older = ctx._reconcileSessionsOnce();
    const newer = ctx._reconcileSessionsOnce();
    // 新的先回，帶新 label
    pending[1](JSON.stringify([{ sid: 's1', label: 'new label',
                                 bridge_enabled: true }]));
    await newer;
    check('新回應套用了新 label', ctx.sessions.s1.label === 'new label',
          ctx.sessions.s1.label);
    // 舊的晚到，帶舊 label
    pending[0](JSON.stringify([{ sid: 's1', label: 'old label',
                                 bridge_enabled: true }]));
    await older;
    check('晚到的舊回應沒有把 label 蓋回去',
          ctx.sessions.s1.label === 'new label',
          '變成 ' + ctx.sessions.s1.label);
  }

  // ── 4. 進行中期間的觸發不會被吞掉 ──
  {
    const { ctx, pending } = makeCtx();
    const first = ctx.syncSessionsFromBackend();
    ctx.syncSessionsFromBackend();          // 進行中 → 記成 pending
    check('進行中被觸發會記成 pending',
          ctx.window.__sfSyncStats().pending === true,
          JSON.stringify(ctx.window.__sfSyncStats()));
    pending[0](JSON.stringify([]));
    await first;
    await new Promise(r => setTimeout(r, 30));
    check('這一輪結束後補跑了一次（session 變動不會沒人對帳）',
          pending.length === 2, 'list_sessions 共 ' + pending.length + ' 次');
  }

  console.log('\nResults: ' + passed + ' passed, ' + failed + ' failed');
  console.log(failed ? failed + ' FAILED' : 'ALL PASS');
  process.exitCode = failed ? 1 : 0;
})().catch(e => { console.error(e); process.exitCode = 1; });
