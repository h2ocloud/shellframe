"""
agent_grok — Grok Build（xAI 的 `grok` TUI）的狀態、模型、對話與用量讀取器。

grok 沒有 Claude／Codex 那種「檔名就是 session id」的 transcript，也沒有 hook 可以回報。
每個對話放在 ~/.grok/sessions/<cwd 編碼>/<session-uuid>/ 底下：
  events.jsonl               turn／tool／permission 的時間點——燈號的依據，不靠畫面猜
  chat_history.jsonl         對話本文——上滑歷史與手機的 chat 視圖
  summary.json               current_model_id、reasoning_effort（/model 切換會寫回）
  usage.json、signals.json   這個 session 的 token 與 context 用量
分頁要對到自己的那個目錄，依序試：lsof 看 pane 行程正開著哪個 events.jsonl（/new 之後也
跟得上）→ 啟動時 pin 的 --session-id → manifest 記下的 uuid。全都不中就回 None，**不**退回
「同一個 cwd 最新的那份」：兩個 grok 分頁開在同一目錄時會互相讀到對方的對話。

純標準庫。agent_status 在頂層 import 這裡，所以需要它的 helper 時在函式內 lazy import。
"""

import glob
import json
import os
import re
import shlex
import time
import uuid

GROK_SESSIONS = os.path.expanduser("~/.grok/sessions")

# `grok <子指令>` 是管理動作（login、sessions…），不是開一段對話，不能 pin session。
SUBCOMMANDS = frozenset((
    "agent clone completions cursor-worker dashboard doctor du disk-usage export "
    "help inspect leader login logout mcp memory models plugin sessions setup "
    "trace update usage version v worktree wrap").split())

# 有這些旗標就是「接續／分叉／無互動」，不另外指定 session id。
_NO_PIN_FLAGS = frozenset((
    "--session-id", "-s", "--resume", "-r", "--continue", "-c", "--fork-session",
    "-p", "--single", "--prompt-file", "--prompt-json"))
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_UUID_RE = re.compile(_UUID)
_CMD_UUID_RE = re.compile(
    r"(?:^|\s)(?:--session-id|-s|--resume|-r)(?:=|\s+)(" + _UUID + r")(?=\s|$)", re.I)

_TAIL_BYTES = 64 * 1024
DONE_WINDOW_S = 15          # 一輪結束後仍亮 done 的窗口（同 agent_status 其他 provider）
STALL_S = 60                # 最後一筆事件超過這麼久、畫面又沒有忙碌 footer → 視為已中斷
# 忙碌時的 footer 是 `Ctrl+c:cancel`；閒置時是 `Ctrl+x:shortcuts`，不會命中。
# `[stop]` 是 spinner 那一行的結尾。
TURN_BUSY_RE = re.compile(r"Ctrl\+c:cancel|\[stop\]")
_DIR_TTL = 5.0              # lsof 很慢；狀態每 0.5 秒問一次，同一分頁 5 秒內沿用結果
_DIR_CACHE = {}             # tmux_name → (查詢時間, session 目錄或 None)
_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S)
# Claude 紀錄一定帶這些鍵；grok 的 type 名稱（user／assistant／system）跟 Claude 撞名，
# 所以判別靠「沒有這些鍵」。
_CLAUDE_KEYS = ("sessionId", "uuid", "timestamp", "message")
_RECORD_TYPES = frozenset(("system", "user", "assistant", "reasoning", "tool_result"))


def _tokens(cmd):
    try:
        return shlex.split(cmd or "")
    except ValueError:
        return []


def is_grok_cmd(cmd: str) -> bool:
    """第一個 token 的 basename 是 grok（或 sf-grok 啟動器）。

    `agent` 不算：安裝器會把它連到同一支 binary，但它是通用字，不能讓所有叫 agent 的
    指令都被當成 grok。
    """
    toks = _tokens(cmd)
    if not toks:
        return False
    base = toks[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base == "grok" or base.startswith("sf-grok")


def cmd_session_uuid(cmd: str):
    """cmd 裡 --session-id／-s／--resume／-r 指定的 uuid（小寫），沒有回 None。"""
    m = _CMD_UUID_RE.search(cmd or "")
    return m.group(1).lower() if m else None


def pin_session_id(cmd: str):
    """新開的 grok 分頁先替它指定 session uuid（--session-id），之後才認得出它的目錄。

    grok 的目錄名是 cwd 的編碼，沒有任何東西會回報「這個分頁剛開了哪個 session」；
    自己指定 uuid 是唯一不必猜的對應。續接、分叉、無互動與子指令都原樣不動。
    回傳 (cmd, uuid 或 None)。
    """
    toks = _tokens(cmd)
    if not is_grok_cmd(cmd) or (len(toks) > 1 and toks[1] in SUBCOMMANDS):
        return cmd, None
    if any(t.split("=", 1)[0] in _NO_PIN_FLAGS for t in toks[1:]):
        return cmd, None
    sid = str(uuid.uuid4())
    return f"{cmd.rstrip()} --session-id {sid}", sid


def cmd_with_resume(cmd: str, sid: str) -> str:
    """把 grok 的啟動指令改成 `--resume <sid>`：同一個 session，不 fork。

    舊的 --session-id／-s／--resume／-r 連同它的值一起拿掉。值只在下一個 token 不是旗標時
    才吃，所以 `--resume --effort low` 的 --effort 會留下。--continue、-c、--fork-session 也拿掉：
    已經指定了 id，不需要「最近一次」；--fork-session 則會開新的。其他旗標與 wrapper 名稱原樣保留。
    """
    toks = _tokens(cmd)
    if not sid or not toks or not is_grok_cmd(cmd):
        return cmd
    out = [toks[0], "--resume", sid]
    i = 1
    while i < len(toks):
        t = toks[i]
        name = t.split("=", 1)[0]
        if name in ("--session-id", "-s", "--resume", "-r"):
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            takes_value = "=" not in t and nxt and not nxt.startswith("-")
            i += 2 if takes_value else 1
        elif name in ("--continue", "-c", "--fork-session"):
            i += 1
        else:
            out.append(t)
            i += 1
    return shlex.join(out)


# ── tab → session 目錄 ──────────────────────────────────────────────────────

def _dir_for_id(sid):
    """uuid → 它在 GROK_SESSIONS 底下的目錄（要有 summary 或 events 才算）。"""
    sid = (sid or "").strip().lower()
    if not _UUID_RE.fullmatch(sid):
        return None
    for d in glob.glob(os.path.join(GROK_SESSIONS, "*", sid)):
        if any(os.path.exists(os.path.join(d, f)) for f in ("summary.json", "events.jsonl")):
            return d
    return None


def _recency(d):
    """同一個 pane 的 lsof 可能同時看到好幾份（/new 之後舊的 events.jsonl 還開著）。
    取 created_at 最晚的那份；沒有 created_at 就退 events.jsonl 的 mtime。"""
    import agent_status as A
    created = None
    try:
        with open(os.path.join(d, "summary.json"), encoding="utf-8") as f:
            created = A._parse_iso(json.load(f).get("created_at"))
    except (OSError, ValueError, AttributeError):
        pass
    return (created or 0.0, A._safe_mtime(os.path.join(d, "events.jsonl")))


def _live_dir(tmux_name):
    """pane 行程樹裡 lsof 看到、正開著 events.jsonl 的 session 目錄（新的優先）。"""
    import agent_status as A
    pane = A._tmux_pane_pid(tmux_name)
    if not pane:
        return None
    paths = A._lsof_open_paths(A._pid_tree(pane), "/events.jsonl")
    dirs = {os.path.dirname(p) for p in paths
            if p.endswith("/events.jsonl") and A._under(p, GROK_SESSIONS)}
    return max(dirs, key=_recency) if dirs else None


def session_dir(worker):
    """這個分頁的 grok session 目錄；認不出來回 None。永不拋例外。"""
    try:
        tmux = worker.get("tmux_name") or ""
        now = time.time()
        hit = _DIR_CACHE.get(tmux) if tmux else None
        if hit and now - hit[0] < _DIR_TTL:
            return hit[1]
        d = _live_dir(tmux) if tmux else None
        if not d:
            for sid in (worker.get("grok_session_id"), worker.get("session_id"),
                        cmd_session_uuid(worker.get("cmd") or "")):
                d = _dir_for_id(sid)
                if d:
                    break
        if tmux:
            _DIR_CACHE[tmux] = (now, d)
        return d
    except Exception:
        return None


def session_id(worker):
    d = session_dir(worker)
    return os.path.basename(d) if d else None


def session_exists(sid):
    return _dir_for_id(sid) is not None


def transcript_path(worker):
    d = session_dir(worker)
    p = os.path.join(d, "chat_history.jsonl") if d else ""
    return p if p and os.path.exists(p) else None


# ── chat_history.jsonl → agent_status 事件 ─────────────────────────────────

def is_grok_record(o) -> bool:
    """chat_history.jsonl 的一筆（見檔頭的判別說明）。"""
    return (isinstance(o, dict) and o.get("type") in _RECORD_TYPES
            and not any(k in o for k in _CLAUDE_KEYS))


def norm(o):
    """一筆 chat_history 紀錄 → agent_status 的事件：一筆、一串，或跳過時 None。

    使用者訊息只留真正的提問。沒有 prompt_index 的是前面塞進去的 context，有 synthetic_reason
    的是系統提醒，兩者都不是使用者說的話。提問外殼 <user_query> 剝掉。
    """
    import agent_status as A
    t = o.get("type")
    if t == "user":
        if o.get("synthetic_reason") or "prompt_index" not in o:
            return None
        raw = A._content_text(o.get("content"))
        m = _QUERY_RE.search(raw)
        text = m.group(1) if m else raw
        return {"kind": "user_msg", "ts": None, "text": text} if text else None
    if t == "assistant":
        evs = []
        text = A._content_text(o.get("content"))
        if text:
            evs.append({"kind": "assistant_text", "ts": None, "text": text})
        for c in o.get("tool_calls") or []:
            if not isinstance(c, dict):
                continue
            name = str(c.get("name") or "")
            try:
                args = json.loads(c.get("arguments") or "{}")
            except (TypeError, ValueError):
                args = {}
            evs.append({"kind": "tool_call", "ts": None, "tool": name,
                        "target": A._target(name, args)})
        if not evs:
            return None
        return evs[0] if len(evs) == 1 else evs
    if t == "tool_result":
        return {"kind": "tool_result", "ts": None}
    return None


# ── 模型 ──────────────────────────────────────────────────────────────────

def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _parse_summary(path):
    d = _load_json(path) or {}
    return {"model": (d.get("current_model_id") or "").strip() or None,
            "effort": (d.get("reasoning_effort") or "").strip() or None}


def _cmd_flags(cmd):
    """啟動指令裡明寫的 -m／--model 與 --effort／--reasoning-effort。"""
    toks = _tokens(cmd)
    out = {}
    for i, t in enumerate(toks):
        key, eq, val = t.partition("=")
        if not eq:
            val = toks[i + 1] if i + 1 < len(toks) else ""
        if not val or val.startswith("-"):
            continue
        if key in ("-m", "--model"):
            out["model"] = val
        elif key in ("--effort", "--reasoning-effort"):
            out["effort"] = val
    return out


def model_info(worker):
    """tab 的 {name, effort, provider}。優先 summary.json（/model、--effort 切換都會寫進去），
    沒有才退回啟動指令裡明寫的值；什麼都沒有回 None。"""
    import agent_status as A
    try:
        d = session_dir(worker)
        info = A._cached_parse(os.path.join(d, "summary.json"), _parse_summary) if d else None
        flags = _cmd_flags(worker.get("cmd") or "")
        name = (info or {}).get("model") or flags.get("model")
        if not name:
            return None
        effort = (info or {}).get("effort") or flags.get("effort") or ""
        return {"name": name, "effort": effort, "provider": "grok"}
    except Exception:
        return None


# ── 狀態 ──────────────────────────────────────────────────────────────────

def _tail_events(path):
    """events.jsonl 檔尾的事件（舊→新），略過 mcp_* 的伺服器雜訊。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - _TAIL_BYTES))
            lines = f.read().split(b"\n")
    except OSError:
        return []
    if size > _TAIL_BYTES:
        lines = lines[1:]          # 第一行可能被切半
    out = []
    for line in lines:
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if isinstance(o, dict) and not str(o.get("type") or "").startswith("mcp_"):
            out.append(o)
    return out


def _awaiting_permission(evs):
    """從後往前找：先遇到 permission_resolved 或 turn 邊界，代表沒有待決的；先遇到請求，就是在等人。"""
    for e in reversed(evs):
        t = e.get("type")
        if t in ("permission_resolved", "turn_started", "turn_ended"):
            return False
        if t == "permission_requested" or (
                t == "phase_changed" and e.get("phase") == "permission_prompt"):
            return True
    return False


def _status(worker, now, screen):
    busy = bool(TURN_BUSY_RE.search(screen))
    d = session_dir(worker)
    path = os.path.join(d, "events.jsonl") if d else ""
    evs = _tail_events(path) if path else []
    if not evs:
        if busy:
            return "working", "grok screen: busy footer"
        if screen.strip():
            return "idle", "grok screen: idle"
        return "unknown", "grok no screen"
    import agent_status as A
    last = evs[-1]
    ts = A._parse_iso(last.get("ts")) or A._safe_mtime(path)
    age = max(0.0, now - ts)
    if last.get("type") == "turn_ended":
        if age <= DONE_WINDOW_S:
            return "done", f"grok turn {last.get('outcome') or 'ended'}"
        return "idle", "grok settled"
    if _awaiting_permission(evs):
        return "decision", "grok permission prompt"
    if age > STALL_S and not busy:
        return "idle", f"grok silent {int(age)}s, no busy footer"
    return "working", f"grok {last.get('type')}"


def status(worker, now=None, screen_tail=""):
    """(state, why)，state 用 agent_status 既有的名字。events.jsonl 是 turn 的權威紀錄，
    畫面只在它不存在時補位。永不拋例外。"""
    try:
        return _status(worker, now or time.time(), screen_tail or "")
    except Exception as e:
        return "unknown", f"grok exc:{e}"


# ── 用量 ──────────────────────────────────────────────────────────────────

def usage(worker):
    """這個 session 的累計用量（usage.json）與目前 context（signals.json）。沒有 usage.json 回 None。"""
    try:
        d = session_dir(worker)
        sess = (_load_json(os.path.join(d, "usage.json")) or {}).get("session") if d else None
        if not isinstance(sess, dict) or "totalTokens" not in sess:
            return None
        sig = _load_json(os.path.join(d, "signals.json")) or {}
        return {"tokens": int(sess["totalTokens"] or 0),
                "turns": int(sess.get("turnCount") or 0),
                "context_used": sig.get("contextTokensUsed"),
                "context_window": sig.get("contextWindowTokens")}
    except Exception:
        return None


def _k(n):
    return f"{round(int(n) / 1000)}K"


def usage_text(worker):
    """/usage 在 grok 分頁補充的一行，例如 `Grok Build 本 session：context 24K/256K · 72,986 tokens · 2 回合`。沒有資料回 ""。"""
    try:
        u = usage(worker)
        if not u:
            return ""
        parts = []
        if u["context_used"] and u["context_window"]:
            parts.append(f"context {_k(u['context_used'])}/{_k(u['context_window'])}")
        parts.append(f"{u['tokens']:,} tokens")
        parts.append(f"{u['turns']} 回合")
        return "Grok Build 本 session：" + " · ".join(parts)
    except Exception:
        return ""
