"""A connection test in a temporary Claude tab, shared by the services that judge one the same way: the
Claude-account connector services (_claude_connector.py) and the local servers on this Mac (_apple_events.py).
The file starts with `_`, so the registry never takes it for a service (it reads only `*.json`).

The test opens a temporary Claude tab (`ctx.tabs`, the core's kit), asks it one fixed question that only reads,
and judges from two things it can already see: which tools the tab called (their names, by `prefix`) and the last
line it wrote. What a tool returned is never looked at, kept or returned. `auth_tools` are names that mean the
tools are there but not authorized yet, so they do not count as using them.
"""

import re
import threading
import uuid

import sf_lang
from connectors.adapter import Adapter

TEST_TIMEOUT_S = 180.0                 # from the question going in, not counting a wait for the person's OK
READY_TIMEOUT_S = 90.0                 # the tab getting to its prompt (the kit waits this long)


class TabTestAdapter(Adapter):
    """One service whose route is proven by a temporary Claude tab. `prefix` starts the names of the tools that
    count, `mark` is the word of the test's last line, `name` the service's (en, zh) name, `lookup` the (en, zh)
    words for the one small read it asks for, `failed_step` the step each verdict sends the person back to."""

    def __init__(self, service_id: str, prefix: str, mark: str, name, route: str, failed_step: dict,
                 auth_tools=(), lookup=("a search or a list", "搜尋或列清單")):
        self.id = service_id
        self.route = route
        self.prefix = prefix
        self.auth_tools = set(auth_tools)
        self.mark = mark
        self.name = tuple(name)
        self.lookup = tuple(lookup)
        self.failed_step = dict(failed_step)
        self.checks = {}
        self.actions = {}
        self._verdict = re.compile(r"^" + re.escape(mark) + r":\s*(OK|FAIL)\b", re.I)
        self._runs = {}                 # handle -> one test (a temporary tab); this service's own, never another's
        self._lock = threading.Lock()

    # ── the question and how its answer is read ────────────────────────────
    def prompt(self) -> str:
        """The fixed one-line question the test tab gets, in the OS language like every agent-facing text.
        It asks for a count and forbids every change: the tools may well be able to write."""
        en_name, zh_name = self.name
        en_what, zh_what = self.lookup
        en = (f"This is a {en_name} connection test. First load the {en_name} tools with ToolSearch, then make one small "
              f"lookup that only reads ({en_what}) and count what came back. Do not open or read any item's content, "
              "and do not create, send, change, share or delete anything. Report only the number, in one line. Then write a "
              f"last line on its own: {self.mark}: OK, or {self.mark}: FAIL followed by the reason.")
        zh = (f"這是 {zh_name} 連結測試。先用 ToolSearch 載入 {zh_name} 的工具，只做一次唯讀的查詢（{zh_what}），數一下"
              "回來幾筆。不要打開或讀任何項目的內容，也不要新增、寄出、修改、分享或刪除任何東西。用一行只回報數字。最後單獨一行寫 "
              f"{self.mark}: OK，失敗就寫 {self.mark}: FAIL 加原因。")
        return sf_lang.pick(en, zh)

    def verdict_line(self, text) -> str:
        """"OK", "FAIL" or "" from the last two lines of a reply; the rest of the reply is dropped here."""
        lines = [re.sub(r"[`*_>\s]+", " ", ln).strip() for ln in str(text or "").splitlines()]
        lines = [ln for ln in lines if ln]
        for ln in reversed(lines[-2:]):
            m = self._verdict.match(ln)
            if m:
                return m.group(1).upper()
        return ""

    def judge(self, events: list, tab_state: str = "", elapsed: float = 0.0) -> dict:
        """What the test came to, from the test tab's transcript events (oldest first; only `kind`, the tool name
        and the last reply's marker line are used).

          {"state": "running", "phase": "asking" | "reading", "calls": n}
          {"state": "needs_allow", "calls": n}           the tab waits for the person to allow a tool
          {"state": "done", "verdict": <code>, "calls": n}

        verdicts: pass | needs_auth | no_tools | failed | said_ok | unclear | timeout."""
        start = max((i for i, e in enumerate(events or ()) if e.get("kind") == "user_msg" and self.mark in (e.get("text") or "")),
                    default=-1)
        if start < 0:
            if elapsed <= TEST_TIMEOUT_S:
                return {"state": "running", "phase": "asking", "calls": 0}
            return {"state": "done", "verdict": "timeout", "calls": 0}
        turn = list(events)[start + 1:]
        names = [str(e.get("tool") or "") for e in turn if e.get("kind") == "tool_call"]
        calls = [n for n in names if n.startswith(self.prefix) and n not in self.auth_tools]
        ended = any(e.get("kind") == "turn_end" for e in turn)
        reply = next((e.get("text") for e in reversed(turn) if e.get("kind") == "assistant_text"), "")
        marker = self.verdict_line(reply)
        if not ended:
            if tab_state == "decision":
                return {"state": "needs_allow", "calls": len(calls)}
            if elapsed > TEST_TIMEOUT_S:
                return {"state": "done", "verdict": "timeout", "calls": len(calls)}
            return {"state": "running", "phase": "reading" if names else "asking", "calls": len(calls)}
        if not calls and any(n in self.auth_tools for n in names):
            verdict = "needs_auth"
        elif not calls:
            verdict = "said_ok" if marker == "OK" else "no_tools"
        elif marker == "OK":
            verdict = "pass"
        elif marker == "FAIL":
            verdict = "failed"
        else:
            verdict = "unclear"
        return {"state": "done", "verdict": verdict, "calls": len(calls)}

    # ── the test ───────────────────────────────────────────────────────────
    def _fail(self, code: str, sid: str = "", calls: int = 0) -> dict:
        return {"state": "done", "ok": False, "code": code, "failed_step": self.failed_step.get(code, ""), "calls": calls, "sid": sid}

    def _forget_tabs(self, ctx) -> None:
        """Close every test tab of this service that is still open (one left from before, a cancel, a disconnect)."""
        with self._lock:
            left = [r for r in self._runs.values() if not r["closed"]]
            for r in left:
                r["closed"] = True
            self._runs = {h: r for h, r in self._runs.items() if not r["closed"]}
        for r in left:
            try:
                ctx.tabs.close(r["sid"])
            except Exception:        # a tab that is already gone is what we wanted
                pass

    def test(self, ctx, route, handle=None) -> dict:
        """Start (handle=None) or poll the test. Only "it works" or "which step did not" comes back, with the number
        of the connector's calls and the test tab's id (so the page can offer "see the test conversation")."""
        if route != self.route:
            return self._fail("start_failed")
        if handle is None:
            return self._start(ctx)
        return self._poll(ctx, str(handle))

    def _start(self, ctx) -> dict:
        self._forget_tabs(ctx)
        kit = ctx.tabs
        try:
            sid = kit.open(kit.command("claude"), sf_lang.pick(f"{self.name[0]} test", f"{self.name[1]} 測試"))
            kit.ask(sid, self.prompt(), READY_TIMEOUT_S)      # waits for the prompt in a thread of the kit's, then types the one line
        except Exception:
            return self._fail("start_failed")
        handle = uuid.uuid4().hex[:10]
        with self._lock:
            self._runs[handle] = {"handle": handle, "sid": sid, "closed": False, "sent_at": 0.0, "paused": 0.0, "last_poll": 0.0}
        return {"state": "running", "handle": handle, "phase": "starting", "calls": 0, "sid": sid}

    def _poll(self, ctx, handle: str) -> dict:
        rec = self._runs.get(handle)
        if not rec or rec["closed"]:
            return self._fail("closed")
        sid, kit = rec["sid"], ctx.tabs
        if ctx.cancel:                                   # stopped on purpose: the core puts the service back as it was
            self._forget_tabs(ctx)
            return self._fail("test_cancelled", sid)
        phase = kit.phase(sid)
        if phase == "closed":
            rec["closed"] = True
            return self._fail("closed", sid)
        if phase in ("not_ready", "send_failed"):
            return self._fail("not_ready", sid)
        if phase != "asking":
            return {"state": "running", "handle": handle, "phase": "waiting_ready", "calls": 0, "sid": sid}
        snap = kit.read(sid) or {}
        if not snap.get("alive", False):
            rec["closed"] = True
            return self._fail("closed", sid)
        now = ctx.now()
        if not rec["sent_at"]:                           # the question went in since the last poll: the clock starts now
            rec["sent_at"] = rec["last_poll"] = now
        tab_state = str(snap.get("state") or "")
        if tab_state == "decision":                      # waiting for the person's OK: that time does not count
            rec["paused"] += now - rec["last_poll"]
        rec["last_poll"] = now
        elapsed = max(0.0, now - rec["sent_at"] - rec["paused"])
        out = self.judge(snap.get("events") or [], tab_state, elapsed)
        calls = int(out.get("calls") or 0)
        if out["state"] == "needs_allow":
            return {"state": "needs_user", "handle": handle, "code": "needs_allow", "calls": calls, "sid": sid}
        if out["state"] == "running":
            return {"state": "running", "handle": handle, "phase": out.get("phase", "asking"), "calls": calls, "sid": sid}
        if out["verdict"] == "pass":
            return {"state": "done", "ok": True, "calls": calls, "sid": sid}
        return self._fail(out["verdict"], sid, calls)

    def health(self, ctx, route) -> dict:
        """Nothing to ask without starting a Claude tab: unknown (the core still notices a lost entry itself)."""
        return {"state": "unknown"}

    def disconnect(self, ctx, route) -> dict:
        """What the service keeps on its own side is the person's. This closes a test tab left open."""
        self._forget_tabs(ctx)
        return {"state": "done"}
