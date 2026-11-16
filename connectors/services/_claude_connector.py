"""A service reached through the Claude account's connector (set up once on claude.ai): the adapter several
services share. Each such service keeps a two-line `<id>.py` that builds one `ConnectorAdapter` with its own
names; its steps, words and FAQ stay in its `<id>.json`. The file starts with `_`, so the registry never takes
it for a service (it reads only `*.json`).

A connector route has three steps (ids as the descriptions use them):

  a1  how Claude is signed in (check `claude_account`: the plan ShellBot keeps; information only)
  a2  connect the service on claude.ai (the person's part; nothing to check from here)
  a3  the connection test

The test opens a temporary Claude tab (`ctx.tabs`, the core's kit), asks it for one small lookup that only
reads, and judges from two things it can already see: which tools the tab called (their names) and the last
line it wrote. What a tool returned is never looked at, kept or returned. A tool's name is
`mcp__claude_ai_<Tool>__<what>`; `authenticate` and `complete_authentication` mean the connector is there
but not authorized yet, so they do not count as using it.

What this cannot know: whether the connector is still connected on claude.ai (that lives on claude.ai and
cannot be asked without a Claude tab), so `health()` answers `unknown` and the hourly check changes nothing.
"""

import re
import threading
import uuid

import sf_lang
from connectors.adapter import Adapter, claude_login

TEST_TIMEOUT_S = 180.0                 # from the question going in, not counting a wait for the person's OK
READY_TIMEOUT_S = 90.0                 # the tab getting to its prompt (the kit waits this long)
FAILED_STEP = {"needs_auth": "a2", "no_tools": "a2", "not_ready": "a1"}     # the step a verdict sends the person back to


def check_claude_account(ctx, args: dict) -> dict:
    """How Claude is signed in, from the account label ShellBot keeps (the core's claude_login): the plan only."""
    return claude_login(ctx.cfg)


class ConnectorAdapter(Adapter):
    """One service on the claude.ai connector route. `tool` is the connector's part of the tool names
    (`mcp__claude_ai_<tool>__…`), `mark` the word of the test's last line, `name` the service's (en, zh) name
    for the test's question and tab title."""

    def __init__(self, service_id: str, tool: str, mark: str, name, route: str = "connector"):
        self.id = service_id
        self.route = route
        self.prefix = f"mcp__claude_ai_{tool}__"
        self.auth_tools = {self.prefix + "authenticate", self.prefix + "complete_authentication"}
        self.mark = mark
        self.name = tuple(name)
        self.checks = {"claude_account": check_claude_account}
        self.actions = {}
        self._verdict = re.compile(r"^" + re.escape(mark) + r":\s*(OK|FAIL)\b", re.I)
        self._runs = {}                 # handle -> one test (a temporary tab); this service's own, never another's
        self._lock = threading.Lock()

    # ── the question and how its answer is read ────────────────────────────
    def prompt(self) -> str:
        """The fixed one-line question the test tab gets, in the OS language like every agent-facing text.
        It asks for a count and forbids every change: the connector may well offer tools that write."""
        en_name, zh_name = self.name
        en = (f"This is a {en_name} connection test. First load the {en_name} tools with ToolSearch, then make one small "
              "lookup that only reads (a search or a list) and count what came back. Do not open or read any item's content, "
              "and do not create, send, change, share or delete anything. Report only the number, in one line. Then write a "
              f"last line on its own: {self.mark}: OK, or {self.mark}: FAIL followed by the reason.")
        zh = (f"這是 {zh_name} 連線測試。先用 ToolSearch 載入 {zh_name} 的工具，只做一次唯讀的查詢（搜尋或列清單），數一下"
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
        return {"state": "done", "ok": False, "code": code, "failed_step": FAILED_STEP.get(code, ""), "calls": calls, "sid": sid}

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
        """The connector's state lives on claude.ai, which cannot be asked without a Claude tab: unknown."""
        return {"state": "unknown"}

    def disconnect(self, ctx, route) -> dict:
        """The connector itself is the person's, on claude.ai (the FAQ says where to take it away). This closes a
        test tab left open."""
        self._forget_tabs(ctx)
        return {"state": "done"}
