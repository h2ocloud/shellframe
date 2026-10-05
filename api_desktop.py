"""Api mixin — 桌面整合域（God-class 分批拆解 第二批）.

開檔/開網址、剪貼簿文字/圖片/檔案、拖放 pasteboard、存圖。

行為與搬家前相同（僅搬家）；main.py 的全域經 api_host 的 late-bound `main` 取得。
"""

import base64
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from api_host import main


class DesktopApiMixin:
    def open_local_file(self, path: str) -> str:
        """Open a file (or directory) in the OS default app.
        Used by the terminal Ctrl+Click handler."""
        try:
            if not path:
                return json.dumps({"success": False, "message": "empty path"})
            # Resolve relative paths against the active session's CWD if known
            p = Path(path).expanduser()
            if not p.is_absolute():
                # Try resolving relative to user's home — not perfect but
                # avoids accidentally opening files in shellframe's cwd
                p = Path.home() / p
            if not p.exists():
                return json.dumps({"success": False, "message": f"not found: {p}"})
            if main.IS_WIN:
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.Popen(["/usr/bin/open", str(p)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", str(p)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return json.dumps({"success": True, "path": str(p)})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    def open_url(self, url: str) -> str:
        """Open an http(s) URL in the OS default browser.
        Used by the terminal Ctrl+Click handler for hard-wrapped URLs that
        WebLinksAddon can't stitch across buffer lines."""
        try:
            if not url or not url.lower().startswith(("http://", "https://")):
                return json.dumps({"success": False, "message": "not an http url"})
            if main.IS_WIN:
                os.startfile(url)  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.Popen(["/usr/bin/open", url],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", url],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return json.dumps({"success": True})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    def copy_text(self, text: str, surface: str = "") -> str:
        """Copy text to the system clipboard. Windows writes CF_UNICODETEXT
        through the Win32 API (clip.exe cannot take UTF-16 cleanly, see
        _win_set_clipboard_text); macOS uses pbcopy, Linux xclip/wl-copy.

        `surface` names the UI path that asked (live-rightclick, history-ctrl-c,
        ...). Every call logs surface, length, ok/fail and duration to the
        debug log -- never the text, and never an exception message (a codec
        error message quotes the offending character)."""
        t0 = time.perf_counter()
        n = len(text) if isinstance(text, str) else 0
        tag = "".join(c for c in str(surface or "") if c.isalnum() or c in "_.-")[:32] or "?"
        via, err = ("win32" if main.IS_WIN else "cli"), ""
        try:
            if not n:
                # An empty write would clear whatever the user has on the clipboard.
                err = "empty text"
            elif main.IS_WIN:
                main._win_set_clipboard_text(text)
            else:
                # macOS: pbcopy. Linux fallback: try xclip then wl-copy.
                tool = 'pbcopy' if shutil.which('pbcopy') else (
                    'xclip' if shutil.which('xclip') else (
                        'wl-copy' if shutil.which('wl-copy') else None))
                if not tool:
                    err = "no clipboard tool found"
                else:
                    via = tool
                    args = [tool, '-selection', 'clipboard'] if tool == 'xclip' else [tool]
                    p = subprocess.Popen(args, stdin=subprocess.PIPE)
                    p.communicate(text.encode('utf-8'))
        except Exception as e:
            err = type(e).__name__
            if getattr(e, "winerror", None):
                err += f" winerror={e.winerror}"
        main._dlog("clipboard", f"copy surface={tag} len={n} ok={not err} via={via} "
                           f"ms={(time.perf_counter() - t0) * 1000:.0f}"
                           + (f" err={err}" if err else ""))
        return f"ERROR: {err}" if err else "ok"

    def paste_text(self) -> str:
        """Read text from system clipboard."""
        try:
            if main.IS_WIN:
                # PowerShell Get-Clipboard handles Unicode properly
                result = subprocess.run(
                    ['powershell', '-NoProfile', '-Command', 'Get-Clipboard -Raw'],
                    capture_output=True, text=True, timeout=3
                )
                # PowerShell adds a trailing newline; strip just one
                out = result.stdout
                return out.rstrip('\r\n') if out else ''
            else:
                tool = 'pbpaste' if shutil.which('pbpaste') else (
                    'xclip' if shutil.which('xclip') else (
                        'wl-paste' if shutil.which('wl-paste') else None))
                if not tool:
                    return ''
                args = [tool, '-selection', 'clipboard', '-o'] if tool == 'xclip' else [tool]
                result = subprocess.run(args, capture_output=True, text=True, timeout=3)
                return result.stdout
        except Exception as e:
            return ''

    def get_clipboard_files(self) -> str:
        """Get file paths from system clipboard (Finder copy).
        Returns JSON array of file paths, or empty array if no files."""
        try:
            if main.IS_WIN:
                # Windows: use PowerShell to read clipboard file list
                result = subprocess.run(
                    ["powershell", "-Command", "Get-Clipboard -Format FileDropList | ForEach-Object { $_.FullName }"],
                    capture_output=True, text=True, timeout=3
                )
                paths = [p.strip() for p in result.stdout.strip().split('\n') if p.strip()]
                return json.dumps(paths)
            else:
                # macOS: use osascript to read Finder clipboard
                result = subprocess.run(
                    ["osascript", "-e",
                     'try\n'
                     'set theFiles to (the clipboard as «class furl»)\n'
                     'POSIX path of theFiles\n'
                     'on error\n'
                     'try\n'
                     'set theList to (the clipboard as list)\n'
                     'set out to ""\n'
                     'repeat with f in theList\n'
                     'set out to out & POSIX path of f & linefeed\n'
                     'end repeat\n'
                     'out\n'
                     'on error\n'
                     '""\n'
                     'end try\n'
                     'end try'],
                    capture_output=True, text=True, timeout=3
                )
                paths = [p.strip() for p in result.stdout.strip().split('\n') if p.strip()]
                # Validate paths exist
                paths = [p for p in paths if os.path.exists(p)]
                return json.dumps(paths)
        except Exception:
            return json.dumps([])

    def paths_exist(self, paths_json: str) -> str:
        """拖放路徑修復鏈用：回傳每個候選路徑是否真實存在。

        WebKit 對含非 ASCII 檔名的拖放，text/uri-list 可能只給到資料夾
        （檔名整段消失）——前端用 dt.files 的檔名把資料夾補回完整路徑後，
        必須經這裡驗證存在才敢注入，驗不過就退 blob fallback。"""
        try:
            paths = json.loads(paths_json or "[]")
            return json.dumps([bool(p) and os.path.exists(str(p)) for p in paths])
        except Exception:
            return json.dumps([])

    def drag_pasteboard_paths(self) -> str:
        """macOS：從 drag pasteboard 直讀拖曳檔案的真實路徑（drop 後仍在）。

        新版 macOS 的 Finder 拖曳放上 pasteboard 的是 file-reference URL
        （file:///.file/id=…），WebKit 轉不出 text/uri-list——DOM 端 types
        只剩 ["Files"]、完全拿不到路徑（2026-08-05 實案，js:drop 足跡）。
        原生 pasteboard 上這顆 URL 還在，NSURL.path() 會解回真實路徑
        （含 CJK 檔名）。"""
        if sys.platform != "darwin":
            return json.dumps([])
        try:
            from AppKit import NSPasteboard
            from Foundation import NSURL
            pb = NSPasteboard.pasteboardWithName_("Apple CFPasteboard drag")
            paths = []
            for it in (pb.pasteboardItems() or []):
                u = it.stringForType_("public.file-url")
                if not u:
                    continue
                try:
                    p = NSURL.URLWithString_(u).path()
                except Exception:
                    p = None
                if p:
                    paths.append(str(p))
            main._dlog("drop", f"drag pasteboard → {paths!r}")
            return json.dumps(paths)
        except Exception as e:
            main._dlog("drop", f"drag pasteboard read failed: {e}")
            return json.dumps([])

    def drag_pasteboard_snapshot(self) -> str:
        """同 drag_pasteboard_paths，但附上 pasteboard 的 changeCount。

        drag pasteboard **會留著上一次拖曳的內容**——實測沒有任何拖曳進行中，
        仍讀得到十分鐘前那次拖進來的 pptx。所以「路徑數量跟這次拖進來的檔案數
        對得上」不足以判定它是這次的：從瀏覽器拖 in-memory blob 的來源根本不寫
        這塊 pasteboard，數量又剛好都是 1 個，就會把殘留的舊檔附上去。
        changeCount 只在有人真的寫入時遞增，是唯一分得出「這次寫的」跟「上次留
        下的」的訊號。前端拿它跟最後一次採用過的值比對，相同就不信。"""
        if sys.platform != "darwin":
            return json.dumps({"paths": [], "change": -1})
        try:
            from AppKit import NSPasteboard
            from Foundation import NSURL
            pb = NSPasteboard.pasteboardWithName_("Apple CFPasteboard drag")
            change = int(pb.changeCount())
            paths = []
            for it in (pb.pasteboardItems() or []):
                u = it.stringForType_("public.file-url")
                if not u:
                    continue
                try:
                    p = NSURL.URLWithString_(u).path()
                except Exception:
                    p = None
                if p:
                    paths.append(str(p))
            return json.dumps({"paths": paths, "change": change})
        except Exception as e:
            main._dlog("drop", f"drag pasteboard snapshot failed: {e}")
            return json.dumps({"paths": [], "change": -1})

    def drag_mark(self) -> str:
        """拖曳進入視窗 → 開始盯滑鼠左鍵，記下放開的那一刻。

        JS 拿不到「使用者放開滑鼠」的時間：drop 事件本身就是放開之後才被派送
        的，所以前端量到的 sinceDragOver 分不出兩種完全不同的情況——使用者拖著
        不動幾秒才放手，還是放手後 WebKit 卡在 dispatch 前面。這裡用
        NSEvent.pressedMouseButtons() 補上那一刻（20ms 輪詢，最多盯 60 秒），
        js_debug('drop') 進來時就能算出真正的感知延遲。
        觸控板 tap-drag 之類左鍵本來就沒按下的情況標成 unknown，不要謊報 0。"""
        if sys.platform != "darwin":
            return "skip"
        if getattr(self, "_drag_watch_running", False):
            return "already"
        self._drag_mouse_up_ts = 0.0
        self._drag_watch_running = True

        def _watch():
            try:
                from AppKit import NSEvent
                t0 = time.time()
                # 先確認現在真的按著左鍵，否則這次量測沒有意義
                pressed = False
                while time.time() - t0 < 1.0:
                    if int(NSEvent.pressedMouseButtons()) & 1:
                        pressed = True
                        break
                    time.sleep(0.02)
                if not pressed:
                    self._drag_mouse_up_ts = -1.0     # unknown
                    return
                while time.time() - t0 < 60.0:
                    if not (int(NSEvent.pressedMouseButtons()) & 1):
                        self._drag_mouse_up_ts = time.time()
                        return
                    time.sleep(0.02)
            except Exception as e:
                main._dlog("drop", f"drag_mark watch failed: {e}")
                self._drag_mouse_up_ts = -1.0
            finally:
                self._drag_watch_running = False

        threading.Thread(target=_watch, daemon=True).start()
        return "ok"

    def js_debug(self, tag: str, msg: str) -> str:
        """前端事件落 debug log。拖放/貼上這類 WebKit 行為差異在後端毫無
        足跡（2026-08-05 drop 掉檔名查了半天），給前端一條 log 通道。"""
        extra = ""
        if tag == "drop":
            up = getattr(self, "_drag_mouse_up_ts", 0.0)
            if up and up > 0:
                extra = f"  sinceMouseUp={int((time.time() - up) * 1000)}ms"
            elif up == -1.0:
                extra = "  sinceMouseUp=unknown(左鍵未按下)"
            else:
                extra = "  sinceMouseUp=?(還沒放開就進 drop?)"
        main._dlog(f"js:{tag}", str(msg)[:500] + extra)
        return "ok"

    def save_file_from_clipboard(self, data_url: str, filename: str) -> str:
        """Save a non-image file from clipboard data URL. Returns saved path."""
        try:
            _, encoded = data_url.split(",", 1)
            file_data = base64.b64decode(encoded)
            # Microsecond precision (`%f` = 6 digits) so multi-file pastes
            # within the same second get distinct paths; without this, every
            # blob written in the same second overwrote the previous one and
            # the JS attachFile dedup (matching on path equality) collapsed
            # the chips down to one — user thought only one file attached.
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            # Preserve original extension
            ext = Path(filename).suffix or '.bin'
            safe_name = Path(filename).stem[:50]
            path = main.CLAUDE_TMP / f"clipboard_{ts}_{safe_name}{ext}"
            path.write_bytes(file_data)
            return str(path)
        except Exception as e:
            return f"ERROR: {e}"

    def save_image(self, data_url: str) -> str:
        try:
            _, encoded = data_url.split(",", 1)
            img_data = base64.b64decode(encoded)
            # See save_file_from_clipboard for the multi-paste collision
            # rationale — same fix here.
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = main.CLAUDE_TMP / f"clipboard_{ts}.png"
            path.write_bytes(img_data)

            cutoff = time.time() - 3600
            for f in main.CLAUDE_TMP.glob("clipboard_*.png"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                except OSError:
                    main._swallow("Api.save_image:4472")
            return str(path)
        except Exception as e:
            return f"ERROR: {e}"

    def read_clipboard_image(self) -> str:
        """Read an image from the system clipboard. Returns a data URL
        (data:image/png;base64,...) or '' if no image present.

        WKWebView's navigator.clipboard.read() is unreliable on macOS for
        image blobs (permission gating and incomplete MIME exposure), so the
        UI falls back here. We read NSPasteboard directly via PyObjC: try
        PNG first, fall back to TIFF and re-encode through NSBitmapImageRep
        if the source is e.g. a screenshot (TIFF on the pasteboard).
        """
        if main.IS_WIN:
            try:
                ps = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$img = [System.Windows.Forms.Clipboard]::GetImage()
if ($null -eq $img) { exit 2 }
$ms = New-Object System.IO.MemoryStream
try {
  $img.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
  [Convert]::ToBase64String($ms.ToArray())
} finally {
  $ms.Dispose()
  $img.Dispose()
}
"""
                r = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-STA",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        ps,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if r.returncode != 0:
                    return ''
                b64 = (r.stdout or '').strip()
                if not b64:
                    return ''
                return 'data:image/png;base64,' + b64
            except Exception as e:
                try:
                    main._dlog('clipboard', f'windows read_clipboard_image failed: {e}')
                except Exception:
                    main._swallow("Api.read_clipboard_image:4527")
                return ''
        if platform.system() != 'Darwin':
            return ''
        try:
            from AppKit import (
                NSPasteboard,
                NSPasteboardTypePNG,
                NSPasteboardTypeTIFF,
                NSBitmapImageRep,
            )
            pb = NSPasteboard.generalPasteboard()
            data = pb.dataForType_(NSPasteboardTypePNG)
            if data is None:
                tiff = pb.dataForType_(NSPasteboardTypeTIFF)
                if tiff is None:
                    return ''
                rep = NSBitmapImageRep.imageRepWithData_(tiff)
                if rep is None:
                    return ''
                # NSBitmapImageFileTypePNG = 4
                data = rep.representationUsingType_properties_(4, None)
                if data is None:
                    return ''
            raw = bytes(data)
            return 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')
        except Exception as e:
            try:
                main._dlog('clipboard', f'read_clipboard_image failed: {e}')
            except Exception:
                main._swallow("Api.read_clipboard_image:4557")
            return ''
