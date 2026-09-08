#!/usr/bin/env python3
"""用量快取的並行寫入不能互相蓋掉（v0.35.14）。

帳號面板一次最多開 4 條執行緒查用量（main.py 的 account_usage_all），每一條都會
read-modify-write 同一個 usage_cache.json。修之前沒有鎖，也沒有原子替換：

  1. lost update：「A 讀 → B 讀 → B 寫 → A 寫」是完全合法的交錯，而 A 寫回去的
     是它讀到的舊 blob ＋ 自己那一段——B 那一筆就消失了（已隔離重現）。
  2. 半份 JSON：直接以 "w" 開檔會讓檔案在 dump 期間是截斷狀態，而頂列的 pill
     會定時讀這個檔。
  3. 同一個帳號被兩個 UI 入口同時要求時，兩條都會去打 API——多一次查詢，也多一次
     429 的機會（實測：同一個 token 一分鐘內查兩次就會被擋）。

跑法：.venv/bin/python tests_usage_cache_concurrency.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import usage_probe as U  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


td = tempfile.mkdtemp(prefix="sf-usage-cache-")
CACHE = os.path.join(td, "usage_cache.json")
_orig_file = U._USAGE_CACHE_FILE
U._USAGE_CACHE_FILE = CACHE

# ── 1. 強制那個「合法交錯」——外層寫入中途讓另一個寫入者整輪跑完 ────────────
# 這就是 diagnostics 的重現手法：不靠時序運氣，直接在 mutate 裡巢狀觸發。
U._write_cache_file(lambda b: b.update(claude={"data": {"x": 1}, "ts": 1}))


def outer(blob):
    # A 已經讀到 blob；在 A 寫回去之前，B 完整跑一輪讀改寫
    U._write_cache_file(lambda b: b.setdefault("accounts", {}).update(
        {"account-b": {"data": {"y": 2}, "ts": 2}}))
    blob["claude"] = {"data": {"x": 9}, "ts": 9}


U._write_cache_file(outer)
blob = json.loads(Path(CACHE).read_text())
check("巢狀交錯的兩個寫入者都留在檔案裡（lost update 修掉了）",
      blob.get("claude", {}).get("ts") == 9
      and (blob.get("accounts") or {}).get("account-b", {}).get("ts") == 2,
      json.dumps(blob, ensure_ascii=False))

# ── 2. 真的多執行緒各寫自己的帳號，全部都要在 ─────────────────────────────
Path(CACHE).write_text("{}")
N = 24


def writer(i):
    def mutate(b):
        accounts = b.setdefault("accounts", {})
        # 讀到改到之間刻意讓出 GIL，把交錯機率拉高
        time.sleep(0.001)
        accounts[f"acct-{i}"] = {"data": {"i": i}, "ts": i}
    U._write_cache_file(mutate)


threads = [threading.Thread(target=writer, args=(i,)) for i in range(N)]
for t in threads:
    t.start()
for t in threads:
    t.join()
accounts = (json.loads(Path(CACHE).read_text()).get("accounts") or {})
missing = [i for i in range(N) if f"acct-{i}" not in accounts]
check(f"{N} 條執行緒各寫一個帳號，一筆都沒掉", not missing, f"缺：{missing}")

# ── 3. 讀者不會看到半份 JSON ───────────────────────────────────────────────
Path(CACHE).write_text("{}")
stop = threading.Event()
bad = []


def reader():
    while not stop.is_set():
        try:
            raw = Path(CACHE).read_text()
        except OSError:
            continue
        if not raw:
            continue
        try:
            json.loads(raw)
        except Exception as e:
            bad.append(str(e))


r = threading.Thread(target=reader, daemon=True)
r.start()
big = {"pad": "x" * 40000}
for i in range(40):
    U._write_cache_file(
        lambda b, i=i: b.setdefault("accounts", {}).update(
            {f"a{i}": {"data": big, "ts": i}}))
stop.set()
r.join(timeout=2)
check("寫入期間讀者不會讀到半份 JSON（原子替換）", not bad, str(bad[:2]))
check("寫完沒有殘留暫存檔",
      not [f for f in os.listdir(td) if f.startswith(".usage_cache.")],
      str(os.listdir(td)))

# ── 4. 同一個帳號並行請求只查一次 ─────────────────────────────────────────
U._account_cache.clear()
U._fetch_locks.clear()
Path(CACHE).write_text("{}")
calls = []
_orig_probe = U.PROVIDER_SPECS["agy"]["probe"]


def slow_probe(env=None):
    calls.append(time.time())
    time.sleep(0.15)
    return {"pct": 42}


U.PROVIDER_SPECS["agy"]["probe"] = slow_probe
try:
    results = []
    ts = [threading.Thread(target=lambda: results.append(
        U.account_usage("agy", ref="acct-x", account="a@b"))) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("同一個帳號 4 條並行請求只打一次 API", len(calls) == 1, f"打了 {len(calls)} 次")
    check("4 條都拿到讀數（不是只有第一條）",
          len(results) == 4 and all(r.get("ai") == "agy" for r in results),
          str(results[:1]))
finally:
    U.PROVIDER_SPECS["agy"]["probe"] = _orig_probe

# ── 5. 不同帳號不互相擋 ───────────────────────────────────────────────────
U._account_cache.clear()
U._fetch_locks.clear()
calls2 = []


def slow_probe2(env=None):
    calls2.append(time.time())
    time.sleep(0.15)
    return {"pct": 7}


U.PROVIDER_SPECS["agy"]["probe"] = slow_probe2
try:
    t0 = time.time()
    ts = [threading.Thread(target=U.account_usage, args=("agy",),
                           kwargs={"ref": f"acct-{i}"}) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    elapsed = time.time() - t0
    check("不同帳號各查一次（4 次）", len(calls2) == 4, f"{len(calls2)} 次")
    check("不同帳號是並行的，不是被鎖成序列",
          elapsed < 0.15 * 3, f"花了 {elapsed:.2f}s（序列會 ≥0.6s）")
finally:
    U.PROVIDER_SPECS["agy"]["probe"] = _orig_probe
    U._USAGE_CACHE_FILE = _orig_file
    import shutil
    shutil.rmtree(td, ignore_errors=True)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
