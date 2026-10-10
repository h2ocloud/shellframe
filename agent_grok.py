"""
agent_grok — Grok Build（xAI 的 `grok` TUI）的狀態、模型、對話與用量讀取器。

grok 沒有 Claude／Codex 那種「檔名就是 session id」的 transcript，也沒有 hook 可以回報。
每個對話放在 ~/.grok/sessions/<cwd 編碼>/<session-uuid>/ 底下：
  events.jsonl               turn／tool／permission 的時間點——燈號的依據，不靠畫面猜
  chat_history.jsonl         對話本文——上滑歷史與手機的 chat 視圖
  summary.json               current_model_id、reasoning_effort（/model 切換會寫回）
  usage.json、signals.json   這個 session 的 token 與 context 用量
帳號的週配額不在 session 檔裡。grok 的 /usage 視窗打的是
cli-chat-proxy 的 /v1/billing?format=credits：creditUsagePercent 是已用百分比，
currentPeriod 是週期起迄（實測為 USAGE_PERIOD_TYPE_WEEKLY，沒有 5 小時窗口）。
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
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

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
# lsof 很慢，狀態每 0.5 秒問一次。找到的目錄快取 30 秒：/new 之後最多慢 30 秒才跟到新的，
# 與 codex 的 _resolve_cached 同一個理由；沒找到只快取 5 秒，免得新分頁等太久。
_DIR_TTL_HIT = 30.0
_DIR_TTL_MISS = 5.0
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


def _events_open_in(pid):
    """這個行程 lsof 看到、開著的 events.jsonl（限定在 GROK_SESSIONS 底下）。

    不用 agent_status._lsof_open_paths：它對整棵樹每個 pid 都跑一次 lsof，而且從不提早停。
    grok 的樹裡還有 MCP 伺服器（node／bun…），每次未命中都要多跑好幾次 lsof。
    """
    import agent_status as A
    try:
        r = subprocess.run(["lsof", "-p", str(pid)], capture_output=True, timeout=3, text=True)
    except Exception:
        return []
    if r.returncode != 0:
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split()
        path = parts[-1] if parts else ""
        if path.endswith("/events.jsonl") and A._under(path, GROK_SESSIONS):
            out.append(path)
    return out


def _live_dir(tmux_name):
    """pane 行程樹裡開著 events.jsonl 的 session 目錄（新的優先）。

    樹的順序是根在前。grok 本體就是 pane 的根行程，它握著自己開過的每一份 events.jsonl
    （包括 /new 之前的那份），所以命中就停，不必再去掃 MCP 子行程。
    """
    import agent_status as A
    pane = A._tmux_pane_pid(tmux_name)
    if not pane:
        return None
    for pid in A._pid_tree(pane):
        paths = _events_open_in(pid)
        if paths:
            return max({os.path.dirname(p) for p in paths}, key=_recency)
    return None


def session_dir(worker):
    """這個分頁的 grok session 目錄；認不出來回 None。永不拋例外。"""
    try:
        tmux = worker.get("tmux_name") or ""
        now = time.time()
        hit = _DIR_CACHE.get(tmux) if tmux else None
        if hit and now - hit[0] < (_DIR_TTL_HIT if hit[1] else _DIR_TTL_MISS):
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
        # 實測（grok 1.0.50，預設權限模式，沒有人按任何鍵）：permission_requested 之後
        # 畫面沒有對話框，只有 spinner 與忙碌 footer，22.4 秒後自動 allow（另一次 3.6 秒）。
        # 所以待決的請求只有在畫面**沒有**忙碌訊號時才算等人；忙碌時是 grok 自己在審。
        # 不要因為看到 permission_requested 就改回 decision。
        if busy:
            return "working", "grok permission auto-review"
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


# 帳號週配額。grok 的 /usage 視窗打的就是這支，不是 session 檔推得出來的。
# 實測（grok 1.0.50）：GET 回 {"config": {creditUsagePercent, currentPeriod, productUsage}}。
# creditUsagePercent 是「已用」百分比；currentPeriod 實測為一週，沒有 5 小時窗口。
BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
AUTH_FILE = os.path.expanduser("~/.grok/auth.json")
_QUOTA_OK_TTL = 60
_QUOTA_RETRY_MIN = 45
_QUOTA_TIMEOUT = 12
_QUOTA_LOCK = threading.Lock()
_quota_cache = {"data": None, "ts": 0, "last_try": 0, "error": None}
_PRODUCT_NAMES = {
    "GrokBuild": "Grok Build",
    "GrokAppBuilder": "App Builder",
    "GrokChat": "Chat",
}


def _reset_quota_cache():
    _quota_cache.update(data=None, ts=0, last_try=0, error=None)


def _credentials(blob):
    """auth.json 裡那份登入物件。只取形狀，呼叫端自己決定要不要讀 key。"""
    if not isinstance(blob, dict):
        return None
    named = blob.get("https://accounts.x.ai/sign-in")
    if isinstance(named, dict) and isinstance(named.get("key"), str) and named.get("key"):
        return named
    for value in blob.values():
        if isinstance(value, dict) and isinstance(value.get("key"), str) and value.get("key"):
            return value
    if isinstance(blob.get("key"), str) and blob.get("key"):
        return blob
    return None


def _load_credentials():
    try:
        with open(AUTH_FILE, encoding="utf-8") as f:
            return _credentials(json.load(f))
    except Exception:
        return None


def account_label():
    """登入帳號的 email。權杖留在檔案裡，不進任何回傳字串。沒登入回 ""。"""
    cred = _load_credentials()
    email = (cred or {}).get("email")
    return email.strip() if isinstance(email, str) else ""


def _epoch(iso):
    if not iso or not isinstance(iso, str):
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def _fmt_reset(epoch):
    if not epoch:
        return "?"
    try:
        return datetime.fromtimestamp(epoch, timezone.utc).astimezone().strftime("%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return "?"


def _pct(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        return int(round(float(value)))
    return None


def _quota_from_payload(payload):
    """把 billing JSON 收成 usage_probe 的原始形狀。沒有百分比就不編一個。"""
    if not isinstance(payload, dict):
        return {"_error": "no_data", "_error_message": "帳單回應沒有用量百分比"}
    cfg = payload.get("config") if isinstance(payload.get("config"), dict) else payload
    pct = _pct(cfg.get("creditUsagePercent"))
    if pct is None:
        return {"_error": "no_data", "_error_message": "帳單回應沒有用量百分比"}
    period = cfg.get("currentPeriod") if isinstance(cfg.get("currentPeriod"), dict) else {}
    start = _epoch(period.get("start") or cfg.get("billingPeriodStart"))
    end = _epoch(period.get("end") or cfg.get("billingPeriodEnd"))
    minutes = int(round((end - start) / 60)) if start and end and end > start else None
    kind = str(period.get("type") or "")
    key = "5hr" if ("HOUR" in kind or (minutes is not None and minutes <= 12 * 60)) else "week"
    reset = _fmt_reset(end)
    out = {key: (pct, reset)}
    if end and minutes:
        out["_reset_epoch"] = {key: int(end)}
        out["_window_minutes"] = {key: minutes}
    groups = []
    for item in cfg.get("productUsage") or []:
        if not isinstance(item, dict):
            continue
        used = _pct(item.get("usagePercent"))
        if used is None:
            continue
        product = item.get("product") or ""
        name = _PRODUCT_NAMES.get(product, product)
        if not name:
            continue
        groups.append({
            "name": name, "used": used, "reset": reset,
            "epoch": int(end) if end else None,
            "window": "weekly" if key == "week" else "5h", "key": key,
        })
    if groups:
        out["_groups"] = groups
    return out


def _fetch_billing(token):
    """GET billing。回 (status, body)。連線失敗回 (0, b'')。不把權杖放進例外文字。"""
    req = urllib.request.Request(BILLING_URL, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "x-grok-client-identifier": "grok-shell",
        "User-Agent": "grok-shell",
    })
    try:
        with urllib.request.urlopen(req, timeout=_QUOTA_TIMEOUT) as resp:
            return resp.status, resp.read(65536)
    except urllib.error.HTTPError as exc:
        try:
            exc.read(2048)
        except Exception:
            pass
        return exc.code, b""
    except Exception:
        return 0, b""


def _quota_live():
    cred = _load_credentials()
    token = (cred or {}).get("key") if cred else None
    if not isinstance(token, str) or not token:
        return {"_error": "auth_required",
                "_error_message": "尚未登入 Grok Build，請執行 grok login"}
    status, body = _fetch_billing(token)
    if status == 200:
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return {"_error": "probe_failed", "_error_message": "週配額回應無法解析"}
        return _quota_from_payload(payload)
    if status in (401, 403):
        return {"_error": "auth_required",
                "_error_message": "登入已過期，請執行 grok login"}
    if status == 429:
        return {"_error": "rate_limited", "_error_message": "週配額查詢太頻繁，稍後再試"}
    if status == 0:
        return {"_error": "probe_failed", "_error_message": "週配額查詢失敗（連不上）"}
    return {"_error": "probe_failed", "_error_message": f"週配額查詢失敗（HTTP {status}）"}


def quota():
    """帳號週配額，形狀給 usage_probe。快取 60 秒；失敗時沿用上次成功的讀數並標 stale。永不拋。"""
    now = time.time()
    with _QUOTA_LOCK:
        cached = _quota_cache
        if cached["data"] and now - cached["ts"] < _QUOTA_OK_TTL:
            return dict(cached["data"])
        if now - cached["last_try"] < _QUOTA_RETRY_MIN:
            if cached["data"]:
                return {**cached["data"], "_stale": True}
            if cached.get("error"):
                return dict(cached["error"])
            return {"_error": "rate_limited", "_error_message": "剛查過，稍後再試"}
        cached["last_try"] = now
        try:
            data = _quota_live()
        except Exception:
            data = {"_error": "probe_failed", "_error_message": "週配額查詢失敗"}
        if data and data.get("week") and not data.get("_error"):
            cached["data"] = {k: v for k, v in data.items() if k != "_stale"}
            cached["ts"] = now
            cached["error"] = None
            return dict(cached["data"])
        # 5hr-only 也是一次成功讀數（目前實測沒有，留著以免窗口改成短週期時被當成失敗）。
        if data and data.get("5hr") and not data.get("_error"):
            cached["data"] = {k: v for k, v in data.items() if k != "_stale"}
            cached["ts"] = now
            cached["error"] = None
            return dict(cached["data"])
        cached["error"] = data
        if cached["data"]:
            stale = {**cached["data"], "_stale": True}
            if data and data.get("_error_message"):
                stale["_error_message"] = data["_error_message"]
            return stale
        return data or {"_error": "probe_failed", "_error_message": "週配額查詢失敗"}


def _pace_clause(data):
    """`週配額已用 14%｜重置 10-12 08:35｜配速應 71%（落後 57%）`。沒有重置時刻就不編配速。"""
    week = (data or {}).get("week")
    if not week:
        five = (data or {}).get("5hr")
        if not five:
            return ""
        return f"短週期已用 {five[0]}%｜重置 {five[1]}"
    pct, reset = week
    epoch = ((data.get("_reset_epoch") or {}).get("week"))
    minutes = ((data.get("_window_minutes") or {}).get("week"))
    base = f"週配額已用 {pct}%｜重置 {reset}"
    if not epoch or not minutes:
        return base
    total = minutes * 60
    remaining = epoch - time.time()
    if total <= 0 or remaining <= 0 or remaining > total:
        return base
    target = int(round((total - remaining) / total * 100))
    diff = int(round(pct - target))
    if diff <= -15:
        gap = f"落後 {abs(diff)}%"
    elif diff >= 10:
        gap = f"超前 {diff}%"
    elif diff == 0:
        gap = "剛好在配速線上"
    else:
        gap = f"接近配速（差 {diff:+d}%）"
    return f"{base}｜配速應 {target}%（{gap}）"


def usage_report(worker):
    """grok 分頁 /usage 的整段文字：週配額與配速，再加上這個 session 的 token。"""
    lines = ["AI 水位 Grok Build"]
    try:
        reading = quota()
    except Exception:
        reading = {"_error": "probe_failed", "_error_message": "週配額查詢失敗"}
    if reading and (reading.get("week") or reading.get("5hr")):
        who = account_label()
        if who:
            lines.append(f"帳號 {who}")
        if reading.get("_stale"):
            lines.append("⚠ 本次更新失敗，顯示上次資料")
        clause = _pace_clause(reading)
        if clause:
            lines.append(clause)
        for group in reading.get("_groups") or []:
            if group.get("name") and group.get("used") is not None:
                lines.append(f"{group['name']} {group['used']}%")
    else:
        lines.append((reading or {}).get("_error_message") or "查不到週配額。")
    extra = usage_text(worker)
    if extra:
        lines.append(extra)
    return "\n".join(lines)
