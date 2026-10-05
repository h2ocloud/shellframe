"""Api mixin — 眼鏡中繼域（God-class 分批拆解 第二批）.

分頁開放給眼鏡、眼鏡端狀態與轉錄回報。

行為與搬家前相同（僅搬家）；main.py 的全域經 api_host 的 late-bound `main` 取得。
"""

import json
import os
import time

from sf_config import CONFIG_LOCK
from api_host import main


class GlassesApiMixin:
    def set_session_glasses(self, sid: str, enabled: bool, source: str = "") -> str:
        s = self.sessions.get(sid)
        if not s:
            return json.dumps({"success": False, "message": f"no such session {sid}"})
        with CONFIG_LOCK:
            was = sid in set(main.load_config().get("glasses_allowed_sessions", []) or [])
            s._glasses_enabled = bool(enabled)
            cfg = main.load_config()
            allowed = set(cfg.get("glasses_allowed_sessions", []) or [])
            if enabled:
                allowed.add(sid)
            else:
                allowed.discard(sid)
            cfg["glasses_allowed_sessions"] = sorted(allowed)
            # "沒有全開按鈕" 只是 UI 的性質，不是強制的限制：一個 shell 迴圈五秒就能把
            # 每個分頁各開一次。實際發生過（2026-08-31 有支程式這樣做，11 個分頁全開了
            # 二十分鐘沒人發現）。擋不住的就要看得見——所以每一次變更都留痕，`sfctl
            # glasses` 會把最近幾筆印出來。
            # 只有真的改變狀態才留痕。不然對同一個 sid 連下 40 次 no-op deny
            # 就能把先前的紀錄全部擠出環狀緩衝——而授權本身完全沒動。
            # 「擋不住的就要看得見」，那個「看得見」不能這麼容易被洗掉。
            if was != bool(enabled):
                trail = list(cfg.get("glasses_audit") or [])
                trail.append({
                    "ts": int(time.time()),
                    "sid": sid,
                    "enabled": bool(enabled),
                    "source": source or "?",
                    "label": getattr(s, "_custom_label", None) or "",
                })
                cfg["glasses_audit"] = trail[-40:]
            main.save_config(cfg)
        self._persist_session_manifest()
        main._dlog("glasses", f"{sid} glasses_enabled={bool(enabled)} source={source or '?'}")
        return json.dumps({"success": True, "sid": sid, "enabled": bool(enabled)})

    def _glasses_transcript(self, sid: str) -> str:
        hit = main.Api._glasses_transcript_cache.get(sid)
        if hit and time.time() - hit[0] < 20:
            return hit[1]
        s = self.sessions.get(sid)
        path = ""
        if s is not None:
            try:
                path = main.agent_status.resolve_transcript(
                    self._worker_ctx(sid, s)) or ""
            except Exception:
                main._swallow(f"_glasses_transcript:{sid}")
                path = ""
        main.Api._glasses_transcript_cache[sid] = (time.time(), path)
        return path

    @staticmethod
    def _glasses_bridge_state():
        """(state_dict | None, age_seconds | None) from the bridge heartbeat."""
        try:
            with open(main.GLASSES_STATE_PATH) as f:
                st = json.load(f)
            return st, int(time.time() - os.path.getmtime(main.GLASSES_STATE_PATH))
        except Exception:
            return None, None

    def get_glasses_status(self) -> str:
        st, age = self._glasses_bridge_state()
        allowed = []
        for sid, s in self.sessions.items():
            if not getattr(s, "_glasses_enabled", False):
                continue
            allowed.append({
                "sid": sid,
                "label": getattr(s, "_custom_label", None) or (s.cmd.split()[0] if s.cmd else sid),
                "provider": main._session_provider(s.cmd),
                "alive": bool(s.alive),
            })
        return json.dumps({
            "success": True,
            "allowed": allowed,
            "bridge": st,
            "bridgeAgeSec": age,
            "bridgeStale": age is None or age > main.GLASSES_STATE_STALE_S,
        })

    def _glasses_report(self) -> str:
        st, age = self._glasses_bridge_state()
        out = []
        if st is None:
            out.append("  bridge     未執行 —— 找不到 " + main.GLASSES_STATE_PATH)
            out.append("             眼鏡送得出去，但沒有人在這台機器上收")
        elif age is not None and age > main.GLASSES_STATE_STALE_S:
            out.append(f"  bridge     心跳停在 {age} 秒前 —— 多半掛了或被 launchd 停掉")
        else:
            r = st.get("relay") or {}
            out.append(f"  bridge     執行中（心跳 {age} 秒前，v{st.get('version', '?')}）")
            if r.get("reachable"):
                out.append(f"  relay      通  bridgeOnline={r.get('bridgeOnline')}  "
                           f"devices={r.get('devices')}  queued={r.get('queued')}")
            else:
                out.append(f"  relay      連不到  {r.get('error') or ''}")
            devs = st.get("devices") or []
            out.append(f"  devices    {len(devs)} 副眼鏡已配對"
                       + (f"（{devs[0].get('label', '')}）" if devs else ""))
        out.append("")
        allowed = [(sid, s) for sid, s in self.sessions.items()
                   if getattr(s, "_glasses_enabled", False)]
        if not allowed:
            out.append("  開放中     0 個分頁 —— fail-closed，眼鏡現在送不進任何地方")
        else:
            out.append(f"  開放中     {len(allowed)} 個分頁")
            for sid, s in allowed:
                label = getattr(s, "_custom_label", None) or (s.cmd.split()[0] if s.cmd else sid)
                mark = "\u25cf" if s.alive else "\u25cb"
                out.append(f"    {mark} {sid:<5s} {main._session_provider(s.cmd):<7s} {label}")
        trail = (main.load_config().get("glasses_audit") or [])[-5:]
        if trail:
            out.append("")
            out.append("  最近的授權變更")
            for e in reversed(trail):
                when = time.strftime("%m-%d %H:%M", time.localtime(e.get("ts", 0)))
                verb = "開放" if e.get("enabled") else "收回"
                out.append(f"    {when}  {verb} {e.get('sid', '?'):<5s} "
                           f"{e.get('label', ''):<10s} via {e.get('source', '?')}")
        out.append("")
        out.append("  開放一個分頁：sfctl glasses allow <sid>      收回：sfctl glasses deny <sid>")
        return "\n".join(out)
