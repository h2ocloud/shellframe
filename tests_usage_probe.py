"""usage_probe 回歸測試 — 這個模組是 v0.19.5→v0.22.6 十個版本反覆救火的來源，
之後任何快取/退避/stale 邏輯改動先跑這份。

跑法（兩種皆可）：
    .venv/bin/python tests_usage_probe.py     # 免依賴 plain runner
    .venv/bin/python -m pytest tests_usage_probe.py

覆蓋（全部離線，urlopen 被 mock，絕不打真 API）：
  1. 成功抓取 → 解析 5hr/week、寫入磁碟快取
  2. 45s 內重複查 → 直接用快取、零 API call（多來源 burst 去重）
  3. API 失敗（429）→ 回上次好讀數並標 _stale；probe_data 標 stale=True
  4. 60s 退避窗內 → 不再打 API（重試風暴防護）
  5. 完全沒資料（無快取+API 失敗+無 legacy script）→ no_data
  6. 磁碟快取跨重啟載回；超過 24h 不還魂
  7. detect_ai 判定
"""

import contextlib
import io
import json
import os
import tempfile
import time as real_time
import types
import urllib.error

import usage_probe as U


# ────────────────────────── test harness helpers ──────────────────────────

class FakeClock:
    """usage_probe 只用 time.time / strftime / localtime。"""
    def __init__(self, start=1_760_000_000.0):
        self.now = start

    def module(self):
        return types.SimpleNamespace(
            time=lambda: self.now,
            strftime=real_time.strftime,
            localtime=real_time.localtime,
        )


class UrlopenMock:
    def __init__(self, payload=None, error=None):
        self.calls = 0
        self.payload = payload
        self.error = error

    def __call__(self, req, timeout=0):
        self.calls += 1
        if self.error:
            raise self.error
        body = json.dumps(self.payload).encode()

        class _Resp:
            def read(self_inner):
                return body

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

        return _Resp()


GOOD_API_PAYLOAD = {
    "five_hour": {"utilization": 18.4, "resets_at": "2026-07-03T18:00:00+08:00"},
    "seven_day": {"utilization": 20.2, "resets_at": "2026-07-07T09:00:00+08:00"},
}


@contextlib.contextmanager
def probe_env(clock, urlopen, cache_file=None, token="fake-token"):
    """隔離 usage_probe 的外部世界：時鐘、網路、Keychain、磁碟快取、legacy script。"""
    saved = {k: getattr(U, k) for k in (
        "time", "_claude_cache", "_USAGE_CACHE_FILE", "_claude_oauth", "CLAUDE_SCRIPT")}
    saved_urlopen = U.urllib.request.urlopen
    tmpdir = None
    try:
        if cache_file is None:
            tmpdir = tempfile.TemporaryDirectory()
            cache_file = os.path.join(tmpdir.name, "usage_cache.json")
        U.time = clock.module()
        U._claude_cache = None                    # 重置 lazy cache
        U._USAGE_CACHE_FILE = cache_file
        U._claude_oauth = lambda: {"accessToken": token} if token else {}
        U.CLAUDE_SCRIPT = "/nonexistent/fetch_oauth_usage.sh"  # 關掉 legacy 路徑
        U.urllib.request.urlopen = urlopen
        yield cache_file
    finally:
        for k, v in saved.items():
            setattr(U, k, v)
        U.urllib.request.urlopen = saved_urlopen
        if tmpdir:
            tmpdir.cleanup()


# ────────────────────────── tests ──────────────────────────

def test_success_parses_and_persists():
    clock = FakeClock()
    net = UrlopenMock(payload=GOOD_API_PAYLOAD)
    with probe_env(clock, net) as cache_file:
        out = U._fetch_claude()
        assert out and out["5hr"][0] == 18 and out["week"][0] == 20, out
        assert "_stale" not in out
        assert net.calls == 1
        saved = json.load(open(cache_file))["claude"]
        assert saved["data"]["5hr"][0] == 18


def test_fresh_cache_dedups_burst_zero_calls():
    clock = FakeClock()
    net = UrlopenMock(payload=GOOD_API_PAYLOAD)
    with probe_env(clock, net):
        U._fetch_claude()
        assert net.calls == 1
        clock.now += 30                            # 45s 內
        for _ in range(5):                         # 膠囊+彈窗+事件刷新 burst
            out = U._fetch_claude()
            assert out and "_stale" not in out
        assert net.calls == 1, f"45s 內不應再打 API，卻打了 {net.calls} 次"


def test_429_serves_stale():
    clock = FakeClock()
    net = UrlopenMock(payload=GOOD_API_PAYLOAD)
    with probe_env(clock, net):
        U._fetch_claude()                          # 先有一筆好讀數
        clock.now += 120                           # 過 TTL 與退避窗
        net.error = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, io.BytesIO())
        out = U._fetch_claude()
        assert out and out.get("_stale") is True, out
        assert out["5hr"][0] == 18                 # 內容仍是上次好讀數
        d = U.probe_data("claude --foo")
        assert d["stale"] is True and d["five_hr"]["pct"] == 18, d


def test_backoff_no_retry_storm():
    clock = FakeClock()
    net = UrlopenMock(error=urllib.error.HTTPError("u", 429, "rl", {}, io.BytesIO()))
    with probe_env(clock, net):
        U._fetch_claude()                          # 第一次嘗試（失敗）
        first = net.calls
        for i in range(10):                        # 退避窗內連環查
            clock.now += 5
            U._fetch_claude()
        assert net.calls == first, f"60s 退避窗內不應重打，多打了 {net.calls - first} 次"
        clock.now += 60                            # 出窗後允許再試一次
        U._fetch_claude()
        assert net.calls == first + 1


def test_no_data_when_nothing_available():
    clock = FakeClock()
    net = UrlopenMock(error=urllib.error.URLError("down"))
    with probe_env(clock, net):
        assert U._fetch_claude() is None
        d = U.probe_data("claude")
        assert d["error"] == "no_data" and d["five_hr"] is None, d


def test_disk_cache_survives_restart_but_not_a_day():
    clock = FakeClock()
    net = UrlopenMock(payload=GOOD_API_PAYLOAD)
    with tempfile.TemporaryDirectory() as td:
        cache_file = os.path.join(td, "usage_cache.json")
        with probe_env(clock, net, cache_file=cache_file):
            U._fetch_claude()
        # 模擬重啟（_claude_cache=None 由 probe_env 重置），API 掛掉 → 仍有上次讀數
        net2 = UrlopenMock(error=urllib.error.URLError("down"))
        clock.now += 300
        with probe_env(clock, net2, cache_file=cache_file):
            out = U._fetch_claude()
            assert out and out.get("_stale") and out["5hr"][0] == 18, out
        # 超過 24h → 不還魂
        clock.now += U._CLAUDE_DISK_MAX_AGE + 10
        with probe_env(clock, net2, cache_file=cache_file):
            assert U._fetch_claude() is None


def test_detect_ai():
    assert U.detect_ai("claude --permission-mode x") == "claude"
    assert U.detect_ai("/usr/local/bin/sf-codex resume") == "codex"
    assert U.detect_ai("codex") == "codex"
    assert U.detect_ai("/bin/zsh -l") is None
    assert U.detect_ai("") is None


# ────────────────────────── plain runner ──────────────────────────

if __name__ == "__main__":
    import sys
    import traceback
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception:
                fails += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
