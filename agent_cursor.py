"""
agent_cursor — Cursor Agent（`cursor-agent`）的狀態、模型、對話與用量讀取器。

`agent` 不是這支 CLI。ShellFrame 只認 `cursor-agent`（Windows 是
`cursor-agent.cmd`）與 `sf-cursor` 開頭的啟動器。

對話本體在 Cursor 的本機目錄，不靠網路：
  ~/.cursor/chats/<md5(絕對 cwd)>/<uuid>/store.db
      meta 表 key「0」的值是 UTF-8 JSON 的 hex。只讀 agentId、lastUsedModel、
      createdAt、mode、name。不讀 blobEncryptionKey，也不把那份值寫進 log。
  ~/.cursor/projects/<cwd slug>/agent-transcripts/<id>/<id>.jsonl
      一筆是 {role, message:{content:[{type:text|tool_use}]}}。也接受同名 .txt。

新分頁用隱藏旗標 --new-session-id 對到那份目錄。續接是 --resume <chatId>。
帳號週配額 CLI 沒有本地檔，quota() 說明原因，不估算百分比。
第三方模型（Claude／GPT／Gemini）走預付 credit，billing_class 標成 paid。

純標準庫。需要 agent_status 的 helper 時在函式內 import，避免循環。
"""

import hashlib
import json
import os
import re
import shlex
import sqlite3
import subprocess
import time
import uuid

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_UUID_RE = re.compile(_UUID, re.I)
_CMD_ID_RE = re.compile(
    r"(?:^|\s)(?:--resume|--new-session-id)(?:=|\s+)(" + _UUID + r")(?=\s|$)", re.I)
_EFFORT_RE = re.compile(r"(?:^|,)\s*effort\s*=\s*([^,\]]+)", re.I)
_BUSY_RE = re.compile(r"ctrl\+c to stop", re.I)
_PAID_RE = re.compile(
    r"(claude|gpt-|gemini|sonnet|opus|(?:^|[^a-z])o[134](?:[^0-9]|$))", re.I)
_SUB_RE = re.compile(r"^(?:composer|grok)(?:[.\-_]|$)", re.I)

SUBCOMMANDS = frozenset((
    "persist install-shell-integration uninstall-shell-integration login logout "
    "mcp plugin worker status whoami models team bedrock about update "
    "create-chat generate-rule rule ls resume help").split())
_NO_PIN_FLAGS = frozenset((
    "--resume", "--continue", "--new-session-id", "-p", "--print", "--list-models"))
_STRIP_FLAGS = frozenset(("--resume", "--continue", "--new-session-id"))

DONE_WINDOW_S = 15
_DIR_TTL_HIT = 30.0
_DIR_TTL_MISS = 5.0
_DIR_CACHE = {}
# 測試把這對設成 (config_home, data_home)，正式跑時維持 None，改讀環境變數。
_ROOT_OVERRIDE = None

_META_KEYS = ("agentId", "lastUsedModel", "createdAt", "mode", "name")


def _tokens(cmd):
    """Windows 路徑的反斜線不是跳脫字元。先換成斜線再拆，pin 才不會把路徑吃掉。"""
    text = (cmd or "").replace("\\", "/")
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _basename(token):
    base = (token or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    for ext in (".exe", ".cmd", ".bat"):
        if base.endswith(ext):
            base = base[:-len(ext)]
    return base


def is_cursor_cmd(cmd: str) -> bool:
    """第一個 token 是 cursor-agent，或 sf-cursor 開頭的啟動器。

    `agent` 與 IDE 的 `cursor` 都不是。
    """
    toks = _tokens(cmd)
    if not toks:
        return False
    base = _basename(toks[0])
    return base == "cursor-agent" or base.startswith("sf-cursor")


def cmd_session_uuid(cmd: str):
    """--resume 或 --new-session-id 指定的 uuid（小寫）。沒有回 None。"""
    m = _CMD_ID_RE.search(cmd or "")
    return m.group(1).lower() if m else None


def pin_session_id(cmd: str):
    """新開的分頁加上 --new-session-id，之後才對得到 store.db。

    子指令、續接、列印模式原樣不動。回傳 (cmd, uuid 或 None)。
    """
    toks = _tokens(cmd)
    if not is_cursor_cmd(cmd) or not toks:
        return cmd, None
    if len(toks) > 1 and toks[1] in SUBCOMMANDS:
        return cmd, None
    if any(t.split("=", 1)[0] in _NO_PIN_FLAGS for t in toks[1:]):
        return cmd, None
    sid = str(uuid.uuid4())
    return shlex.join([toks[0], "--new-session-id", sid, *toks[1:]]), sid


def cmd_with_resume(cmd: str, sid: str) -> str:
    """改成 `cursor-agent --resume <sid>`。舊的 resume／continue／new-session-id 拿掉。"""
    toks = _tokens(cmd)
    if not sid or not toks or not is_cursor_cmd(cmd):
        return cmd
    out = [toks[0], "--resume", sid]
    i = 1
    while i < len(toks):
        t = toks[i]
        name = t.split("=", 1)[0]
        if name in _STRIP_FLAGS:
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            takes = "=" not in t and nxt and not nxt.startswith("-")
            i += 2 if takes else 1
        else:
            out.append(t)
            i += 1
    return shlex.join(out)


def roots():
    """(config home, data home)。chats 在 config，projects 在 data。"""
    if _ROOT_OVERRIDE:
        return _ROOT_OVERRIDE
    cfg = (os.environ.get("CURSOR_CONFIG_DIR") or "").strip()
    if not cfg:
        xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
        cfg = os.path.join(xdg, "cursor") if xdg else os.path.expanduser("~/.cursor")
    data = (os.environ.get("CURSOR_DATA_DIR") or "").strip() or os.path.expanduser("~/.cursor")
    return cfg, data


def _slug(path):
    text = re.sub(r"[^A-Za-z0-9]+", "-", os.path.abspath(path or "")).strip("-")
    return text


def _cwd_hash(cwd):
    return hashlib.md5(os.path.abspath(cwd or "").encode()).hexdigest()


def _safe_meta(obj):
    """只留燈號與徽章要用的欄位。加密金鑰即使在檔裡也不出這個函式。"""
    if not isinstance(obj, dict):
        return None
    return {k: obj.get(k) for k in _META_KEYS}


def _read_store(path):
    try:
        uri = "file:" + path.replace("?", "%3F") + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=1)
        try:
            row = con.execute("SELECT value FROM meta WHERE key=?", ("0",)).fetchone()
        finally:
            con.close()
    except Exception:
        return None
    if not row or not isinstance(row[0], str):
        return None
    raw = row[0].strip()
    try:
        blob = bytes.fromhex(raw)
        text = blob.decode("utf-8")
    except ValueError:
        text = raw
    try:
        return _safe_meta(json.loads(text))
    except ValueError:
        return None


def _store_path(cwd, sid):
    cfg, _data = roots()
    return os.path.join(cfg, "chats", _cwd_hash(cwd), sid, "store.db")


def _find_store(sid):
    sid = (sid or "").strip().lower()
    if not _UUID_RE.fullmatch(sid):
        return None
    cfg, _data = roots()
    root = os.path.join(cfg, "chats")
    if not os.path.isdir(root):
        return None
    try:
        names = os.listdir(root)
    except OSError:
        return None
    for name in names:
        path = os.path.join(root, name, sid, "store.db")
        if os.path.isfile(path):
            return path
    return None


def _transcript_file(cwd, sid):
    _cfg, data = roots()
    base = os.path.join(data, "projects", _slug(cwd), "agent-transcripts", sid, sid)
    for ext in (".jsonl", ".txt"):
        path = base + ext
        if os.path.isfile(path):
            return path
    return None


def _live_store(tmux_name):
    """pane 正開著的 store.db。lsof 很慢，呼叫端要自己快取。"""
    import agent_status as A
    pane = A._tmux_pane_pid(tmux_name)
    if not pane:
        return None
    cfg, _data = roots()
    chats = os.path.join(cfg, "chats")
    for pid in A._pid_tree(pane):
        try:
            r = subprocess.run(["lsof", "-p", str(pid)], capture_output=True,
                               timeout=3, text=True)
        except Exception:
            continue
        if r.returncode != 0:
            continue
        for line in r.stdout.splitlines():
            parts = line.split()
            path = parts[-1] if parts else ""
            if path.endswith("/store.db") and A._under(path, chats):
                return path
    return None


def session_dir(worker):
    """這個分頁的 chat 目錄（store.db 的上一層）。認不出來回 None。"""
    try:
        tmux = worker.get("tmux_name") or ""
        now = time.time()
        hit = _DIR_CACHE.get(tmux) if tmux else None
        if hit and now - hit[0] < (_DIR_TTL_HIT if hit[1] else _DIR_TTL_MISS):
            return hit[1]
        path = _live_store(tmux) if tmux else None
        if not path:
            for sid in (worker.get("cursor_session_id"), worker.get("session_id"),
                        cmd_session_uuid(worker.get("cmd") or "")):
                path = _find_store(sid)
                if path:
                    break
            if not path:
                sid = cmd_session_uuid(worker.get("cmd") or "")
                cwd = worker.get("cwd") or ""
                if sid and cwd:
                    guess = _store_path(cwd, sid)
                    if os.path.isfile(guess):
                        path = guess
        d = os.path.dirname(path) if path else None
        if tmux:
            _DIR_CACHE[tmux] = (now, d)
        return d
    except Exception:
        return None


def session_id(worker):
    d = session_dir(worker)
    if d:
        return os.path.basename(d)
    return cmd_session_uuid((worker or {}).get("cmd") or "")


def session_exists(sid):
    if _find_store(sid):
        return True
    _cfg, data = roots()
    root = os.path.join(data, "projects")
    sid = (sid or "").strip().lower()
    if not _UUID_RE.fullmatch(sid) or not os.path.isdir(root):
        return False
    try:
        for name in os.listdir(root):
            base = os.path.join(root, name, "agent-transcripts", sid, sid)
            if os.path.isfile(base + ".jsonl") or os.path.isfile(base + ".txt"):
                return True
    except OSError:
        return False
    return False


def transcript_path(worker):
    sid = session_id(worker)
    if not sid:
        return None
    d = session_dir(worker)
    cwd = worker.get("cwd") or ""
    if cwd:
        hit = _transcript_file(cwd, sid)
        if hit:
            return hit
    _cfg, data = roots()
    root = os.path.join(data, "projects")
    if not os.path.isdir(root):
        return None
    try:
        names = os.listdir(root)
    except OSError:
        return None
    for name in names:
        for ext in (".jsonl", ".txt"):
            path = os.path.join(root, name, "agent-transcripts", sid, sid + ext)
            if os.path.isfile(path):
                return path
    if d:
        return None
    return None


def _content_text(content):
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in (None, "text"):
            parts.append(str(block.get("text") or ""))
    return "\n".join(p for p in parts if p).strip()


def is_cursor_record(o) -> bool:
    """逐字稿的一筆。沒有 Claude 的 sessionId，避免被認成 Claude。"""
    if not isinstance(o, dict) or "sessionId" in o:
        return False
    if o.get("type") == "turn_ended":
        return True
    msg = o.get("message")
    return o.get("role") in ("user", "assistant", "tool") and isinstance(msg, dict)


def norm(o):
    """一筆逐字稿 → agent_status 事件。跳過時回 None。"""
    import agent_status as A
    if not isinstance(o, dict):
        return None
    if o.get("type") == "turn_ended":
        return {"kind": "turn_end", "ts": None}
    role = o.get("role")
    msg = o.get("message") if isinstance(o.get("message"), dict) else {}
    content = msg.get("content")
    if role == "user":
        text = _content_text(content)
        return {"kind": "user_msg", "ts": None, "text": text} if text else None
    if role == "assistant":
        evs = []
        text = _content_text(content)
        if text:
            evs.append({"kind": "assistant_text", "ts": None, "text": text})
        blocks = content if isinstance(content, list) else []
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = str(block.get("name") or "")
            args = block.get("input") if isinstance(block.get("input"), dict) else {}
            evs.append({"kind": "tool_call", "ts": None, "tool": name,
                        "target": A._target(name, args)})
        if not evs:
            return None
        return evs[0] if len(evs) == 1 else evs
    if role == "tool":
        return {"kind": "tool_result", "ts": None}
    return None


def split_model(spec):
    """`name` 或 `name[context=1m,effort=high]` → (name, effort)。"""
    text = (spec or "").strip()
    if not text:
        return "", ""
    name, _, bracket = text.partition("[")
    effort = ""
    if bracket:
        m = _EFFORT_RE.search(bracket)
        if m:
            effort = m.group(1).strip()
    return name.strip(), effort


def billing_class(model: str) -> str:
    """subscription／paid／auto／unknown。paid 是預付 credit，不是訂閱額度。"""
    name, _effort = split_model(model)
    low = name.lower()
    if not low:
        return "unknown"
    if low == "auto":
        return "auto"
    if _PAID_RE.search(name):
        return "paid"
    if _SUB_RE.match(name):
        return "subscription"
    return "unknown"


def _cmd_model(cmd):
    toks = _tokens(cmd)
    for i, t in enumerate(toks):
        key, eq, val = t.partition("=")
        if not eq:
            val = toks[i + 1] if i + 1 < len(toks) else ""
        if key == "--model" and val and not val.startswith("-"):
            return val
    return ""


def model_info(worker):
    """{name, effort, provider, billing}。沒有模型回 None。"""
    try:
        d = session_dir(worker)
        meta = _read_store(os.path.join(d, "store.db")) if d else None
        raw = ((meta or {}).get("lastUsedModel") or "") or _cmd_model(worker.get("cmd") or "")
        name, effort = split_model(str(raw or ""))
        if not name:
            return None
        return {"name": name, "effort": effort, "provider": "cursor",
                "billing": billing_class(name)}
    except Exception:
        return None


def _last_json_event(path):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - 64 * 1024))
            lines = f.read().split(b"\n")
    except OSError:
        return None
    for line in reversed(lines):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def status(worker, now=None, screen_tail=""):
    """(state, why)。忙碌只認生成中的 `ctrl+c to stop`，不認離開確認。"""
    try:
        now = now or time.time()
        screen = screen_tail or ""
        if _BUSY_RE.search(screen):
            return "working", "cursor screen: busy footer"
        path = transcript_path(worker)
        last = _last_json_event(path) if path else None
        if isinstance(last, dict) and last.get("type") == "turn_ended":
            try:
                age = max(0.0, now - os.path.getmtime(path))
            except OSError:
                age = DONE_WINDOW_S + 1
            if age <= DONE_WINDOW_S:
                return "done", "cursor turn ended"
            return "idle", "cursor settled"
        if screen.strip():
            return "idle", "cursor screen: idle"
        return "unknown", "cursor no screen"
    except Exception as e:
        return "unknown", f"cursor exc:{e}"


def status_view(monitor, sid, worker, now, screen_tail, model):
    """agent_status 用的那包 dict。去抖沿用 monitor。"""
    import agent_status as A
    state, why = status(worker, now, screen_tail)
    state = monitor._debounce(sid, state, now)
    _prev, since = monitor._last.get(sid, (state, now))
    tp = transcript_path(worker)
    return {"state": state, "dot": A.DOT.get(state, ""), "activity": {},
            "summary": state, "action": "", "narration": "", "task": "",
            "elapsed": int(now - since), "why": why,
            "transcript": os.path.basename(tp) if tp else None,
            "loop": None, "model": model}


def quota(env=None):
    """沒有本地配額檔。不估算已用百分比。"""
    return {"_error": "no_data",
            "_error_message": "cursor-agent 沒有本地配額檔，不估算已用百分比"}


def account_label(data=None, env=None):
    return ""


def install_command(windows: bool) -> str:
    """安裝分頁在 Windows 跑 PowerShell（見 api_accounts.install_provider），不能用 bash。"""
    if windows:
        return "irm 'https://cursor.com/install?win32=true' | iex"
    return "curl -fsS https://cursor.com/install | bash"


def _billing_label(kind):
    return {"subscription": "訂閱", "paid": "付費",
            "auto": "auto，可能選到付費模型", "unknown": "未分類"}.get(kind, "未分類")


def usage_report(worker):
    """/usage 的文字。有模型就標訂閱或付費；沒有配額百分比。"""
    lines = ["AI 水位 Cursor Agent"]
    try:
        reading = quota()
    except Exception:
        reading = {"_error_message": "用量查詢失敗"}
    lines.append((reading or {}).get("_error_message") or "查不到帳號水位。")
    info = None
    try:
        info = model_info(worker)
    except Exception:
        info = None
    if info:
        extra = f" effort {info['effort']}" if info.get("effort") else ""
        lines.append(f"模型 {info['name']}（{_billing_label(info.get('billing'))}）{extra}")
    return "\n".join(lines)
