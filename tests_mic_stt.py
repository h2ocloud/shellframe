#!/usr/bin/env python3
"""UI 麥克風語音輸入回歸測試（v0.29.18）。

純邏輯面：dshow 裝置解析、STT tag 內容、未錄音/無 ffmpeg/無留檔的
錯誤路徑（前端靠 reason 分流引導安裝，值變了引導就斷）。

跑法：
    .venv/bin/python tests_mic_stt.py
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()
sys.path.insert(0, str(Path(__file__).parent))

from main import Api, MIC_STT_TAG  # noqa: E402

api = Api()
passed = failed = 0


def check(name, ok):
    global passed, failed
    passed += 1 if ok else 0
    failed += 0 if ok else 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")


# ── 1. dshow 音訊裝置解析（Windows 錄音靠它挑裝置）──
listing = '''
[dshow @ 0x1] "Integrated Camera" (video)
[dshow @ 0x1] "Microphone Array (Realtek)" (audio)
[dshow @ 0x1] "Line In" (audio)
'''
devs = Api._parse_dshow_audio_devices(listing)
check("dshow 只取 audio 裝置、保序", devs == ["Microphone Array (Realtek)", "Line In"])
check("dshow 空輸入回空 list", Api._parse_dshow_audio_devices("") == [])

# ── 2. 注入 tag：要能讓 AI 認出是語音逐字稿且先解析意圖 ──
check("tag 標明語音與 STT", "語音" in MIC_STT_TAG and "STT" in MIC_STT_TAG)
check("tag 要求先解析語意/意圖", "解析" in MIC_STT_TAG)

# ── 3. 錯誤路徑的 reason 值（前端 micGuide 據此分流）──
r = json.loads(api.mic_record_stop("s1"))
check("未錄音 stop → empty_audio", r == {"ok": False, "reason": "empty_audio"})

with patch.object(Api, "_mic_ffmpeg", staticmethod(lambda: "")):
    r = json.loads(api.mic_record_start())
check("無 ffmpeg → no_ffmpeg", r == {"ok": False, "reason": "no_ffmpeg"})

api._mic_last_wav = ""
r = json.loads(api.mic_retry_transcribe("s1"))
check("無留檔 retry → no_audio", r == {"ok": False, "reason": "no_audio"})

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
