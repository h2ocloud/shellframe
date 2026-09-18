"""
agent_group — experimental role groups: one message, several agents, one thread.

A group is a name plus a list of **roster roles** (Coding, 研究, …). Roles rather
than session ids on purpose: a role survives a tab being closed and reopened,
which session ids do not, so a group stays meaningful across restarts.

What it buys you: instead of relaying the same question into four tabs by hand
and stitching their answers together in your head, you send once and read one
thread with every reply attributed.

Everything here is gated on `settings.experimental_groups`. Sending to a group
drives several agents at once, each running with permissions bypassed, so the
blast radius is real and the gate is deliberate.

Storage is the ShellFrame config (`groups`), so it travels with the rest of the
user's setup and needs no separate file to keep in sync.
"""

import re
import time

MAX_NAME = 40
MAX_MEMBERS = 8          # a fan-out larger than this is a script, not a chat
MAX_GROUPS = 20


def _clean_name(name: str) -> str:
    return re.sub(r"[\x00-\x1f]", "", str(name or "")).strip()[:MAX_NAME]


def normalize(groups) -> dict:
    """Config may hold anything; hand back a well-formed {name: {...}} map."""
    out = {}
    if not isinstance(groups, dict):
        return out
    for name, g in list(groups.items())[:MAX_GROUPS]:
        name = _clean_name(name)
        if not name or not isinstance(g, dict):
            continue
        roles = [str(r).strip() for r in (g.get("roles") or []) if str(r).strip()]
        out[name] = {
            "roles": roles[:MAX_MEMBERS],
            "created": g.get("created") or time.time(),
        }
    return out


def validate(name: str, roles, roster: dict):
    """(ok, cleaned_name, cleaned_roles, error). Roles must exist in the roster —
    a group naming a role that does not exist would fail silently at send time,
    which is worse than refusing it here."""
    name = _clean_name(name)
    if not name:
        return False, "", [], "群組名稱必填"
    seen, cleaned = set(), []
    for r in (roles or []):
        r = str(r).strip()
        if not r or r in seen:
            continue
        if r not in (roster or {}):
            return False, name, [], f"角色「{r}」不在名冊裡"
        seen.add(r)
        cleaned.append(r)
    if not cleaned:
        return False, name, [], "群組至少要有一個角色"
    if len(cleaned) > MAX_MEMBERS:
        return False, name, [], f"一個群組最多 {MAX_MEMBERS} 個角色"
    return True, name, cleaned, ""


def format_group_message(group: str, members: list, sender: str, text: str,
                         a2a: bool = False) -> str:
    """What each member actually receives.

    Naming the other members matters: an agent that knows who else is in the
    room can defer to the one whose job it is, instead of all of them doing the
    same work in parallel.

    `a2a` says whether agent-to-agent messaging is also switched on. Only then
    are members told about `[[SF:TO:…]]`; offering it while it is off would send
    them at a marker that is refused before delivery."""
    others = [m for m in members if m != sender]
    roster_line = ("；同一個群組裡還有：" + "、".join(others)) if others else ""
    handoff = ("，必要時用 [[SF:TO:<角色>|訊息]] 直接找他" if (a2a and others)
               else "")
    return (f"[[GROUP:{group}｜這是群組訊息，同時送給 {len(members)} 個角色{roster_line}。"
            f"請只做你負責的部分；若該由別人處理，說明後交給對方{handoff}]]\n{text}")


def merge_turns(per_member: dict, limit: int = 120) -> list:
    """Interleave each member's turns into one chronological thread.

    per_member: {role: [turn dicts with a 'ts']}. Each turn is tagged with its
    speaker so the reader can tell the agents apart — without that, a merged
    thread is unreadable."""
    merged = []
    for role, turns in (per_member or {}).items():
        for t in (turns or []):
            if not isinstance(t, dict):
                continue
            t = dict(t)
            t["speaker"] = role
            merged.append(t)
    merged.sort(key=lambda t: float(t.get("ts") or 0))
    return merged[-max(1, int(limit)):]
