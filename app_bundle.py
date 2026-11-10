"""The app as the system shows it: the macOS bundle installed as ShellBot.app, and the
Windows desktop shortcut named ShellBot.lnk. Both were ShellFrame before.

Finder, Launchpad, Spotlight, the Dock and ⌘-Tab name a Mac app by its bundle's file name
(measured: Info.plist's names change nothing there), so the bundle is installed as
ShellBot.app. Nothing inside it changes: the same bundle id and, a rename keeping the
bundle's bytes, the same cdhash (measured), so the permissions macOS keeps for it
(Accessibility for the hotkey, the microphone, Downloads) carry over.

- The repo's template keeps the name ShellFrame.app: an older build's update copies
  APP_DIR/ShellFrame.app, and would skip a renamed one; the next start would then fall
  back to the template, whose executable is the shell payload, not the launcher.
- Everything that looks for the installed app asks find(): either name, the new first.
- migrate() renames what an older build installed, once: at start, after the
  single-instance guard, and before `sfctl restart` relaunches. LaunchServices keeps the
  old path until told (measured), and the Dock keeps a pin's old URL and label (measured;
  it reads them only at launch, so it is restarted after they are rewritten).
- refresh() is the update's copy of the template, now to ShellBot.app.
- pin_dock() is the installer's pin of ShellBot, first in the Dock. install.sh used to write
  the Dock's plist file and restart the Dock; the prefs daemon holds that domain, and a Dock
  with changes it has not saved writes them back as it quits, so the pin could be lost with
  no word said. It goes through `defaults` and is read back, as migrate()'s re-pin is.

The old path is not kept as a symlink: LaunchServices and Spotlight ignore one, so it
would only show in Finder as a second "ShellFrame" (measured); `open -a ShellFrame`
stops working either way (measured).
"""

import os
import plistlib
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

NAME = "ShellBot.app"
OLD_NAME = "ShellFrame.app"
TEMPLATE = "ShellFrame.app"      # the repo's and install dir's copy: never renamed (see above)
BUNDLE_ID = "com.h2ocloud.shellframe"
LABEL = "ShellBot"
OLD_LABEL = "ShellFrame"
LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/"
              "LaunchServices.framework/Support/lsregister")
SHORTCUT, OLD_SHORTCUT = "ShellBot.lnk", "ShellFrame.lnk"
DOCK_SETTLE_S = 3               # the Dock is started again at once; this long to read its pins


def app_dirs() -> list:
    return [Path("/Applications"), Path.home() / "Applications"]


def candidates(dirs=None) -> list:
    """Where an installed bundle can be, in the order to try: each folder's new name, then
    its old one."""
    return [Path(d) / n for d in (dirs or app_dirs()) for n in (NAME, OLD_NAME)]


def find(dirs=None, template_dir=None):
    """The installed app with symlinks resolved; else the template in `template_dir`;
    else None."""
    extra = [Path(template_dir) / TEMPLATE] if template_dir else []
    for c in candidates(dirs) + extra:
        try:
            if c.is_dir():
                return c.resolve()
        except OSError:
            pass
    return None


def bundle_id(app) -> str:
    try:
        with open(Path(app) / "Contents" / "Info.plist", "rb") as f:
            return str(plistlib.load(f).get("CFBundleIdentifier") or "")
    except Exception:
        return ""


def _ours(app) -> bool:
    """A real folder (not a symlink) holding this app."""
    return Path(app).is_dir() and not Path(app).is_symlink() and bundle_id(app) == BUNDLE_ID


def _register(moved, run):
    for old, new in moved:
        run([LSREGISTER, "-u", str(old)], capture_output=True, timeout=20)
        run([LSREGISTER, "-f", str(new)], capture_output=True, timeout=20)


def migrate(dirs=None, run=subprocess.run, dock=True, log=lambda m: None) -> list:
    """Rename an older build's ShellFrame.app to ShellBot.app. Only when the old one is a
    real folder holding this app and the new name is free; otherwise nothing moves and the
    reason is logged: a symlink or another app is not ours to move, two copies wait for the
    next update (refresh), and a folder that cannot be written keeps its name. `dock`: True
    re-pins the Dock here, a callable is handed that job instead (the app's start runs it
    in the background: it can take seconds), False leaves the Dock. Returns the new paths."""
    if sys.platform != "darwin" and dirs is None:
        return []
    moved = []
    for d in dirs or app_dirs():
        old, new = Path(d) / OLD_NAME, Path(d) / NAME
        if not old.exists() or old.is_symlink():
            continue
        if not _ours(old):
            log(f"{old} is not this app ({bundle_id(old)!r}): left alone")
            continue
        if new.exists():
            log(f"{old} and {new} are both there: the next update keeps one")
            continue
        try:
            os.rename(old, new)          # the same folder: the same inode, the same bundle
        except OSError as e:
            log(f"{old} could not be renamed ({e}): it keeps its name")
            continue
        log(f"renamed {old} -> {new}")
        moved.append((old, new))
    _register(moved, run)
    if moved and dock:
        job = lambda: repin_dock(moved, run=run, log=log)  # noqa: E731
        dock(job) if callable(dock) else job()
    return [new for _, new in moved]


def _pin_url(tile: dict) -> str:
    """The URL a Dock pin's tile points at, or "" (the tile's file-data may be missing)."""
    data = tile.get("file-data")
    return str(data.get("_CFURLString") or "") if isinstance(data, dict) else ""


def dock_repin(apps: list, moved) -> bool:
    """The Dock's persistent-apps with each pin of a moved bundle pointing at its new path
    and labelled ShellBot (pure; changes `apps` in place; True when anything changed). A
    pin is the bundle's by its old URL, or its new one with the old label (the Dock may
    follow the move itself and keep the label). A rewritten pin loses its bookmark: the Dock
    makes a new one from the URL. Other pins, and the order, stay."""
    urls = {}
    for old, new in moved:
        urls[Path(old).as_uri().rstrip("/")] = Path(new)
        urls[Path(new).as_uri().rstrip("/")] = Path(new)
    changed = False
    for item in apps:
        tile = item.get("tile-data") if isinstance(item, dict) else None
        if not isinstance(tile, dict):
            continue
        url = _pin_url(tile).rstrip("/")
        new = urls.get(url)
        if new is None or (url == new.as_uri().rstrip("/") and tile.get("file-label") == LABEL):
            continue
        tile["file-data"] = {"_CFURLString": new.as_uri() + "/", "_CFURLStringType": 15}
        tile["file-label"] = LABEL
        tile.pop("book", None)
        changed = True
    return changed


def _ours_pin(item) -> bool:
    """A Dock pin of this app, made under either name: by its label, its bundle id, or a URL
    naming either bundle (pure; an entry that is not a pin is not one of ours)."""
    tile = item.get("tile-data") if isinstance(item, dict) else None
    if not isinstance(tile, dict):
        return False
    url = _pin_url(tile)
    return (tile.get("file-label") in (LABEL, OLD_LABEL) or tile.get("bundle-identifier") == BUNDLE_ID
            or NAME in url or OLD_NAME in url)


def dock_pin(apps: list, app) -> bool:
    """The Dock's persistent-apps with this app's pin first (no I/O; changes `apps` in place;
    True when anything changed). Every pin of the app goes, whatever it was made under
    (_ours_pin), and a new one for `app` comes first; other pins and their order stay. A pin
    that is first and right is left alone, and only its tile type, label, bundle id and URL
    say so: the Dock adds a bookmark, a GUID and dates once it has loaded one, and the read-back
    must agree with what was written."""
    url = Path(app).as_uri() + "/"
    ours = [i for i, item in enumerate(apps) if _ours_pin(item)]
    if ours == [0]:
        tile = apps[0]["tile-data"]
        if (apps[0].get("tile-type") == "file-tile" and tile.get("file-label") == LABEL
                and tile.get("bundle-identifier") == BUNDLE_ID and _pin_url(tile).rstrip("/") == url.rstrip("/")):
            return False
    pin = {"tile-data": {"bundle-identifier": BUNDLE_ID, "dock-extra": 0,
                         "file-data": {"_CFURLString": url, "_CFURLStringType": 15},
                         "file-label": LABEL, "file-mod-date": int(time.time()), "file-type": 41},
           "tile-type": "file-tile"}
    apps[:] = [pin] + [item for i, item in enumerate(apps) if i not in ours]
    return True


def _edit_dock(change, run, log, pause, done, kept) -> bool:
    """Change the Dock's prefs through `defaults` (the prefs daemon, not the file) and restart
    the Dock: it reads its pins only at launch. `change(data)` edits the exported domain in
    place and says whether it changed anything. A Dock holding changes of its own it has not
    saved yet writes them over ours as it quits (measured: a pin made a moment before), so what
    it loaded is read back, and the second try stops it without letting it save. `done` is
    logged once the change is read back, `kept` when the Dock has undone it three times. True
    when the change was made and the Dock kept it; False when there was nothing to change, or
    it could not be made."""
    for attempt in range(3):
        try:
            r = run(["defaults", "export", "com.apple.dock", "-"], capture_output=True, timeout=10)
            if r.returncode != 0:
                return False
            data = plistlib.loads(r.stdout)
            changed = change(data)
        except Exception as e:
            log(f"the Dock's pins could not be read: {e}")
            return False
        if not changed:
            if attempt:
                log(done + ("" if attempt == 1 else " (the Dock had written its own over the first try)"))
            return attempt > 0
        if attempt == 2:
            log(kept)
            return False
        try:
            run(["defaults", "import", "com.apple.dock", "-"], input=plistlib.dumps(data),
                capture_output=True, timeout=10)
            run(["killall", "Dock"] if attempt == 0 else ["killall", "-KILL", "Dock"],
                capture_output=True, timeout=10)
        except Exception as e:
            log(f"the Dock's pin was not changed: {e}")
            return False
        pause(DOCK_SETTLE_S)
    return False


def repin_dock(moved, run=subprocess.run, log=lambda m: None, pause=time.sleep) -> bool:
    """Point the Dock's pins of moved bundles at their new paths (dock_repin), through
    _edit_dock: `defaults`, a restart, a read-back and a second try that stops the Dock unsaved."""
    return _edit_dock(lambda data: dock_repin(data.get("persistent-apps") or [], moved), run, log, pause,
                      done="the Dock's pin now points at " + ", ".join(str(n) for _, n in moved),
                      kept="the Dock kept its old pin; it can be pinned again by hand")


def pin_dock(app_path, run=subprocess.run, log=lambda m: None, pause=time.sleep) -> bool:
    """Pin the app first in the Dock, as the installer does (dock_pin), through _edit_dock:
    the same `defaults` import, restart, read-back and second try as repin_dock. Nothing is
    changed, and the Dock is not restarted, when its pin is already first and right. True when
    this call pinned it and the Dock kept it; False when there was nothing to do, or it could
    not be done (said through `log`)."""
    app = Path(app_path)

    def change(data):
        if not isinstance(data.get("persistent-apps"), list):
            data["persistent-apps"] = []        # a Dock with none: the pin still lands in `data`
        return dock_pin(data["persistent-apps"], app)

    return _edit_dock(change, run, log, pause, done=f"{LABEL} is pinned first in the Dock",
                      kept=f"the Dock did not keep {LABEL}'s pin; it can be pinned again by hand")


def refresh(template, dirs=None, run=subprocess.run, prepare=None, dock=True) -> list:
    """The update's copy of the template, to the first folder there is: as ShellBot.app,
    or in place under the old name when the folder cannot be written but that copy can.
    An old-named copy of this app beside the new one goes (its Dock pin re-pointed), and so
    do this app's copies in the later folders. `prepare(dest)` -> (ok, message) builds the
    launcher. Returns lines for the update's report."""
    steps = []
    template = Path(template)
    if not template.is_dir():
        return steps
    dirs = [Path(d) for d in (dirs or app_dirs())]
    for i, d in enumerate(dirs):
        if not d.is_dir():
            continue
        new, old = d / NAME, d / OLD_NAME
        dest = new if os.access(d, os.W_OK) else (old if _ours(old) and os.access(old, os.W_OK) else None)
        if dest is None:
            steps.append(f".app not copied: {d} cannot be written")
            continue
        shutil.rmtree(dest, ignore_errors=True)
        # A folder that cannot be written keeps an emptied copy: fill it, or `cp` would nest
        # the template inside it.
        src = str(template) + ("/" if dest.exists() else "")
        r = run(["cp", "-R", src, str(dest)], capture_output=True, timeout=30)
        if getattr(r, "returncode", 0) != 0 or not dest.is_dir():
            steps.append(f".app copy to {dest} failed: {(getattr(r, 'stderr', b'') or b'')[:200]!r}")
            continue
        if prepare is not None:
            ok, msg = prepare(dest)
            steps.append(f".app launcher: {msg}")
        steps.append(f".app copied to {dest}")
        moved = []
        if dest == new and _ours(old):
            shutil.rmtree(old, ignore_errors=True)
            moved.append((old, new))
            steps.append(f"removed the old-named copy: {old}")
        for later in dirs[i + 1:]:
            for c in (later / NAME, later / OLD_NAME):
                if _ours(c):
                    shutil.rmtree(c, ignore_errors=True)
                    moved.append((c, dest))
                    steps.append(f"removed stale app copy: {c}")
        _register(moved, run)
        run([LSREGISTER, "-f", str(dest)], capture_output=True, timeout=20)
        if moved and dock:
            repin_dock(moved, run=run)
        break
    return steps


# ── Windows: the desktop shortcut ─────────────────────────────────────────
def windows_desktop():
    """The Desktop folder as Windows has it (OneDrive may have moved it): the shell's
    known folder, as install.ps1's [Environment]::GetFolderPath("Desktop")."""
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        g = uuid.UUID("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}")          # FOLDERID_Desktop
        guid = GUID(g.fields[0], g.fields[1], g.fields[2], (ctypes.c_ubyte * 8)(*g.bytes[8:]))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) != 0:
            return None
        try:
            return Path(out.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)
    except Exception:
        return None


def rename_desktop_shortcut(desktop=None, log=lambda m: None) -> bool:
    """An older install's desktop shortcut, ShellFrame.lnk, renamed ShellBot.lnk (what the
    desktop shows is the file's name). Left alone when ShellBot.lnk is already there. A
    copy pinned to the taskbar is another file and keeps its name."""
    desktop = Path(desktop) if desktop else windows_desktop()
    if not desktop:
        return False
    old, new = desktop / OLD_SHORTCUT, desktop / SHORTCUT
    if not old.is_file() or new.exists():
        return False
    try:
        os.rename(old, new)
    except OSError as e:
        log(f"{old} could not be renamed: {e}")
        return False
    log(f"renamed {old} -> {new}")
    return True


def isolated() -> str:
    """Why this process must leave the installed app alone, or "": a test run, or an
    instance started with a config folder or HOME of its own (a second backend for testing
    beside the app in use). /Applications is the same for it, and renaming there, or
    restarting the Dock, would act on the app in use."""
    if os.environ.get("SHELLFRAME_TESTS"):
        return "a test run"
    if os.environ.get("SHELLFRAME_CONFIG_DIR"):
        return "a config folder of its own"
    try:
        import pwd
        if os.path.realpath(os.path.expanduser("~")) != os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir):
            return "a HOME of its own"
    except Exception:
        pass
    return ""


def at_start(log=lambda m: None):
    """What a start does, after the single-instance guard: the Mac bundle's new name, or
    the Windows shortcut's. Not in an isolated instance (isolated())."""
    why = isolated()
    if why:
        log(f"the app's name is left alone: {why}")
        return
    try:
        if sys.platform == "darwin":
            migrate(log=log, dock=lambda job: threading.Thread(target=job, name="app-rename-dock", daemon=True).start())
        elif os.name == "nt":
            rename_desktop_shortcut(log=log)
    except Exception as e:
        log(f"the app's new name was not applied: {e}")
