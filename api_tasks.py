"""Tasks: `sfctl task` and the daily upkeep of the task store (state_store.py).

A bot keeps what it is doing, what it waits for and what the person must decide as tasks, so
the next session, another bot or the person can see it without reading a conversation.

  sfctl task add "title" [--owner NAME|user] [--decision] [--due WHEN] [--next TEXT] [--parent N] [--after N,M]
  sfctl task list [--owner NAME|user] [--all|--closed] [--decisions]
  sfctl task mine              this tab's bot: its open tasks, and the ones waiting on it
  sfctl task show N            a task and what happened to it
  sfctl task update N [--status S] [--owner NAME] [--waiting-on NAME] [--next TEXT] [--due WHEN] [--title TEXT]
  sfctl task note N "text"     something happened, nothing changed
  sfctl task done N ["result"] / drop N ["why"]
  sfctl task stuck             open tasks nothing has happened to for too long

Who acts is the calling tab (SF_SID), never an argument: its bot's roster name, else the tab's
title, else the sid (the rule A2A uses). An owner given by name is matched to a bot by any of
its names (bot_names); `user` is the person. The status monitor calls `_tasks_tick` on every
pass; once a day a thread backs the store up and drops tasks closed a year ago.
"""

import hashlib
import json
import threading
import time
from datetime import datetime

import sf_lang
import state_store as ss
from sf_log import _swallow

_ = sf_lang.pick
FIRST_TICK_S = 300
STATUS_WORDS = {"todo": ("to do", "待辦"), "doing": ("doing", "進行中"), "waiting": ("waiting", "等別人"),
                "done": ("done", "完成"), "dropped": ("dropped", "放棄")}
USER_WORDS = ("the user", "使用者")
USER_NAMES = {"user", "me-user", "使用者", "主人", "老闆"}


def _when(ts) -> str:
    return datetime.fromtimestamp(float(ts)).strftime("%m/%d %H:%M") if ts else ""


def _status(s: str) -> str:
    en, zh = STATUS_WORDS.get(s, (s, s))
    return _(en, zh)


def _who(name: str) -> str:
    return _(*USER_WORDS) if name == ss.USER else (name or "—")


def line(t: dict) -> str:
    """One task on one line, the way every answer lists them."""
    bits = [f"#{t['id']}", f"[{_status(t['status'])}]"]
    if t.get("kind") == "decision":
        bits.append(_("decision:", "決定："))
    bits.append(t["title"])
    meta = [_(f"owner {_who(t['owner'])}", f"負責 {_who(t['owner'])}")] if t.get("owner") else []
    if t.get("waiting_on"):
        meta.append(_(f"waiting on {_who(t['waiting_on'])}", f"等 {_who(t['waiting_on'])}"))
    if t.get("due_at"):
        late = t["status"] in ss.OPEN and t["due_at"] < time.time()
        meta.append(_(f"due {_when(t['due_at'])}", f"期限 {_when(t['due_at'])}") + (_(" (late)", "（已過）") if late else ""))
    if t.get("idle_s"):
        meta.append(_(f"quiet {t['idle_s'] // 3600} h", f"{t['idle_s'] // 3600} 小時沒動靜"))
    out = " ".join(bits) + ("  · " + " · ".join(meta) if meta else "")
    if t.get("next_step") and t["status"] in ss.OPEN:
        out += "\n      " + _("next: ", "下一步：") + t["next_step"]
    return out


def _lines(tasks: list, empty: tuple) -> str:
    return "\n".join(line(t) for t in tasks) if tasks else _(*empty)


class TasksApiMixin:
    # ── who is asking ─────────────────────────────────────────────────────
    def _tasks_actor(self, sid: str) -> str:
        sid = (sid or "").strip()
        if not sid:
            return "sfctl"
        try:
            role = self._bots_role_of(sid)
        except Exception:
            _swallow("tasks:actor")
            role = ""
        if role:
            return role
        session = self.sessions.get(sid)
        return (getattr(session, "_custom_label", None) or sid) if session else sid

    def _tasks_name(self, name: str, actor: str) -> str:
        """An owner or waiting-on given on the command line: `user` (and its synonyms) is the person,
        `me`/`self` the caller, a bot by any of its names its roster name; anything else as written."""
        raw = str(name or "").strip()
        if not raw:
            return ""
        if raw.lower() in USER_NAMES:
            return ss.USER
        if raw.lower() in ("me", "self", "我"):
            return actor
        try:
            res = self._resolve_name(raw, prefix=False)
            if res.get("ok") and res.get("role"):
                return res["role"]
        except Exception:
            _swallow("tasks:name")
        return raw

    # ── sfctl task ────────────────────────────────────────────────────────
    def _tasks_sfctl(self, args: dict) -> dict:
        args = args or {}
        action = str(args.get("action") or "")
        actor = self._tasks_actor(str(args.get("from_sid") or ""))
        try:
            return self._tasks_do(action, args, actor)
        except ss.Unavailable as e:
            return {"success": False, "message": _(e.en, e.zh), "details": {"code": e.code}}
        except ss.NotFound as e:
            return {"success": False, "message": _(f"No task #{e.args[0]}.", f"沒有 #{e.args[0]} 這件任務。"),
                    "details": {"code": "not_found"}}
        except ValueError as e:
            return {"success": False, "message": _(f"Not accepted: {e}", f"不接受：{e}"), "details": {"code": "invalid"}}

    def _tasks_do(self, action: str, a: dict, actor: str) -> dict:
        def ok(message, **details):
            return {"success": True, "message": message, "details": details}

        def tid():
            try:
                return int(str(a.get("id") or "").lstrip("#"))
            except ValueError:
                raise ValueError(_("a task number, e.g. 12", "要給任務編號，例如 12")) from None

        due = None
        if a.get("due") not in (None, ""):
            try:
                due = ss.parse_due(a["due"])
            except ValueError:
                raise ValueError(_(f"due: {a['due']} (use 2026-11-20, '2026-11-20 18:00', +3h, +2d or none)",
                                   f"期限：{a['due']}（可用 2026-11-20、「2026-11-20 18:00」、+3h、+2d 或 none）")) from None

        if action == "add":
            decision = bool(a.get("decision"))
            owner = self._tasks_name(a.get("owner") or ("user" if decision else "me"), actor)
            after = [int(x) for x in str(a.get("after") or "").replace("#", "").split(",") if x.strip()]
            t = ss.add(str(a.get("title") or ""), actor=actor, owner=owner, requester=actor,
                       kind="decision" if decision else "work", next_step=str(a.get("next") or ""),
                       due_at=due, parent_id=a.get("parent") or None, depends_on=after,
                       origin="sfctl", sid=str(a.get("from_sid") or ""))
            return ok(_("Added ", "已新增 ") + line(t), task=t)

        if action in ("list", "mine", "stuck"):
            if action == "stuck":
                tasks = ss.stuck()
                return ok(_lines(tasks, ("Nothing is stuck.", "沒有卡住的任務。")), tasks=tasks)
            if action == "mine":
                mine = ss.listing(owner=actor)
                waiting = [t for t in ss.listing(status="open", limit=200) if t["waiting_on"] == actor and t["owner"] != actor]
                text = _(f"{_who(actor)}: {len(mine)} open", f"{_who(actor)}：{len(mine)} 件未完成") + \
                    ("\n" + _lines(mine, ("", "")) if mine else "")
                if waiting:
                    text += "\n" + _("Waiting on you:", "在等你的：") + "\n" + _lines(waiting, ("", ""))
                return ok(text, tasks=mine, waiting_on_me=waiting)
            status = "all" if a.get("all") else "closed" if a.get("closed") else "open"
            owner = self._tasks_name(a.get("owner"), actor) if a.get("owner") else None
            kind = "decision" if a.get("decisions") else None
            if kind and owner is None:
                owner = ss.USER
            tasks = ss.listing(owner=owner, status=status, kind=kind, limit=int(a.get("limit") or 50))
            return ok(_lines(tasks, ("No tasks.", "沒有任務。")), tasks=tasks)

        if action == "show":
            t = ss.get(tid())
            rows = [line(t)]
            if t.get("parent_id"):
                rows.append(_(f"  part of #{t['parent_id']}", f"  屬於 #{t['parent_id']}"))
            if t.get("depends_on"):
                rows.append(_("  after ", "  要等 ") + ", ".join(f"#{d}" for d in t["depends_on"]))
            if t.get("children"):
                rows.append(_("  parts: ", "  底下：") + ", ".join(f"#{c}" for c in t["children"]))
            for e in t.get("events") or []:
                rows.append(f"  {_when(e['ts'])}  {_who(e['actor'])}  {e['kind']}  {e['text']}".rstrip())
            return ok("\n".join(rows), task=t)

        if action == "update":
            changes = {}
            for key, col in (("title", "title"), ("next", "next_step"), ("status", "status")):
                if a.get(key) not in (None, ""):
                    changes[col] = a[key]
            for key, col in (("owner", "owner"), ("waiting_on", "waiting_on")):
                if a.get(key) is not None and a.get(key) != "":
                    changes[col] = "" if str(a[key]).lower() in ("none", "-") else self._tasks_name(a[key], actor)
            if a.get("due") not in (None, ""):
                changes["due_at"] = due
            if changes.get("waiting_on") and "status" not in changes:
                changes["status"] = "waiting"
            if not changes:
                raise ValueError(_("nothing to change", "沒有要改的欄位"))
            t = ss.update(tid(), actor=actor, **changes)
            return ok(_("Updated ", "已更新 ") + line(t), task=t)

        if action == "note":
            t = ss.note(tid(), str(a.get("text") or ""), actor=actor)
            return ok(_("Noted on ", "已記在 ") + line(t), task=t)

        if action in ("done", "drop"):
            n = tid()
            if str(a.get("text") or "").strip():
                ss.note(n, a["text"], actor=actor, kind="result" if action == "done" else "why")
            t = ss.update(n, actor=actor, status="done" if action == "done" else "dropped", waiting_on="")
            return ok(line(t), task=t)

        raise ValueError(_(f"unknown action: {action}", f"不認得的動作：{action}"))

    def task_user_act(self, task_id, action, text="") -> str:
        """The person acts on a task from the office's board (their own hand, never a bot's): "done" or
        "note", with an optional text. JSON: {success, message, task, tell} where `tell` is the name of
        whoever should hear about it ("" when that is the person). Done on the person's own task closes
        it; done on a task waiting on the person is their part done: a result note, and the owner carries
        on (the task stays open). Anything else is refused in words."""
        try:
            return json.dumps(self._task_user_act(task_id, str(action or ""), str(text or "").strip()), ensure_ascii=False)
        except ss.Unavailable as e:
            return json.dumps({"success": False, "code": e.code, "message": _(e.en, e.zh)}, ensure_ascii=False)
        except ss.NotFound:
            return json.dumps({"success": False, "code": "not_found",
                               "message": _(f"No task #{task_id}.", f"沒有 #{task_id} 這件任務。")}, ensure_ascii=False)
        except (ValueError, TypeError) as e:
            return json.dumps({"success": False, "code": "invalid", "message": _(f"Not accepted: {e}", f"不接受：{e}")}, ensure_ascii=False)

    def _task_user_act(self, task_id, action: str, text: str) -> dict:
        def other(*names):                      # the first of these that is somebody (not the person, not empty)
            return next((n for n in names if n and n != ss.USER), "")

        def refuse(code, en, zh):
            return {"success": False, "code": code, "message": _(en, zh)}

        try:
            n = int(str(task_id).lstrip("#"))
        except ValueError:
            raise ValueError(_("a task number, e.g. 12", "要給任務編號，例如 12")) from None
        if action not in ("done", "note"):
            raise ValueError(_(f"unknown action: {action}", f"不認得的動作：{action}"))
        t = ss.get(n, events=False)
        if action == "note":
            if not text:
                return refuse("empty", "Write something first.", "要先寫點什麼。")
            t = ss.note(n, text, actor=ss.USER)
            return {"success": True, "message": _("Noted.", "已留言。"), "task": t,
                    "tell": other(t["owner"]) or other(t["requester"])}
        if t["status"] in ss.CLOSED:
            return refuse("closed", f"#{n} is already closed.", f"#{n} 已經結束了。")
        if t["owner"] == ss.USER:
            res = self._tasks_do("done", {"id": n, "text": text}, ss.USER)
            return {"success": True, "message": _("Marked done.", "已標成做好了。"), "task": res["details"]["task"],
                    "tell": other(t["requester"])}
        if t["waiting_on"] == ss.USER:
            ss.note(n, text or _("Done on my side.", "做好了"), actor=ss.USER, kind="result")
            t = ss.update(n, actor=ss.USER, waiting_on="", status="doing")
            return {"success": True, "message": _("Marked done on your side; the owner carries on.", "已標成做好了，交回負責人繼續。"),
                    "task": t, "tell": other(t["owner"])}
        return refuse("not_waiting", "This task is not waiting on you.", "這件不是在等你")

    # ── hand-offs tied to a task (a2a_host calls these; none of them may break A2A) ──
    def _tasks_link_check(self, task_id):
        """Before an A2A message `--for-task N` goes: (int id, "") or (None, why not)."""
        try:
            n = int(str(task_id).lstrip("#"))
            t = ss.get(n, events=False)
        except (ValueError, TypeError):
            return None, _(f"--for-task: {task_id} is not a task number", f"--for-task：{task_id} 不是任務編號")
        except ss.NotFound:
            return None, _(f"--for-task: no task #{task_id}", f"--for-task：沒有 #{task_id} 這件任務")
        except ss.Unavailable as e:
            return None, _(e.en, e.zh)
        if t["status"] in ss.CLOSED:
            return None, _(f"--for-task: #{n} is closed ({t['status']})", f"--for-task：#{n} 已經結束（{_status(t['status'])}）")
        return n, ""

    def _tasks_link_line(self, task_id: int, remote: bool) -> str:
        """The line the message carries, so the other side can write on the same task."""
        if remote:      # the task lives on this computer; the other side reports with a2a-result
            return _(f"\n\n(Part of task #{task_id} on the sender's computer; report back with a2a-result.)",
                     f"\n\n（這是對方電腦上任務 #{task_id} 的一部分；用 a2a-result 回報即可。）")
        return _(f"\n\n(Part of task #{task_id}: `sfctl task show {task_id}` to see it, "
                 f"`sfctl task note {task_id} \"…\"` to write progress on it.)",
                 f"\n\n（這是任務 #{task_id} 的一部分：`sfctl task show {task_id}` 看全貌，"
                 f"`sfctl task note {task_id} \"…\"` 把進度記上去。）")

    def _tasks_link_sent(self, task_id: int, kind: str, target: str, res: dict, from_sid: str) -> None:
        """A message went out for a task: one event (its id as ref, so the result finds the task),
        and a hand-off makes the task wait on whoever got it."""
        try:
            d = (res or {}).get("details") or {}
            if not ((res or {}).get("success") or d.get("delivered")):
                return
            actor = self._tasks_actor(from_sid)
            who = self._tasks_name(target, actor) if "/" not in target else target
            ss.note(task_id, _(f"{kind} to {who}", f"{'交辦給' if kind == 'handoff' else '傳訊給'} {who}"),
                    actor=actor, kind=kind, ref=str(d.get("id") or ""))
            if kind == "handoff":
                ss.update(task_id, actor=actor, waiting_on=who, status="waiting")
        except Exception:
            _swallow("tasks:link-sent")

    def _tasks_link_result(self, handoff_id: str, from_sid: str, from_label: str, text: str, res: dict) -> None:
        """A hand-off's result came back: written on its task, which goes back to its owner (doing)."""
        try:
            if not (res or {}).get("success"):
                return
            n = ss.find_by_ref("handoff", handoff_id)
            if not n:
                return
            actor = self._tasks_actor(from_sid) if from_sid else (from_label or "a2a")
            ss.note(n, str(text or "")[:ss.TEXT_MAX] or _("(empty result)", "（空的結果）"), actor=actor,
                    kind="result", ref=handoff_id)
            t = ss.get(n, events=False)
            if t["status"] == "waiting":
                ss.update(n, actor=actor, waiting_on="", status="doing")
        except Exception:
            _swallow("tasks:link-result")

    def _tasks_brief(self, role: str, limit: int = 12) -> str:
        """For a waking bot's brief: its open tasks and what other bots wait on it; "" when none or the
        store is unavailable."""
        try:
            mine = ss.listing(owner=role, limit=limit)
            waiting = [t for t in ss.listing(status="open", limit=200) if t["waiting_on"] == role and t["owner"] != role]
        except Exception:
            return ""
        rows = [line(t) for t in mine]
        if waiting:
            rows.append(_("Waiting on you:", "在等你的："))
            rows.extend(line(t) for t in waiting[:limit])
        return "\n".join(rows)

    # ── upkeep ────────────────────────────────────────────────────────────
    def _tasks_tick(self, now: float) -> None:
        """Called by the status monitor on every pass: cheap, never raises. The first upkeep runs a few
        minutes after the app starts, then once a day (state_store.maintain keeps its own day)."""
        try:
            nxt = getattr(self, "_tasks_next", 0.0)
            if not nxt:
                self._tasks_next = now + FIRST_TICK_S
                return
            if now < nxt or getattr(self, "_tasks_busy", False):
                return
            self._tasks_next, self._tasks_busy = now + 3600, True
            threading.Thread(target=self._tasks_upkeep, daemon=True, name="tasks-upkeep").start()
        except Exception:
            _swallow("tasks:tick")

    def _tasks_upkeep(self):
        try:
            ss.maintain()
        except ss.Unavailable:
            pass
        except Exception:
            _swallow("tasks:upkeep")
        finally:
            self._tasks_busy = False

    # ── for a page or the phone (read only) ─────────────────────────────────
    def _tasks_lists(self) -> dict:
        """The three lists, from the store (raises ss.Unavailable): what the page, a paired machine's office
        board and the phone read."""
        return {"decisions": ss.listing(owner=ss.USER, kind="decision"),
                "stuck": ss.stuck(),
                "open": ss.listing(limit=100)}

    def tasks_overview(self) -> str:
        """The person's three lists: waiting for their decision, stuck, open. JSON."""
        try:
            return json.dumps({"success": True, **self._tasks_lists()}, ensure_ascii=False)
        except ss.Unavailable as e:
            return json.dumps({"success": False, "code": e.code, "message": _(e.en, e.zh)}, ensure_ascii=False)

    def _tasks_for_peer(self, args: dict) -> dict:
        """GET /link/tasks?rev=: the same three lists for a paired machine that may control this one (its
        office draws them as a read-only board). `rev` hashes the lists but not `idle_s`, which grows with the
        clock; a caller that sends its last rev gets {"unchanged": true} back. The texts were masked when
        the store wrote them."""
        try:
            lists = self._tasks_lists()
        except ss.Unavailable as e:
            return {"ok": False, "success": False, "code": e.code, "message": _(e.en, e.zh)}
        calm = dict(lists, stuck=[{k: v for k, v in t.items() if k != "idle_s"} for t in lists["stuck"]])
        rev = hashlib.sha1(json.dumps(calm, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:12]
        if str(args.get("rev") or "") == rev:
            return {"ok": True, "success": True, "rev": rev, "unchanged": True}
        return {"ok": True, "success": True, "rev": rev, **lists}

    def task_detail(self, task_id) -> str:
        """One task with what happened to it, newest first (at most 20 events), as JSON: the
        office's task board opens a row with it. Read only; the store masked the texts when it
        wrote them."""
        try:
            t = ss.get(int(str(task_id).lstrip("#")), limit=20)
            t["events"] = list(reversed(t.get("events") or []))
            return json.dumps({"success": True, "task": t}, ensure_ascii=False)
        except ss.Unavailable as e:
            return json.dumps({"success": False, "code": e.code, "message": _(e.en, e.zh)}, ensure_ascii=False)
        except ss.NotFound:
            return json.dumps({"success": False, "code": "not_found",
                               "message": _(f"No task #{task_id}.", f"沒有 #{task_id} 這件任務。")}, ensure_ascii=False)
        except (ValueError, TypeError):
            return json.dumps({"success": False, "code": "invalid",
                               "message": _("a task number, e.g. 12", "要給任務編號，例如 12")}, ensure_ascii=False)
