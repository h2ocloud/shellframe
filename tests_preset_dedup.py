#!/usr/bin/env python3
"""內建 preset 只提供一次，不能在清單裡出現同名兩份（v0.35.7）。

`load_config` 會替每個支援的 AI CLI 各補一次 preset，靠 `_default_ai_presets_offered`
記錄提供過哪些。原本的重複判斷只比 cmd 字串：使用者一旦改過內建 preset 的指令
（例如把 `opencode` 換成絕對路徑），而 offered 記錄又因為任何原因回退——config
從舊備份還原，或只剩舊的 `_default_ai_presets_migrated` 旗標（它只代表
Claude/Codex）——同名 preset 就會被再加一次。

跑法：.venv/bin/python tests_preset_dedup.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()
sys.path.insert(0, str(HERE))

import main as _main  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def load(cfg_dict):
    """跑真正的 load_config，config 檔換到臨時目錄。"""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "config.json"
        f.write_text(json.dumps(cfg_dict, ensure_ascii=False), encoding="utf-8")
        orig = _main.CONFIG_FILE
        _main.CONFIG_FILE = f
        try:
            return _main.load_config()
        finally:
            _main.CONFIG_FILE = orig


def names(cfg):
    return [p.get("name") for p in cfg.get("presets", [])]


def dupes(seq):
    seen, out = set(), []
    for x in seq:
        if x in seen:
            out.append(x)
        seen.add(x)
    return out

DEFAULT_NAMES = [p["name"] for p in _main._DEFAULT_AI_PRESETS]

# 1. 空 config：每個內建各一次
cfg = load({})
check("空 config 每個內建 preset 各補一次",
      sorted(names(cfg)) == sorted(DEFAULT_NAMES), str(names(cfg)))
check("沒有重複", not dupes(names(cfg)), str(dupes(names(cfg))))

# 2. offered 記錄完整 → 什麼都不加
full = {"presets": [dict(p) for p in _main._DEFAULT_AI_PRESETS],
        "_default_ai_presets_offered": sorted(DEFAULT_NAMES)}
cfg = load(full)
check("offered 完整時不再補", sorted(names(cfg)) == sorted(DEFAULT_NAMES), str(names(cfg)))

# 3. 使用者改過內建 preset 的 cmd，且 offered 記錄整個不見（等同從舊備份還原）
edited = {
    "presets": [
        {"name": "Claude", "cmd": "claude --my-own-flags", "icon": "🚀"},
        {"name": "OpenCode", "cmd": "/Users/alice/.opencode/bin/opencode", "icon": "🧩"},
    ],
}
cfg = load(edited)
check("cmd 被改過＋offered 遺失，同名 preset 不會被加第二份",
      not dupes(names(cfg)), f"重複：{dupes(names(cfg))}  全部：{names(cfg)}")
check("使用者改過的指令沒有被覆寫",
      next(p["cmd"] for p in cfg["presets"] if p["name"] == "Claude")
      == "claude --my-own-flags")
check("還沒提供過的內建仍然會補上",
      "Codex" in names(cfg) and "Pi" in names(cfg), str(names(cfg)))

# 4. 只有舊旗標（它只代表 Claude/Codex）＋ cmd 改過
legacy = {
    "presets": [
        {"name": "Claude", "cmd": "claude --my-own-flags", "icon": "🚀"},
        {"name": "Codex", "cmd": "sf-codex --my-own-flags", "icon": "🤖"},
        {"name": "Pi", "cmd": "/opt/bin/pi", "icon": "𝜋"},
    ],
    "_default_ai_presets_migrated": True,
}
cfg = load(legacy)
check("只剩舊旗標時，Pi 也不會被加第二份",
      not dupes(names(cfg)), f"重複：{dupes(names(cfg))}  全部：{names(cfg)}")

# 5. 使用者自己刪掉的內建，提供過就不再回來
deleted = {"presets": [], "_default_ai_presets_offered": sorted(DEFAULT_NAMES)}
cfg = load(deleted)
check("使用者刪掉的內建不會自己長回來", names(cfg) == [], str(names(cfg)))

# 6. 使用者自訂的變體（同一支 CLI 的另一個啟動器）本來就該共存，不是重複
variants = {
    "presets": [
        {"name": "Claude", "cmd": "claude --permission-mode bypassPermissions "
                                  "--dangerously-skip-permissions", "icon": "🚀"},
        {"name": "Claude (home)", "cmd": "sf-claude-home", "icon": "🏠"},
    ],
    "_default_ai_presets_offered": sorted(DEFAULT_NAMES),
}
cfg = load(variants)
check("同一支 CLI 的自訂變體共存，不被當成重複刪掉",
      "Claude" in names(cfg) and "Claude (home)" in names(cfg), str(names(cfg)))

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
