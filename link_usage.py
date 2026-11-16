"""What a paired phone shows under Settings → 用量與配速: every signed-in AI account's allowance,
with the pace already worked out (GET /link/usage, frame_link.py; the command `usage_accounts`).

The desktop reads the numbers (usage_probe, through Api.account_usage_all, the AI accounts panel's
source); this module only turns them into what a phone draws, so the phone does no arithmetic of its
own and two phones never disagree:
  - per window (five_hr, week, and a provider's extra groups): used %, when it resets, and, when
    the reading carries its window (reset instant and length), `pace`: how much of the window has
    gone by, in %, the share you could have used by now at an even rate; `ahead`: used minus pace
    (positive: faster than the pace); `full_at`: when, at the rate so far, it reaches 100 %, given
    only when that is before the reset
  - an account it cannot read says why (`error`: auth_required, rate_limited, no_data …), never a guess
  - a stale reading (the last good one; the refresh failed) is marked stale

`refresh=1` re-reads every account, at most once a minute from phones (the providers rate-limit).

Leaf module: stdlib and usage_probe only, no `import main`.
"""

import time

import usage_probe

REFRESH_FLOOR_S = 60.0
_last_refresh = {"at": 0.0}


def refresh_allowed(now: float = None) -> bool:
    """A phone's refresh re-reads every account at most once a minute; otherwise the cached readings."""
    now = time.time() if now is None else now
    if now - _last_refresh["at"] < REFRESH_FLOOR_S:
        return False
    _last_refresh["at"] = now
    return True


def window(w: dict, now: float) -> dict:
    """One allowance window, with its pace when the reading says where the window is."""
    if not isinstance(w, dict) or w.get("pct") is None:
        return None
    out = {"pct": w.get("pct"), "reset": w.get("reset") or ""}
    epoch, minutes = w.get("reset_epoch"), w.get("window_minutes")
    if epoch:
        out["reset_epoch"] = epoch
    if epoch and minutes:
        span = float(minutes) * 60.0
        gone = span - (float(epoch) - now)
        pace = max(0.0, min(100.0, 100.0 * gone / span)) if span > 0 else None
        if pace is not None:
            out["pace"] = round(pace, 1)
            out["ahead"] = round(float(out["pct"]) - pace, 1)
            if gone > 60 and 0 < float(out["pct"]) < 100:
                rate = float(out["pct"]) / gone                     # % per second so far
                full = now + (100.0 - float(out["pct"])) / rate
                if full < float(epoch):
                    out["full_at"] = int(full)
    return out


def phone_accounts(providers: dict, now: float = None) -> list:
    """`providers` as Api.account_usage_all returns it ({provider: {ref: reading}}) → one entry per account."""
    now = time.time() if now is None else now
    out = []
    for provider, accounts in (providers or {}).items():
        label = (usage_probe.PROVIDER_SPECS.get(provider) or {}).get("label") or provider
        for ref, d in (accounts or {}).items():
            d = d if isinstance(d, dict) else {}
            entry = {"provider": provider, "label": label, "ref": ref, "account": d.get("account") or "",
                     "current": bool(d.get("is_current_login")), "stale": bool(d.get("stale")),
                     "checked": d.get("checked") or d.get("snapshot") or ""}
            if d.get("error"):
                entry["error"] = d["error"]
                entry["error_message"] = d.get("error_message") or ""
            for key in ("five_hr", "week"):
                w = window(d.get(key), now)
                if w:
                    entry[key] = w
            groups = [{"name": g.get("name") or "", "pct": g.get("pct"), "reset": g.get("reset") or ""}
                      for g in d.get("groups") or [] if g.get("pct") is not None]
            if groups:
                entry["groups"] = groups
            out.append(entry)
    # The one to look at first: the furthest ahead of its pace, then the most used.
    def urgency(e):
        ws = [e.get("five_hr") or {}, e.get("week") or {}]
        ahead = [w["ahead"] for w in ws if w.get("ahead") is not None]
        return (-(max(ahead) if ahead else -999.0), -max((w.get("pct") or 0) for w in ws))
    out.sort(key=urgency)
    return out
