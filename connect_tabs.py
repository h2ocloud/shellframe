"""A temporary AI tab for an adapter's connection test: open one, ask it a question, read what it did, close it.

`ctx.tabs` in an adapter is a `TabKit`. The tab is a tab of ShellBot's own and not a bot (a bot writes its
conversation into long-term memory when it closes, which would keep whatever the test touched), named for
what it does, its Telegram forwarding off, started in an empty folder of ShellBot's (`<config>/connect-test/`).

An adapter judges from two things it can already see and nothing else: the names of the tools the tab
called and the last line it wrote (`read()` gives the transcript events: only `kind`, the tool name and the
text are for the adapter to use; keep none of it). ShellBot itself never reads a service's content.

    kit.open(cmd, title) -> sid                    a new tab (cmd e.g. "claude")
    kit.folder() -> str                            the empty folder a test tab starts in
    kit.ask(sid, text, ready_timeout=90)           wait until the tab is ready, then put the text in (a thread)
    kit.phase(sid) -> waiting_ready | asking | not_ready | send_failed | closed
    kit.read(sid) -> {"alive", "state", "events"}  the tab's state ("decision" = waiting for the person's OK) and its transcript
    kit.close(sid)
"""

import os
import shlex
import sys
import threading

import agent_status
import sf_config
from api_host import main
from sf_log import _dlog, _swallow


class TabKit:
    def __init__(self, api):
        self.api = api
        self._phase = {}
        self._lock = threading.Lock()

    def folder(self) -> str:
        d = str(sf_config.config_dir() / "connect-test")
        os.makedirs(d, exist_ok=True)
        return d

    def command(self, cli: str = "claude") -> str:
        """What the tab runs: `cd <folder> && <cli>` where there is a shell that reads it, a plain program name on Windows."""
        return cli if sys.platform == "win32" else f"cd {shlex.quote(self.folder())} && {cli}"

    def open(self, cmd: str, title: str) -> str:
        sid = self.api.new_session(cmd, 200, 50, source="connect", handoff=False)
        self.api.rename_session(sid, title, manual=True)
        self.api.set_session_bridge(sid, False)
        with self._lock:
            self._phase[sid] = "waiting_ready"
        return sid

    def ask(self, sid: str, text: str, ready_timeout: float = 90.0) -> None:
        threading.Thread(target=self._ask, args=(sid, text, ready_timeout), daemon=True, name=f"connect-ask-{sid}").start()

    def _set(self, sid, phase):
        with self._lock:
            self._phase[sid] = phase

    def _ask(self, sid, text, timeout):
        if not self.api._wait_until_ready(sid, timeout=timeout):
            self._set(sid, "not_ready")
            _dlog("connect", f"test tab {sid}: never became ready")
            return
        s = self.api.sessions.get(sid)
        if not s:
            self._set(sid, "closed")
            return
        s._startup_trust_pending = False
        s._init_pending = False                    # the generic opening prompt must not go in front of the question
        self._set(sid, "asking" if self.api._send_text_to_session(s, text, submit=True) else "send_failed")

    def phase(self, sid: str) -> str:
        s = self.api.sessions.get(sid)
        if not s or not getattr(s, "alive", True):
            return "closed"
        with self._lock:
            return self._phase.get(sid, "closed")

    def read(self, sid: str) -> dict:
        s = self.api.sessions.get(sid)
        if not s or not getattr(s, "alive", True):
            return {"alive": False, "state": "", "events": []}
        try:
            snap = self.api._agent_status_snapshot(sid)
            state = str(((snap[0] if isinstance(snap, tuple) else snap) or {}).get("state") or "")
            path = agent_status.resolve_transcript(self.api._worker_ctx(sid, s))
            events = agent_status._read_tail_events(path, tail_bytes=1_000_000, max_records=400)[1] if path else []
        except Exception:
            _swallow("connect:tabs:read")
            state, events = "", []
        return {"alive": True, "state": state, "events": events}

    def close(self, sid: str) -> None:
        with self._lock:
            self._phase.pop(sid, None)
        if sid in self.api.sessions:
            self.api.close_session(sid, reason="connect-test")

        def _mut(cfg):                              # set_session_bridge(sid, False) is kept in config.json; the tab is gone, so is that
            off = [x for x in (cfg.get("bridge_disabled_sessions") or []) if x != sid]
            if off != (cfg.get("bridge_disabled_sessions") or []):
                cfg["bridge_disabled_sessions"] = off
        try:
            main.update_config(_mut)
        except Exception:
            _swallow("connect:tabs:forget")
