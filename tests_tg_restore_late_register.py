#!/usr/bin/env python3
"""重啟後 TG 路由還原：分頁比 poll loop 晚註冊（v0.37.7）。

回報：家用機 Telegram「整個脫鉤」，訊息送不進去。bridge 其實有收到訊息、
也沒有 409；錯在路由——v0.36.1 起 bridge 由 Python 先起，poll loop 一開始就
跑 `_restore_user_routing`，那時 UI 還沒把分頁註冊進來（log：`[restore] slots=[]`），
使用者原本 /N 選的分頁還原不了，`get_active_sid` 退到第一格，而且收到訊息時
會把第一格「寫成」使用者的選擇、存檔再把磁碟上原本的選擇蓋掉——錯的落點
從此黏住，每則訊息都掉進第一個分頁（實例：那格剛好 OAuth 401，完全沒回應）。

這裡釘住：
  1. restore 當下還原不了的選擇會暫存，該分頁註冊時補上
  2. 暫存期間有訊息進來，不能把第一格寫成使用者的選擇
  3. 暫存期間存檔，不能把磁碟上原本的選擇洗掉
  4. 分頁已註冊時照舊立即還原（熱重載路徑）

跑法：.venv/bin/python tests_tg_restore_late_register.py
"""

import importlib.util
import json
import os
import tempfile
import threading
import time
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(_HERE, "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
_bt._blog = lambda msg: None

UID, CHAT = 42, 4200
FAILED = []


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        FAILED.append(name)


def _bridge(state_file: Path, saved: dict):
    state_file.write_text(json.dumps(saved), encoding="utf-8")
    br = object.__new__(_bt.TelegramBridge)
    br.slots = {}
    br._slot_order = []
    br._slots_lock = threading.Lock()
    br._user_active = {}
    br._user_chat = {}
    br._default_active_sid = None
    br._offset = 7
    br._last_prune_ts = time.time() + 3600      # 別讓測試去打 sfctl
    br.paused = False
    br.config = type("C", (), {"bot_token": "t"})()
    br._OFFSET_FILE = state_file                # _save_state 寫這個（instance）
    # _load_persisted 是 classmethod、讀 class 層的真狀態檔——這裡一定要蓋掉，
    # 否則測試會去讀使用者本機的 ~/.config/shellframe/tg_offset.json。
    br._load_persisted = lambda: json.loads(state_file.read_text(encoding="utf-8"))
    br._load_rate_limit_seen = lambda: None     # 跟本測試無關
    _bt.tg_api = lambda token, method, payload=None, **kw: {"ok": True}
    return br


def _reg(br, sid, label):
    br.register_session(sid, label, lambda t: None, cols=100, rows=30)


def _persisted_active(state_file: Path):
    return json.loads(state_file.read_text(encoding="utf-8")).get("user_active", {})


def main():
    tmp = Path(tempfile.mkdtemp(prefix="sf-restore-"))

    # ── 1+2+3：poll loop 先跑、分頁後註冊（實際 v0.36.1 之後的順序）──
    sf = tmp / "state1.json"
    br = _bridge(sf, {"offset": 7, "user_active": {str(UID): "s110"},
                      "user_chat": {str(UID): CHAT}})
    br._restore_user_routing()                   # slots=[] 的當下
    check("slots 空時選擇沒被丟掉、先暫存",
          getattr(br, "_pending_user_active", {}).get(UID) == "s110",
          getattr(br, "_pending_user_active", None))
    check("暫存期間 _user_active 還是空的（沒亂指）", UID not in br._user_active)

    _reg(br, "s86", "spark 模型總控")             # 第一格先註冊
    check("第一格註冊後，暫存的選擇仍在",
          br._pending_user_active.get(UID) == "s110")

    # 暫存期間的存檔（每則訊息處理前都會存）不能把磁碟上的 s110 洗掉
    br._save_state()
    check("暫存期間存檔保留原本的選擇",
          _persisted_active(sf).get(str(UID)) == "s110", _persisted_active(sf))

    # 暫存期間模擬「收到訊息時的自動追蹤」那一段
    active = br.get_active_sid(UID)
    if (active and UID not in br._user_active
            and UID not in (br._pending_user_active or {})):
        br._user_active[UID] = active
    check("暫存期間不把第一格寫成使用者的選擇", UID not in br._user_active,
          br._user_active)

    _reg(br, "s110", "研報")                      # 使用者原本選的分頁註冊進來
    check("該分頁註冊時補回使用者的選擇",
          br._user_active.get(UID) == "s110", br._user_active)
    check("get_active_sid 指回 s110（不是第一格）",
          br.get_active_sid(UID) == "s110", br.get_active_sid(UID))
    check("補上後暫存清空", UID not in br._pending_user_active)
    check("補上後磁碟也是 s110", _persisted_active(sf).get(str(UID)) == "s110")

    # 使用者重啟後自己 /N 換過分頁 → 之後才註冊的舊選擇不能蓋掉新的
    sf2 = tmp / "state2.json"
    br2 = _bridge(sf2, {"offset": 7, "user_active": {str(UID): "s110"}})
    br2._restore_user_routing()
    _reg(br2, "s86", "spark 模型總控")
    br2._user_active[UID] = "s86"                # 使用者手動 /1
    br2._pending_user_active.pop(UID, None)       # /N 走的是正式路徑
    _reg(br2, "s110", "研報")
    check("使用者重啟後手動換的分頁不被舊選擇蓋掉",
          br2._user_active.get(UID) == "s86", br2._user_active)

    # ── 4：熱重載路徑——分頁已註冊，restore 當下就還原 ──
    sf3 = tmp / "state3.json"
    br3 = _bridge(sf3, {"offset": 7, "user_active": {str(UID): "s110"}})
    _reg(br3, "s86", "spark 模型總控")
    _reg(br3, "s110", "研報")
    br3._restore_user_routing()
    check("分頁已註冊時照舊立即還原", br3._user_active.get(UID) == "s110",
          br3._user_active)
    check("立即還原時不留暫存", not br3._pending_user_active)

    # ── default_active_sid 同樣適用 ──
    sf4 = tmp / "state4.json"
    br4 = _bridge(sf4, {"offset": 7, "user_active": {},
                        "default_active_sid": "s110"})
    br4._restore_user_routing()
    br4._save_state()
    check("暫存的 default 存檔不被洗成 None",
          json.loads(sf4.read_text())["default_active_sid"] == "s110")
    _reg(br4, "s86", "spark 模型總控")
    _reg(br4, "s110", "研報")
    check("default 分頁註冊後補上", br4._default_active_sid == "s110")
    check("沒有個人選擇時落到補回的 default（不是第一格）",
          br4.get_active_sid(UID) == "s110", br4.get_active_sid(UID))

    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED")
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
