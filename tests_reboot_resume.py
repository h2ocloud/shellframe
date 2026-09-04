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
check("找得到的 uuid → True（用真實 manifest 裡的一筆驗）", any(
    E(x.get("claude_session_id", ""))
    for x in __import__("json").load(
        open(Path.home() / ".config/shellframe/config.json")).get("session_manifest", [])
    if x.get("claude_session_id")))

# ── Windows 路徑：沒有 lsof、沒有 tmux pane，只能靠「spawn 之後才出現的
#    rollout」＋認領表。用真的 rollout 檔造情境，不是 mock 檔名。 ──
import glob as _glob
import os as _os
import types as _types
from unittest.mock import patch as _patch

rolls = sorted(
    _glob.glob(_os.path.expanduser("~/.codex/sessions/*/*/*/rollout-*.jsonl")),
    key=_os.path.getmtime)
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
else:
    print("  [SKIP] 本機 rollout 少於兩份，跳過 Windows 認領情境")

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
