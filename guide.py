"""The guide: one entry per feature, for a first-run tour and a help list.

guide.json is the single source. Each feature names where it is on screen by its id: the
desktop page marks that element with `data-guide="<id>"` (web/guide.js finds it), and the
phone app will mark its view with the same id. tests_guide.py binds the two: a feature
without its mark, a mark without a feature, an `experimental_*` switch without an entry
(or a reason in `no_guide`), and a release with an `### Added` section but no entry of
its version (or a reason in `added_without_guide`) all fail. So adding a feature means
writing its entry.

Who sees the tour: a first launch has no config.json and gets config["guide"]["tour"]
"pending" from DEFAULT_CONFIG; it is shown once, then "done". A computer that already ran
ShellBot keeps its routine (keep_old_install: "skip", and everything up to this version
counts as seen). What is new: entries whose `since` is newer than config["guide"]["seen"].
It is config["guide"], not settings: the page saves `settings` whole from its own copy
(save_settings), which would put "pending" back after the tour.

Leaf module: stdlib only, no `import main`.
"""

import json
import re
from pathlib import Path

GUIDE_FILE = Path(__file__).resolve().with_name("guide.json")
VERSION_FILE = Path(__file__).resolve().with_name("version.json")
SURFACES = ("desktop", "ios")
TOUR_STATES = ("pending", "done", "skip")

_cache = {"mtime": None, "data": None}


def load(path: Path = None) -> dict:
    """guide.json, read again only when it changed on disk."""
    p = Path(path) if path else GUIDE_FILE
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {"features": [], "tours": {}}
    if path is None and _cache["mtime"] == mtime and _cache["data"] is not None:
        return _cache["data"]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"features": [], "tours": {}}
    if path is None:
        _cache.update(mtime=mtime, data=data)
    return data


def app_version() -> str:
    try:
        return str(json.loads(VERSION_FILE.read_text(encoding="utf-8")).get("version") or "")
    except (OSError, ValueError):
        return ""


def vtuple(v) -> tuple:
    """'0.44.6' -> (0, 44, 6); anything unreadable sorts first."""
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:3]) or (0,)


def newer(a, b) -> bool:
    return vtuple(a) > vtuple(b)


def for_surface(surface: str, settings: dict = None, data: dict = None) -> list:
    """The features `surface` shows, each with `on`: False when it needs a setting that is
    off (the entry still shows, so the person learns it exists and where to turn it on)."""
    data = data if data is not None else load()
    st = settings if isinstance(settings, dict) else {}
    out = []
    for f in data.get("features") or []:
        if surface not in (f.get("surfaces") or []):
            continue
        e = dict(f)
        e["on"] = bool(st.get(f["flag"])) if f.get("flag") else True
        out.append(e)
    return out


def _g(cfg: dict) -> dict:
    g = cfg.get("guide") if isinstance(cfg, dict) else None
    return g if isinstance(g, dict) else {}


def state(cfg: dict, version: str = None) -> dict:
    """{"tour", "seen", "version"}: whether the first-run tour is still due, and the version
    whose features the person has seen."""
    g = _g(cfg)
    tour = g.get("tour")
    return {"tour": tour if tour in TOUR_STATES else "skip",
            "seen": str(g.get("seen") or ""),
            "version": version if version is not None else app_version()}


def keep_old_install(cfg: dict, version: str = None) -> bool:
    """config["guide"] for a config.json that exists but predates the guide: this computer
    already used ShellBot, so no tour pops up, and what it has now counts as seen. A first
    launch has no config.json and gets DEFAULT_CONFIG's "pending". True when written."""
    if _g(cfg).get("tour") in TOUR_STATES:
        return False
    cfg["guide"] = {"tour": "skip", "seen": version if version is not None else app_version()}
    return True


def mark(cfg: dict, tour: str = "", seen: str = "") -> bool:
    """Record that the tour ended (`tour`: "done") and/or that the features of version `seen`
    were looked at. Never goes back to an older `seen`. True when something changed."""
    g = cfg.get("guide")
    if not isinstance(g, dict):
        g = cfg["guide"] = {}
    changed = False
    if tour in TOUR_STATES and g.get("tour") != tour:
        g["tour"] = tour
        changed = True
    if seen and newer(seen, g.get("seen") or ""):
        g["seen"] = seen
        changed = True
    return changed
