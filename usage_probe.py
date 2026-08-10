"""Per-tab AI usage probe for ShellFrame slash commands (/usage, /水位).

Given a session's launch command, detect whether it runs Claude or Codex and
fetch that provider's quota water-level using the existing local scripts:

  - Claude: ~/.openclaw/workspace/skills/claude-usage/scripts/fetch_oauth_usage.sh
            (Keychain OAuth token → api.anthropic.com/api/oauth/usage; no browser)
  - Codex:  ~/.openclaw/workspace/skills/openai-codex-usage/scripts/codex_usage.py
            (codex app-server JSONRPC account/rateLimits/read)

Returns a short, friendly text block. Never raises — failures become a friendly
message so a slash command can't crash a tab.
"""

import base64
import glob
import json
import os
import queue
import shutil
import sqlite3
import subprocess
import time
import urllib.request
import urllib.error
from datetime import datetime

# Primary source for Claude water-level: the OAuth usage API, called directly
# with the local Keychain token (no browser, no external script). The old
# openclaw script lives only on the openclaw host, so depending on it made
# /usage fail on every other machine — see _fetch_claude.
CLAUDE_USAGE_API = "https://api.anthropic.com/api/oauth/usage"
_CLAUDE_OAUTH_BETA = "oauth-2025-04-20"

# Older Codex versions logged a `codex.rate_limits` event to this local SQLite
# log. Keep it as a read-only compatibility fallback; current versions persist
# the snapshot in rollout JSONL below.
CODEX_LOG_GLOB = os.path.expanduser("~/.codex/logs_*.sqlite")

# Newer Codex versions persist the same snapshot in rollout JSONL instead of
# the retired feedback log table.  Read only the tail: rollout files can be
# very large, while rate-limit events are appended near the end of each turn.
CODEX_ROLLOUT_GLOB = os.path.expanduser(
    "~/.codex/sessions/*/*/*/rollout-*.jsonl"
)
CODEX_ROLLOUT_TAIL_BYTES = 512 * 1024
CODEX_APP_SERVER_TIMEOUT = 12

# Legacy fallback only (present on the openclaw host, absent elsewhere).
CLAUDE_SCRIPT = os.path.expanduser(
    "~/.openclaw/workspace/skills/claude-usage/scripts/fetch_oauth_usage.sh"
)
CODEX_SCRIPT_DIR = os.path.expanduser(
    "~/.openclaw/workspace/skills/openai-codex-usage/scripts"
)


def detect_ai(cmd: str):
    """Return 'claude', 'codex', or None based on a session launch command."""
    if not cmd:
        return None
    for tok in cmd.split():
        base = tok.split("/")[-1]
        if "." in base:
            base = base.rsplit(".", 1)[0]
        if base in ("codex", "sf-codex"):
            return "codex"
        if base == "claude":
            return "claude"
    return None


def _fmt_epoch(epoch) -> str:
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(epoch)))
    except (TypeError, ValueError):
        return "?"


def _fmt_iso(iso: str) -> str:
    if not iso:
        return "?"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        return "?"


_ENTERPRISE_PLANS = {"team", "business", "enterprise", "edu"}
_PLAN_NICE = {
    "team": "Team", "business": "Business", "enterprise": "Enterprise",
    "edu": "Edu", "pro": "Pro", "plus": "Plus", "max": "Max", "free": "Free",
}


def _plan_label(plan, org=None) -> str:
    """e.g. 'Team（企業·Neux Com）' / 'Pro（個人）'. '' if unknown."""
    if not plan:
        return ""
    low = plan.lower()
    nice = _PLAN_NICE.get(low, plan.capitalize())
    if low in _ENTERPRISE_PLANS:
        tag = f"企業·{org}" if org else "企業"
    else:
        tag = "個人"
    return f"{nice}（{tag}）"


def _claude_oauth(env=None) -> dict:
    """Read the Claude Code OAuth blob from the macOS Keychain. {} on failure.

    All claude tabs on this machine share the same ~/.claude credentials, so
    this reflects whatever account the current claude tab is signed in as.
    """
    if env and env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return {"accessToken": env["CLAUDE_CODE_OAUTH_TOKEN"]}
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if raw:
            return json.loads(raw).get("claudeAiOauth") or {}
    except Exception:
        pass
    return {}


def _claude_account() -> str:
    """'howardwu@neux.com.tw · Team（企業·Neux Com）' or '' if unavailable."""
    email = org = None
    try:
        cj = json.load(open(os.path.expanduser("~/.claude.json")))
        oa = cj.get("oauthAccount", {}) or {}
        email = oa.get("emailAddress")
        org = oa.get("organizationName")
    except Exception:
        pass
    sub = _claude_oauth().get("subscriptionType")
    plan = _plan_label(sub, org)
    parts = [p for p in (email, plan) if p]
    return " · ".join(parts)


def profile_account(profile: dict | None) -> str:
    """Format the safe account metadata stored by ShellFrame."""
    if not profile:
        return ""
    plan = _plan_label(profile.get("plan"), profile.get("organization"))
    return " · ".join(
        p for p in (profile.get("email"), plan or profile.get("label")) if p
    )


def _codex_account(result_plan=None, home=None) -> str:
    """'neux.ios@neux.com.tw · Pro（個人）' or '' if unavailable.

    Reads ~/.codex/auth.json — the account sf-codex actually runs as.
    """
    email = plan = None
    try:
        auth_path = os.path.join(home, "auth.json") if home else os.path.expanduser("~/.codex/auth.json")
        d = json.load(open(auth_path))
        tok = (d.get("tokens") or {}).get("access_token", "")
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.b64decode(payload))
        email = claims.get("https://api.openai.com/profile", {}).get("email")
        plan = claims.get("https://api.openai.com/auth", {}).get("chatgpt_plan_type")
    except Exception:
        pass
    label = _plan_label(plan or result_plan)
    parts = [p for p in (email, label) if p]
    return " · ".join(parts)


# The OAuth usage API rate-limits aggressively (HTTP 429). Several callers share
# this fetch (top-bar pill, /usage modal, event-driven refresh), so we cache the
# last good reading and back off hard: reuse fresh data without any call, hit the
# API at most once per retry window, and on failure serve the last good reading
# flagged stale — the reset times stay useful even when a live refresh fails.
_USAGE_CACHE_FILE = os.path.expanduser("~/.config/shellframe/usage_cache.json")
_CLAUDE_OK_TTL = 45          # within this, reuse good data with no API call
_CLAUDE_RETRY_MIN = 60       # min gap between live API attempts (rate-limit guard)
_CLAUDE_DISK_MAX_AGE = 86400  # don't resurrect a reading older than a day on boot
_claude_cache = None         # {"data": {...}|None, "ts": epoch, "last_try": epoch}


def _claude_cache_state():
    """Lazy-load the persistent cache so reset times survive an app restart."""
    global _claude_cache
    if _claude_cache is not None:
        return _claude_cache
    _claude_cache = {"data": None, "ts": 0, "last_try": 0}
    try:
        with open(_USAGE_CACHE_FILE) as f:
            d = (json.load(f) or {}).get("claude") or {}
        if d.get("data") and (time.time() - d.get("ts", 0)) < _CLAUDE_DISK_MAX_AGE:
            _claude_cache["data"] = d["data"]
            _claude_cache["ts"] = d.get("ts", 0)
    except Exception:
        pass
    return _claude_cache


def _save_claude_cache(c):
    try:
        os.makedirs(os.path.dirname(_USAGE_CACHE_FILE), exist_ok=True)
        with open(_USAGE_CACHE_FILE, "w") as f:
            json.dump({"claude": {"data": c["data"], "ts": c["ts"]}}, f)
    except Exception:
        pass


def _claude_stale(c):
    """Last good reading, tagged so callers know this refresh didn't land."""
    s = dict(c["data"])
    s["_stale"] = True
    return s


def _fetch_claude(env=None):
    """Return {'5hr': (pct, reset), 'week': (pct, reset)[, '_stale': True]} or None.

    Primary path: call the OAuth usage API directly with the local Keychain
    token — self-contained, works on any machine the user is signed in on. The
    legacy openclaw script is tried only as a fallback (it exists solely on the
    openclaw host), so /usage no longer dead-ends on machines without it.
    """
    now = time.time()
    c = _claude_cache_state()
    # Fresh good reading → reuse, no network (dedups bursts from multiple callers)
    if c["data"] and now - c["ts"] < _CLAUDE_OK_TTL:
        return dict(c["data"])
    # Back off between attempts so a retry storm can't keep us rate-limited.
    if now - c["last_try"] < _CLAUDE_RETRY_MIN:
        return _claude_stale(c) if c["data"] else None
    c["last_try"] = now
    token = (_claude_oauth(env) if env else _claude_oauth()).get("accessToken")
    if token:
        try:
            req = urllib.request.Request(CLAUDE_USAGE_API, headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": _CLAUDE_OAUTH_BETA,
                "anthropic-version": "2023-06-01",
                "User-Agent": "shellframe-usage-probe",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            out = {}
            fh = data.get("five_hour") or {}
            if fh.get("utilization") is not None:
                out["5hr"] = (round(fh["utilization"]), _fmt_iso(fh.get("resets_at", "")))
            sd = data.get("seven_day") or {}
            if sd.get("utilization") is not None:
                out["week"] = (round(sd["utilization"]), _fmt_iso(sd.get("resets_at", "")))
            if out:
                c["data"] = out
                c["ts"] = now
                _save_claude_cache(c)
                return out
        except Exception:
            pass  # rate-limited / network — serve stale below, then legacy script
    if c["data"]:
        return _claude_stale(c)
    return _fetch_claude_script()


def _fetch_claude_profile(env):
    """Fetch a non-global Claude profile without touching the shared cache."""
    token = _claude_oauth(env).get("accessToken") if env else None
    if not token:
        return None
    try:
        req = urllib.request.Request(CLAUDE_USAGE_API, headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": _CLAUDE_OAUTH_BETA,
            "anthropic-version": "2023-06-01",
            "User-Agent": "shellframe-usage-probe",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        out = {}
        for source, target in (("five_hour", "5hr"), ("seven_day", "week")):
            item = data.get(source) or {}
            if item.get("utilization") is not None:
                out[target] = (round(item["utilization"]), _fmt_iso(item.get("resets_at", "")))
        return out or None
    except Exception:
        return None


def _fetch_claude_script():
    """Legacy fallback: the openclaw fetch_oauth_usage.sh (absent off-host)."""
    if not os.path.exists(CLAUDE_SCRIPT):
        return None
    try:
        r = subprocess.run(
            ["bash", CLAUDE_SCRIPT],
            capture_output=True, text=True, timeout=20,
        )
        data = json.loads(r.stdout.strip())
    except (subprocess.SubprocessError, json.JSONDecodeError, ValueError):
        return None
    if not data.get("ok"):
        return None
    out = {}
    if data.get("five_hour"):
        fh = data["five_hour"]
        out["5hr"] = (fh.get("pct", 0), _fmt_iso(fh.get("resets", "")))
    if data.get("seven_day"):
        sd = data["seven_day"]
        out["week"] = (sd.get("pct", 0), _fmt_iso(sd.get("resets", "")))
    return out or None


def _extract_json(text, anchor):
    """Pull the first brace-balanced JSON object starting at `anchor` out of a
    log line. None if not found / not parseable."""
    i = text.find(anchor)
    if i < 0:
        return None
    depth = 0
    for j in range(i, len(text)):
        ch = text[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[i:j + 1])
                except ValueError:
                    return None
    return None


def _codex_ratelimits_snapshot(log_glob=None):
    """Latest `codex.rate_limits` event from codex's local SQLite log, or None.

    Codex emits this on every API turn (see ~/.codex/logs_*.sqlite), so the most
    recent row is the freshest known usage without any network/app-server call.
    """
    dbs = sorted(glob.glob(log_glob or CODEX_LOG_GLOB), key=os.path.getmtime, reverse=True)
    for db in dbs:
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                row = con.execute(
                    "SELECT feedback_log_body FROM logs "
                    "WHERE feedback_log_body LIKE '%codex.rate_limits%' "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            continue
        if not row or not row[0]:
            continue
        obj = _extract_json(row[0], '{"type":"codex.rate_limits"')
        if obj:
            return obj
    return None


def _codex_rollout_snapshot(rollout_glob=None):
    """Return the newest rate-limit snapshot from Codex rollout JSONL, or None."""
    candidates = []
    for path in glob.glob(rollout_glob or CODEX_ROLLOUT_GLOB):
        try:
            candidates.append((os.path.getmtime(path), path))
        except OSError:
            continue

    for _, path in sorted(candidates, reverse=True):
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - CODEX_ROLLOUT_TAIL_BYTES))
                text = f.read().decode("utf-8", errors="replace")
        except OSError:
            continue

        for line in reversed(text.splitlines()):
            try:
                row = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            payload = row.get("payload") or {}
            rate_limits = (
                payload.get("rate_limits")
                or payload.get("rateLimits")
                or row.get("rate_limits")
                or row.get("rateLimits")
            )
            if isinstance(rate_limits, dict):
                return {
                    "rate_limits": rate_limits,
                    "timestamp": row.get("timestamp"),
                }
    return None


def _codex_window_key(window, fallback):
    """Map a Codex rate-limit window to ShellFrame's UI keys."""
    try:
        minutes = int(window.get("window_minutes"))
    except (AttributeError, TypeError, ValueError):
        return fallback
    if minutes <= 300:
        return "5hr"
    if minutes <= 10080:
        return "week"
    return None


def _fetch_codex_rollout(rollout_glob=None):
    """Normalize the current rollout JSONL rate-limit shape for the UI."""
    snapshot = _codex_rollout_snapshot(rollout_glob)
    if not snapshot:
        return None
    rate_limits = snapshot["rate_limits"]
    out = {}
    for name, fallback in (("primary", "5hr"), ("secondary", "week")):
        window = rate_limits.get(name)
        if not isinstance(window, dict):
            continue
        pct = window.get("used_percent", window.get("usedPercent"))
        if pct is None:
            continue
        key = _codex_window_key(window, fallback)
        if key is None:
            continue
        reset = window.get(
            "resets_at", window.get("reset_at", window.get("resetsAt"))
        )
        if isinstance(reset, (int, float)):
            reset_text = _fmt_epoch(reset)
        else:
            reset_text = _fmt_iso(reset or "")
        out[key] = (round(pct), reset_text)
    if not out:
        return None
    out["_plan"] = rate_limits.get("plan_type", rate_limits.get("planType"))
    timestamp = snapshot.get("timestamp")
    if timestamp:
        try:
            out["_ts"] = datetime.fromisoformat(
                timestamp.replace("Z", "+00:00")
            ).timestamp()
        except (AttributeError, TypeError, ValueError):
            pass
    return out


def _fetch_codex(home=None):
    """Return {'5hr': (pct, reset), 'week': (pct, reset), '_plan': ...} or None.

    Primary: read the latest snapshot from Codex rollout JSONL. Older Codex
    versions are supported through the local SQLite log, then the legacy
    openclaw codex_usage module.
    """
    rollout_glob = None
    log_glob = None
    if home:
        rollout_glob = os.path.join(home, "sessions", "*", "*", "*", "rollout-*.jsonl")
        log_glob = os.path.join(home, "logs_*.sqlite")
    rollout = _fetch_codex_rollout(rollout_glob)
    if rollout:
        return rollout

    snap = _codex_ratelimits_snapshot(log_glob)
    if snap:
        rl = snap.get("rate_limits") or {}
        out = {}
        primary = rl.get("primary") or {}
        secondary = rl.get("secondary") or {}
        if primary.get("used_percent") is not None:
            out["5hr"] = (round(primary["used_percent"]), _fmt_epoch(primary.get("reset_at")))
        if secondary.get("used_percent") is not None:
            out["week"] = (round(secondary["used_percent"]), _fmt_epoch(secondary.get("reset_at")))
        if out:
            out["_plan"] = snap.get("plan_type")
            # Snapshot time = reset_at - reset_after_seconds. The snapshot is only
            # as fresh as codex's last API turn, so surface when it was taken.
            ra, raf = primary.get("reset_at"), primary.get("reset_after_seconds")
            if ra and raf is not None:
                out["_ts"] = ra - raf
            return out
    live = _fetch_codex_app_server(home=home)
    if live:
        return live
    return _fetch_codex_openclaw()


def _fetch_codex_openclaw():
    """Legacy fallback: openclaw codex_usage app-server reader (openclaw host)."""
    import sys
    if not os.path.isdir(CODEX_SCRIPT_DIR):
        return None
    if CODEX_SCRIPT_DIR not in sys.path:
        sys.path.insert(0, CODEX_SCRIPT_DIR)
    try:
        import codex_usage
        result = codex_usage.get_codex_rate_limits()
    except Exception:
        return None
    if not result:
        return None
    rl = result.get("rateLimits", {}) or {}
    out = {}
    primary = rl.get("primary")
    secondary = rl.get("secondary")
    if primary:
        out["5hr"] = (primary.get("usedPercent", 0), _fmt_epoch(primary.get("resetsAt")))
    if secondary:
        out["week"] = (secondary.get("usedPercent", 0), _fmt_epoch(secondary.get("resetsAt")))
    if not out:
        return None
    out["_plan"] = rl.get("planType")
    return out


def _codex_app_server_binary() -> str | None:
    """Find the ShellFrame Codex launcher without hard-coding an install path."""
    return shutil.which("sf-codex") or shutil.which("codex")


def _read_jsonrpc_response(proc, response_id: int, timeout: float):
    """Read one JSON-RPC response from a long-running app-server process."""
    lines = queue.Queue()

    def _reader():
        try:
            for line in proc.stdout:
                lines.put(line)
        except Exception:
            pass

    import threading
    threading.Thread(target=_reader, daemon=True).start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            line = lines.get(timeout=max(0.05, min(0.25, deadline - time.time())))
        except queue.Empty:
            continue
        try:
            message = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if message.get("id") == response_id:
            return message
    return None


def _fetch_codex_app_server(home=None):
    """Fetch live Codex rate limits through the local app-server JSON-RPC API.

    This is read-only: account/rateLimits/read asks Codex for the current quota
    and does not start a model turn. It fills the gap before a new tab has a
    local rollout/SQLite snapshot.
    """
    binary = _codex_app_server_binary()
    if not binary:
        return None
    proc = None
    env = os.environ.copy()
    if home:
        env["CODEX_HOME"] = home
    try:
        proc = subprocess.Popen(
            [binary, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
        )
        requests = (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"clientInfo": {"name": "shellframe-usage-probe", "version": "0.1"}}},
            {"jsonrpc": "2.0", "method": "initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": {}},
        )
        for request in requests:
            proc.stdin.write(json.dumps(request) + "\n")
            proc.stdin.flush()
        response = _read_jsonrpc_response(proc, 2, CODEX_APP_SERVER_TIMEOUT)
        if not response:
            return None
        if response.get("error"):
            message = str((response.get("error") or {}).get("message") or "")
            if "401" in message or "token_invalidated" in message or "Unauthorized" in message:
                return {"_error": "auth_required", "_error_message": "Codex 登入已失效，請重新登入"}
            return {"_error": "app_server_error", "_error_message": "Codex 用量服務暫時無法查詢"}
        result = response.get("result") or {}
        rate_limits = result.get("rateLimits") or {}
        out = {}
        for source, target, fallback_minutes in (
            ("primary", "5hr", 300), ("secondary", "week", 10080)
        ):
            window = rate_limits.get(source)
            if not isinstance(window, dict):
                continue
            pct = window.get("usedPercent", window.get("used_percent"))
            if pct is None:
                continue
            minutes = window.get(
                "windowDurationMins", window.get("window_minutes", fallback_minutes)
            )
            key = _codex_window_key({"window_minutes": minutes}, target)
            if key is None:
                continue
            reset = window.get("resetsAt", window.get("resets_at"))
            reset_text = (
                _fmt_epoch(reset) if isinstance(reset, (int, float))
                else _fmt_iso(reset or "")
            )
            out[key] = (round(pct), reset_text)
        if not out:
            return None
        out["_plan"] = rate_limits.get("planType", rate_limits.get("plan_type"))
        return out
    except (OSError, subprocess.SubprocessError, BrokenPipeError):
        return None
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def probe_data(cmd: str, env=None) -> dict:
    """Structured usage water-level for the inline top-bar indicator.

    Same fetch path as probe(), but returns machine-readable fields the web UI
    can render as a compact pill (used %, reset time) instead of a text block.
    Percentages are *utilisation* (how much is used), matching the /usage modal.
    """
    ai = detect_ai(cmd)
    if ai is None:
        return {"ai": None, "error": "not_ai"}

    home = (env or {}).get("CODEX_HOME")
    if ai == "codex":
        data = _fetch_codex(home=home)
    else:
        data = _fetch_claude_profile(env) if env else _fetch_claude()
    account = (_codex_account(data.get("_plan") if data else None, home=home)
               if ai == "codex" else _claude_account())

    out = {"ai": ai, "account": account, "five_hr": None, "week": None,
           "snapshot": None, "error": None, "stale": False}
    if data and data.get("_error"):
        out["error"] = data["_error"]
        out["error_message"] = data.get("_error_message", "")
        return out
    if not data:
        out["error"] = "no_data"
        return out
    if data.get("_stale"):
        out["stale"] = True  # last good reading; this refresh failed (e.g. 429)
    if "5hr" in data:
        pct, reset = data["5hr"]
        out["five_hr"] = {"pct": pct, "reset": reset}
    if "week" in data:
        pct, reset = data["week"]
        out["week"] = {"pct": pct, "reset": reset}
    if data.get("_ts"):
        out["snapshot"] = _fmt_epoch(data["_ts"])
    return out


def probe_text(d: dict) -> str:
    """Render an already-fetched structured reading as the slash-command text."""
    ai = d.get("ai")
    if ai is None:
        return "此 tab 不是 claude / codex，無法查用量。"

    account = d.get("account")
    if d.get("error"):
        lines = [f"AI 水位 {ai}"]
        if account:
            lines.append(f"帳號 {account}")
        lines.append(d.get("error_message") or f"查不到資料（請確認已登入 {ai}）")
        return "\n".join(lines)

    lines = [f"AI 水位 {ai}"]
    if account:
        lines.append(f"帳號 {account}")
    if d.get("stale"):
        lines.append("⚠ 本次更新失敗，顯示上次資料")
    if d.get("five_hr"):
        fh = d["five_hr"]
        lines.append(f"1. 5hr：{fh['pct']}%｜重置 {fh['reset']}")
    if d.get("week"):
        wk = d["week"]
        lines.append(f"2. Week：{wk['pct']}%｜重置 {wk['reset']}")
    if d.get("snapshot"):
        lines.append(f"快照 {d['snapshot']}")
    return "\n".join(lines)


def probe(cmd: str, env=None) -> str:
    """Detect provider from cmd, fetch usage, return a friendly text block."""
    return probe_text(probe_data(cmd, env=env))
