#!/usr/bin/env python3
"""拖放路徑修復鏈後端回歸測試（v0.29.26）。

WebKit 對含非 ASCII 檔名的拖放，text/uri-list 可能只給到資料夾——前端用
dt.files 檔名補回完整路徑後靠 paths_exist 驗證，驗過才注入。這裡測後端
驗證的正確性與容錯。

跑法：.venv/bin/python tests_drop_paths.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()
sys.path.insert(0, str(Path(__file__).parent))

from main import Api  # noqa: E402

api = Api()
passed = failed = 0


def check(name, ok):
    global passed, failed
    passed += 1 if ok else 0
    failed += 0 if ok else 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")


# CJK 檔名真檔（就是實案的形狀）
with tempfile.NamedTemporaryFile(suffix="_遠東商銀_測試.pptx", delete=False) as f:
    real = f.name

r = json.loads(api.paths_exist(json.dumps(
    [real, "/Users/nobody/不存在.pptx", "/tmp/", "", None])))
check("CJK 真檔 True、不存在/空值 False", r[0] is True and r[1:] == [False, True, False, False]
      if os.path.isdir("/tmp") else r == [True, False, False, False, False])
check("目錄也算存在（前端另擋結尾斜線）", json.loads(
    api.paths_exist(json.dumps(["/tmp"])))[0] is True)
check("壞 JSON 回空 list", json.loads(api.paths_exist("not json")) == [])
check("空輸入回空 list", json.loads(api.paths_exist("")) == [])

os.unlink(real)

# drag_pasteboard_paths：非 darwin 回空；darwin 回 JSON list（內容依當下
# pasteboard，不驗值只驗形狀與不炸）
from unittest.mock import patch as _patch
with _patch("sys.platform", "linux"):
    check("非 darwin 回空 list", json.loads(api.drag_pasteboard_paths()) == [])
if sys.platform == "darwin":
    r = json.loads(api.drag_pasteboard_paths())
    check("darwin 回 JSON list 不炸", isinstance(r, list))

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
