"""Api mixin — 語音域（God-class 分批拆解 第二批）.

STT 設定與本機安裝、介面麥克風錄音 → 轉錄 → 注入分頁。

行為與搬家前相同（僅搬家）；main.py 的全域經 api_host 的 late-bound `main` 取得。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

from sf_config import CONFIG_LOCK
from api_host import main


class VoiceApiMixin:
    # ── STT (Speech-to-Text) settings ──
    def stt_status(self) -> str:
        """Return diagnostic info: which STT backends are available."""
        cfg = main.load_config().get("bridge", {})
        remote_url = cfg.get("stt_remote_url", "")
        backend = cfg.get("stt_backend", "auto")
        try:
            status = main.TelegramBridge.stt_status(remote_url)
        except Exception as e:
            return json.dumps({"error": str(e)})
        status["backend"] = backend
        return json.dumps(status)

    def stt_save_settings(self, backend: str, providers_json: str) -> str:
        """Update STT backend + provider chain in config + live bridge."""
        with CONFIG_LOCK:
            cfg = main.load_config()
            bridge_cfg = cfg.get("bridge", {})
            if backend in ("auto", "plugin", "local", "remote", "off"):
                bridge_cfg["stt_backend"] = backend
            if providers_json is not None:
                try:
                    providers = json.loads(providers_json) if providers_json else []
                    if not isinstance(providers, list):
                        return json.dumps({"success": False, "message": "providers must be a list"})
                    bridge_cfg["stt_providers"] = providers
                except json.JSONDecodeError as e:
                    return json.dumps({"success": False, "message": f"invalid JSON: {e}"})
            cfg["bridge"] = bridge_cfg
            main.save_config(cfg)
        # Apply to running bridge
        if self.bridge:
            self.bridge.config.stt_backend = bridge_cfg.get("stt_backend", "auto")
        return json.dumps({"success": True})

    def stt_get_providers(self) -> str:
        """Return the configured provider chain (for the settings UI)."""
        cfg = main.load_config()
        return json.dumps((cfg.get("bridge", {}) or {}).get("stt_providers") or [])

    def stt_install_local(self) -> str:
        """Install whisper.cpp + download base model.

        Picks the right package manager per platform:
          macOS:   brew install whisper-cpp
          Windows: winget install ggerganov.whisper-cpp (or choco)
          Linux:   apt / dnf hint (no auto-install — too varied)

        Always downloads the GGML base model to LOCAL_MODEL_DIR regardless
        of platform."""
        try:
            steps = []

            if main.IS_WIN:
                # Windows: try winget first, then chocolatey
                winget = shutil.which("winget")
                choco = shutil.which("choco")
                installed = False
                if winget:
                    r = subprocess.run(
                        [winget, "install", "--id", "ggerganov.whisper.cpp",
                         "--accept-source-agreements", "--accept-package-agreements",
                         "--silent"],
                        capture_output=True, text=True, timeout=600,
                    )
                    steps.append({"step": "winget install whisper.cpp", "rc": r.returncode,
                                  "out": r.stdout[-500:], "err": r.stderr[-500:]})
                    if r.returncode == 0 or "already installed" in (r.stdout + r.stderr).lower():
                        installed = True
                if not installed and choco:
                    r = subprocess.run(
                        [choco, "install", "whisper-cpp", "-y"],
                        capture_output=True, text=True, timeout=600,
                    )
                    steps.append({"step": "choco install whisper-cpp", "rc": r.returncode,
                                  "out": r.stdout[-500:], "err": r.stderr[-500:]})
                    if r.returncode == 0 or "already installed" in (r.stdout + r.stderr).lower():
                        installed = True
                if not installed:
                    return json.dumps({
                        "success": False,
                        "message": "No winget or chocolatey found. Install whisper.cpp manually from https://github.com/ggml-org/whisper.cpp/releases and add it to PATH.",
                        "steps": steps,
                    })
            else:
                # macOS / Linux: prefer Homebrew
                brew = shutil.which("brew")
                if not brew:
                    hint = ""
                    if shutil.which("apt"):
                        hint = " (or try `sudo apt install whisper-cpp` if your distro packages it)"
                    return json.dumps({
                        "success": False,
                        "message": f"Homebrew not found. Install from https://brew.sh first.{hint}",
                    })
                r = subprocess.run([brew, "install", "whisper-cpp"], capture_output=True, text=True, timeout=600)
                steps.append({"step": "brew install whisper-cpp", "rc": r.returncode,
                              "out": r.stdout[-500:], "err": r.stderr[-500:]})
                if r.returncode != 0 and "already installed" not in (r.stderr + r.stdout).lower():
                    return json.dumps({
                        "success": False,
                        "message": f"brew install failed: {r.stderr[-300:]}",
                        "steps": steps,
                    })

            # Download model (cross-platform via urllib.request)
            model_dir = main.TelegramBridge.LOCAL_MODEL_DIR
            model_dir.mkdir(parents=True, exist_ok=True)
            model_path = model_dir / main.TelegramBridge.LOCAL_MODEL_NAME
            if not model_path.exists():
                steps.append({"step": "download model", "url": main.TelegramBridge.LOCAL_MODEL_URL})
                req = urllib.request.Request(main.TelegramBridge.LOCAL_MODEL_URL, headers={"User-Agent": "shellframe"})
                with urllib.request.urlopen(req, timeout=600) as resp, open(model_path, "wb") as out:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
            steps.append({"step": "model_path", "path": str(model_path), "exists": model_path.exists()})

            return json.dumps({
                "success": True,
                "message": "Local STT installed",
                "model": str(model_path),
                "steps": steps,
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return json.dumps({"success": False, "message": str(e)})

    @staticmethod
    def _mic_ffmpeg() -> str:
        return shutil.which("ffmpeg") or (
            "/opt/homebrew/bin/ffmpeg" if os.path.exists("/opt/homebrew/bin/ffmpeg") else "")

    @staticmethod
    def _parse_dshow_audio_devices(listing: str) -> list:
        """從 `ffmpeg -list_devices` 的 stderr 撈 dshow 音訊裝置名。"""
        return re.findall(r'"([^"]+)"\s*\(audio\)', listing or "")

    def mic_record_start(self) -> str:
        proc = getattr(self, "_mic_proc", None)
        if proc is not None and proc.poll() is None:
            return json.dumps({"ok": False, "reason": "busy"})
        ffmpeg = self._mic_ffmpeg()
        if not ffmpeg:
            return json.dumps({"ok": False, "reason": "no_ffmpeg"})
        import tempfile
        out = os.path.join(tempfile.gettempdir(), f"sf_mic_{int(time.time())}.wav")

        def _spawn(in_args):
            # stdin=PIPE：之後寫 'q' 讓 ffmpeg 優雅收尾（正確寫 WAV header）
            return subprocess.Popen(
                [ffmpeg, "-hide_banner", "-loglevel", "error", *in_args,
                 "-ac", "1", "-ar", "16000", "-t", str(self._MIC_MAX_SEC), "-y", out],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        if sys.platform == "darwin":
            proc = _spawn(["-f", "avfoundation", "-i", ":default"])
            time.sleep(0.8)
            if proc.poll() is not None:  # 舊版 ffmpeg 不認 :default → 退 :0
                proc = _spawn(["-f", "avfoundation", "-i", ":0"])
                time.sleep(0.8)
        elif main.IS_WIN:
            try:
                r = subprocess.run(
                    [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                    capture_output=True, text=True, timeout=10)
                devices = self._parse_dshow_audio_devices((r.stderr or "") + (r.stdout or ""))
            except Exception:
                devices = []
            if not devices:
                return json.dumps({"ok": False, "reason": "no_mic_device"})
            proc = _spawn(["-f", "dshow", "-i", f"audio={devices[0]}"])
            time.sleep(0.8)
        else:
            proc = _spawn(["-f", "alsa", "-i", "default"])
            time.sleep(0.8)

        if proc.poll() is not None:
            detail = ""
            try:
                detail = (proc.stderr.read() or b"").decode("utf-8", "replace")[-400:]
            except Exception:
                pass
            main._dlog("mic", f"record start failed: {detail!r}")
            return json.dumps({"ok": False, "reason": "record_failed", "detail": detail})
        self._mic_proc = proc
        self._mic_path = out
        main._dlog("mic", f"recording → {out}")
        return json.dumps({"ok": True})

    def mic_record_stop(self, sid: str = "", cancel: bool = False) -> str:
        proc = getattr(self, "_mic_proc", None)
        path = getattr(self, "_mic_path", "") or ""
        self._mic_proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.write(b"q")
                proc.stdin.flush()
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
            try:
                proc.wait(timeout=8)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if cancel:
            try:
                os.unlink(path)
            except OSError:
                pass
            return json.dumps({"ok": True, "cancelled": True})
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        # 16kHz mono s16 ≈ 32KB/s；小於這個幾乎必是沒收到聲音（含 TCC 拒絕）
        if size < 12000:
            try:
                os.unlink(path)
            except OSError:
                pass
            return json.dumps({"ok": False, "reason": "empty_audio"})
        return self._mic_transcribe_inject(path, sid)

    def mic_retry_transcribe(self, sid: str = "") -> str:
        """裝完 STT 後重轉上一段錄音（音檔在失敗時被保留）。"""
        path = getattr(self, "_mic_last_wav", "") or ""
        if not path or not os.path.exists(path):
            return json.dumps({"ok": False, "reason": "no_audio"})
        return self._mic_transcribe_inject(path, sid)

    def _mic_transcribe_inject(self, path: str, sid: str) -> str:
        br = self.bridge
        text = ""
        try:
            if br is not None:
                text = br._transcribe_voice(path)
            else:
                # TG bridge 沒開也能轉：借 TelegramBridge 的 STT 鏈（方法只用
                # config.stt_backend 與 class 屬性，不碰 bridge 執行狀態）
                import types as _t
                backend = (main.load_config().get("bridge", {}) or {}).get("stt_backend", "auto")
                shim = _t.SimpleNamespace(config=_t.SimpleNamespace(
                    stt_backend=backend, stt_remote_url=""))
                text = main.TelegramBridge._transcribe_voice(shim, path)
        except Exception as e:
            main._dlog("mic", f"transcribe error: {e}")
        if not (text or "").strip():
            ready = False
            try:
                cfg = main.load_config().get("bridge", {}) or {}
                st = main.TelegramBridge.stt_status(cfg.get("stt_remote_url", ""))
                ready = bool(st["local"]["ready"] or st["plugin"]["ready"] or st["remote"]["ready"])
            except Exception:
                pass
            self._mic_last_wav = path  # 留檔給裝完後 mic_retry_transcribe
            return json.dumps({"ok": False, "reason": "stt_failed" if ready else "no_backend"})
        try:
            if br is not None:
                text = br._refine_transcript(text) or text
        except Exception:
            pass
        try:
            os.unlink(path)
        except OSError:
            pass
        self._mic_last_wav = ""
        s = self.sessions.get(sid)
        if not s:
            return json.dumps({"ok": True, "text": text, "injected": False})
        is_ai = bool(main.bridge_telegram._detect_ai(getattr(s, "cmd", "") or ""))
        if is_ai:
            # 下 tag 讓 AI 知道這是語音轉文字、要先解析語意再動作
            payload = main.MIC_STT_TAG + "\n" + text
            self._send_text_to_session(s, payload, submit=True)
        else:
            # 非 AI 分頁（shell 等）：純文字貼進輸入行、不送出，避免誤執行
            self._send_text_to_session(s, text, submit=False)
        return json.dumps({"ok": True, "text": text, "injected": True, "ai": is_ai})

    def mic_install_ffmpeg(self) -> str:
        """引導安裝 ffmpeg（錄音依賴）——一次到位，不只給指令。"""
        try:
            if main.IS_WIN:
                winget = shutil.which("winget")
                if not winget:
                    return json.dumps({"success": False,
                                       "message": "找不到 winget，請手動安裝 ffmpeg 後重試"})
                r = subprocess.run(
                    [winget, "install", "--id", "Gyan.FFmpeg",
                     "--accept-source-agreements", "--accept-package-agreements", "--silent"],
                    capture_output=True, text=True, timeout=900)
            elif sys.platform == "darwin":
                brew = shutil.which("brew") or (
                    "/opt/homebrew/bin/brew" if os.path.exists("/opt/homebrew/bin/brew") else "")
                if not brew:
                    return json.dumps({"success": False,
                                       "message": "找不到 Homebrew，請先裝 brew 或手動安裝 ffmpeg"})
                r = subprocess.run([brew, "install", "ffmpeg"],
                                   capture_output=True, text=True, timeout=1800)
            else:
                return json.dumps({"success": False,
                                   "message": "請用系統套件管理器安裝 ffmpeg（apt/dnf）"})
            ok = bool(self._mic_ffmpeg())
            return json.dumps({
                "success": ok,
                "message": "ffmpeg 已就緒" if ok else ((r.stderr or r.stdout) or "")[-300:],
            })
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})
