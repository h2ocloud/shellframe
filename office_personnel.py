"""The office's personnel cabinet (a built-in office object, see office_objects.py).

One file per bot on this computer, read only: its duty, the CLI and model it runs on, whether it is the
primary bot and whether it is awake (and in which tab), the open tasks it holds, the schedules it owns
(`owners` in schedules.json) and how much it keeps on the bookshelf, each linked to where it lives (the
whiteboard card, the calendar, the bookshelf's filter for that bot). A bot that lives outside ShellBot
is labelled external and shows nothing of its endpoint; a bot on a local model is labelled as such.
Paired computers' bots are not listed. No conversation is read.

Only roster fields `bots.public_roster` would hand the page are used, and the one action opens the
existing bot editor (`go: {settings: role}`; the page closes the office and opens it).
"""

import ipaddress
import json
import os
import re
from urllib.parse import urlparse

import agent_model
import agent_status
import bots
import office_objects as oo

CLI_LABELS = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode", "pi": "pi", "grok": "Grok Build",
              "cursor": "Cursor Agent", "agy": "Antigravity", "gemini": "Gemini CLI"}
LOCAL_PROVIDERS = ("ollama", "lmstudio", "lm-studio", "llamacpp", "llama.cpp", "vllm", "localai", "mlx")
TASK_WORDS = {"todo": ("To do", "待辦"), "doing": ("Doing", "進行中"), "waiting": ("Waiting", "等別人")}

DECLARATION = {
    "id": "personnel",
    "title": {"en": "Personnel cabinet", "zh": "人事檔案櫃"},
    "short": {"en": "Personnel", "zh": "人事"},           # the toolbar button's word (decided: 「書櫃」「人事」)
    "subtitle": {"en": "Each bot's duty, the model it runs on, its tasks and schedules. Read only, this computer.",
                 "zh": "每個 bot 的執掌、跑的模型、手上的任務和負責的排程。只讀，這台電腦。"},
    "furniture": "cabinet",
    "place": "lobby:wall",
    "button": True,
    "badge_hint": {"en": "", "zh": ""},
    "search_hint": {"en": "Search a name or a duty (e.g. “schedule”)…", "zh": "搜名字或執掌（例如「排程」「Scrum」）…"},
    "empty": {"en": "No bots yet.", "zh": "還沒有 bot。"},
    "readonly_note": {"en": "Read only: “Edit duty” opens the bot's settings.", "zh": "只看不改：「編輯執掌」會打開 bot 的設定頁。"},
    "filters": [
        {"id": "group", "label": {"en": "Group", "zh": "分組"}},
        {"id": "state", "label": {"en": "State", "zh": "狀態"},
         "options": [["awake", {"en": "Awake", "zh": "醒著"}], ["asleep", {"en": "Asleep", "zh": "休息"}],
                     ["external", {"en": "External", "zh": "外部"}]]},
    ],
    "sorts": [["name", {"en": "Name", "zh": "名稱"}], ["state", {"en": "State", "zh": "狀態"}]],
    "actions": [{"id": "edit_duty", "label": {"en": "Edit duty", "zh": "編輯執掌"}, "primary": True}],
}


def pi_provider_hosts(path=None) -> dict:
    """provider → the host of its `baseUrl` in pi's models.json. Only that host is read: the file also
    holds each provider's key, which is never kept."""
    path = path or os.path.join(os.environ.get("PI_CODING_AGENT_DIR") or os.path.expanduser("~/.pi/agent"), "models.json")
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            providers = (json.load(f) or {}).get("providers") or {}
        for name, spec in providers.items() if isinstance(providers, dict) else ():
            if isinstance(spec, dict):
                url = spec.get("baseUrl") or spec.get("baseURL") or spec.get("base_url") or ""
                out[str(name)] = (urlparse(str(url)).hostname or "") if url else ""
    except (OSError, ValueError, AttributeError):
        return {}
    return out


def is_local_host(host: str) -> bool:
    host = str(host or "").strip("[]").lower()
    if host in ("localhost", "host.docker.internal") or host.endswith((".local", ".lan", ".internal")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def runs_local(cmd: str, model: str = "", pi_hosts=None) -> bool:
    """Whether a bot's command runs a model on this computer (or its network): a provider named like a local
    server (`ollama/…`, `--provider lmstudio`), or pi on a provider whose base URL is local; pi with no
    provider to look at counts as local (that is what it is for here), pi on a public host does not."""
    cmd, model = str(cmd or ""), str(model or "")
    m = re.search(r"--provider(?:=|\s+)[\"']?([A-Za-z0-9][\w.\-]*)", cmd)
    mm = re.search(r"(?:--model|-m)(?:=|\s+)[\"']?([^\s\"']+)", cmd)
    pinned = model if "/" in model else (mm.group(1) if mm and "/" in mm.group(1) else "")
    prov = (m.group(1) if m else (pinned.split("/", 1)[0] if pinned else "")).lower()
    if prov in LOCAL_PROVIDERS:
        return True
    if agent_status.worker_kind(cmd) != "pi":
        return False
    hosts = pi_hosts or {}
    if prov and prov in hosts:
        return is_local_host(hosts[prov]) if hosts[prov] else True
    if not prov and len(hosts) == 1:
        only = next(iter(hosts.values()))
        return is_local_host(only) if only else True
    return True


def _names(role, entry) -> set:
    return {role, bots.label_of(role, entry), *bots.former_names(entry)}


def _cli(entry) -> str:
    kind = agent_status.worker_kind(str(entry.get("cmd") or ""))
    return "" if kind == "other" else CLI_LABELS.get(kind, kind)


def _state(role, entry, states) -> str:
    if bots.is_external(entry):
        return "external"
    return "awake" if (states.get(role) or {}).get("state") == "awake" else "asleep"


class Personnel(oo.OfficeObject):
    declaration = DECLARATION

    def _roster(self, ctx) -> dict:
        return {r: e for r, e in (ctx.roster() or {}).items() if bots.is_bot(e)}

    def _group(self, ctx, role, entry) -> str:
        custom = (ctx.groups() or {}).get(role)
        return str(custom).strip()[:24] if custom and str(custom).strip() else (_cli(entry) or ctx.t("Other", "其他"))

    def _local(self, ctx, entry) -> bool:
        return not bots.is_external(entry) and runs_local(entry.get("cmd"), entry.get("model"), ctx.pi_hosts())

    def summary(self, ctx) -> dict:
        return {"badge": 0}

    def list(self, ctx, query="", filters=None, sort="name", offset=0, limit=oo.LIST_MAX) -> dict:
        filters, q = filters or {}, str(query or "").strip().casefold()
        states = ctx.bot_states() or {}
        rows = []
        for role, entry in self._roster(ctx).items():
            label, state, group = bots.label_of(role, entry), _state(role, entry, states), self._group(ctx, role, entry)
            if filters.get("group") and filters["group"] != group:
                continue
            if filters.get("state") and filters["state"] != state:
                continue
            duty = str(entry.get("responsibility") or "")
            if q and q not in " ".join((label, duty, group, _cli(entry))).casefold():
                continue
            rows.append((role, entry, label, state, group, duty))
        primary = bots.primary_role(self._roster(ctx))
        word = {"awake": ctx.t("Awake", "醒著"), "asleep": ctx.t("Asleep", "休息"), "external": ctx.t("External", "外部")}
        items = []
        for role, entry, label, state, group, duty in rows:
            tags = []
            if role == primary:
                tags.append({"id": "primary", "label": ctx.t("Primary", "主要"), "tone": "info"})
            if state == "external":
                tags.append({"id": "external", "label": ctx.t("External", "外部"), "tone": "dim"})
            if self._local(ctx, entry):
                tags.append({"id": "local", "label": ctx.t("Local model", "地端模型"), "tone": "info"})
            items.append({"id": f"bot:{role}", "group": group, "title": label, "subtitle": duty[:120], "type": state,
                          "owner": label, "owner_key": "" if state == "external" else f"b:{role}", "tags": tags,
                          "tone": "ok" if state == "awake" else "", "state_word": word[state]})
        if sort == "state":
            rank = {"awake": 0, "asleep": 1, "external": 2}
            items.sort(key=lambda r: (rank.get(r["type"], 3), r["title"].casefold()))
        else:
            items.sort(key=lambda r: r["title"].casefold())
        names = {}
        for r in items:
            names[r["group"]] = names.get(r["group"], 0) + 1
        groups = [{"id": g, "title": g, "count": n} for g, n in sorted(names.items(), key=lambda kv: kv[0].casefold())]
        order = {g["id"]: i for i, g in enumerate(groups)}
        items.sort(key=lambda r: order[r["group"]])
        return {"groups": groups, "items": items[:limit], "total": len(items)}

    def item(self, ctx, item_id) -> dict:
        m = re.fullmatch(r"bot:(.+)", str(item_id))
        roster = self._roster(ctx)
        role = m.group(1) if m else ""
        entry = roster.get(role)
        if entry is None:
            return {}
        label, names = bots.label_of(role, entry), _names(role, entry)
        states = ctx.bot_states() or {}
        state = _state(role, entry, states)
        st = states.get(role) or {}
        ext = bots.is_external(entry)
        local = self._local(ctx, entry)
        pin = agent_model.pinned(str(entry.get("cmd") or ""), entry.get("model")) if not ext else {}
        duty = str(entry.get("responsibility") or "").strip()
        chips = []
        if role == bots.primary_role(roster):
            chips.append({"text": "★ " + ctx.t("Primary", "主要"), "tone": "info"})
        chips.append({"text": {"awake": ctx.t("Awake", "醒著"), "asleep": ctx.t("Asleep", "休息"),
                               "external": ctx.t("External", "外部")}[state], "tone": "ok" if state == "awake" else "dim"})
        if state == "awake" and st.get("position"):
            chips.append({"text": ctx.t(f"Tab {st['position']}", f"第 {st['position']} 個分頁"), "tone": ""})
        if ext:
            chips.append({"text": ctx.t("Lives outside ShellBot", "在 ShellBot 外面"), "tone": "dim"})
        else:
            if _cli(entry):
                chips.append({"text": _cli(entry), "tone": "info"})
            chips.append({"text": (pin.get("label") or pin.get("model")) or ctx.t("CLI default model", "CLI 預設模型"), "tone": ""})
            if local:
                chips.append({"text": ctx.t("Local model", "地端模型"), "tone": "info"})
            chips.append({"text": ctx.t("Memory on", "記憶開") if bots.is_memory_bot(entry) else ctx.t("Memory off", "記憶關"),
                          "tone": "ok" if bots.is_memory_bot(entry) else "dim"})
        links = []
        lines = [f"## {ctx.t('Duty', '執掌')}", duty or ctx.t("_(not written yet)_", "_（還沒寫）_"), ""]
        mine = [t for t in (ctx.open_tasks() or []) if str(t.get("owner") or "") in names]
        lines.append(f"## {ctx.t('Open tasks', '手上任務')} · {len(mine)}")
        for t in mine[:12]:
            en, zh = TASK_WORDS.get(t.get("status"), (str(t.get("status") or ""), str(t.get("status") or "")))
            tag = ctx.t(en, zh)
            links.append({"label": f"#{t.get('id')} {t.get('title') or ''} · {tag}", "view": "board", "task": t.get("id"),
                          "note": ctx.t("whiteboard", "白板")})
        lines.append(ctx.t("Click one below to see its card on the whiteboard.", "點下面的一項，到白板看那張卡。") if mine
                     else ctx.t("None.", "沒有。"))
        lines.append("")
        sched = [s for s in (ctx.schedules() or []) if str(s.get("owner") or "") in names]
        lines.append(f"## {ctx.t('Schedules it owns', '負責的排程')} · {len(sched)}")
        for s in sched[:12]:
            when = s.get("when") if isinstance(s.get("when"), dict) else {"en": s.get("when") or "", "zh": s.get("when") or ""}
            links.append({"label": f"{s.get('title') or s.get('id')} · {oo.loc(when, ctx.lang)}", "view": "cal",
                          "note": ctx.t("calendar", "日曆")})
        lines.append(ctx.t("Click one below to see it on the calendar.", "點下面的一項，到日曆看。") if sched
                     else ctx.t("None.", "沒有。"))
        lines.append("")
        if not ext:
            n_mem = 1 if bots.memory_file(role).is_file() else 0
            n_skill = len(bots.own_skill_catalog(role))
            lines.append(f"## {ctx.t('Its files', '它的檔案')}")
            lines.append(ctx.t(f"{n_mem} memory · {n_skill} own skills", f"{n_mem} 份記憶 · {n_skill} 個自己的技能"))
            links.append({"label": ctx.t("Bookshelf", "書櫃") + " / " + label, "object": "bookshelf",
                          "filters": {"owner": role}, "note": ctx.t("shelf", "書櫃")})
        return {"title": label, "subtitle": ctx.t("Personnel file", "人事檔案"), "markdown": "\n".join(lines),
                "chips": chips, "links": links, "actions": ["edit_duty"], "owner": label,
                "owner_key": "" if ext else f"b:{role}"}

    def action(self, ctx, item_id, action_id, args) -> dict:
        m = re.fullmatch(r"bot:(.+)", str(item_id))
        role = m.group(1) if m else ""
        if action_id == "edit_duty" and role in self._roster(ctx):
            return {"ok": True, "go": {"settings": role}}
        return {"ok": False}
