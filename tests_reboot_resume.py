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

for other in ("codex --full-auto", "agy", "bash -l", "/usr/bin/env python3 x.py"):
    check(f"非 claude 不碰：{other.split()[0]}", R(other, UUID) == other, R(other, UUID))

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

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
