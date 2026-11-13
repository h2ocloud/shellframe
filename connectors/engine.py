"""The steps: where a person is in connecting a service, kept so they can pick it up after a restart.

The state is a JSON file in the app's state folder (`connectors.json`). It holds, per service: the route
chosen, the step reached, each step's state, what each AI program said, when it last checked. It never
holds a secret (`secrets.assert_clean` is run on every save) and never holds a sentence: words come from
the description when they are shown.

A step moves  pending → waiting_user → (verifying) → done, or failed; the whole service is one of
not_started, waiting_user, verifying, connected, failed, needs_reconnect (health.py).

    start(service, route=None, reconnect=False)   begin, or go on where it was left
    step(service, op, args)                       check | act | done | back | route | stuck
    test(service, handle=None, cancel=False)      the adapter's connection test
    disconnect(service)                           take away only what ShellBot added
    refresh(service) / refresh_all()              the health check (nothing runs by itself: the caller decides when)

Every answer is a dict with `success`; on `success: false`, a `code`, and `message` = {text, next}.
Checks, actions and tests run outside the file's lock. Each service has one operation at a time; a second
one that arrives meanwhile gets `busy` at once instead of waiting.
"""

import copy
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

import sf_config
import sf_lang
from sf_log import _dlog

from . import SCHEMA, clis as clis_mod, health, problems, registry as reg_mod, secrets
from .adapter import Ctx

HANDLE_OK = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
CLI_STATE = {"added": "connected", "replaced": "connected", "failed": "failed", "missing": "missing",
             "manual": "manual", "unsupported": "unsupported"}


def stuck_message(service: str, route: str, n, total, step: str, problem: str = "", lang: str = None) -> str:
    """What "ask a bot to look" types into the bot's tab: the route, the step and the problem the person
    ticked (strings the wizard itself showed) and a line saying nothing secret is in it."""
    service, route, step, problem = (reg_mod.clean_line(x) for x in (service, route, step, problem))
    try:
        n, total = int(n), int(total)
    except (TypeError, ValueError):
        n = total = 0
    where_en = f"step {n} of {total}, \"{step}\"" if n and total else f"\"{step}\""
    where_zh = f"第 {n} 步（共 {total} 步）「{step}」" if n and total else f"「{step}」"
    en = (f"I'm connecting {service} with ShellBot (way: {route}) and I'm stuck at {where_en}."
          + (f" What goes wrong: \"{problem}\"." if problem else "")
          + " Please help me look at it. This message has no password, token or code from a web address.")
    zh = (f"我在用 ShellBot 接 {service}（接法：{route}），卡在{where_zh}。"
          + (f"遇到的問題：「{problem}」。" if problem else "")
          + "請幫我看看。這則訊息不含密碼、token，也沒有網址裡的代碼。")
    return sf_lang.pick(en, zh, lang)


def connect_words(name: str, lang: str = None) -> str:
    """The one sentence a phone, a banner or a Telegram chat gets for a bot's [[SF:RED:connect:<service>]]."""
    return sf_lang.pick(f"Needs {name} connected: open ShellBot on the computer (Settings → Connections)",
                        f"需要接上 {name}：請到電腦上的 ShellBot 接（設定 → 連接）", lang)


class Store:
    """connectors.json: {"schema": 1, "services": {<id>: record}}. Reads and writes hold one lock (never
    across anything else); a write is a temp file and a rename."""

    def __init__(self, path=None):
        self._path = Path(path) if path else None
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path or (sf_config.state_dir() / "connectors.json")

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("services"), dict):
                return data
        except (OSError, ValueError):
            pass
        return {"schema": SCHEMA, "services": {}}

    def _write(self, data: dict) -> None:
        secrets.assert_clean(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".connectors-", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def all(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._read()["services"])

    def get(self, service: str) -> dict:
        with self._lock:
            return copy.deepcopy(self._read()["services"].get(service) or {})

    def update(self, service: str, fn) -> dict:
        """Load the record, let `fn(record)` change it, save, and give back a copy."""
        with self._lock:
            data = self._read()
            rec = data["services"].setdefault(service, {})
            fn(rec)
            if not rec:
                data["services"].pop(service, None)
            self._write(data)
            return copy.deepcopy(rec)

    def delete(self, service: str) -> None:
        with self._lock:
            data = self._read()
            if data["services"].pop(service, None) is not None:
                self._write(data)


def _safe_result(res) -> dict:
    """An adapter's answer, kept to plain values: a `state` string and scalars (masked), nothing nested."""
    if not isinstance(res, dict) or not isinstance(res.get("state"), str):
        return {"state": "__error__"}
    out = {}
    for k, v in res.items():
        if not isinstance(k, str) or secrets._SECRET_KEY_NAME.search(k):
            continue
        if isinstance(v, bool) or isinstance(v, (int, float)) or v is None:
            out[k] = v
        elif isinstance(v, str):
            out[k] = secrets.mask(v)[:200]
        elif isinstance(v, (list, tuple)) and all(isinstance(x, (str, int, bool)) for x in v):
            out[k] = [secrets.mask(x) if isinstance(x, str) else x for x in v][:20]
    return out


class Engine:
    def __init__(self, registry=None, path=None, clis=None, backend=None, ctx_factory=None, clock=time.time):
        self.registry = registry or reg_mod.default()
        self.store = Store(path)
        self.clis = clis or clis_mod.Clis()
        self.backend = backend
        self.ctx_factory = ctx_factory
        self.clock = clock
        self._ops = {}
        self._ops_lock = threading.Lock()
        self._settle()

    # ── plumbing ───────────────────────────────────────────────────────────
    def _settle(self) -> None:
        """After a restart nothing is running: 'verifying' goes back to 'waiting for you', and a test that
        was in flight is forgotten (its handle means nothing now)."""
        for service, rec in self.store.all().items():
            if rec.get("state") == "verifying" or any(s.get("state") == "verifying" for s in (rec.get("steps") or {}).values()):
                def fix(r):
                    if r.get("state") == "verifying":
                        r["state"] = "waiting_user"
                    for s in (r.get("steps") or {}).values():
                        if s.get("state") == "verifying":
                            s["state"] = "waiting_user"
                    r.pop("test_handle", None)
                self.store.update(service, fix)

    def _op(self, service: str):
        with self._ops_lock:
            lock = self._ops.setdefault(service, threading.Lock())
        return lock

    def _ctx(self, service: str, route_id: str, cancel: bool = False) -> Ctx:
        c = self.ctx_factory(service, route_id) if self.ctx_factory else Ctx(service=service)
        c.service, c.route, c.cancel = service, route_id, cancel
        c.env = c.env or {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN")}
        c.lang = c.lang or sf_lang.prompt_lang()
        c.secrets = c.secrets or secrets.SecretStore(service, self.backend)
        c.run = c.run or (lambda argv, timeout=clis_mod.TIMEOUT_S, env=None: self.clis.runner(argv, timeout, env or c.env))
        c.find = c.find or (lambda binary: clis_mod.find_binary(binary, c.env))
        c.now = self.clock
        return c

    def _secrets(self, service: str) -> secrets.SecretStore:
        return secrets.SecretStore(service, self.backend)

    def _fail(self, service, code, **extra) -> dict:
        desc = self.registry.get(service)
        rec = self.store.get(service) if desc else {}
        route = next((r for r in (desc or {}).get("routes", []) if r["id"] == rec.get("route")), None)
        p = problems.explain(code, desc, route)
        out = {"success": False, "code": code, "message": {"text": p["text"], "next": p["next"]}, **extra}
        if desc:
            out["status"] = health.build(desc, rec)
        return out

    def _reply(self, service, **extra) -> dict:
        desc = self.registry.get(service)
        rec = self.store.get(service)
        return {"success": True, "status": health.build(desc, rec), "view": self.view(service), **extra}

    def _route_of(self, desc, rec):
        return next((r for r in desc["routes"] if r["id"] == rec.get("route")), None)

    def _call(self, service, route_id, fn, args=None, cancel=False) -> dict:
        try:
            return _safe_result(fn(self._ctx(service, route_id, cancel), args or {}))
        except Exception:
            _dlog("connect", f"adapter error in {service}")
            return {"state": "__error__"}

    # ── reading ────────────────────────────────────────────────────────────
    def snapshot(self, service: str) -> dict:
        """{"success", "status", "view"} for a service as it is now (a failure with a code for an unknown one)."""
        return self._reply(service) if self.registry.get(service) else self._fail(service, "unknown_service")

    def status(self, service: str):
        desc = self.registry.get(service)
        return health.build(desc, self.store.get(service)) if desc else None

    def list(self) -> list:
        saved = self.store.all()
        return [health.build(self.registry.get(s), saved.get(s) or {}) for s in self.registry.ids()]

    def _step_words(self, table, code, fallback_tone="warn") -> dict:
        hit = (table or {}).get(code)
        if hit:
            return {"tone": hit["tone"], "text": hit["text"], "hint": hit.get("hint")}
        if code in problems.RESULTS:
            tone, text = problems.RESULTS[code]
            return {"tone": tone, "text": text, "hint": None}
        p = problems.explain(code)
        return {"tone": fallback_tone, "text": p["text"], "hint": p["next"]}

    def view(self, service: str):
        """What a card draws for the service right now: the routes, the steps with their state, and the
        step in front of the person with its last result and check."""
        desc = self.registry.get(service)
        if not desc:
            return None
        rec = self.store.get(service)
        route = self._route_of(desc, rec)
        routes = [{"id": r["id"], "kind": r["kind"], "name": r["name"], "blurb": r.get("blurb"), "recommended": bool(r.get("recommended")),
                   "minutes": r.get("minutes", 0), "needs": r.get("needs", []), "scopes": r.get("scopes", [])} for r in desc["routes"]]
        if not route:
            return {"route": None, "routes": routes, "steps": [], "step": None}
        recs = rec.get("steps") or {}
        steps = [{"id": s["id"], "title": s["title"], "kind": s.get("kind", "step"), "state": (recs.get(s["id"]) or {}).get("state", "pending")}
                 for s in route["steps"]]
        cur = next((s for s in route["steps"] if s["id"] == rec.get("step")), None)
        step = None
        if cur:
            sr = recs.get(cur["id"]) or {}
            step = copy.deepcopy(cur)
            step["state"] = sr.get("state", "pending")
            res = sr.get("result")
            action = next((a for a in cur.get("actions", []) if res and a["id"] == res.get("action")), None)
            step["result"] = {"action": res["action"], "code": res["code"], **self._step_words((action or {}).get("results"), res["code"])} if res else None
            chk = sr.get("check")
            step["check"] = ({"code": chk["code"], "passed": bool(chk.get("passed")),
                              **self._step_words((cur.get("check") or {}).get("states"), chk["code"], "info" if chk.get("passed") else "warn")}
                             if chk and cur.get("check") else None)
            prob = rec.get("problem") or {}
            step["message"] = (health.build(desc, rec, route)["problem"]
                               if prob.get("code") and (prob.get("step") in ("", None, cur["id"])) else None)
            step["options_values"] = {o["id"]: (rec.get("options") or {}).get(o["id"], bool(o.get("default"))) for o in cur.get("options", [])}
            step["notes"] = []
            if any(a.get("builtin") == "cli_add" for a in cur.get("actions", [])):
                if (route.get("mcp") or {}).get("env_from_secret"):
                    step["notes"].append(problems.NOTES["secret_in_cli"])
                step["manual"] = [self.clis.manual_entry(c, route["mcp"]) for c in clis_mod.CLI_IDS
                                  if (route.get("clis", {}).get(c) or {}).get("via") == "mcp" and clis_mod.mode_for(c, route["kind"]) == "manual"]
            if any(a.get("builtin") == "secret_set" for a in cur.get("actions", [])):
                step["secrets_saved"] = {s["id"]: bool((rec.get("secrets") or {}).get(s["id"])) for s in route.get("secrets", [])}
        return {"route": {"id": route["id"], "kind": route["kind"], "name": route["name"], "scopes": route.get("scopes", [])},
                "routes": routes, "steps": steps, "step": step}

    # ── start ──────────────────────────────────────────────────────────────
    def start(self, service: str, route: str = None, reconnect: bool = False) -> dict:
        desc = self.registry.get(service)
        if not desc:
            return self._fail(service, "unknown_service")
        lock = self._op(service)
        if not lock.acquire(blocking=False):
            return self._fail(service, "busy")
        try:
            rec = self.store.get(service)
            r = self.registry.route(service, route or rec.get("route"))
            if not r and not route:                          # the way it was left on is gone (the description changed): the default
                r = self.registry.route(service)
            if not r:
                return self._fail(service, "unknown_route")
            switching = bool(rec.get("route")) and rec["route"] != r["id"]
            if not switching and rec.get("route") and rec.get("step") not in [s["id"] for s in r["steps"]]:
                switching = True                              # the steps changed under a saved record: begin again on this route
            if rec.get("state") == "connected" and not reconnect and not switching:
                return self._reply(service)
            if switching and rec.get("route"):
                self._forget_entries(service, desc, rec)       # a new way: what the old way added goes first
            if rec.get("state") in ("needs_reconnect",) or (reconnect and rec.get("state") == "connected"):
                if not switching:
                    return self._reconnect(service, desc, r, rec)
            if switching or rec.get("state") in (None, "", "not_started") or not rec.get("steps"):
                self._fresh(service, r)
            else:                                           # waiting_user / failed: go on where it was left
                def resume(x):                  # the problem, if any, stays on its step until that step is done
                    if x.get("state") in ("failed", "verifying", "needs_reconnect"):
                        x["state"] = "waiting_user"
                self.store.update(service, resume)
            self._enter(service, desc, r)
            return self._reply(service)
        finally:
            lock.release()

    def _fresh(self, service, route) -> None:
        steps = route["steps"]

        def fn(x):
            x.clear()
            x.update({"route": route["id"], "state": "waiting_user", "step": steps[0]["id"], "scopes": list(route.get("scopes", [])),
                      "steps": {s["id"]: {"state": "pending"} for s in steps}, "options": {}, "clis": {}, "secrets": {},
                      "connected_at": 0, "checked_at": 0, "problem": None, "started_at": int(self.clock())})
        self.store.update(service, fn)

    def _enter(self, service, desc, route) -> None:
        """Make the current step the one in front of the person; a step whose check runs on entering and
        passes is done by itself, and so on to the first that needs them."""
        for _ in range(len(route["steps"])):
            rec = self.store.get(service)
            step = next((s for s in route["steps"] if s["id"] == rec.get("step")), None)
            if not step:
                return
            self._mark(service, step["id"], state="waiting_user")
            chk = step.get("check")
            if step.get("kind") == "test" or not chk or chk.get("on") != "enter":
                return
            ok, code = self._run_check(service, route, step)
            if not ok:
                return
            self._advance(service, route, step["id"])

    def _mark(self, service, step_id, **fields) -> None:
        def fn(x):
            x.setdefault("steps", {}).setdefault(step_id, {}).update(fields)
        self.store.update(service, fn)

    def _advance(self, service, route, step_id) -> None:
        ids = [s["id"] for s in route["steps"]]
        nxt = ids[min(ids.index(step_id) + 1, len(ids) - 1)]

        def fn(x):
            x.setdefault("steps", {}).setdefault(step_id, {})["state"] = "done"
            if nxt != step_id:
                x["step"] = nxt
                x["steps"].setdefault(nxt, {})["state"] = "waiting_user"
            x["state"], x["problem"] = "waiting_user", None
        self.store.update(service, fn)

    def _run_check(self, service, route, step, record=True):
        """(passed, code). The code is recorded on the step; a check the adapter does not have is an error code."""
        chk = step["check"]
        ad = self.registry.adapter(service)
        fn = (getattr(ad, "checks", None) or {}).get(chk["id"]) if ad else None
        if not fn:
            code = "no_adapter" if ad is None else "unknown_check"
            passed = False
        else:
            res = self._call(service, route["id"], fn)
            code = "adapter_error" if res["state"] == "__error__" else res["state"]
            passed = code in chk["pass"]
        if record:
            self._mark(service, step["id"], check={"code": code, "passed": passed})
        return passed, code

    # ── reconnect: keep what is still true ─────────────────────────────────
    def _reconnect(self, service, desc, route, rec):
        """After 'needs reconnect': a step stays done only if it is not marked redo_on_reconnect and its
        check (if it has one) still passes; the test always runs again. Then on to the first step left."""
        lost = (rec.get("problem") or {}).get("code")
        redo_builtin = {"cli_entry_missing": "cli_add", "secret_missing": "secret_set"}.get(lost)
        done = [s for s in route["steps"] if (rec.get("steps") or {}).get(s["id"], {}).get("state") == "done"]
        for s in done:
            keep = (s.get("kind") != "test" and not s.get("redo_on_reconnect")
                    and not any(a.get("builtin") == redo_builtin for a in s.get("actions", [])))
            if keep and s.get("check"):
                keep, _ = self._run_check(service, route, s, record=False)
            if not keep:
                self._mark(service, s["id"], state="pending", check=None, result=None)
        for s in route["steps"]:
            if s.get("kind") == "test":
                self._mark(service, s["id"], state="pending", check=None, result=None)
        recs = self.store.get(service).get("steps") or {}
        first = next((s["id"] for s in route["steps"] if recs.get(s["id"], {}).get("state") != "done"), route["steps"][-1]["id"])

        def fn(x):
            x["state"], x["step"], x["problem"] = "waiting_user", first, None
        self.store.update(service, fn)
        self._enter(service, desc, route)
        return self._reply(service)

    # ── steps ──────────────────────────────────────────────────────────────
    def step(self, service: str, op: str, args: dict = None) -> dict:
        args = args if isinstance(args, dict) else {}
        desc = self.registry.get(service)
        if not desc:
            return self._fail(service, "unknown_service")
        if op == "route":
            return self.start(service, route=str(args.get("route") or ""))
        rec = self.store.get(service)
        route = self._route_of(desc, rec)
        if not route or rec.get("state") in (None, "", "not_started"):
            return self._fail(service, "not_started")
        cur = next((s for s in route["steps"] if s["id"] == rec.get("step")), None)
        if not cur or (args.get("step") and args["step"] != cur["id"]):
            return self._fail(service, "unknown_step")
        if op == "stuck":
            ids = [s["id"] for s in route["steps"]]
            q = next((x for x in cur.get("stuck", []) if x["id"] == args.get("problem")), None)
            problem = reg_mod.pick(q["q"]) if q else reg_mod.clean_line(args.get("problem") or "")
            msg = stuck_message(reg_mod.pick(desc["name"]), reg_mod.pick(route["name"]), ids.index(cur["id"]) + 1, len(ids),
                                reg_mod.pick(cur["title"]), problem)
            return self._reply(service, message=msg)
        if op not in ("check", "act", "done", "back"):
            return self._fail(service, "bad_request")
        lock = self._op(service)
        if not lock.acquire(blocking=False):
            return self._fail(service, "busy")
        try:
            self._remember_options(service, cur, args.get("options"))
            if op == "back":
                ids = [s["id"] for s in route["steps"]]
                i = ids.index(cur["id"])
                if i > 0 and rec.get("state") != "connected":
                    def fn(x):
                        x["steps"][cur["id"]]["state"] = "pending"
                        x["step"] = ids[i - 1]
                        x["steps"][ids[i - 1]]["state"] = "waiting_user"
                        x["problem"] = None
                    self.store.update(service, fn)
                return self._reply(service)
            if cur.get("kind") == "test":
                return self._fail(service, "bad_request")
            if op == "check":
                if not cur.get("check"):
                    return self._reply(service)
                passed, _ = self._run_check(service, route, cur)
                if passed:
                    self._advance(service, route, cur["id"])
                    self._enter(service, desc, route)
                return self._reply(service)
            if op == "done":
                chk = cur.get("check")
                if chk:
                    passed, _ = self._run_check(service, route, cur)
                    if not passed and chk.get("gate"):
                        return self._reply(service)
                self._advance(service, route, cur["id"])
                self._enter(service, desc, route)
                return self._reply(service)
            return self._act(service, desc, route, rec, cur, str(args.get("action") or ""), args)
        finally:
            lock.release()

    def _remember_options(self, service, step, options) -> None:
        if not isinstance(options, dict):
            return
        known = {o["id"] for o in step.get("options", [])}
        picked = {k: bool(v) for k, v in options.items() if k in known}
        if picked:
            self.store.update(service, lambda x: x.setdefault("options", {}).update(picked))

    def _act(self, service, desc, route, rec, step, action_id, args) -> dict:
        action = next((a for a in step.get("actions", []) if a["id"] == action_id), None)
        if not action:
            return self._fail(service, "unknown_action")
        builtin = action.get("builtin")
        if builtin:
            res = getattr(self, f"_b_{builtin}")(service, route, args)
        else:
            ad = self.registry.adapter(service)
            fn = (getattr(ad, "actions", None) or {}).get(action_id) if ad else None
            if not fn:
                return self._fail(service, "no_adapter" if ad is None else "unknown_action")
            opts = {**{o["id"]: bool(o.get("default")) for o in step.get("options", [])}, **(self.store.get(service).get("options") or {})}
            res = self._call(service, route["id"], fn, {**{k: v for k, v in args.items() if k not in ("value",)}, "options": opts})
        if res["state"] == "__error__":
            return self._fail(service, "adapter_error")
        self._mark(service, step["id"], result={"action": action_id, "code": res["state"]})
        _dlog("connect", f"act {service}/{step['id']}/{action_id}: {res['state']}")
        extra = {k: v for k, v in res.items() if k != "state"}
        done = False
        if builtin in ("cli_add", "secret_set") and res["state"] in ("added", "replaced", "saved"):
            done = True
        elif action.get("after") == "check" and step.get("check"):
            done, _ = self._run_check(service, route, step)
        if done:
            self._advance(service, route, step["id"])
            self._enter(service, desc, route)
        return self._reply(service, detail=extra)

    # built-in actions: what the core can do for any service ------------------
    def _targets(self, route, args) -> list:
        want = args.get("clis")
        pool = [c for c in clis_mod.CLI_IDS if (route.get("clis", {}).get(c) or {}).get("via") == "mcp"]
        if not isinstance(want, list):     # unless asked for more: the one CLI people use most, never all of them at once
            want = ["claude"] if "claude" in pool else pool[:1]
        return [c for c in want if c in pool]

    def _record_clis(self, service, results) -> None:
        def fn(x):
            for cli, r in results.items():
                if r["state"] in CLI_STATE:
                    x.setdefault("clis", {})[cli] = CLI_STATE[r["state"]]
        self.store.update(service, fn)

    def _b_cli_add(self, service, route, args) -> dict:
        mcp = route["mcp"]
        extra = {}
        for env_name, secret_id in (mcp.get("env_from_secret") or {}).items():
            try:
                v = self._secrets(service).get(secret_id)
            except secrets.Unsupported:
                return {"state": "unsupported"}
            if not v:
                return {"state": "secret_missing"}
            extra[env_name] = v
        results = {c: self.clis.add(c, mcp, extra) for c in self._targets(route, args)}
        self._record_clis(service, results)
        states = [r["state"] for r in results.values()]
        ok = [s for s in states if s in ("added", "replaced")]
        if "failed" in states:
            code = "failed"
        elif ok:
            code = "replaced" if all(s == "replaced" for s in ok) else "added"
        elif "manual" in states:
            code = "manual"
        else:
            code = "no_cli"
        return {"state": code, "clis": {c: r["state"] for c, r in results.items()},
                "shown": {c: r["shown"] for c, r in results.items() if r.get("shown")}}

    def _b_cli_remove(self, service, route, args) -> dict:
        results = {c: self.clis.remove(c, route["mcp"]["server"]) for c in self._targets(route, args)}
        def fn(x):
            for cli, r in results.items():
                if r["state"] in ("removed", "absent"):
                    (x.get("clis") or {}).pop(cli, None)
        self.store.update(service, fn)
        return {"state": "removed", "clis": {c: r["state"] for c, r in results.items()}}

    def _b_cli_check(self, service, route, args) -> dict:
        results = {c: self.clis.check(c, route["mcp"]["server"]) for c in self._targets(route, args)}
        states = [r["state"] for r in results.values() if r["state"] in ("present", "absent", "unknown")]
        code = "absent" if "absent" in states else ("present" if states and all(s == "present" for s in states) else "unknown")
        return {"state": code, "clis": {c: r["state"] for c, r in results.items()}}

    def _b_secret_set(self, service, route, args) -> dict:
        ids = [s["id"] for s in route.get("secrets", [])]
        name = str(args.get("name") or (ids[0] if ids else ""))
        if name not in ids:
            return {"state": "failed"}
        value = str(args.get("value") or "")
        if not value.strip():
            return {"state": "empty"}
        try:
            self._secrets(service).set(name, value)
        except secrets.Unsupported:
            return {"state": "unsupported"}
        except Exception:
            return {"state": "failed"}
        self.store.update(service, lambda x: x.setdefault("secrets", {}).update({name: True}))
        return {"state": "saved", "name": name}

    # ── the test ───────────────────────────────────────────────────────────
    def test(self, service: str, handle: str = None, cancel: bool = False) -> dict:
        desc = self.registry.get(service)
        if not desc:
            return self._fail(service, "unknown_service")
        rec = self.store.get(service)
        route = self._route_of(desc, rec)
        if not route:
            return self._fail(service, "not_started")
        steps = route["steps"]
        recs = rec.get("steps") or {}
        before = steps[:-1]
        if rec.get("state") != "connected" and not all((recs.get(s["id"]) or {}).get("state") == "done" for s in before):
            return self._fail(service, "test_not_ready")
        ad = self.registry.adapter(service)
        if ad is None:
            return self._fail(service, "no_adapter")
        lock = self._op(service)
        if handle is None and not lock.acquire(blocking=False):
            return self._fail(service, "busy")
        held = handle is None
        try:
            handle = handle or rec.get("test_handle")
            if handle is not None and not HANDLE_OK.match(str(handle)):
                handle = None
            was = rec.get("prev_state") if rec.get("state") == "verifying" else rec.get("state")
            if was not in ("connected", "waiting_user", "failed"):
                was = "waiting_user"
            if handle is None and not cancel:
                self.store.update(service, lambda x: x.update(state="verifying", prev_state=was, problem=None))
            try:
                res = ad.test(self._ctx(service, route["id"], cancel), route["id"], handle)
            except Exception:
                _dlog("connect", f"test error in {service}")
                res = None
            res = self._norm_test(res)
            return self._apply_test(service, desc, route, res, was)
        finally:
            if held:
                lock.release()

    def _norm_test(self, res) -> dict:
        if not isinstance(res, dict) or res.get("state") not in ("done", "running", "needs_user"):
            return {"state": "done", "ok": False, "code": "adapter_error", "failed_step": ""}
        out = {"state": res["state"]}
        if res["state"] == "done":
            out["ok"] = bool(res.get("ok"))
            if not out["ok"]:
                out["code"] = reg_mod.clean_line(res.get("code") or "test_failed", 60) or "test_failed"
                out["failed_step"] = str(res.get("failed_step") or "")
        else:
            h = str(res.get("handle") or "")
            out["handle"] = h if HANDLE_OK.match(h) else ""
            out["phase" if res["state"] == "running" else "code"] = reg_mod.clean_line(res.get("phase") or res.get("code") or "", 40)
        if isinstance(res.get("calls"), int) and not isinstance(res.get("calls"), bool):
            out["calls"] = res["calls"]                    # how many times the service's tool was used: a count, no content
        if re.fullmatch(r"s\d+", str(res.get("sid") or "")):
            out["sid"] = res["sid"]                        # the test tab, for "look at the test"
        return out

    def _apply_test(self, service, desc, route, res, was) -> dict:
        ids = [s["id"] for s in route["steps"]]
        test_id = ids[-1]
        now = int(self.clock())
        if res["state"] == "running":
            self.store.update(service, lambda x: x.update(state="verifying", test_handle=res["handle"]))
        elif res["state"] == "needs_user":
            self.store.update(service, lambda x: x.update(state="waiting_user", test_handle=res["handle"],
                                                          problem={"code": "needs_user", "step": test_id}))
        elif not res["ok"] and res["code"] == "test_cancelled":
            def undo(x):                        # stopped on purpose: back to what it was, not a failure
                x.pop("test_handle", None)
                x.pop("prev_state", None)
                x["state"], x["problem"] = was, None
            self.store.update(service, undo)
        elif res["ok"]:
            def ok(x):
                x.pop("test_handle", None)
                x.pop("prev_state", None)
                x["state"], x["problem"], x["connected_at"], x["checked_at"], x["step"] = "connected", None, now, now, test_id
                x["steps"][test_id] = {"state": "done"}
            self.store.update(service, ok)
        else:
            bad_step = res["failed_step"] if res["failed_step"] in ids[:-1] else ""

            def no(x):
                x.pop("test_handle", None)
                x.pop("prev_state", None)
                x["state"], x["checked_at"] = "failed", now
                x["problem"] = {"code": res["code"], "step": bad_step or test_id}
                x["steps"][test_id] = {"state": "pending"}
                if bad_step:
                    x["steps"][bad_step] = {"state": "waiting_user"}
                x["step"] = bad_step or test_id
            self.store.update(service, no)
        _dlog("connect", f"test {service}: {res['state']} {res.get('code', '')}".strip())
        out = self._reply(service, result=res)
        return out

    # ── health ─────────────────────────────────────────────────────────────
    def refresh(self, service: str, force: bool = False) -> dict:
        """Is a connected service still connected: its entries in the AI programs, its keys, the adapter's
        own health check. Within `ttl_s` of the last look (default 300) it only answers with the status."""
        desc = self.registry.get(service)
        if not desc:
            return self._fail(service, "unknown_service")
        rec = self.store.get(service)
        route = self._route_of(desc, rec)
        ttl = (desc.get("health") or {}).get("ttl_s", 300)
        if not route or rec.get("state") not in ("connected", "needs_reconnect") or (
                not force and self.clock() - float(rec.get("checked_at") or 0) < ttl):
            return self._reply(service)
        lock = self._op(service)
        if not lock.acquire(blocking=False):
            return self._reply(service)
        try:
            code = self._lost(service, route, rec)
            ad = self.registry.adapter(service)
            answer = {"state": "unknown"}
            if not code and ad is not None:
                try:
                    answer = ad.health(self._ctx(service, route["id"]), route["id"])
                except Exception:
                    _dlog("connect", f"health error in {service}")
            now = int(self.clock())
            if code:
                outcome = {"state": "needs_reconnect", "problem": {"code": code}}
            else:
                outcome = health.apply_health(rec, answer if isinstance(answer, dict) else {})
            was = rec.get("state")

            def fn(x):
                x["state"], x["problem"], x["checked_at"] = outcome["state"], outcome["problem"], now
            self.store.update(service, fn)
            if outcome["state"] != was:
                _dlog("connect", f"health {service}: {was} -> {outcome['state']}")
            return self._reply(service)
        finally:
            lock.release()

    def _lost(self, service, route, rec):
        """The code of what has gone missing from a connected service (an AI program's entry, a key), or ''."""
        saved = rec.get("clis") or {}
        for cli in clis_mod.CLI_IDS:
            if saved.get(cli) == "connected" and route.get("mcp") and (route.get("clis", {}).get(cli) or {}).get("via") == "mcp":
                if self.clis.check(cli, route["mcp"]["server"])["state"] == "absent":
                    return "cli_entry_missing"
        for sid in (rec.get("secrets") or {}):
            try:
                if not self._secrets(service).has(sid):
                    return "secret_missing"
            except secrets.Unsupported:
                return "secret_store_unsupported"
        return ""

    def refresh_all(self, force: bool = False) -> dict:
        """The hourly hook (the status monitor calls it; no thread of its own here): refresh every connected
        service and say which need attention now. {"checked": [ids], "attention": [compact status…]}"""
        out = {"checked": [], "attention": []}
        for s in self.registry.ids():
            rec = self.store.get(s)
            if rec.get("state") in ("connected", "needs_reconnect"):
                out["checked"].append(s)
                st = self.refresh(s, force).get("status")
                if st and st["attention"]:
                    out["attention"].append(health.compact(st))
        return out

    # ── disconnect ─────────────────────────────────────────────────────────
    def _forget_entries(self, service, desc, rec) -> dict:
        route = self._route_of(desc, rec)
        removed = {"clis": {}, "secrets": []}
        if route and route.get("mcp"):
            for cli in self._targets(route, {}):                 # only ours (shellbot-…), whatever the record says
                removed["clis"][cli] = self.clis.remove(cli, route["mcp"]["server"])["state"]
        names = sorted(set(rec.get("secrets") or {}) | {s["id"] for s in (route or {}).get("secrets", [])})
        for name in names:
            try:
                if self._secrets(service).delete(name):
                    removed["secrets"].append(name)
            except secrets.Unsupported:
                pass
        return removed

    def disconnect(self, service: str) -> dict:
        """Remove what ShellBot added (its own `shellbot-*` entries, the keys it stored), let the adapter
        clean up on the service's side, and forget the progress. Nothing else is touched."""
        desc = self.registry.get(service)
        if not desc:
            return self._fail(service, "unknown_service")
        lock = self._op(service)
        if not lock.acquire(blocking=False):
            return self._fail(service, "busy")
        try:
            rec = self.store.get(service)
            route = self._route_of(desc, rec)
            removed = self._forget_entries(service, desc, rec)
            ad = self.registry.adapter(service)
            if ad is not None and route:
                try:
                    ad.disconnect(self._ctx(service, route["id"]), route["id"])
                except Exception:
                    _dlog("connect", f"disconnect error in {service}")
            self.store.delete(service)
            _dlog("connect", f"disconnect {service}")
            return self._reply(service, removed=removed)
        finally:
            lock.release()
