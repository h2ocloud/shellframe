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
import json
import os
import subprocess
import time
from datetime import datetime

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


def _claude_account() -> str:
    """'howardwu@neux.com.tw · Team（企業·Neux Com）' or '' if unavailable.

    All claude tabs on this machine share the same ~/.claude credentials, so
    this reflects whatever account the current claude tab is signed in as.
    """
    email = org = None
    try:
        cj = json.load(open(os.path.expanduser("~/.claude.json")))
        oa = cj.get("oauthAccount", {}) or {}
        email = oa.get("emailAddress")
        org = oa.get("organizationName")
    except Exception:
        pass
    sub = None
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if raw:
            sub = (json.loads(raw).get("claudeAiOauth") or {}).get("subscriptionType")
    except Exception:
        pass
    plan = _plan_label(sub, org)
    parts = [p for p in (email, plan) if p]
    return " · ".join(parts)


def _codex_account(result_plan=None) -> str:
    """'neux.ios@neux.com.tw · Pro（個人）' or '' if unavailable.

    Reads ~/.codex/auth.json — the account sf-codex actually runs as.
    """
    email = plan = None
    try:
        d = json.load(open(os.path.expanduser("~/.codex/auth.json")))
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


def _fetch_claude():
    """Return {'5hr': (pct, reset), 'week': (pct, reset)} or None."""
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


def _fetch_codex():
    """Return {'5hr': (pct, reset), 'week': (pct, reset)} or None."""
    import sys
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


def probe(cmd: str) -> str:
    """Detect provider from cmd, fetch usage, return a friendly text block."""
    ai = detect_ai(cmd)
    if ai is None:
        return "此 tab 不是 claude / codex，無法查用量。"

    data = _fetch_codex() if ai == "codex" else _fetch_claude()

    if ai == "codex":
        account = _codex_account(data.get("_plan") if data else None)
    else:
        account = _claude_account()

    if not data:
        lines = [f"AI 水位 {ai}"]
        if account:
            lines.append(f"帳號 {account}")
        lines.append(f"查不到資料（請確認已登入 {ai}）")
        return "\n".join(lines)

    lines = [f"AI 水位 {ai}"]
    if account:
        lines.append(f"帳號 {account}")
    if "5hr" in data:
        pct, reset = data["5hr"]
        lines.append(f"1. 5hr：{pct}%｜重置 {reset}")
    if "week" in data:
        pct, reset = data["week"]
        lines.append(f"2. Week：{pct}%｜重置 {reset}")
    return "\n".join(lines)
