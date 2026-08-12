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
  8. Codex rollout JSONL 的新版 weekly-only rate limit 格式
  9. Codex rollout JSONL 的 5hr/week window 正規化
"""

import contextlib
import io
import json
import os
import tempfile
import time as real_time
import types
import urllib.error
from unittest import mock

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
        "time", "_claude_cache", "_account_cache", "_USAGE_CACHE_FILE",
        "_claude_oauth", "CLAUDE_SCRIPT")}
    saved_urlopen = U.urllib.request.urlopen
    tmpdir = None
    try:
        if cache_file is None:
            tmpdir = tempfile.TemporaryDirectory()
            cache_file = os.path.join(tmpdir.name, "usage_cache.json")
        U.time = clock.module()
        U._claude_cache = None                    # 重置 lazy cache
        U._account_cache = {}                     # 重置 per-account 快取
        U._USAGE_CACHE_FILE = cache_file
        # 真實 _claude_oauth 會優先吃 env 的 per-account token，帳號面板就是走
        # 這條；沒帶 env 時才回退到 Keychain（這裡以 token 假裝）。
        U._claude_oauth = lambda env=None: (
            {"accessToken": (env or {}).get("CLAUDE_CODE_OAUTH_TOKEN")}
            if (env or {}).get("CLAUDE_CODE_OAUTH_TOKEN")
            else ({"accessToken": token} if token else {})
        )
        U.CLAUDE_SCRIPT = "/nonexistent/fetch_oauth_usage.sh"  # 關掉 legacy 路徑
        U.urllib.request.urlopen = urlopen
        yield cache_file
    finally:
        for k, v in saved.items():
            setattr(U, k, v)
        U.urllib.request.urlopen = saved_urlopen
        if tmpdir:
            tmpdir.cleanup()


class TokenAwareUrlopen:
    """依 Authorization header 分流：模擬各帳號有各自 token 與各自結果。

    outcomes = {token: payload | Exception}
    """

    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    def __call__(self, req, timeout=0):
        token = (req.get_header("Authorization") or "").split()[-1]
        self.calls.append(token)
        outcome = self.outcomes.get(token)
        if isinstance(outcome, Exception):
            raise outcome
        body = json.dumps(outcome).encode()

        class _Resp:
            def read(self_inner):
                return body

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

        return _Resp()


def account_env(token):
    return {"CLAUDE_CODE_OAUTH_TOKEN": token, "CLAUDE_CONFIG_DIR": "/tmp/profile"}


def http_error(code):
    return urllib.error.HTTPError("u", code, "boom", {}, io.BytesIO())


@contextlib.contextmanager
def codex_rollout_env(pattern):
    """隔離 Codex rollout/legacy lookup，避免測試讀到本機真實帳號資料。"""
    saved = {k: getattr(U, k) for k in (
        "CODEX_LOG_GLOB", "CODEX_ROLLOUT_GLOB", "CODEX_SCRIPT_DIR")}
    try:
        U.CODEX_LOG_GLOB = "/nonexistent/logs_*.sqlite"
        U.CODEX_ROLLOUT_GLOB = pattern
        U.CODEX_SCRIPT_DIR = "/nonexistent/codex-usage"
        yield
    finally:
        for k, v in saved.items():
            setattr(U, k, v)


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


def test_account_usage_expired_login_reports_reason_not_no_data():
    """帳號面板列出每個帳號：憑證過期要明講「重新登入」，不能只說查不到。"""
    clock = FakeClock()
    net = TokenAwareUrlopen({"tok-expired": http_error(401)})
    with probe_env(clock, net):
        out = U.account_usage("claude", env=account_env("tok-expired"), ref="claude-b")
        assert out["error"] == "auth_required", out
        assert "重新登入" in out["error_message"], out
        assert out["five_hr"] is None and out["week"] is None, out


def test_account_usage_expired_token_is_detected_without_burning_the_api():
    """本地就知道 token 過期 → 不打 API（打了只會 401，還害下一次變 429 誤報）。"""
    clock = FakeClock()
    net = TokenAwareUrlopen({"tok-expired": GOOD_API_PAYLOAD})
    with tempfile.TemporaryDirectory() as profile_dir:
        with open(os.path.join(profile_dir, ".credentials.json"), "w") as f:
            json.dump({"claudeAiOauth": {
                "accessToken": "tok-expired",
                "expiresAt": int((clock.now - 3600) * 1000),
            }}, f)
        env = {"CLAUDE_CODE_OAUTH_TOKEN": "tok-expired",
               "CLAUDE_CONFIG_DIR": profile_dir}
        with probe_env(clock, net):
            out = U.account_usage("claude", env=env, ref="claude-b")
    assert out["error"] == "auth_required", out
    assert net.calls == [], f"過期 token 不該打 API：{net.calls}"


def test_account_usage_backoff_replays_real_reason():
    """退避窗內不再打 API，但要重播真因（過期），不能蓋成「剛查過」。"""
    clock = FakeClock()
    net = TokenAwareUrlopen({"tok-expired": http_error(401)})
    with probe_env(clock, net):
        U.account_usage("claude", env=account_env("tok-expired"), ref="claude-b")
        calls = len(net.calls)
        clock.now += 10
        out = U.account_usage("claude", env=account_env("tok-expired"), ref="claude-b")
        assert len(net.calls) == calls, "退避窗內不應再打 API"
        assert out["error"] == "auth_required", out


def test_account_usage_is_cached_per_account_without_cross_talk():
    """一個帳號查得到、另一個過期，數字不能互相污染；TTL 內零 API call。"""
    clock = FakeClock()
    net = TokenAwareUrlopen({"tok-ok": GOOD_API_PAYLOAD, "tok-expired": http_error(401)})
    with probe_env(clock, net):
        ok = U.account_usage("claude", env=account_env("tok-ok"), ref="claude-a")
        bad = U.account_usage("claude", env=account_env("tok-expired"), ref="claude-b")
        assert ok["five_hr"]["pct"] == 18 and ok["week"]["pct"] == 20, ok
        assert bad["five_hr"] is None and bad["error"] == "auth_required", bad
        calls = len(net.calls)
        clock.now += 30                                   # _ACCOUNT_OK_TTL 內
        again = U.account_usage("claude", env=account_env("tok-ok"), ref="claude-a")
        assert again["five_hr"]["pct"] == 18 and again["stale"] is False, again
        assert len(net.calls) == calls, f"TTL 內不該再打 API：{net.calls}"


def test_account_usage_429_serves_that_accounts_own_stale_reading():
    clock = FakeClock()
    net = TokenAwareUrlopen({"tok-ok": GOOD_API_PAYLOAD})
    with probe_env(clock, net):
        U.account_usage("claude", env=account_env("tok-ok"), ref="claude-a")
        clock.now += 200                                  # 過 TTL 與退避窗
        net.outcomes["tok-ok"] = http_error(429)
        out = U.account_usage("claude", env=account_env("tok-ok"), ref="claude-a")
        assert out["stale"] is True and out["five_hr"]["pct"] == 18, out
        assert "頻繁" in out["error_message"], out


def test_account_usage_current_claude_shares_the_pill_cache():
    """目前登入的帳號走共享快取，面板與膠囊不會互相把 token 打到 429。"""
    clock = FakeClock()
    net = TokenAwareUrlopen({"fake-token": GOOD_API_PAYLOAD})
    with probe_env(clock, net):
        U._fetch_claude()                                 # 膠囊先查（1 次 API）
        calls = len(net.calls)
        with mock.patch.object(U, "_claude_account", return_value="me · Team"):
            out = U.account_usage("claude", env=account_env("tok-ok"),
                                  ref="claude-a", is_current=True)
        assert out["five_hr"]["pct"] == 18, out
        assert len(net.calls) == calls, "目前帳號應重用膠囊快取，不該另打一次"


def test_account_usage_codex_weekly_only_plan_is_not_an_error():
    """Codex Team 只有週限額（無 5h 窗口）→ 5h 留空但不是錯誤。"""
    clock = FakeClock()
    net = TokenAwareUrlopen({})
    with probe_env(clock, net):
        with mock.patch.object(U, "_fetch_codex",
                               return_value={"week": (0, "08-18 12:48"), "_plan": "team"}):
            out = U.account_usage("codex", env={"CODEX_HOME": "/tmp/profile"},
                                  ref="codex-a")
    assert out["error"] is None and out["five_hr"] is None, out
    assert out["week"] == {"pct": 0, "reset": "08-18 12:48"}, out


def test_pill_and_panel_caches_coexist_on_disk():
    """兩邊都寫同一個快取檔：read-modify-write，不能互相清掉。"""
    clock = FakeClock()
    net = TokenAwareUrlopen({"fake-token": GOOD_API_PAYLOAD, "tok-ok": GOOD_API_PAYLOAD})
    with probe_env(clock, net) as cache_file:
        U.account_usage("claude", env=account_env("tok-ok"), ref="claude-a")
        U._fetch_claude()                                 # 膠囊路徑後寫
        blob = json.load(open(cache_file))
        assert blob["claude"]["data"]["5hr"][0] == 18, blob
        assert blob["accounts"]["claude:claude-a"]["data"]["5hr"][0] == 18, blob


def test_detect_ai():
    assert U.detect_ai("claude --permission-mode x") == "claude"
    assert U.detect_ai("/usr/local/bin/sf-codex resume") == "codex"
    assert U.detect_ai("codex") == "codex"
    assert U.detect_ai("/bin/zsh -l") is None
    assert U.detect_ai("") is None


def test_codex_rollout_jsonl_reads_weekly_only_rate_limit():
    event = {
        "timestamp": "2026-08-10T02:02:01.635Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "primary": {
                    "used_percent": 13.0,
                    "window_minutes": 10080,
                    "resets_at": 1786826020,
                },
                "secondary": None,
                "plan_type": "pro",
            },
        },
    }
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "rollout-2026-08-10.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps(event) + "\n")
        with codex_rollout_env(os.path.join(td, "rollout-*.jsonl")):
            out = U._fetch_codex()
    assert out and out["week"][0] == 13, out
    assert "5hr" not in out, out
    assert out["_plan"] == "pro", out


def test_codex_rollout_jsonl_normalizes_five_hour_and_week_windows():
    event = {
        "timestamp": "2026-08-10T02:02:01.635Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "primary": {
                    "used_percent": 18.0,
                    "window_minutes": 300,
                    "resets_at": 1786327200,
                },
                "secondary": {
                    "used_percent": 42.0,
                    "window_minutes": 10080,
                    "resets_at": 1786826020,
                },
                "plan_type": "pro",
            },
        },
    }
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "rollout-2026-08-10.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps(event) + "\n")
        with codex_rollout_env(os.path.join(td, "rollout-*.jsonl")):
            out = U._fetch_codex()
    assert out and out["5hr"][0] == 18 and out["week"][0] == 42, out


def test_codex_app_server_normalizes_live_rate_limits():
    class FakeStdin:
        def write(self, value):
            return len(value)
        def flush(self):
            return None

    class FakeProc:
        stdin = FakeStdin()
        stdout = []
        def terminate(self):
            return None
        def wait(self, timeout=None):
            return None

    response = {
        "id": 2,
        "result": {"rateLimits": {
            "primary": {"usedPercent": 7, "windowDurationMins": 10080,
                         "resetsAt": 1786944032},
            "secondary": None,
            "planType": "team",
        }},
    }
    with mock.patch.object(U.shutil, "which", return_value="/fake/sf-codex"), \
         mock.patch.object(U.subprocess, "Popen", return_value=FakeProc()), \
         mock.patch.object(U, "_read_jsonrpc_response", return_value=response):
        out = U._fetch_codex_app_server(home="/tmp/profile")
    assert out and out["week"][0] == 7 and out["_plan"] == "team", out


def test_codex_app_server_surfaces_invalid_auth():
    class FakeStdin:
        def write(self, value):
            return len(value)
        def flush(self):
            return None

    class FakeProc:
        stdin = FakeStdin()
        stdout = []
        def terminate(self):
            return None
        def wait(self, timeout=None):
            return None

    response = {"id": 2, "error": {
        "code": -32603, "message": "401 Unauthorized token_invalidated"
    }}
    with mock.patch.object(U.shutil, "which", return_value="/fake/sf-codex"), \
         mock.patch.object(U.subprocess, "Popen", return_value=FakeProc()), \
         mock.patch.object(U, "_read_jsonrpc_response", return_value=response):
        out = U._fetch_codex_app_server(home="/tmp/profile")
    assert out == {
        "_error": "auth_required",
        "_error_message": "Codex 登入已失效，請重新登入",
    }


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
