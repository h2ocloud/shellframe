"""
agent_link — experimental agent-to-agent (A2A) messaging between ShellFrame tabs.

An agent writes a marker into its own output:

    [[SF:TO:Coding|幫我把 relay 的 timeout 調成 30 秒]]

and the bridge delivers that text into the tab holding the `Coding` role, tagged
with who sent it. The reply travels the same way. The user watches the whole
exchange as one conversation rather than switching tabs to relay messages by
hand.

Why this module exists separately (mirrors `board.py`):

  * Delivery is an *autonomous write into another agent's prompt*, and every tab
    runs with permissions bypassed. That is a real blast radius, so the rules
    live in one reviewable place instead of being scattered through the bridge.

  * The obvious failure is a loop: A messages B, B replies to A, forever, each
    turn burning tokens unattended. Depth, rate and self-send limits below are
    the whole point of the module, not an afterthought.

Guardrails, all enforced in `authorize()` before anything is delivered:

  - off unless `settings.experimental_a2a` is true (default false)
  - the target must be a named role in the configured roster; no free-form sids
  - a tab may not message itself
  - MAX_DEPTH: a message caused by a delivered message carries depth+1; past the
    cap the chain stops. Two agents can converse, but not indefinitely.
  - MAX_PER_MINUTE: per-sender rate cap, so a wedged agent cannot flood
  - MAX_CHARS: one message cannot become a prompt-injection payload dump

Everything accepted or refused is appended to a rolling audit log, which is also
what the phone app renders as the group conversation.
"""

import json
import re
import threading
import time
import uuid
from pathlib import Path

import a2a_ssh

STATE_DIR = Path.home() / ".local" / "state" / "shellframe"
LOG_FILE = STATE_DIR / "agent_link.json"

MAX_DEPTH = 4            # hops in one causal chain before it is cut
MAX_PER_MINUTE = 6       # messages a single tab may send per minute
MAX_CHARS = 4000         # per message
LOG_KEEP = 500           # rolling audit entries

_lock = threading.RLock()
_recent = {}             # sid -> [timestamps] for the rate limit
# sid -> depth of the message most recently delivered INTO that tab. A message
# that tab then sends inherits depth+1, which is what bounds a back-and-forth.
_inbound_depth = {}
# 訊息 id 的去重不放進 authorize()：速率測試直接打 authorize，同一句要能送滿額度。
_seen_ids = set()
_fingerprints = {}       # (from, kind, target, text) -> ts
_SID_RE = re.compile(r"s\d+")


def _now() -> float:
    return time.time()


def _load() -> list:
    try:
        return json.loads(LOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(entries: list):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = LOG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries[-LOG_KEEP:], ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(LOG_FILE)
    except Exception:
        pass


def record(entry: dict) -> dict:
    """Append one audit entry (delivered or refused) and return it."""
    entry = dict(entry)
    entry.setdefault("ts", _now())
    with _lock:
        entries = _load()
        entry["id"] = (entries[-1]["id"] + 1) if entries else 1
        entries.append(entry)
        _save(entries)
    return entry


def history(limit: int = 100) -> list:
    with _lock:
        return _load()[-max(1, min(int(limit or 100), LOG_KEEP)):]


def note_delivered(to_sid: str, depth: int):
    """Remember how deep the chain was when a tab last received something."""
    with _lock:
        _inbound_depth[to_sid] = int(depth)


def depth_for(from_sid: str) -> int:
    """Depth a message sent by this tab should carry."""
    with _lock:
        return int(_inbound_depth.get(from_sid, 0)) + 1


def clear_chain(sid: str):
    """The user typed into this tab, so whatever follows is a fresh chain, not a
    continuation of an agent-to-agent one."""
    with _lock:
        _inbound_depth.pop(sid, None)


def authorize(*, enabled: bool, from_sid: str, from_label: str,
              to_role: str, to_sid: str, text: str, roster: dict,
              remote: bool = False, skip_depth: bool = False) -> tuple:
    """Decide whether one tab may message another.

    Returns (ok: bool, reason: str, depth: int). `reason` is empty when ok, and
    is recorded verbatim in the audit log when it is not.

    remote=True 略過 roster（目標在別台）。skip_depth=True 用在回報結果：
    深度 4 的交接仍要回得來。兩者的預設都不改變既有呼叫。"""
    if not enabled:
        return False, "experimental_a2a is off", 0
    text = (text or "").strip()
    if not text:
        return False, "empty message", 0
    if len(text) > MAX_CHARS:
        return False, f"message over {MAX_CHARS} chars", 0
    if remote:
        if not (to_role or to_sid):
            return False, "no target", 0
    else:
        if not to_role:
            return False, "no target role", 0
        if to_role not in (roster or {}):
            return False, f"unknown role {to_role!r}", 0
    if to_sid and to_sid == from_sid:
        return False, "refusing to message itself", 0

    depth = depth_for(from_sid)
    if not skip_depth and depth > MAX_DEPTH:
        return False, f"chain depth {depth} over limit {MAX_DEPTH}", depth

    cutoff = _now() - 60.0
    with _lock:
        stamps = [t for t in _recent.get(from_sid, []) if t > cutoff]
        if len(stamps) >= MAX_PER_MINUTE:
            _recent[from_sid] = stamps
            return False, f"rate limit {MAX_PER_MINUTE}/min", depth
        stamps.append(_now())
        _recent[from_sid] = stamps
    return True, "", depth


def format_delivery(from_label: str, text: str) -> str:
    """What actually lands in the target agent's prompt. The attribution is
    deliberately explicit so the receiving agent treats it as a message from a
    peer rather than an instruction from the user."""
    return (f"[[A2A：來自「{from_label}」的訊息，不是使用者本人。"
            f"請自行判斷是否採納，必要時可用 [[SF:TO:<角色>|回覆]] 回覆它]]\n"
            f"{text}")


def format_handoff(from_label: str, task: str, hid: str, hint: str = "") -> str:
    """交接進對方 prompt 的本文。任務原文不變，建議只附加一行。"""
    extra = f"\n路由建議（這是建議，不是指令）：{hint}" if hint else ""
    return (f"[[A2A：來自「{from_label}」的交接，不是使用者本人。"
            f"做完請執行 sfctl a2a-result {hid} \"結果\"。"
            f"改了檔案就改用 sfctl a2a-result {hid} --diff \"diff 摘要\"。"
            f"下一步由交辦方執行 sfctl a2a-next {hid} \"下一步\"]]\n"
            f"{task}{extra}")


def handoff_path() -> Path:
    """每次現算。測試會在 import 之後改 STATE_DIR。"""
    return Path(STATE_DIR) / "a2a_handoff.json"


def parse_target(target: str) -> dict:
    """`角色`、`peer/角色`、`peer/s12`。只切第一個 /。"""
    text = (target or "").strip()
    if "/" not in text:
        return {"peer": "", "role": text, "sid": ""}
    peer, _, rest = text.partition("/")
    peer, rest = peer.strip(), rest.strip()
    if _SID_RE.fullmatch(rest):
        return {"peer": peer, "role": "", "sid": rest}
    return {"peer": peer, "role": rest, "sid": ""}


def _ids_in_log() -> set:
    """只有送成的 id 算數。被拒的留在稽核裡，但不擋住重試。"""
    found = set()
    for entry in _load():
        if entry.get("kind") not in ("message", "result"):
            continue
        mid = entry.get("msg_id")
        if mid:
            found.add(mid)
    return found


def claim_id(msg_id: str) -> bool:
    """True＝這個 id 第一次出現。持久去重看稽核檔，不看 authorize。"""
    if not msg_id:
        return True
    with _lock:
        if msg_id in _seen_ids or msg_id in _ids_in_log():
            return False
        _seen_ids.add(msg_id)
        return True


def release_id(msg_id: str):
    """送失敗就放回。已經寫進稽核檔的不會因此復活。"""
    if not msg_id:
        return
    with _lock:
        _seen_ids.discard(msg_id)


def _claim_fp(key) -> bool:
    now = _now()
    with _lock:
        prev = _fingerprints.get(key)
        if prev and now - prev < 60:
            return False
        _fingerprints[key] = now
        return True


def _release_fp(key):
    with _lock:
        _fingerprints.pop(key, None)


def _load_handoffs() -> dict:
    try:
        data = json.loads(handoff_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_handoffs(data: dict):
    try:
        path = handoff_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _put_handoff(hid: str, rec: dict):
    with _lock:
        data = _load_handoffs()
        data[hid] = rec
        _save_handoffs(data)


def _get_handoff(hid: str):
    with _lock:
        return _load_handoffs().get(hid)


def _mark_answered(hid: str) -> bool:
    with _lock:
        data = _load_handoffs()
        rec = data.get(hid)
        if not rec or rec.get("answered"):
            return False
        rec["answered"] = True
        data[hid] = rec
        _save_handoffs(data)
        return True


def _patch_handoff(hid: str, **fields) -> bool:
    """在鎖內改欄位。記錄已被丟掉就不復活，也不蓋掉背景執行剛寫的 answered。"""
    with _lock:
        data = _load_handoffs()
        rec = data.get(hid)
        if not rec:
            return False
        rec.update(fields)
        data[hid] = rec
        _save_handoffs(data)
        return True


def _drop_handoff(hid: str):
    with _lock:
        data = _load_handoffs()
        if hid in data:
            data.pop(hid, None)
            _save_handoffs(data)


def _peer_id(ctx, name: str) -> str:
    peers = ctx.peers() or {}
    if name in peers:
        return name
    for key, entry in peers.items():
        if (entry or {}).get("name") == name:
            return key
    return ""


def _fail(reason: str, **extra) -> dict:
    record({"kind": "refused", "reason": reason, "text": (extra.get("text") or "")[:200],
            **{k: v for k, v in extra.items() if k != "text"},
            "text": (extra.get("text") or "")[:200]})
    return {"success": False, "message": reason, "details": {"reason": reason}}


def _ok(message: str, details: dict) -> dict:
    return {"success": True, "message": message, "details": details}


def _is_coordinator(ctx, role: str, to_label: str) -> bool:
    """目標是總管（用角色名或分頁標籤指到都算）。總管不接交辦，但收訊息與結果。"""
    roster = ctx.roster() or {}
    if (roster.get(role) or {}).get("coordinator"):
        return True
    if not to_label:
        return False
    return any((entry or {}).get("coordinator")
               and ((entry or {}).get("label") or key) == to_label
               for key, entry in roster.items())


def deliver(ctx, *, role: str, sid: str, text: str, from_sid: str, from_label: str,
            kind: str = "send", msg_id: str = None, hint: str = "",
            route_hint=None, remote: bool = False, direction: str = "local",
            reply_peer: str = "", origin_sid: str = "", target_key: str = ""):
    """本機交付。remote=True 是別台送來的，不再往外送。"""
    text = (text or "").strip()
    target_key = target_key or sid or role
    if not ctx.enabled():
        return _fail("experimental_a2a is off", text=text, target=target_key,
                     from_sid=from_sid, from_label=from_label)
    if kind == "handoff" and not msg_id:
        msg_id = uuid.uuid4().hex[:12]
    if msg_id and not claim_id(msg_id):
        return _fail("duplicate id", text=text, target=target_key, msg_id=msg_id,
                     from_sid=from_sid)
    fp = (from_sid, kind, target_key, text)
    if not _claim_fp(fp):
        release_id(msg_id)
        return _fail("duplicate message", text=text, target=target_key, msg_id=msg_id,
                     from_sid=from_sid)

    to_sid, to_label = ctx.resolve(role, sid)
    if kind == "handoff" and _is_coordinator(ctx, role, to_label):
        release_id(msg_id)
        _release_fp(fp)
        return _fail("總管只派工與驗收", text=text, target=target_key, msg_id=msg_id,
                     from_sid=from_sid, from_label=from_label)
    roster = {} if remote else (ctx.roster() or {})
    ok, reason, depth = authorize(
        enabled=True, from_sid=from_sid, from_label=from_label,
        to_role=role or sid or "remote", to_sid="" if remote else to_sid,
        text=text, roster=roster, remote=remote)
    if not ok:
        release_id(msg_id)
        return _fail(reason, text=text, target=target_key, msg_id=msg_id,
                     from_sid=from_sid, from_label=from_label)

    if remote and not to_sid and not to_label:
        release_id(msg_id)
        return _fail(f"unknown role {role or sid!r}", text=text, target=target_key,
                     msg_id=msg_id, from_sid=from_sid)

    body = (format_handoff(from_label, text, msg_id, hint) if kind == "handoff"
            else format_delivery(from_label, text))
    if to_sid:
        res = ctx.send(to_sid, body) or {}
    elif role:
        res = ctx.delegate(role, body) or {}
        to_sid = ((res.get("details") or {}).get("sid") or "")
    else:
        res = {"success": False, "message": "no target"}
    if not res.get("success"):
        release_id(msg_id)
        record({"kind": "failed", "reason": res.get("message") or "",
                "text": text[:200], "target": target_key, "msg_id": msg_id,
                "from_sid": from_sid, "from_label": from_label, "depth": depth})
        return {"success": False, "message": res.get("message") or "delivery failed",
                "details": {"id": msg_id, "reason": res.get("message") or ""}}

    if to_sid:
        note_delivered(to_sid, depth)
    if from_sid.startswith("peer:"):
        note_delivered(from_sid, depth)
    if kind == "handoff" and msg_id:
        _put_handoff(msg_id, {
            "direction": direction, "origin_sid": origin_sid or from_sid,
            "reply_peer": reply_peer, "depth": depth, "answered": False,
            "target": target_key, "from_label": from_label,
        })
    record({"kind": "message", "text": text, "target": target_key, "msg_id": msg_id,
            "from_sid": from_sid, "from_label": from_label, "to_sid": to_sid,
            "to_role": role, "depth": depth})
    details = {"id": msg_id, "depth": depth, "target": target_key, "sid": to_sid}
    if kind == "handoff" and route_hint:
        details["route_hint"] = route_hint
    label = to_label or role or to_sid
    return _ok(f"已交給 {label} ({to_sid or 'new'})", details)


def _worker_name(target: str) -> str:
    text = (target or "").strip()
    if not text.startswith("worker:"):
        return ""
    return text.split(":", 1)[1].strip()


def dispatch_worker(ctx, *, name: str, target: str, text: str, from_sid: str = "",
                    from_label: str = "", kind: str = "handoff", msg_id: str = None,
                    hint: str = "", route_hint=None, allow_write: bool = False) -> dict:
    """SSH 只是執行器。結果在這台送回交辦方，不進 Frame Link 的離線佇列。

    驗證都是同步的，失敗立刻回。通過後 ssh 在背景跑（ctx.spawn），馬上回「已交出」，
    因為 sfctl 指令由同一條執行緒逐一處理，不能被一次 ssh 卡住。"""
    text = (text or "").strip()
    if not ctx.enabled():
        return _fail("experimental_a2a is off", text=text, target=target,
                     from_sid=from_sid, from_label=from_label)
    if not from_sid:
        return _fail("missing origin", text=text, target=target, from_sid=from_sid)
    worker = (ctx.workers() or {}).get(name) if getattr(ctx, "workers", None) else None
    if not worker:
        return _fail("unknown worker", text=text, target=target, from_sid=from_sid)
    if kind == "handoff" and not msg_id:
        msg_id = uuid.uuid4().hex[:12]
    if msg_id and not claim_id(msg_id):
        return _fail("duplicate id", text=text, target=target, msg_id=msg_id,
                     from_sid=from_sid)
    fp = (from_sid, kind, target, text)
    if not _claim_fp(fp):
        release_id(msg_id)
        return _fail("duplicate message", text=text, target=target, msg_id=msg_id,
                     from_sid=from_sid)
    ok, reason, depth = authorize(
        enabled=True, from_sid=from_sid, from_label=from_label or from_sid,
        to_role=name, to_sid="", text=text, roster={}, remote=True)
    if not ok:
        release_id(msg_id)
        _release_fp(fp)
        return _fail(reason, text=text, target=target, msg_id=msg_id, from_sid=from_sid)
    mode, gate_err = a2a_ssh.gate(worker, text, allow_write=allow_write)
    if gate_err:
        release_id(msg_id)
        _release_fp(fp)
        return _fail(gate_err, text=text, target=target, msg_id=msg_id, from_sid=from_sid)
    handoff = kind == "handoff" and bool(msg_id)
    if handoff:
        # 先寫記錄：結果回來之前，continue_handoff 看到 answered=False 會擋。
        _put_handoff(msg_id, {
            "direction": "local", "origin_sid": from_sid, "reply_peer": "",
            "depth": depth, "answered": False, "target": target,
            "from_label": from_label or from_sid,
        })

    def _failed(message: str, notify: bool):
        release_id(msg_id)
        _release_fp(fp)
        if handoff:
            _drop_handoff(msg_id)
        record({"kind": "failed", "reason": message, "text": text[:200],
                "target": target, "msg_id": msg_id, "from_sid": from_sid,
                "depth": depth})
        if notify:
            try:
                ctx.send(from_sid, format_delivery(name, f"執行失敗：{message}"))
            except Exception:
                pass

    def _run():
        """可能在背景執行緒。結果或失敗都送回交辦分頁，不丟例外。"""
        try:
            ran = a2a_ssh.execute(worker, text, allow_write=allow_write,
                                  runner=getattr(ctx, "run_ssh", None))
        except Exception as e:
            ran = {"ok": False, "message": str(e)[:200] or "ssh failed"}
        if not ran.get("ok"):
            _failed(ran.get("message") or "ssh failed", True)
            return
        out = (ran.get("out") or "").strip()
        if len(out) > MAX_CHARS:
            out = out[:MAX_CHARS] + "\n…（已截斷）"
        try:
            res = ctx.send(from_sid, format_delivery(name, out or "(no output)")) or {}
        except Exception as e:
            res = {"success": False, "message": str(e)[:200]}
        if not res.get("success"):
            _failed(res.get("message") or "delivery failed", False)
            return
        if handoff:
            _mark_answered(msg_id)
        record({"kind": "result", "text": out[:200], "target": target,
                "msg_id": msg_id, "from_sid": from_sid, "from_label": name,
                "depth": depth})

    spawn = getattr(ctx, "spawn", None)
    if callable(spawn):
        spawn(_run)
    else:
        _run()
    details = {"id": msg_id, "depth": depth, "target": target, "mode": mode}
    if kind == "handoff" and route_hint:
        details["route_hint"] = route_hint
    return _ok(f"已交給 {name}（ssh，{mode}），結果會送回這個分頁", details)


def dispatch(ctx, *, target: str, text: str, from_sid: str = "", from_label: str = "",
             kind: str = "send", msg_id: str = None, hint: str = "", route_hint=None,
             allow_write: bool = False) -> dict:
    """本機 sfctl 入口。peer/… 走簽過名的 remote，不進離線佇列。"""
    parsed = parse_target(target)
    text = (text or "").strip()
    if not ctx.enabled():
        return _fail("experimental_a2a is off", text=text, target=target,
                     from_sid=from_sid, from_label=from_label)
    worker = _worker_name(target)
    if worker:
        return dispatch_worker(
            ctx, name=worker, target=target, text=text, from_sid=from_sid,
            from_label=from_label, kind=kind, msg_id=msg_id, hint=hint,
            route_hint=route_hint, allow_write=allow_write)
    if not parsed["peer"]:
        return deliver(ctx, role=parsed["role"], sid="", text=text, from_sid=from_sid,
                       from_label=from_label or from_sid, kind=kind, msg_id=msg_id,
                       hint=hint, route_hint=route_hint, direction="local",
                       target_key=target)
    name = parsed["peer"]
    if name == (ctx.frame_name() or "") or name == (ctx.frame_id() or ""):
        return _fail("refusing to message itself", text=text, target=target,
                     from_sid=from_sid)
    pid = _peer_id(ctx, name)
    if not pid or pid == (ctx.frame_id() or ""):
        return _fail("unknown peer", text=text, target=target, from_sid=from_sid)
    if kind == "handoff" and not msg_id:
        msg_id = uuid.uuid4().hex[:12]
    if msg_id and not claim_id(msg_id):
        return _fail("duplicate id", text=text, target=target, msg_id=msg_id,
                     from_sid=from_sid)
    if not _claim_fp((from_sid, kind, target, text)):
        release_id(msg_id)
        return _fail("duplicate message", text=text, target=target, msg_id=msg_id,
                     from_sid=from_sid)
    remote_role = parsed["role"] or parsed["sid"]
    ok, reason, depth = authorize(
        enabled=True, from_sid=from_sid, from_label=from_label or from_sid,
        to_role=remote_role, to_sid="", text=text, roster={}, remote=True)
    if not ok:
        release_id(msg_id)
        return _fail(reason, text=text, target=target, msg_id=msg_id, from_sid=from_sid)
    payload = {"kind": kind, "id": msg_id, "target": remote_role, "text": text,
               "from_sid": from_sid, "from_label": from_label or from_sid,
               "depth": depth, "hint": hint}
    if kind == "handoff":
        _put_handoff(msg_id, {"direction": "out", "origin_sid": from_sid,
                              "reply_peer": pid, "depth": depth, "answered": False,
                              "target": target, "from_label": from_label or from_sid})
    res = ctx.remote(pid, payload) or {}
    if not res.get("success"):
        release_id(msg_id)
        _drop_handoff(msg_id)
        record({"kind": "failed", "reason": res.get("message") or "",
                "text": text[:200], "target": target, "msg_id": msg_id,
                "from_sid": from_sid, "depth": depth})
        return {"success": False, "message": res.get("message") or "peer unreachable",
                "details": {"id": msg_id, "reason": res.get("message") or ""}}
    record({"kind": "message", "text": text, "target": target, "msg_id": msg_id,
            "from_sid": from_sid, "from_label": from_label, "depth": depth,
            "to_role": remote_role})
    details = {"id": msg_id, "depth": depth, "target": target, "peer": pid}
    if kind == "handoff" and route_hint:
        details["route_hint"] = route_hint
    return _ok(f"已交給 {target}", details)


def _inject_result(ctx, rec: dict, text: str, from_label: str) -> dict:
    sid = rec.get("origin_sid") or ""
    if not sid:
        return {"success": False, "message": "missing origin"}
    return ctx.send(sid, format_delivery(from_label, text)) or {}


def report_result(ctx, hid: str, text: str, from_sid: str = "", from_label: str = "",
                  remote_return: bool = False, diff: bool = False) -> dict:
    """handoff 的結果只送一次。direction=out 的那筆要等對方回，自己不能回。

    diff=True 時在原文前加一行標籤，讓交辦方看得出這是 diff 摘要而不是狀態。"""
    text = (text or "").strip()
    if diff and text:
        text = "diff 摘要：\n" + text
    hid = (hid or "").strip()
    rec = _get_handoff(hid)
    if not rec:
        return _fail("unknown handoff", text=text, msg_id=hid, from_sid=from_sid)
    if rec.get("answered"):
        return _fail("result already delivered", text=text, msg_id=hid, from_sid=from_sid)
    if rec.get("direction") == "out" and not remote_return:
        return _fail("結果要由對方回報", text=text, msg_id=hid, from_sid=from_sid)
    result_id = hid + ":result"
    if not claim_id(result_id):
        return _fail("duplicate result", text=text, msg_id=result_id, from_sid=from_sid)
    if not ctx.enabled():
        release_id(result_id)
        return _fail("experimental_a2a is off", text=text, msg_id=hid, from_sid=from_sid)
    if rec.get("direction") == "in" and not remote_return:
        res = ctx.remote(rec.get("reply_peer") or "", {
            "kind": "result", "id": hid, "text": text,
            "from_sid": from_sid, "from_label": from_label or from_sid,
        }) or {}
    else:
        res = _inject_result(ctx, rec, text, from_label or from_sid)
    if not res.get("success"):
        release_id(result_id)
        return {"success": False, "message": res.get("message") or "result failed",
                "details": {"id": hid}}
    _mark_answered(hid)
    record({"kind": "result", "text": text, "msg_id": result_id, "target": hid,
            "from_sid": from_sid, "from_label": from_label})
    return _ok(f"已回報 {hid}", {"id": hid})


def continue_handoff(ctx, hid: str, task: str, from_sid: str = "", from_label: str = "",
                     hint: str = "", route_hint=None, allow_write: bool = False) -> dict:
    """交辦方把下一步送回同一個目標。受任方不能自己接續。"""
    hid = (hid or "").strip()
    step = (task or "").strip()
    rec = _get_handoff(hid)
    if not rec:
        return _fail("unknown handoff", text=step, msg_id=hid, from_sid=from_sid)
    origin = rec.get("origin_sid") or ""
    if rec.get("direction") == "in" or (origin and from_sid != origin):
        return _fail("下一步要由交辦方接續", text=step, msg_id=hid, from_sid=from_sid)
    if not rec.get("answered"):
        return _fail("上一步還沒回報", text=step, msg_id=hid, from_sid=from_sid)
    target = rec.get("target") or ""
    if not step:
        return _fail("empty message", text="", msg_id=hid, from_sid=from_sid)
    res = dispatch(
        ctx, target=target, text=f"延續 {hid}。\n{step}",
        from_sid=from_sid, from_label=from_label or from_sid, kind="handoff",
        hint=hint, route_hint=route_hint, allow_write=allow_write)
    new_id = ((res.get("details") or {}).get("id") or "")
    if res.get("success") and new_id:
        _patch_handoff(new_id, continues=hid)
        details = dict(res.get("details") or {})
        details["continues"] = hid
        res = dict(res)
        res["details"] = details
    return res


def accept_remote(ctx, payload: dict) -> dict:
    """對方 POST /link/a2a/send 之後。source_peer 由簽章端寫入，不信 body 自填以外的覆寫。"""
    payload = dict(payload or {})
    source = (payload.get("source_peer") or "").strip()
    kind = payload.get("kind") or "send"
    if kind == "result":
        return report_result(
            ctx, payload.get("id") or "", payload.get("text") or "",
            from_sid=f"peer:{source}" if source else (payload.get("from_sid") or ""),
            from_label=payload.get("from_label") or source, remote_return=True)
    target = (payload.get("target") or "").strip()
    if _SID_RE.fullmatch(target):
        role, sid = "", target
    else:
        role, sid = target, ""
    rate_sid = f"peer:{source}" if source else (payload.get("from_sid") or "")
    try:
        claimed = int(payload.get("depth") or 0)
    except (TypeError, ValueError):
        claimed = 0
    if claimed:
        with _lock:
            prev = int(_inbound_depth.get(rate_sid, 0))
            _inbound_depth[rate_sid] = max(prev, claimed - 1)
    return deliver(
        ctx, role=role, sid=sid, text=payload.get("text") or "",
        from_sid=rate_sid, from_label=payload.get("from_label") or source,
        kind="handoff" if kind == "handoff" else "send",
        msg_id=payload.get("id") or None, hint=payload.get("hint") or "",
        remote=True, direction="in", reply_peer=source,
        origin_sid=payload.get("from_sid") or "", target_key=target)


def log_text(limit: int = 50) -> dict:
    entries = history(limit)
    lines = []
    for entry in entries:
        who = entry.get("from_label") or entry.get("from_sid") or "?"
        where = entry.get("target") or entry.get("to_role") or ""
        why = entry.get("reason") or ""
        lines.append(" ".join(x for x in (entry.get("kind") or "", who, "->", where, why) if x))
    return {"success": True, "message": "\n".join(lines) or "(empty)",
            "details": {"entries": entries}}
