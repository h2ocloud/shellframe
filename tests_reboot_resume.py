#!/usr/bin/env python3
"""重新開機後分頁要接回原本的對話（v0.30.22）。

機器重開後 tmux server 不在了，ShellFrame 走 manifest 的 soft restore 重新 spawn
每個分頁。manifest 存的 cmd 是「當初怎麼開的」——沒有 --resume 就是開全新對話，
所有分頁的上下文一次丟光；就算有 --resume，那個 uuid 也是啟動當時的（/clear 會輪替
uuid、resume 也常 fork 出新檔）。一律以 hook 回報、落地在 manifest 的 uuid 為準。

跑法：.venv/bin/python tests_reboot_resume.py
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()
sys.path.insert(0, str(Path(__file__).parent))

from main import Api  # noqa: E402

R = Api._cmd_with_resume
UUID = "939069ef-4f9e-4ebe-8c04-e2ea62feabcc"
OLD = "a6c737b5-b155-48b4-bb3f-bff736d775a4"
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


base = "claude --permission-mode bypassPermissions --dangerously-skip-permissions"
out = R(base, UUID)
check("沒有 --resume 的指令會補上",
      out == f"claude --resume {UUID} --permission-mode bypassPermissions "
             "--dangerously-skip-permissions", out)

out = R(f"claude --resume {OLD} --permission-mode bypassPermissions", UUID)
check("既有的舊 --resume 會被當前 uuid 取代（不是保留舊的）",
      OLD not in out and f"--resume {UUID}" in out, out)

out = R(f"claude --session-id {OLD} --permission-mode bypassPermissions", UUID)
check("--session-id 也會被換掉（同樣是啟動當時的值）",
      OLD not in out and f"--resume {UUID}" in out, out)

out = R(f"claude --resume={OLD} -p foo", UUID)
check("等號寫法的 --resume= 也處理", OLD not in out and f"--resume {UUID}" in out, out)

check("其他旗標原封不動", "--dangerously-skip-permissions" in R(base, UUID))

# ── codex：`codex resume <SESSION_ID>`，resume 是子指令必須緊接執行檔 ──
# Windows 沒有 tmux，關掉 ShellFrame 等於整批 session 斷線，這條路是唯一能接回的。
check("codex 不帶參數 → 補上 resume",
      R("codex", UUID) == f"codex resume {UUID}", R("codex", UUID))
check("codex 的其他旗標保留在 id 後面",
      R("codex --full-auto", UUID) == f"codex resume {UUID} --full-auto",
      R("codex --full-auto", UUID))
check("codex resume --last 會換成指定 id（--last 拿掉）",
      R("codex resume --last", UUID) == f"codex resume {UUID}",
      R("codex resume --last", UUID))
check("codex 既有的舊 session id 被取代，不會變成 resume resume",
      R("codex resume oldid --full-auto", UUID)
      == f"codex resume {UUID} --full-auto",
      R("codex resume oldid --full-auto", UUID))
check("codex.cmd（Windows 包裝）也處理",
      R("codex.cmd --full-auto", UUID) == f"codex.cmd resume {UUID} --full-auto",
      R("codex.cmd --full-auto", UUID))

for other in ("agy", "bash -l", "/usr/bin/env python3 x.py"):
    check(f"其他 CLI 不碰：{other.split()[0]}", R(other, UUID) == other, R(other, UUID))

# 判斷哪些分頁算 codex
from main import _worker_is_codex  # noqa: E402
check("codex 判定：codex / codex.cmd / sf-codex 都算",
      all(_worker_is_codex(c) for c in ("codex", "codex --full-auto",
                                        "codex.cmd", "sf-codex.bat", "/usr/local/bin/codex")))
check("codex 判定：claude / agy / bash 不算",
      not any(_worker_is_codex(c) for c in ("claude --resume x", "agy", "bash -l", "")))

check("uuid 為空時原樣返回", R(base, "") == base)
check("cmd 為空時不炸", R("", UUID) == "")
check("引號路徑不會被拆壞",
      R('"/opt/my apps/claude" --permission-mode bypassPermissions', UUID)
      == f"'/opt/my apps/claude' --resume {UUID} --permission-mode bypassPermissions",
      R('"/opt/my apps/claude" --permission-mode bypassPermissions', UUID))

# soft restore 真的有接上這條路
src = (Path(__file__).parent / "main.py").read_text(encoding="utf-8")
check("soft restore 會呼叫 _cmd_with_resume",
      "self._cmd_with_resume(cmd, csid)" in src)
check("soft restore 用 uuid 找 transcript，不信存下來的路徑",
      "self._claude_transcript_exists(csid)" in src)
# 存下來的 transcript_path 是 hook 最後看到檔案的位置，/clear 會讓它搬家——
# 真實 manifest 乾跑時 14 個分頁有 5 個因此被誤判成不能接回。
E = Api._claude_transcript_exists
check("不存在的 uuid → False", E("00000000-dead-beef-0000-000000000000") is False)
check("空 uuid → False", E("") is False)
# 用自己造的暫存 HOME 與 manifest 驗，不讀這台電腦真實的 config.json 與 ~/.claude。
import os as _os0  # noqa: E402
import tempfile as _tf0  # noqa: E402
from unittest.mock import patch as _p0  # noqa: E402

_home0 = _tf0.mkdtemp(prefix="sf_resume_home_")
_live = "dddddddd-4444-4444-8444-dddddddddddd"
_os0.makedirs(_os0.path.join(_home0, ".claude", "projects", "-moved-project"))
open(_os0.path.join(_home0, ".claude", "projects", "-moved-project", f"{_live}.jsonl"), "w").close()
_manifest = [{"claude_session_id": "", "transcript_path": "/gone/x.jsonl"},
             {"claude_session_id": "eeeeeeee-5555-4555-8555-eeeeeeeeeeee",      # no transcript anywhere
              "transcript_path": "/gone/y.jsonl"},
             {"claude_session_id": _live,                    # /clear moved the file: stored path is stale
              "transcript_path": "/gone/old-slug/" + _live + ".jsonl"}]
with _p0.dict(_os0.environ, {"HOME": _home0}):
    check("找得到的 uuid → True（manifest 的路徑已消失，仍以 uuid 找到）", any(
        E(x.get("claude_session_id", "")) for x in _manifest if x.get("claude_session_id")))
    check("manifest 裡找不到檔案的 uuid → False",
          E("eeeeeeee-5555-4555-8555-eeeeeeeeeeee") is False)

# ── Windows 路徑：沒有 lsof、沒有 tmux pane，只能靠「spawn 之後才出現的
#    rollout」＋認領表。用 Codex 真正的 rollout 檔名格式造情境（暫存資料夾裡的三份），
#    不讀這台電腦的 ~/.codex：以前拿真的最新兩份來用，只要 60 秒內多開過幾次 codex
#    （例如 codex exec 試模型），更早的一份就落進範圍，測試隨機變紅。 ──
import os as _os
import tempfile as _tempfile
import types as _types
from unittest.mock import patch as _patch

_root = _tempfile.mkdtemp(prefix="sf_codex_rollouts_")
_day = _os.path.join(_root, "2026", "11", "17")
_os.makedirs(_day)
rolls = []
for _i, (_stamp, _uuid) in enumerate([("2026-11-17T09-00-00", "11111111-1111-4111-8111-111111111111"),
                                      ("2026-11-17T10-00-00", "22222222-2222-4222-8222-222222222222"),
                                      ("2026-11-17T10-05-00", "33333333-3333-4333-8333-333333333333")]):
    _f = _os.path.join(_day, f"rollout-{_stamp}-{_uuid}.jsonl")
    open(_f, "w").close()
    _os.utime(_f, (1_800_000_000 + _i * 300 - (3000 if _i == 0 else 0),) * 2)   # the first one an hour earlier
    rolls.append(_f)
_codex_root = _patch("main.agent_status.codex_sessions_root", lambda ctx: _root)
_codex_root.start()
if len(rolls) >= 2:
    older, newer = rolls[-2], rolls[-1]
    uid = lambda f: Api._CODEX_ROLLOUT_RE.search(f).group(1)  # noqa: E731

    def _fake_api(spawn_ts, others=()):
        api = Api()
        s1 = _types.SimpleNamespace(cmd="codex", cwd="~", _tmux_name=None,
                                    _spawn_ts=spawn_ts)
        api.sessions = {"s1": s1}
        for i, taken in enumerate(others):
            api.sessions[f"o{i}"] = _types.SimpleNamespace(_codex_sid=taken)
        return api, s1

    # spawn 在「最後兩份 rollout」之前 → 應該認領較早的那一份
    api, s1 = _fake_api(_os.path.getmtime(older) - 60)
    with _patch("main.IS_WIN", True):
        got = api._codex_session_id("s1", s1)
    check("Windows：認領 spawn 之後最早出現的 rollout",
          got == uid(older), f"拿到 {got[:8]}，預期 {uid(older)[:8]}")

    # 同一份已被別的分頁認領 → 要跳過，改拿下一份
    api, s1 = _fake_api(_os.path.getmtime(older) - 60, others=[uid(older)])
    with _patch("main.IS_WIN", True):
        got = api._codex_session_id("s1", s1)
    check("Windows：已被其他分頁認領的 rollout 不會被搶走",
          got == uid(newer), f"拿到 {got[:8]}，預期 {uid(newer)[:8]}")

    # spawn 在所有 rollout 之後 → 認不出來，回空字串（寧可開新的）
    api, s1 = _fake_api(_os.path.getmtime(newer) + 3600)
    with _patch("main.IS_WIN", True):
        got = api._codex_session_id("s1", s1)
    check("Windows：spawn 之後沒有新 rollout → 不亂認", got == "", f"拿到 {got}")

    # 認過就記住，不用每次重掃
    api, s1 = _fake_api(_os.path.getmtime(older) - 60)
    with _patch("main.IS_WIN", True):
        first = api._codex_session_id("s1", s1)
    s1._spawn_ts = _os.path.getmtime(newer) + 3600      # 條件已失效
    check("認領結果會快取（第二次不重掃）",
          api._codex_session_id("s1", s1) == first)
_codex_root.stop()

# ── ShellFrame 自己的啟動器也要能接回對話（v0.35.17）──────────────────────
# sf-codex / sf-claude-home 都是 `exec <真的 CLI> "$@"`，參數原樣透傳，所以續接
# 的處理跟裸指令完全一樣——但名字比對只認 "codex"／"claude" 的話會漏掉它們，那些
# 分頁重開機後一律開新對話。而那正是實際在用的指令。
U = "11111111-2222-3333-4444-555555555555"
for cmd, want in [
    ("sf-codex --search", f"sf-codex resume {U} --search"),
    ("sf-codex resume old-id --search", f"sf-codex resume {U} --search"),
    ("sf-codex resume --last", f"sf-codex resume {U}"),
    ("sf-claude-home", f"sf-claude-home --resume {U}"),
    ("sf-claude-home --dangerously-skip-permissions",
     f"sf-claude-home --resume {U} --dangerously-skip-permissions"),
    ("codex --search", f"codex resume {U} --search"),
    ("agy", "agy"),
    ("bash -l", "bash -l"),
]:
    got = Api._cmd_with_resume(cmd, U)
    check(f"wrapper 續接：{cmd!r}", got == want, f"得到 {got!r}")


# ── 指令前面有 cd／env／VAR= 的分頁 ────────────────────────────────────────
# 量到：⟳ 重開 `cd ~/work/x && claude … '<開場 prompt>' --session-id <uuid>` 時拿 `cd`
# 當執行檔，指令原封不動重開——session id 已被占用，CLI 當場結束，分頁就死了；
# `env -u CLAUDE_CONFIG_DIR claude …` 的分頁則接回啟動當時的舊 uuid。
for cmd, want in [
    (f"cd ~/work/site-check && claude --model haiku --dangerously-skip-permissions "
     f"'請讀並完成 BRIEF.md 的任務' --session-id {OLD}",
     f"cd ~/work/site-check && claude --resume {U} --model haiku --dangerously-skip-permissions"),
    (f"env -u CLAUDE_CONFIG_DIR claude --permission-mode bypassPermissions --resume {OLD}",
     f"env -u CLAUDE_CONFIG_DIR claude --resume {U} --permission-mode bypassPermissions"),
    ("FOO=1 BAR='a b' claude --model opus", f"FOO=1 BAR='a b' claude --resume {U} --model opus"),
    ("cd \"/tmp/dir with space\" && env -u X claude 'hi'",
     f"cd \"/tmp/dir with space\" && env -u X claude --resume {U}"),
    ("cd /tmp; exec claude --model sonnet", f"cd /tmp; exec claude --resume {U} --model sonnet"),
    ("cd /tmp && codex --search", f"cd /tmp && codex resume {U} --search"),
    ("cd /tmp && bash -l", "cd /tmp && bash -l"),
]:
    got = Api._cmd_with_resume(cmd, U)
    check(f"前綴原樣保留、只改 CLI：{cmd[:46]!r}", got == want, f"得到 {got!r}")

print("開場 prompt 不重送")
for cmd, want in [
    ("claude --model opus 'start the task'", f"claude --resume {U} --model opus"),
    ("claude --append-system-prompt 'be brief' --model opus 'start'",
     f"claude --resume {U} --append-system-prompt 'be brief' --model opus"),
    ("claude --add-dir /a /b --model x", f"claude --resume {U} --add-dir /a /b --model x"),
    ("claude -c --model x", f"claude --resume {U} --model x"),
    (f"claude -r {OLD} --model x", f"claude --resume {U} --model x"),
    ("claude --model=opus -- 'a prompt'", f"claude --resume {U} --model=opus"),
    ("claude mcp list", "claude mcp list"),
    ("claude --model x && echo done", "claude --model x && echo done"),
]:
    got = Api._cmd_with_resume(cmd, U)
    check(f"claude 參數：{cmd!r}", got == want, f"得到 {got!r}")

import launch_cmd  # noqa: E402
check("沒有前綴：前綴是空的", launch_cmd.split_prefix("claude --model x") == ("", "claude --model x"))
check("前綴與 CLI 切得開", launch_cmd.split_prefix("cd a && env -u X FOO=1 claude -c")
      == ("cd a && env -u X FOO=1", "claude -c"))

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
