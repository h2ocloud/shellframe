"""
agent_status — 自動偵測每個 tab 的 agent 狀態（不靠 agent 自報 [[SF:...]]）。

P1：獨立、可測試的 server 端模組。main.py 在 P2 才接線。
設計原則：
  - 不 import main.py（解耦，純資料進出）。
  - 全部對外 API 包 try/except，**偵測失敗回 None / 'unknown'，永遠不可拋例外影響終端**。
  - transcript 只 seek 檔尾增量讀，500ms 等級成本。

對外 API：
  resolve_transcript(worker)        tab → transcript 路徑（codex=lsof, claude=session-id/newest-mtime）
  StatusTracker().status_for(...)   算某 tab 的 {state, dot, activity, task}

worker 是個 dict（main.py 用 Session 屬性填）：
  {sid, cmd, cwd, tmux_name, session_id(optional)}

狀態機與解析邏輯同 docs/poc/agent_state_detector.py（已對真實 log 驗證）。
"""
from __future__ import annotations

import json
import os
import glob
import sqlite3
import time
import subprocess
import re

import usage_probe

# ── 狀態機門檻（秒）──
WORKING_FRESH_S = 8
STUCK_IDLE_S = 180        # turn 未結束又超過 180s 全無事件(無 error/spinner/pending tool) → 真的卡住
DONE_QUIET_S = 3

DOT = {"working": "sig-working", "decision": "sig-decision",
       "done": "sig-done", "stuck": "sig-stuck", "idle": "", "unknown": ""}

VERB = {
    "Read": "Reading", "Edit": "Editing", "Write": "Writing", "NotebookEdit": "Editing",
    "Bash": "Running", "Grep": "Searching", "Glob": "Searching", "Task": "Delegating",
    "WebFetch": "Fetching", "WebSearch": "Searching", "AskUserQuestion": "Asking",
    "TodoWrite": "Planning",
    "exec_command": "Running", "shell": "Running", "apply_patch": "Editing",
    "read_file": "Reading", "image_generation_call": "Generating image", "mcp": "Calling",
}

CLAUDE_PROJECTS = os.path.expanduser("~/.claude/projects")
CODEX_SESSIONS = os.path.expanduser("~/.codex/sessions")

# Antigravity CLI (agy) keeps no JSONL transcript. Each conversation is its own
# SQLite file, and the live process holds an open lock naming that conversation
# — which is how a tab is matched to it (same lsof trick codex needs).
AGY_HOME = os.path.expanduser("~/.gemini/antigravity-cli")
AGY_PRESENCE_DIR = os.path.join(AGY_HOME, "presence")
AGY_CONVERSATIONS_DIR = os.path.join(AGY_HOME, "conversations")
AGY_LOG_DIR = os.path.join(AGY_HOME, "log")
# steps.status values, established by sampling a live run: a step sits at 8
# while the agent works on it and settles to 3 when it is finished.
AGY_STEP_RUNNING = 8
AGY_STEP_DONE = 3
# How long a finished turn keeps reporting "done" (must exceed DONE_QUIET_S so
# the state survives debouncing and the completion is actually noticed).
AGY_DONE_WINDOW_S = 15


# ────────────────────────── tab → transcript 對應 ──────────────────────────

def _worker_kind(cmd: str) -> str:
    """Provider key for a launch command, or 'other'.

    The loose substring match for claude/codex is kept deliberately: wrapped
    commands (`bash -lc "codex resume"`) rely on it. Everything else comes from
    the shared provider registry, so a newly supported CLI is recognised here
    without editing this function (see docs/adding-a-provider.md).
    """
    c = (cmd or "").lower()
    if "codex" in c:
        return "codex"
    if "claude" in c:
        return "claude"
    try:
        return usage_probe.detect_ai(cmd) or "other"
    except Exception:
        return "other"


def worker_kind(cmd: str) -> str:
    """Public alias for `_worker_kind` — the glasses bridge needs the same
    claude/codex/other classification main.py uses, and duplicating the
    wrapped-command handling in a second place is how the two drift apart."""
    return _worker_kind(cmd)


def _cwd_slug(cwd: str) -> str:
    """Claude Code 的 project 目錄 slug：把 / 與 . 換成 -。"""
    p = os.path.realpath(os.path.expanduser(cwd or "~"))
    return p.replace("/", "-").replace(".", "-")


def _tmux_pane_pid(tmux_name: str):
    if not tmux_name:
        return None
    try:
        r = subprocess.run(["tmux", "list-panes", "-t", tmux_name,
                            "-F", "#{pane_pid}"], capture_output=True,
                           timeout=2, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.strip().splitlines()[0])
    except Exception:
        pass
    return None


def _pid_tree(root_pid: int, depth=2):
    """root + 子孫 pid（限定深度，避免無限）。"""
    pids = [root_pid]
    frontier = [root_pid]
    for _ in range(depth):
        nxt = []
        for p in frontier:
            try:
                r = subprocess.run(["pgrep", "-P", str(p)], capture_output=True,
                                   timeout=2, text=True)
                kids = [int(x) for x in r.stdout.split()] if r.returncode == 0 else []
            except Exception:
                kids = []
            nxt.extend(kids)
            pids.extend(kids)
        frontier = nxt
    return pids


def _lsof_open_jsonl(pids, name_contains: str):
    """在 pid 樹中找開啟的、路徑含 name_contains 的 .jsonl（codex 用）。"""
    for p in pids:
        try:
            r = subprocess.run(["lsof", "-p", str(p)], capture_output=True,
                               timeout=3, text=True)
        except Exception:
            continue
        if r.returncode != 0:
            continue
        for line in r.stdout.splitlines():
            path = line.split()[-1] if line.split() else ""
            if path.endswith(".jsonl") and name_contains in path:
                return path
    return None


def _lsof_open_paths(pids, contains: str):
    """Paths open anywhere in the pid tree whose name contains `contains`."""
    hits = []
    for p in pids:
        try:
            r = subprocess.run(["lsof", "-p", str(p)], capture_output=True,
                               timeout=3, text=True)
        except Exception:
            continue
        if r.returncode != 0:
            continue
        for line in r.stdout.splitlines():
            parts = line.split()
            path = parts[-1] if parts else ""
            if contains in path:
                hits.append(path)
    return hits


def _agy_conversation_db(worker: dict):
    """tab → its agy conversation SQLite, or None.

    The running process holds `presence/<conversation-id>.lock` open, so the id
    comes straight from the pid tree rather than from guessing by mtime — two
    agy tabs in the same directory would otherwise collide.
    """
    pane = _tmux_pane_pid(worker.get("tmux_name"))
    if not pane:
        return None
    for path in _lsof_open_paths(_pid_tree(pane), "/antigravity-cli/presence/"):
        conversation_id = os.path.basename(path).removesuffix(".lock")
        if not conversation_id:
            continue
        db = os.path.join(AGY_CONVERSATIONS_DIR, f"{conversation_id}.db")
        if os.path.exists(db):
            return db
    return None


def _agy_log_path(worker: dict):
    """The live process's own log file (it is that process's stdout/stderr)."""
    pane = _tmux_pane_pid(worker.get("tmux_name"))
    if not pane:
        return None
    for path in _lsof_open_paths(_pid_tree(pane), "/antigravity-cli/log/cli-"):
        if path.endswith(".log") and os.path.exists(path):
            return path
    return None


def _agy_last_step(db_path: str):
    """{'idx', 'status', 'age'} for the newest step, or None.

    Read-only, and the freshness clock comes from the -wal file: the database
    runs in WAL mode, so the main file's mtime stops moving while a turn writes.
    """
    if not db_path or not os.path.exists(db_path):
        return None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
        try:
            row = con.execute(
                "SELECT idx, status FROM steps ORDER BY idx DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    newest = 0.0
    for candidate in (db_path + "-wal", db_path):
        try:
            newest = max(newest, os.path.getmtime(candidate))
        except OSError:
            continue
    return {"idx": row[0], "status": row[1],
            "age": max(0.0, time.time() - newest) if newest else None}


_AGY_MODEL_RE = re.compile(r'selected model override to backend: label="([^"]+)"')


def _parse_agy_model(log_path: str):
    """Newest model label the process announced, e.g. 'Gemini 3.7 Flash (High)'.

    Reads only the tail: these logs grow steadily and the current selection is
    re-announced on every model switch.
    """
    try:
        with open(log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    matches = _AGY_MODEL_RE.findall(text)
    return matches[-1] if matches else None


def _split_agy_model_label(label: str):
    """'Gemini 3.7 Flash (High)' → ('Gemini 3.7 Flash', 'high')."""
    m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", label or "")
    if m:
        return m.group(1).strip(), m.group(2).strip().lower()
    return (label or "").strip(), ""


def _cmd_session_uuid(cmd: str):
    """cmd 的 --resume <uuid> / --session-id <uuid>（resume=同檔續寫，uuid
    即 transcript 檔名）。"""
    m = re.search(r"--(?:resume|session-id)[= ]([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})",
                  cmd or "")
    return m.group(1).lower() if m else None


def _claude_proc_start(pane_pid):
    """pane 樹裡「最新啟動的 claude process」的 start epoch，無則 None。
    同一 pane 退出重開 claude 時，pane 首 process 的 start 是舊的，拿它做
    nearest-birth 錨點會對到上一段 session 的 transcript。"""
    try:
        pids = _pid_tree(pane_pid)
        if not pids:
            return None
        r = subprocess.run(
            ["ps", "-o", "pid=,command=", "-p", ",".join(str(p) for p in pids)],
            capture_output=True, text=True, timeout=2)
        best = None
        for line in r.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) < 2:
                continue
            pid, cmd = parts
            head = cmd.split()[0].rsplit("/", 1)[-1]
            if head != "claude" and not re.search(r"(^|/)claude(\s|$)", cmd):
                continue
            st = _proc_start_epoch(pid)
            if st and (best is None or st > best):
                best = st
        return best
    except Exception:
        return None


def _proc_start_epoch(pid):
    """Process start time as epoch seconds (macOS `ps -o lstart`)."""
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                           capture_output=True, text=True, timeout=2)
        s = r.stdout.strip()
        if not s:
            return None
        from datetime import datetime
        return datetime.strptime(s, "%a %b %d %H:%M:%S %Y").timestamp()
    except Exception:
        return None


def _nearest_birth_jsonl(pattern: str, target_epoch: float, max_delta=900):
    """The transcript whose file birth time is closest to target_epoch (a tab's
    claude process start). Each claude session creates its .jsonl at launch, so
    matching process-start↔file-birth maps each tab to its OWN transcript even
    when many share the $HOME slug — unlike newest-mtime, which converges on the
    one currently-active file. Returns None if nothing is within max_delta."""
    best, best_d = None, max_delta
    for f in glob.glob(pattern):
        try:
            b = os.stat(f).st_birthtime
        except (OSError, AttributeError):
            continue
        d = abs(b - target_epoch)
        if d < best_d:
            best, best_d = f, d
    return best


def _newest_jsonl(pattern: str, within_s=3600):
    cutoff = time.time() - within_s
    best, best_m = None, 0.0
    for f in glob.glob(pattern):
        try:
            m = os.path.getmtime(f)
        except OSError:
            continue
        if m < cutoff:
            continue
        if m > best_m:
            best, best_m = f, m
    return best


def resolve_transcript(worker: dict):
    """tab → transcript 路徑。失敗回 None。
    worker: {cmd, cwd, tmux_name, session_id(optional)}
    """
    try:
        kind = _worker_kind(worker.get("cmd", ""))
        if kind == "agy":
            # Not a transcript: agy's state lives in a per-conversation SQLite.
            # Returned through the same slot so the resolve cache applies.
            return _agy_conversation_db(worker)
        if kind == "codex":
            # codex 持續持有 rollout fd → lsof 直接命中（最可靠）
            pane = _tmux_pane_pid(worker.get("tmux_name"))
            if pane:
                hit = _lsof_open_jsonl(_pid_tree(pane), "/.codex/sessions/")
                if hit:
                    return hit
            # fallback：整棵 sessions 樹取最新 mtime
            return _newest_jsonl(os.path.join(CODEX_SESSIONS, "*/*/*/rollout-*.jsonl"))
        if kind == "claude":
            # 優先序（愈前愈接近「即時真相」）：
            # (0) hook 回報的 transcript_path——sf_agent_hook 每個事件都帶，
            #     唯一能跟上 /clear 的 uuid 輪替（同 process 換檔，其餘來源
            #     全會釘在舊檔 → badge 顯示舊模型）。
            # (1) session_id（spawn 的 --session-id；hook 活著時會被更新成
            #     當前 uuid）。
            # (2) cmd 的 --resume/--session-id uuid——resume 是同檔續寫，
            #     birth 是最初建立日，nearest-birth 必錯；uuid 直接對。
            # (3) nearest-birth，錨定 pane 樹裡「最新啟動的 claude process」
            #     ——claude 在同一 pane 退出重開時，pane 首 process 的 start
            #     是舊的。The newest-mtime guess is deliberately NOT used:
            #     tabs sharing the $HOME slug would all resolve to whichever
            #     transcript was written last.
            hint = worker.get("transcript_hint")
            if hint and os.path.exists(hint):
                return hint
            slug = _cwd_slug(worker.get("cwd", "~"))
            sid = worker.get("session_id")
            if sid:
                p = os.path.join(CLAUDE_PROJECTS, slug, f"{sid}.jsonl")
                if os.path.exists(p):
                    return p
            cmd_sid = _cmd_session_uuid(worker.get("cmd", ""))
            if cmd_sid:
                p = os.path.join(CLAUDE_PROJECTS, slug, f"{cmd_sid}.jsonl")
                if os.path.exists(p):
                    return p
            pane = _tmux_pane_pid(worker.get("tmux_name"))
            if pane:
                start = _claude_proc_start(pane) or _proc_start_epoch(pane)
                if start:
                    return _nearest_birth_jsonl(
                        os.path.join(CLAUDE_PROJECTS, slug, "*.jsonl"), start)
            return None
    except Exception:
        return None
    return None


# ────────────────────────── 模型 / thinking effort 偵測 ──────────────────────────
# tab 目前跑什麼模型、thinking/reasoning effort 開多大。來源：
#   claude: transcript 最新 assistant 記錄的 message.model（per-session 準確，
#           /model 切換後下一則 assistant 就反映）；effort 讀全域
#           ~/.claude/settings.json 的 effortLevel（/model 選單本來就存全域）。
#   codex:  rollout 最新 turn_context 的 model/effort（per-session 準確）；
#           沒 rollout 時退 ~/.codex/config.toml。
# 全部走 stat/mtime 快取 —— status_for 每 ~500ms 呼叫，未變動時只有 stat 成本。

CLAUDE_SETTINGS_JSON = os.path.expanduser("~/.claude/settings.json")
CODEX_CONFIG_TOML = os.path.expanduser("~/.codex/config.toml")

_model_file_cache = {}  # path -> ((mtime, size), parsed_value)


def _cached_parse(path, parser):
    """parser(path) 的 mtime+size 快取。檔案不存在/解析失敗回 None（也快取，
    避免每 500ms 重試壞檔）。快取 key 含 parser 名——同一 path 換 parser
    不會拿到污染值（latent trap，生產路徑樹分離但不賭）。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    ck = (path, parser.__name__)
    key = (st.st_mtime, st.st_size)
    hit = _model_file_cache.get(ck)
    if hit and hit[0] == key:
        return hit[1]
    try:
        val = parser(path)
    except Exception:
        val = None
    _model_file_cache[ck] = (key, val)
    return val


def _tail_lines(path, tail_bytes=262144):
    """檔尾 raw lines（新→舊）。第一行可能被截斷，reversed 掃描時通常無妨。"""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - tail_bytes))
        data = f.read()
    return list(reversed(data.decode("utf-8", errors="replace").splitlines()))


_ALIAS_NAMES = {"opus", "sonnet", "haiku", "fable"}

def _pretty_model(mid: str) -> str:
    """模型 id → 顯示名。claude-fable-5 → Fable 5、claude-opus-4-8 → Opus 4.8、
    claude-haiku-4-5-20251001 → Haiku 4.5；bare alias（opus/sonnet/haiku/fable）
    → 首字大寫（無版號）；gpt-* 首段大寫；其餘原樣。
    任意位置的 [1m] 與 ANSI escape 先 strip 再判斷。"""
    if not mid:
        return ""
    # strip ANSI escape sequences
    mid = re.sub(r"\x1b\[[0-9;]*m", "", mid.strip())
    # strip [1m] anywhere in the string（含結尾、中間）
    mid = re.sub(r"\[1m\]", "", mid).strip()
    if not mid:
        return ""
    # bare alias（in-session /model 選的別名如 opus、sonnet…）
    if mid.lower() in _ALIAS_NAMES:
        return mid.capitalize()
    # full claude-* id
    m = re.match(r"^claude-([a-z]+)-(\d+)(?:-(\d))?(?:-|$)", mid)
    if m:
        name = m.group(1).capitalize()
        ver = m.group(2) + (f".{m.group(3)}" if m.group(3) else "")
        return f"{name} {ver}"
    if mid.lower().startswith("gpt"):
        return "GPT" + mid[3:]
    return mid


def _parse_claude_transcript_model(path):
    """transcript 最新 **main-chain**（isSidechain=False）assistant 的 message.model。
    略過 <synthetic> 錯誤佔位；略過 isSidechain=True（Task subagent 的模型）。"""
    scanned = 0
    for ln in _tail_lines(path):
        if '"model"' not in ln:
            continue
        scanned += 1
        if scanned > 400:
            break
        try:
            o = json.loads(ln)
        except Exception:
            continue
        # 只採 main-chain：type=assistant 且非 sidechain（False 或欄位不存在）
        if o.get("type") != "assistant":
            continue
        if o.get("isSidechain") is True:
            continue
        model = ((o.get("message") or {}).get("model") or "").strip()
        if model and not model.startswith("<"):
            return model
    return None


_EFFORT_MARK_RE = re.compile(r"(?:Set|Kept) effort level (?:to|as) ([a-z]+)", re.I)
_effort_scan_cache = {}  # path -> (scanned_bytes, level_or_None)


def _parse_claude_transcript_effort(path):
    """transcript 中最新的 /effort 痕跡（Set/Kept effort level to <level>）。

    /effort <level> 是 session-only、不寫全域 settings.json——badge 若只讀
    全域 effortLevel，每個分頁都顯示同一個值（2026-08-06：tab13 實際
    ultracode、badge 顯示 xhigh）。標記可能離檔尾很遠（早上設、之後累積
    十幾 MB 輸出），固定 tail 窗會漏——改增量掃描：首次全掃、之後只掃
    新增 bytes，結果沿用。assistant 行略過（對話內容貼到這段字串不算數）。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    scanned, level = _effort_scan_cache.get(path, (0, None))
    if size < scanned:              # 檔案被截斷/替換 → 重掃
        scanned, level = 0, None
    if size > scanned:
        try:
            with open(path, "rb") as f:
                f.seek(scanned)
                new = f.read(size - scanned).decode("utf-8", errors="replace")
        except OSError:
            return level
        for ln in reversed(new.splitlines()):
            if "effort level" not in ln or '"assistant"' in ln:
                continue
            m = _EFFORT_MARK_RE.search(ln)
            if m:
                level = m.group(1).lower()
                break
        _effort_scan_cache[path] = (size, level)
    return level


def _parse_model_flag(cmd: str):
    """從 cmd 字串解析 --model <x> 或 --model=<x>，回 normalized model 字串或 None。
    支援 bare alias（opus/sonnet/haiku/fable）與完整 claude-* id。"""
    if not cmd:
        return None
    m = re.search(r"--model[= ](['\"]?)(\S+)\1", cmd)
    if not m:
        return None
    val = m.group(2).strip("'\"")
    # strip [1m] / ANSI
    val = re.sub(r"\x1b\[[0-9;]*m", "", val)
    val = re.sub(r"\[1m\]", "", val).strip()
    if not val:
        return None
    return val


def _parse_claude_settings(path):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    return {"model": (cfg.get("model") or "").strip() or None,
            "effort": (cfg.get("effortLevel") or "").strip() or None}


def _parse_codex_rollout(path):
    """rollout 最新 turn_context 的 model/effort。"""
    for ln in _tail_lines(path):
        if '"turn_context"' not in ln:
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        p = o.get("payload") or o
        model = (p.get("model") or "").strip()
        if model:
            return {"model": model, "effort": (p.get("effort") or "").strip() or None}
    return None


def _parse_codex_config(path):
    model = effort = None
    with open(path, encoding="utf-8") as f:
        for ln in f:
            m = re.match(r'\s*model\s*=\s*"([^"]+)"', ln)
            if m:
                model = m.group(1)
            m = re.match(r'\s*model_reasoning_effort\s*=\s*"([^"]+)"', ln)
            if m:
                effort = m.group(1)
    return {"model": model, "effort": effort} if model or effort else None


def detect_model_info(worker: dict, transcript_path=None):
    """tab 的 {name, effort, provider}；非 AI tab 或偵測不到回 None。永不拋。"""
    try:
        kind = _worker_kind(worker.get("cmd", ""))
        if kind == "agy":
            # The label the running process last announced beats any config
            # file: it follows /model switches inside the session.
            log_path = _agy_log_path(worker)
            label = _parse_agy_model(log_path) if log_path else None
            if not label:
                return None
            name, effort = _split_agy_model_label(label)
            return {"name": name, "effort": effort, "provider": "agy"}
        if kind == "codex":
            info = _cached_parse(transcript_path, _parse_codex_rollout) if transcript_path else None
            info = info or _cached_parse(CODEX_CONFIG_TOML, _parse_codex_config)
            if not info or not info.get("model"):
                return None
            return {"name": _pretty_model(info["model"]),
                    "effort": info.get("effort") or "", "provider": "codex"}
        if kind == "claude":
            # 優先序（per-tab 最準的放最前）：
            # (a) transcript main-chain 最新 assistant.message.model（反映 /model 切換）
            # (b) cmd 的 --model flag（啟動時指定的模型）
            # (c) 都無 → None，不退回全域 settings.json（全域是 session 預設，
            #     不代表每個分頁的實際模型，是主要錯誤來源）
            model = _cached_parse(transcript_path, _parse_claude_transcript_model) \
                if transcript_path else None
            if not model:
                model = _parse_model_flag(worker.get("cmd", ""))
            if not model:
                return None
            # effort：per-tab transcript 的 /effort 痕跡優先（session-only，
            # 不寫全域）；沒有才退全域 settings.json 的 effortLevel。
            # 不走 _cached_parse——parser 自帶增量掃描快取（mtime 快取會在
            # 活躍分頁每次寫入時整檔重掃 14MB）。
            effort = _parse_claude_transcript_effort(transcript_path) \
                if transcript_path else None
            if not effort:
                glob_cfg = _cached_parse(CLAUDE_SETTINGS_JSON, _parse_claude_settings) or {}
                effort = glob_cfg.get("effort") or ""
            return {"name": _pretty_model(model),
                    "effort": effort, "provider": "claude"}
    except Exception:
        pass
    return None


# ────────────────────────── JSONL 解析（normalize）──────────────────────────

def _target(tool, inp):
    if not isinstance(inp, dict):
        return ""
    for k in ("file_path", "path", "notebook_path"):
        if inp.get(k):
            return os.path.basename(str(inp[k]))
    if inp.get("pattern"):
        return str(inp["pattern"])[:30]
    for k in ("command", "cmd", "query", "description", "prompt"):
        if inp.get(k):
            return str(inp[k]).strip().splitlines()[0][:40] if str(inp[k]).strip() else ""
    return ""


def _parse_iso(s):
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _epoch(v):
    try:
        return float(v)
    except Exception:
        return None


def _content_text(content):
    """Pull plain text out of a Claude/Codex message content (str or block list)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                parts.append(c["text"])
            elif isinstance(c, dict) and c.get("type") == "output_text" and c.get("text"):
                parts.append(c["text"])
            elif isinstance(c, str):
                parts.append(c)
        return " ".join(parts).strip()
    return ""


def _norm_claude(o):
    t = o.get("type")
    ts = _parse_iso(o.get("timestamp"))
    m = o.get("message") if isinstance(o.get("message"), dict) else {}
    if t == "user":
        content = m.get("content")
        if isinstance(content, list) and any(
                isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            return {"kind": "tool_result", "ts": ts}
        text = _content_text(content)
        # 本地 slash command（/model、/clear…）也會寫進 transcript 一筆 user
        # 訊息，但 agent 不會回應它 → 不能當「未回應的指令」，否則閒置 tab
        # 跑過 /model 就被誤判 stuck。直接忽略。
        if ("<command-name>" in text or "<local-command-stdout>" in text
                or "local-command-caveat" in text):
            return None
        return {"kind": "user_msg", "ts": ts, "text": text}
    if t == "assistant":
        sr = m.get("stop_reason")
        out = None
        for c in (m.get("content") or []):
            if isinstance(c, dict) and c.get("type") == "tool_use":
                name = c.get("name")
                ev = {"kind": "tool_call", "ts": ts, "tool": name,
                      "target": _target(name, c.get("input"))}
                if name == "AskUserQuestion":
                    ev["kind"] = "decision_req"
                out = ev
        if sr == "end_turn":
            return {"kind": "turn_end", "ts": ts}
        if out:
            return out
        return {"kind": "assistant_text", "ts": ts, "text": _content_text(m.get("content"))}
    return None


def _norm_codex(o):
    outer = o.get("type")
    p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
    pt = p.get("type")
    ts = _parse_iso(o.get("timestamp"))
    if outer == "event_msg":
        if pt in ("task_started", "turn_started", "user_message"):
            return {"kind": "user_msg", "ts": _epoch(p.get("started_at")) or ts,
                    "text": str(p.get("message", "") or "").strip()}
        if pt in ("task_complete", "turn_complete"):
            return {"kind": "turn_end", "ts": _epoch(p.get("completed_at")) or ts}
        if pt in ("exec_approval_request", "apply_patch_approval_request"):
            cmd = " ".join(p.get("command", []) or []) if p.get("command") else "patch"
            return {"kind": "decision_req", "ts": ts, "target": cmd[:40]}
        if pt in ("error", "stream_error"):
            return {"kind": "error", "ts": ts, "text": str(p.get("message", ""))[:60]}
        if pt == "agent_message":
            return {"kind": "assistant_text", "ts": ts,
                    "text": str(p.get("message", "") or "").strip()}
        if pt == "mcp_tool_call_begin":
            return {"kind": "tool_call", "ts": ts, "tool": "mcp",
                    "target": str(p.get("invocation", ""))[:40]}
        if pt in ("mcp_tool_call_end", "patch_apply_end"):
            return {"kind": "tool_result", "ts": ts}
    if outer == "response_item":
        if pt == "function_call":
            name = p.get("name", "")
            try:
                args = json.loads(p.get("arguments") or "{}")
            except Exception:
                args = {}
            return {"kind": "tool_call", "ts": ts, "tool": name,
                    "target": _target(name, args)}
        if pt in ("custom_tool_call", "tool_search_call"):
            name = p.get("name") or ("tool_search" if pt == "tool_search_call" else "")
            args = p.get("arguments") if isinstance(p.get("arguments"), dict) else {}
            if not args and isinstance(p.get("input"), dict):
                args = p.get("input")
            return {"kind": "tool_call", "ts": ts, "tool": name,
                    "target": _target(name, args)}
        if pt in ("function_call_output", "custom_tool_call_output", "tool_search_output"):
            return {"kind": "tool_result", "ts": ts}
        if pt == "message":
            return {"kind": "assistant_text", "ts": ts,
                    "text": _content_text(p.get("content"))}
        if pt == "reasoning":
            return {"kind": "assistant_text", "ts": ts}
        if pt == "image_generation_call":
            return {"kind": "tool_call", "ts": ts, "tool": "image_generation_call", "target": ""}
    return None


def _detect_format(first_line):
    try:
        o = json.loads(first_line)
    except Exception:
        return None
    if o.get("type") in ("session_meta", "response_item", "event_msg", "turn_context"):
        return "codex"
    if "sessionId" in o or o.get("type") in ("user", "assistant", "system", "summary"):
        return "claude"
    return None


def _read_tail_events(path, tail_bytes=262144, max_records=300):
    """只讀檔尾 tail_bytes，避免整檔讀（codex log 可達 GB）。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > tail_bytes:
                f.seek(size - tail_bytes)
                f.readline()  # 丟掉被切半的第一行
            raw = f.read()
        # 第一行格式判斷需檔頭；單獨讀一次
        with open(path, "r", errors="replace") as fh:
            head = fh.readline()
    except Exception as e:
        return None, [], str(e)
    fmt = _detect_format(head)
    if not fmt:
        return None, [], "unknown format"
    norm = _norm_claude if fmt == "claude" else _norm_codex
    evs = []
    for line in raw.decode("utf-8", errors="replace").splitlines()[-max_records:]:
        try:
            o = json.loads(line)
        except Exception:
            continue
        e = norm(o)
        if e:
            evs.append(e)
    if evs and evs[-1].get("ts") is None:
        try:
            evs[-1]["ts"] = os.path.getmtime(path)
        except OSError:
            pass
    return fmt, evs, None


# ────────────────────────── 排程／loop 偵測 ──────────────────────────
# 一個對話「有排程」的訊號全部來自 transcript 裡的 harness tool_use：
#   ScheduleWakeup → /loop（自我排程喚醒），帶 delaySeconds / reason
#   CronCreate / CronDelete → 雲端 routine（設一次長期有效）
# 純讀檔尾推斷，不靠 agent 自報，與狀態機完全解耦。
_SCHED_TOOLS = ("ScheduleWakeup", "CronCreate", "CronDelete")
# 上一次喚醒已觸發、但尚未排下一次的 loop 視為「執行中」的寬限；超過就當作
# 已結束（避免被中斷／停掉的 loop 永遠掛在面板上）。
LOOP_RUNNING_GRACE = 1800


def _sched_blocks(o):
    """從一筆 claude assistant 紀錄裡，yield 所有排程相關 tool_use 的
    (name, input, ts)。一個 turn 可能同時呼叫別的工具，全部掃不只看最後一個。"""
    if o.get("type") != "assistant":
        return
    m = o.get("message") if isinstance(o.get("message"), dict) else {}
    ts = _parse_iso(o.get("timestamp"))
    for c in (m.get("content") or []):
        if (isinstance(c, dict) and c.get("type") == "tool_use"
                and c.get("name") in _SCHED_TOOLS):
            yield c.get("name"), (c.get("input") or {}), ts


def detect_schedules(path, now, tail_bytes=262144, max_records=800):
    """掃 claude transcript 檔尾，回傳該對話的 loop/cron 排程摘要，沒有則 None。
        {kind: 'loop'|'cron', status: 'sleeping'|'running'|'scheduled',
         next_ts: epoch|None, reason: str, rounds: int, crons: [...]}"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > tail_bytes:
                f.seek(size - tail_bytes)
                f.readline()
            raw = f.read()
        with open(path, "r", errors="replace") as fh:
            head = fh.readline()
    except Exception:
        return None
    if _detect_format(head) != "claude":   # codex 沒有 ScheduleWakeup
        return None
    last_wake = None
    wake_count = 0
    crons = {}            # key -> {desc, schedule}
    deleted = set()
    for line in raw.decode("utf-8", errors="replace").splitlines()[-max_records:]:
        try:
            o = json.loads(line)
        except Exception:
            continue
        for name, inp, ts in _sched_blocks(o):
            if name == "ScheduleWakeup":
                wake_count += 1
                try:
                    delay = float(inp.get("delaySeconds"))
                except (TypeError, ValueError):
                    delay = None
                last_wake = {"ts": ts, "delay": delay,
                             "reason": str(inp.get("reason") or "").strip()[:90]}
            elif name == "CronCreate":
                key = str(inp.get("name") or inp.get("schedule") or len(crons))
                crons[key] = {
                    "desc": str(inp.get("name") or inp.get("prompt")
                                or "cron").strip()[:60],
                    "schedule": str(inp.get("schedule")
                                    or inp.get("cronExpression") or "").strip()[:40]}
            elif name == "CronDelete":
                deleted.add(str(inp.get("name") or inp.get("id")
                                or inp.get("cronId") or ""))
    active_crons = [v for k, v in crons.items() if k not in deleted]
    loop = None
    if last_wake and last_wake.get("ts") and last_wake.get("delay") is not None:
        next_ts = last_wake["ts"] + last_wake["delay"]
        if next_ts > now:
            loop = {"kind": "loop", "status": "sleeping"}
        elif (now - next_ts) < LOOP_RUNNING_GRACE:
            loop = {"kind": "loop", "status": "running"}
        if loop is not None:
            loop.update(next_ts=next_ts, reason=last_wake["reason"], rounds=wake_count)
    if loop is None and active_crons:
        loop = {"kind": "cron", "status": "scheduled", "next_ts": None,
                "reason": active_crons[0]["desc"], "rounds": 0}
    if loop is not None:
        loop["crons"] = active_crons
    return loop


# ────────────────────────── 狀態機 ──────────────────────────

def _detail(evs):
    """Rich display detail, scanning the WHOLE tail (not just the current turn)
    so a working agent almost always shows a concrete action + task:
      action    most recent tool call  ("Editing main.py")
      task      most recent real user instruction (what it's on)
      narration most recent assistant text line (what it just said)
    """
    action = task = narration = ""
    for e in reversed(evs):
        k = e.get("kind")
        if not action and k == "tool_call":
            verb = VERB.get(e.get("tool"), e.get("tool") or "")
            action = f"{verb} {e.get('target', '')}".strip()
        elif not narration and k == "assistant_text" and e.get("text"):
            line = e["text"].strip().splitlines()[0].strip() if e["text"].strip() else ""
            if line:
                narration = line[:90]
        elif not task and k == "user_msg" and e.get("text"):
            for line in e["text"].splitlines():
                line = line.strip()
                if (line and not line.startswith(("---", "[[SF:", "<", "[Image"))
                        and "system-reminder" not in line):
                    task = line[:90]
                    break
        if action and task and narration:
            break
    return action, task, narration


SPINNER_RE = ("esc to interrupt", "Working (", "Running…", "↑")
# pi 的狀態列**固定**顯示「↑79k ↓1.5k 14.5%/128k (auto)   spark-vision」——
# 那個 ↑ 是 token 計數，不是進度指示，但它命中 SPINNER_RE 的 "↑" → pi 分頁
# 永遠被判成 working，跑完也不會變燈（2026-08-24 回報：蒸餾任務跑完、檔案已
# 產出、token 停在 ↑79k 不動，燈號仍是工作中）。pi 真正的工作訊號是 braille
# spinner + Working，完成訊號則是「token 數不再變動」。見 _pi_status。
_PI_STATUS_RE = re.compile(
    r"↑\s*([\d.]+[kKmM]?)\s+↓\s*([\d.]+[kKmM]?)\s+([\d.]+)%/")
_PI_WORKING_RE = re.compile(r"[⠁-⣿]\s*Working")
MENU_RE = ("❯ 1.", "› 1.", "Do you want", "Would you like to run",
           "1. Yes", "Esc to cancel")


def _screen_signals(scr):
    """Wording-based signals from the live rendered screen. Computed up front
    so they work even with NO transcript events (brand-new tab whose
    <uuid>.jsonl isn't written yet)."""
    tail_lines = scr.splitlines()[-8:]
    bottom = "\n".join(tail_lines)
    return {
        "spinner": any(x in scr for x in SPINNER_RE),
        "menu": any(x in scr for x in MENU_RE),
        # "esc to interrupt" is the one unambiguous proof of LIVE work — Claude
        # Code shows it only while a turn is actively running (tool exec, token
        # streaming, extended thinking). A permission/decision prompt never
        # shows it (it footers "esc to cancel").
        "actively_working": "esc to interrupt" in scr,
        # editable input prompt in the bottom input region (❯ / ›, with or
        # without a typed draft after it) → TUI is waiting for the user.
        "has_input_prompt": any(l.lstrip()[:1] in ("❯", "›") for l in tail_lines),
        # Codex idle prompt: an input box may show placeholder text such as
        # "Improve documentation in @filename" with the model/cwd footer under
        # it. It is not a running task unless the status line says "Working".
        "codex_idle_prompt": (
            "gpt-" in bottom
            and "·" in bottom
            and not re.search(r"\bWorking\s*\(", bottom)
        ),
        "rating": "How is Claude doing this session" in scr,
    }


def compute_state(events, now=None, screen_tail=""):
    now = now or time.time()
    scr = screen_tail or ""
    sig = _screen_signals(scr)
    spinner = sig["spinner"]
    menu = sig["menu"]
    actively_working = sig["actively_working"]
    has_input_prompt = sig["has_input_prompt"]

    # The live screen is ground truth and works BEFORE any transcript exists.
    # esc-to-interrupt overrides everything (incl. a stale decision_req or an
    # un-flushed thinking block in the transcript).
    activity = {}
    if events:
        last_tool = None
        for e in reversed(events):
            if e["kind"] == "tool_call":
                last_tool = e
                break
            if e["kind"] in ("turn_end", "user_msg"):
                break
        if last_tool:
            activity = {"tool": last_tool.get("tool"),
                        "verb": VERB.get(last_tool.get("tool"), last_tool.get("tool") or "Working"),
                        "target": last_tool.get("target") or ""}

    if actively_working:
        return "working", activity, "interruptible (live screen)"

    # No transcript events (or none yet) → drive purely from the screen so a
    # freshly spawned tab that's actually working still shows up instead of
    # vanishing as 'unknown'. 回報：新增的 tab 被當沒看到.
    if not events:
        if menu and not spinner:
            return "decision", {}, "menu (screen only)"
        if has_input_prompt or sig["codex_idle_prompt"] or sig["rating"]:
            return "done", {}, "idle prompt (screen only)"
        if spinner:
            return "working", {}, "spinner (screen only)"
        return "idle", {}, "no events"

    last = events[-1]
    last_ts = last.get("ts")
    pending_tool = last if last["kind"] == "tool_call" else None
    age = (now - last_ts) if last_ts else None

    # The live screen is authoritative over a stale transcript verdict.
    #  • a menu on screen (and no spinner) is a real, pending decision.
    #  • an editable input prompt / rating prompt with NO menu means the TUI is
    #    back to waiting for input → any decision_req still sitting at the tail
    #    of the transcript was already answered or dismissed and must NOT pin
    #    "等決策" forever (回報：idle tabs stuck on 等決策 for 3394m/979m, i.e.
    #    their whole lifetime, because decision_req had no staleness guard the
    #    way pending_tool does).
    if menu and not spinner:
        return "decision", activity, "menu (screen)"
    if not spinner and (has_input_prompt or sig["codex_idle_prompt"] or sig["rating"]):
        return "done", {}, "idle prompt (overrides stale transcript)"
    # No corroborating screen wording → trust the transcript's last event.
    if last["kind"] == "decision_req":
        return "decision", activity, "decision_req (transcript)"
    if last["kind"] == "error":
        return "stuck", activity, "error event"
    if last["kind"] == "turn_end":
        return "done", {}, "turn_end"
    if spinner:
        return "working", activity, "screen spinner"
    # A pending tool call means a tool is RUNNING — long commands (ssh, builds,
    # MCP calls) are normal and must read as working, not stuck. BUT only while
    # it's plausibly still running: a tool_call left pending for longer than the
    # stuck threshold with no spinner on screen has almost certainly finished
    # (its tool_result just isn't in our parsed tail) and the turn went idle —
    # otherwise every such tab shows "working" for hours (回報：102m「Running
    # open …」on a long-idle tab).
    if pending_tool and (spinner or age is None or age < STUCK_IDLE_S):
        return "working", activity, "tool running"
    if pending_tool:
        return "done", {}, "stale pending tool"
    if age is None or age < WORKING_FRESH_S:
        return "working", activity, "fresh event"
    # Turn not formally ended but the last word was plain assistant text and
    # nothing is pending — that's a finished reply. Claude Code often ends a
    # turn without an end_turn record, so treating this as stuck produced
    # false 卡住 on every idle tab.
    if last["kind"] == "assistant_text":
        return "done", {}, "trailing text (informal end)"
    # Genuine stalls: a user message the agent never started answering, or a
    # tool result followed by nothing for a long time.
    if age > STUCK_IDLE_S:
        return "stuck", activity, f"no progress {int(age)}s"
    return "working", activity, "between events"


# ────────────────────────── 對外：StatusTracker ──────────────────────────

class StatusTracker:
    """per-sid 快取 transcript 路徑 + 去抖。main.py 每 ~500ms 呼叫 status_for。"""

    def __init__(self):
        self._path_cache = {}   # sid -> (transcript_path, resolved_at)
        self._last = {}         # sid -> (state, since_ts)
        self._pending = {}      # sid -> (candidate_state, first_seen_ts)
        self._detail = {}       # sid -> (action, task, narration) 最近非空值（穩定呈現）
        # sid -> (computed_at, result)。TG 長回合心跳唯讀這份快取，**不得**自己
        # 呼叫 status_for()——那會觸發 transcript 解析，把成本帶進 bridge 的
        # flush loop。main.py 的 0.6s monitor thread 已經在算了，直接讀。
        self._last_result = {}

    def last_result(self, sid, max_age=None):
        """回 (result_dict, age_seconds)，沒有／過期回 (None, None)。純唯讀。"""
        got = self._last_result.get(sid)
        if not got:
            return None, None
        at, res = got
        age = time.time() - at
        if max_age is not None and age > max_age:
            return None, age
        return res, age

    def _resolve_cached(self, sid, worker, now):
        path, at = self._path_cache.get(sid, (None, 0))
        # codex 用 lsof 較貴 + 路徑可能變，30s 重解一次；claude 便宜可 10s
        ttl = 30 if _worker_kind(worker.get("cmd", "")) == "codex" else 10
        if path and os.path.exists(path) and (now - at) < ttl:
            return path
        path = resolve_transcript(worker)
        self._path_cache[sid] = (path, now)
        return path

    def _debounce(self, sid, state, now):
        """狀態翻轉去抖：decision/stuck 需穩定 ~1.2s（防畫面瞬閃誤觸），
        其餘需 DONE_QUIET_S。回傳生效狀態。

        候選的預設值**必須**是 None：先前寫成 `(state, now)`，於是
        `cand != state` 永遠不成立、pending 從不寫入、每次呼叫都把 first
        重設成 now，`now - first` 恆為 0 → 除了 `prev is None` 的第一次，
        任何狀態轉移都無法生效（狀態點在首次判定後就凍住）。
        """
        prev, since = self._last.get(sid, (None, now))
        if state == prev:
            self._pending.pop(sid, None)
            return state
        hold = 1.2 if state in ("decision", "stuck") else DONE_QUIET_S
        cand, first = self._pending.get(sid, (None, now))
        if cand != state:
            self._pending[sid] = (state, now)
            return prev or state
        if (now - first) >= hold or prev is None:
            self._last[sid] = (state, now)
            self._pending.pop(sid, None)
            return state
        return prev

    def status_for(self, sid, worker, screen_tail="", now=None):
        """回傳 {state, dot, activity, summary, why, transcript}。永不拋例外。

        結果順手存進 `_last_result`，供 `last_result(sid)` 唯讀取用（TG 心跳）。
        """
        ret = self._status_for_impl(sid, worker, screen_tail=screen_tail, now=now)
        try:
            self._last_result[sid] = (now or time.time(), ret)
        except Exception:
            pass
        return ret

    def _agy_status(self, sid, worker, now, screen_tail, model):
        """agy state, straight from its per-conversation SQLite.

        agy has no JSONL transcript, so the event-based state machine has
        nothing to chew on; the steps table answers the question directly (the
        newest step sits at AGY_STEP_RUNNING while the agent works). Falls back
        to the shared screen-only read when the conversation can't be found —
        a brand-new tab still on the welcome screen has no conversation yet.
        """
        db = self._resolve_cached(sid, worker, now)
        step = _agy_last_step(db) if db else None
        if not step:
            state, act, why = compute_state([], now=now, screen_tail=screen_tail)
            state = self._debounce(sid, state, now)
            _, since = self._last.get(sid, (state, now))
            return {"state": state, "dot": DOT.get(state, ""), "activity": act,
                    "summary": (act.get("verb") if act else "") or state,
                    "action": "", "narration": "", "task": "",
                    "elapsed": int(now - since), "why": "agy screen-only: " + why,
                    "transcript": db, "loop": None, "model": model}
        # Completion is measured from when this tab was last seen RUNNING, not
        # from file mtime: a live agy process keeps touching the -wal in the
        # background (quota refresh and friends), so an idle tab would look
        # freshly finished forever. It has to be a *window* rather than a
        # one-shot transition, because _debounce only promotes a state that
        # holds for DONE_QUIET_S — a single-sample "done" never survives.
        seen = getattr(self, "_agy_seen", None)
        if seen is None:
            seen = self._agy_seen = {}
        _, _, last_running = seen.get(sid, (None, None, 0.0))
        if step["status"] == AGY_STEP_RUNNING:
            last_running = now
            stalled = step["age"] is not None and step["age"] > STUCK_IDLE_S
            state = "stuck" if stalled else "working"
            why = f"agy step {step['idx']} running" + (" (no writes)" if stalled else "")
        elif last_running and now - last_running <= AGY_DONE_WINDOW_S:
            state, why = "done", f"agy step {step['idx']} just finished"
        else:
            state, why = "idle", f"agy step {step['idx']} settled"
        seen[sid] = (step["idx"], step["status"], last_running)
        # No _debounce here on purpose: that guard exists to swallow flicker in
        # screen-scraped signals, and this state comes from a committed SQLite
        # row — it doesn't bounce. (It also currently never promotes a changed
        # state; see the note in _debounce.)
        prev_state, since = self._last.get(sid, (None, now))
        if state != prev_state:
            since = now
            self._last[sid] = (state, now)
        return {"state": state, "dot": DOT.get(state, ""), "activity": None,
                "summary": state, "action": "", "narration": "", "task": "",
                "elapsed": int(now - since), "why": why,
                "transcript": db, "loop": None, "model": model}

    # pi 完成後 token 數就不再變動；隔這麼久沒動＝這一輪真的結束了。
    _PI_IDLE_S = 6.0
    # 剛完成的「亮一下」窗口，與 agy 的 AGY_DONE_WINDOW_S 同義。
    _PI_DONE_WINDOW_S = 90.0

    def _pi_status(self, sid, worker, now, screen_tail, model):
        """pi 的燈號，純看畫面（pi 沒有 JSONL transcript，跟 agy 一樣）。

        兩個訊號，都取自 pi TUI 的固定樣式：
        1. `⠧ Working...`（braille spinner）＝ 正在跑，最直接。
        2. 狀態列 `↑79k ↓1.5k 14.5%/128k (auto)` 的 token 數——**變動中**代表
           還在產出；停住超過 _PI_IDLE_S 就是這一輪結束了。

        不能沿用共用的 `compute_state`：`SPINNER_RE` 含 "↑"，而 pi 狀態列永遠
        有 ↑（token 計數），會把每個 pi 分頁釘死在 working。
        """
        scr = screen_tail or ""
        seen = getattr(self, "_pi_seen", None)
        if seen is None:
            seen = self._pi_seen = {}
        prev_sig, prev_change, last_active = seen.get(sid, (None, 0.0, 0.0))

        m = _PI_STATUS_RE.search(scr)
        sig = m.group(0) if m else None
        spinning = bool(_PI_WORKING_RE.search(scr))

        if sig != prev_sig:          # token 數變了 → 正在產出
            prev_change = now
        changed_recently = (now - prev_change) < self._PI_IDLE_S if prev_change else False

        if spinning or changed_recently:
            state = "working"
            why = "pi spinner" if spinning else "pi tokens moving"
            last_active = now
        elif last_active and now - last_active <= self._PI_DONE_WINDOW_S:
            state, why = "done", "pi tokens settled"
        elif sig or scr.strip():
            state, why = "idle", "pi idle"
        else:
            state, why = "unknown", "pi no screen"
        seen[sid] = (sig, prev_change, last_active)

        summary = "working" if state == "working" else state
        _, since = self._last.get(sid, (state, now))
        if self._last.get(sid, (None,))[0] != state:
            self._last[sid] = (state, now)
            since = now
        return {"state": state, "dot": DOT.get(state, ""), "activity": {},
                "summary": summary, "action": "", "narration": "", "task": "",
                "elapsed": int(now - since), "why": why,
                "transcript": None, "loop": None, "model": model}

    def _status_for_impl(self, sid, worker, screen_tail="", now=None):
        now = now or time.time()
        try:
            _kind = _worker_kind(worker.get("cmd", ""))
            if _kind == "pi":
                return self._pi_status(
                    sid, worker, now, screen_tail, detect_model_info(worker))
            if _kind == "agy":
                return self._agy_status(
                    sid, worker, now, screen_tail,
                    detect_model_info(worker),
                )
            path = self._resolve_cached(sid, worker, now)
            model = detect_model_info(worker, path if (path and os.path.exists(path)) else None)
            if not path or not os.path.exists(path):
                # No transcript yet (brand-new tab, file not written) — fall
                # back to a screen-only read so an actively-working new tab is
                # detected immediately instead of showing 'unknown' and being
                # hidden from the feed. compute_state([]) uses the screen
                # signals (esc-to-interrupt / menu / prompt).
                state, act, why = compute_state([], now=now, screen_tail=screen_tail)
                state = self._debounce(sid, state, now)
                _, since = self._last.get(sid, (state, now))
                return {"state": state, "dot": DOT.get(state, ""), "activity": act,
                        "summary": (act.get("verb") if act else "") or state,
                        "action": "", "narration": "", "task": "",
                        "elapsed": int(now - since), "why": "screen-only: " + why,
                        "transcript": None, "loop": None, "model": model}
            try:
                loop = detect_schedules(path, now)
            except Exception:
                loop = None
            fmt, evs, err = _read_tail_events(path)
            if err:
                state, act, why = compute_state([], now=now, screen_tail=screen_tail)
                state = self._debounce(sid, state, now)
                _, since = self._last.get(sid, (state, now))
                return {"state": state, "dot": DOT.get(state, ""), "activity": act,
                        "summary": (act.get("verb") if act else "") or state,
                        "action": "", "narration": "", "task": "",
                        "elapsed": int(now - since), "why": "screen-only(" + err + ")",
                        "transcript": path, "loop": loop, "model": model}
            state, act, why = compute_state(evs, now=now, screen_tail=screen_tail)
            state = self._debounce(sid, state, now)
            action, task, narration = _detail(evs)
            # 穩定呈現：事件間隙抓不到細節時沿用上一次的非空值，卡片不閃空
            pa, pt, pn = self._detail.get(sid, ("", "", ""))
            action, task, narration = action or pa, task or pt, narration or pn
            self._detail[sid] = (action, task, narration)
            summary = action or narration or state
            _, since = self._last.get(sid, (state, now))
            return {"state": state, "dot": DOT.get(state, ""), "activity": act,
                    "summary": summary, "action": action, "narration": narration,
                    "task": task, "elapsed": int(now - since), "why": why,
                    "transcript": os.path.basename(path), "fmt": fmt, "loop": loop,
                    "model": model}
        except Exception as e:
            return {"state": "unknown", "dot": "", "activity": {},
                    "summary": "", "why": f"exc:{e}", "transcript": None, "loop": None,
                    "model": None}


# 模組自測：python3 agent_status.py
if __name__ == "__main__":
    import sys
    tr = StatusTracker()
    # 掃所有近期 active worker（用 fallback 對應，不需 main.py）
    seen = set()
    rows = []
    cutoff = time.time() - 1800
    for f in glob.glob(os.path.join(CLAUDE_PROJECTS, "*/*.jsonl")):
        try:
            if os.path.getmtime(f) < cutoff:
                continue
        except OSError:
            continue
        fmt, evs, err = _read_tail_events(f)
        if err or not evs:
            continue
        st, act, why = compute_state(evs)
        s = f"{act.get('verb','')} {act.get('target','')}".strip() or st
        rows.append(("claude", st, s, why, os.path.basename(f)))
    for f in glob.glob(os.path.join(CODEX_SESSIONS, "*/*/*/rollout-*.jsonl")):
        try:
            if os.path.getmtime(f) < cutoff:
                continue
        except OSError:
            continue
        fmt, evs, err = _read_tail_events(f)
        if err or not evs:
            continue
        st, act, why = compute_state(evs)
        s = f"{act.get('verb','')} {act.get('target','')}".strip() or st
        rows.append(("codex", st, s, why, os.path.basename(f)))
    for src, st, s, why, fn in sorted(rows, key=lambda x: x[1]):
        print(f"[{src:6}] {st:8} | {s:45} | {why}  ({fn})")
