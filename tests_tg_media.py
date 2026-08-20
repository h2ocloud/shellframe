#!/usr/bin/env python3
"""TG 媒體下載/影片掉訊回歸測試（v0.29.14）。

修前：`_handle_update` 只認 photo/doc/voice/audio，`video`/`video_note`/
`animation` 全漏 → 傳影片靜默丟棄；且 getFile 有 20MB 上限，超過就失敗
回空字串又不通知。本測試涵蓋新的 `_fetch_media`：20MB 防護、失敗必通知、
成功回路徑。

跑法：
    .venv/bin/python tests_tg_media.py
"""

import importlib.util
import os
import types

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)

# 測試不得寫進 production 的 bridge log（使用者靠那份 log 除錯）
_bt._blog = lambda msg: None


def _bridge(sent, download_result="/tmp/tg_x.mp4"):
    br = object.__new__(_bt.TelegramBridge)
    br.config = types.SimpleNamespace(bot_token="TEST")
    _bt.tg_api = lambda tok, m, p=None: (sent.append((m, p)) or {"ok": True})
    calls = {"getfile": 0}

    def fake_dl(file_id, ext=""):
        calls["getfile"] += 1
        return download_result
    br._download_tg_file = fake_dl
    br._getfile_calls = calls
    return br


# ── 1. 超過 20MB → 不呼叫 getFile、主動通知、回 '' ──
def test_oversize_notifies_and_skips_download():
    sent = []
    br = _bridge(sent)
    big = {"file_id": "V1", "file_size": 60 * 1024 * 1024, "file_name": "clip.mp4"}
    path = br._fetch_media(big, ".mp4", chat_id=999, label="影片")
    assert path == "", path
    assert br._getfile_calls["getfile"] == 0, "超過 20MB 不該還去 getFile"
    assert sent and any("20MB" in (p or {}).get("text", "") for _, p in sent), sent


# ── 2. 正常大小、下載成功 → 回本地路徑、不通知 ──
def test_small_download_ok():
    sent = []
    br = _bridge(sent, download_result="/tmp/tg_ok.mp4")
    vid = {"file_id": "V2", "file_size": 5 * 1024 * 1024}
    path = br._fetch_media(vid, ".mp4", chat_id=999, label="影片")
    assert path == "/tmp/tg_ok.mp4", path
    assert br._getfile_calls["getfile"] == 1
    assert sent == [], f"成功不該通知：{sent}"


# ── 3. 下載失敗（回 ''）→ 主動通知、回 '' ──
def test_download_failure_notifies():
    sent = []
    br = _bridge(sent, download_result="")
    vid = {"file_id": "V3", "file_size": 3 * 1024 * 1024}
    path = br._fetch_media(vid, ".mp4", chat_id=999, label="影片")
    assert path == ""
    assert sent and any("下載失敗" in (p or {}).get("text", "") for _, p in sent), sent


# ── 4. 沒有 file_size（TG 偶爾不給）→ 照樣嘗試下載（不誤擋）──
def test_missing_file_size_still_downloads():
    sent = []
    br = _bridge(sent, download_result="/tmp/tg_nosize.mp4")
    vid = {"file_id": "V4"}  # 無 file_size
    path = br._fetch_media(vid, ".mp4", chat_id=999, label="影片")
    assert path == "/tmp/tg_nosize.mp4"
    assert br._getfile_calls["getfile"] == 1


# ── 5. 壞的 media dict（無 file_id）→ 安全回 '' 不炸 ──
def test_bad_media_safe():
    sent = []
    br = _bridge(sent)
    assert br._fetch_media({}, ".mp4", 999, "影片") == ""
    assert br._fetch_media(None, ".mp4", 999, "影片") == ""


if __name__ == "__main__":
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
                fails.append(name)
    print(f"\n=== {'ALL PASS' if not fails else f'{len(fails)} FAILED'} ===")
    raise SystemExit(1 if fails else 0)
