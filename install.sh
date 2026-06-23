#!/bin/bash
set -euo pipefail

# ShellFrame installer
# Usage: curl -fsSL https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh | bash

INSTALL_DIR="${HOME}/.local/apps/shellframe"
BIN_DIR="${HOME}/.local/bin"

echo "Installing ShellFrame..."

# ── Helper ──────────────────────────────────────────────────
# Ensure Homebrew exists on macOS; auto-install non-interactively if missing.
ensure_homebrew() {
  if command -v brew &>/dev/null; then return 0; fi
  echo "  Homebrew not found — installing (non-interactive)..."
  NONINTERACTIVE=1 /bin/bash -c \
    "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || true
  # Surface brew in this session for both Apple Silicon and Intel prefixes
  for brew_bin in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$brew_bin" ] && eval "$("$brew_bin" shellenv)" && break
  done
  if ! command -v brew &>/dev/null; then
    echo "  Error: Homebrew install failed (it may need an interactive sudo password)."
    echo "  Install it manually in a normal terminal, then re-run this installer:"
    echo "    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    exit 1
  fi
}

install_if_missing() {
  local cmd="$1" pkg_brew="$2" pkg_apt="${3:-$2}" pkg_dnf="${4:-$2}"
  if command -v "$cmd" &>/dev/null; then return 0; fi
  echo "  Installing $cmd..."
  if [ "$(uname)" = "Darwin" ]; then
    ensure_homebrew
    brew install -q "$pkg_brew"
  elif command -v apt-get &>/dev/null; then
    sudo apt-get update -q && sudo apt-get install -y -q $pkg_apt
  elif command -v dnf &>/dev/null; then
    sudo dnf install -y $pkg_dnf
  elif command -v pacman &>/dev/null; then
    sudo pacman -S --noconfirm "$cmd"
  else
    echo "  Error: Could not install $cmd. Install it manually and re-run."
    exit 1
  fi
}

# ── 1. System dependencies ──────────────────────────────────
echo "Checking dependencies..."

# git
if ! command -v git &>/dev/null; then
  echo "Error: git is required."
  [ "$(uname)" = "Darwin" ] && echo "  Run: xcode-select --install" || echo "  Run: sudo apt install git"
  exit 1
fi

# Python 3
if ! command -v python3 &>/dev/null; then
  if [ "$(uname)" = "Darwin" ]; then
    install_if_missing python3 python@3.12
  else
    install_if_missing python3 python@3.12 "python3 python3-venv" python3
  fi
fi

# tmux (session persistence — sessions survive ShellFrame restart)
install_if_missing tmux tmux tmux tmux

# ── 2. Clone or update ──────────────────────────────────────
REPO_URL="https://github.com/h2ocloud/shellframe.git"
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "Updating existing installation..."
  cd "$INSTALL_DIR"
  # Auto-stash local changes so pull never blocks
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    git stash push -u -m "install.sh-auto-$(date +%s)" >/dev/null 2>&1 || true
  fi
  # Try ff-only pull, fall back to force-sync if history diverged
  if ! git pull --ff-only 2>/dev/null; then
    echo "  ff-only pull failed — force-syncing to origin/main"
    git fetch origin main && git reset --hard origin/main
  fi
elif [ -d "$INSTALL_DIR" ] && [ "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
  # Directory exists with files but no .git — user downloaded a zip or copied a
  # previous agent-built tree. Do not `git init` in place: untracked app bundles,
  # generated launchers, and stale scripts survive reset and produce mixed
  # installs. Keep a timestamped backup, then clone a clean tree.
  BACKUP_DIR="${INSTALL_DIR}.non-git-backup.$(date +%Y%m%d%H%M%S)"
  echo "Backing up non-git install to $BACKUP_DIR"
  mv "$INSTALL_DIR" "$BACKUP_DIR"
  echo "Cloning repository..."
  git clone "$REPO_URL" "$INSTALL_DIR"
else
  echo "Cloning repository..."
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ── 3. Python venv + pip dependencies ───────────────────────
# On macOS we MUST avoid Apple's bundled Python (Xcode CLT, /usr/bin/python3,
# /Library/Frameworks/Python.framework). At runtime that interpreter rewraps
# itself into Python.app, so the live process registers with LaunchServices
# as bundleID com.apple.python3 instead of com.h2ocloud.shellframe — TCC
# scopes Accessibility per code identity, so NSEvent.addGlobalMonitor
# silently returns nil and the ⌃⌥Space hotkey dies in the background.
# Homebrew's python ships as a plain framework binary that doesn't self-wrap,
# so the python process inherits ShellFrame.app's bundle identity from the
# .app launcher and TCC behaves.
PYTHON_FOR_VENV=""
if [ "$(uname)" = "Darwin" ]; then
  for candidate in \
      /opt/homebrew/bin/python3 \
      /usr/local/bin/python3 ; do
    if [ -x "$candidate" ]; then
      real="$(readlink -f "$candidate" 2>/dev/null || /usr/bin/python3 -c "import os,sys;print(os.path.realpath(sys.argv[1]))" "$candidate")"
      case "$real" in
        */Xcode.app/*|/usr/bin/python3|/Library/Frameworks/Python.framework/*) continue ;;
      esac
      PYTHON_FOR_VENV="$candidate"
      break
    fi
  done
  if [ -z "$PYTHON_FOR_VENV" ]; then
    echo "Warning: no non-Apple python3 found. Installing Homebrew python..."
    install_if_missing python3.14 python@3.14 || true
    if [ -x /opt/homebrew/bin/python3 ]; then
      PYTHON_FOR_VENV=/opt/homebrew/bin/python3
    fi
  fi
fi
[ -z "$PYTHON_FOR_VENV" ] && PYTHON_FOR_VENV="python3"

# If an existing .venv was built against Apple's Python (the Xcode/Framework
# self-wrapping kind), rebuild it so the global hotkey actually works.
if [ -d ".venv" ] && [ "$(uname)" = "Darwin" ]; then
  current_real="$(.venv/bin/python -c 'import os,sys;print(os.path.realpath(sys.executable))' 2>/dev/null || true)"
  case "$current_real" in
    */Xcode.app/*|/Library/Frameworks/Python.framework/*|/System/Library/Frameworks/Python.framework/*)
      echo "Existing .venv was built against Apple Python — rebuilding for TCC sanity..."
      mv .venv ".venv.applepython-bak.$(date +%s)" 2>/dev/null || rm -rf .venv
      ;;
  esac
fi

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment with $PYTHON_FOR_VENV..."
  "$PYTHON_FOR_VENV" -m venv .venv
fi
echo "Installing Python dependencies..."
.venv/bin/pip install -q -r requirements.txt

# ── 4. CLI launchers ────────────────────────────────────────
mkdir -p "$BIN_DIR"

# shellframe — main GUI app
cat > "$BIN_DIR/shellframe" << 'LAUNCHER'
#!/bin/bash
if [ -f "$HOME/.zprofile" ]; then source "$HOME/.zprofile" 2>/dev/null; fi
if [ -f "$HOME/.zshrc" ]; then source "$HOME/.zshrc" 2>/dev/null; fi
cd ~/.local/apps/shellframe
PY_LAUNCHER=".venv/bin/python"
if [ "$(uname)" = "Darwin" ]; then
  REAL_PY="$("$PY_LAUNCHER" -c 'import os,sys; print(os.path.realpath(sys.executable))' 2>/dev/null || true)"
  case "$REAL_PY" in
    */Frameworks/Python.framework/Versions/*/bin/python*)
      PY_APP="${REAL_PY%/bin/python*}/Resources/Python.app/Contents/MacOS/Python"
      if [ -x "$PY_APP" ]; then
        ln -sf "$PY_APP" ".venv/bin/ShellFrame"
        PY_LAUNCHER=".venv/bin/ShellFrame"
      fi
      ;;
  esac
fi
# Force arm64 on Apple Silicon so the python child doesn't inherit a
# Rosetta parent shell — see ShellFrame.app launcher for full rationale.
if [[ "$(sysctl -in hw.optional.arm64 2>/dev/null)" == "1" ]]; then
  exec arch -arm64 "$PY_LAUNCHER" main.py "$@"
fi
exec "$PY_LAUNCHER" main.py "$@"
LAUNCHER
chmod +x "$BIN_DIR/shellframe"

# sfctl — remote control for AI agents
cat > "$BIN_DIR/sfctl" << 'SFCTL'
#!/bin/bash
exec ~/.local/apps/shellframe/.venv/bin/python ~/.local/apps/shellframe/sfctl.py "$@"
SFCTL
chmod +x "$BIN_DIR/sfctl"

# ── 5. macOS .app (Spotlight + Launchpad + Finder) ──────────
if [ "$(uname)" = "Darwin" ]; then
  # Prefer /Applications (Launchpad visible), fall back to ~/Applications
  if [ -w /Applications ] || [ -w /Applications/ShellFrame.app ]; then
    APP_DEST="/Applications/ShellFrame.app"
  else
    APP_DEST="${HOME}/Applications/ShellFrame.app"
    mkdir -p ~/Applications
  fi

  # Copy .app bundle (not symlink — Spotlight/Launchpad ignore symlinks to dot-folders)
  rm -rf "$APP_DEST"
  cp -R "$INSTALL_DIR/ShellFrame.app" "$APP_DEST"

  mkdir -p "$APP_DEST/Contents/Resources"

  # The app template historically kept the shell payload at
  # Contents/MacOS/shellframe. For LaunchServices/Dock identity, the executable
  # must be a real Mach-O process that stays alive while Python runs as a child.
  if [ ! -f "$APP_DEST/Contents/Resources/shellframe.sh" ]; then
    cp "$INSTALL_DIR/ShellFrame.app/Contents/MacOS/shellframe" "$APP_DEST/Contents/Resources/shellframe.sh"
  fi
  if [ -f "$APP_DEST/Contents/MacOS/shellframe.sh" ]; then
    mv "$APP_DEST/Contents/MacOS/shellframe.sh" "$APP_DEST/Contents/Resources/shellframe.sh"
  fi
  if [ -f "$INSTALL_DIR/scripts/macos_app_launcher.c" ] && command -v clang >/dev/null 2>&1; then
    LAUNCHER_ARCH_FLAGS=()
    case "$(uname -m)" in
      arm64) LAUNCHER_ARCH_FLAGS=(-arch arm64) ;;
      x86_64) LAUNCHER_ARCH_FLAGS=(-arch x86_64) ;;
    esac
    clang "${LAUNCHER_ARCH_FLAGS[@]}" -mmacosx-version-min=12.0 \
      "$INSTALL_DIR/scripts/macos_app_launcher.c" \
      -o "$APP_DEST/Contents/MacOS/shellframe"
  else
    echo "  Warning: clang not found; ShellFrame.app will use the shell launcher fallback."
  fi
  chmod 755 "$APP_DEST/Contents/MacOS/shellframe" 2>/dev/null || true
  chmod 644 "$APP_DEST/Contents/Resources/shellframe.sh" 2>/dev/null || true
  xattr -cr "$APP_DEST" 2>/dev/null || true

  # Stamp Info.plist with current version from version.json
  CURRENT_VER=$(python3 -c "import json; print(json.load(open('$INSTALL_DIR/version.json'))['version'])" 2>/dev/null || echo "0.0.0")
  PLIST="$APP_DEST/Contents/Info.plist"
  if [ -f "$PLIST" ]; then
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $CURRENT_VER" "$PLIST" 2>/dev/null || true
    /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $CURRENT_VER" "$PLIST" 2>/dev/null || true
  fi

  # Clean up old ~/Applications copy if we migrated to /Applications
  [ "$APP_DEST" = "/Applications/ShellFrame.app" ] && rm -rf "${HOME}/Applications/ShellFrame.app" 2>/dev/null

  # Ad-hoc code sign. Avoid --deep: it can stamp shell payloads as nested code
  # and leave LaunchServices with a broken bundle cache.
  codesign --force --sign - "$APP_DEST/Contents/MacOS/shellframe" 2>/dev/null || true
  codesign --force --sign - "$APP_DEST" 2>/dev/null || true

  # Register with Launch Services for Spotlight indexing, and clear stale
  # ShellFrame registrations that commonly survive failed installs.
  LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
  "$LSREGISTER" -u "${HOME}/.Trash/ShellFrame.app" 2>/dev/null || true
  "$LSREGISTER" -u "/Applications/ShellFrame.app" 2>/dev/null || true
  "$LSREGISTER" -u "${HOME}/Applications/ShellFrame.app" 2>/dev/null || true
  "$LSREGISTER" -f "$APP_DEST" 2>/dev/null || true

  # Keep ShellFrame pinned in Dock by default. This is idempotent and can be
  # skipped for scripted installs with SHELLFRAME_SKIP_DOCK=1.
  if [ "${SHELLFRAME_SKIP_DOCK:-0}" != "1" ]; then
    APP_DEST="$APP_DEST" python3 - <<'PY' 2>/dev/null || true
import os
import pathlib
import plistlib
import time

app = pathlib.Path(os.environ["APP_DEST"])
plist = pathlib.Path.home() / "Library/Preferences/com.apple.dock.plist"
if plist.exists():
    data = plistlib.loads(plist.read_bytes())
else:
    data = {}

url = app.as_uri() + "/"
apps = data.get("persistent-apps", [])
filtered = []
for item in apps:
    tile = item.get("tile-data", {})
    label = tile.get("file-label")
    item_url = (tile.get("file-data") or {}).get("_CFURLString")
    bundle_id = tile.get("bundle-identifier")
    if label == "ShellFrame" or bundle_id == "com.h2ocloud.shellframe" or (item_url and "ShellFrame.app" in item_url):
        continue
    filtered.append(item)

entry = {
    "tile-data": {
        "bundle-identifier": "com.h2ocloud.shellframe",
        "dock-extra": 0,
        "file-data": {"_CFURLString": url, "_CFURLStringType": 15},
        "file-label": "ShellFrame",
        "file-mod-date": int(time.time()),
        "file-type": 41,
    },
    "tile-type": "file-tile",
}
data["persistent-apps"] = [entry] + filtered
plist.write_bytes(plistlib.dumps(data, sort_keys=False))
PY
    killall Dock 2>/dev/null || true
  fi

  echo "  App: $APP_DEST"
fi

# ── 6. Ensure ~/.local/bin is in PATH ───────────────────────
SHELL_RC=""
case "$(basename "${SHELL:-zsh}")" in
  zsh)  SHELL_RC="$HOME/.zshrc" ;;
  bash) SHELL_RC="$HOME/.bashrc" ;;
  fish) SHELL_RC="$HOME/.config/fish/config.fish" ;;
esac
if [ -n "$SHELL_RC" ] && ! grep -q '.local/bin' "$SHELL_RC" 2>/dev/null; then
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
  echo "  Added ~/.local/bin to PATH in $(basename "$SHELL_RC")"
fi

# ── Done ────────────────────────────────────────────────────
VERSION=$(.venv/bin/python -c "import json; print(json.load(open('version.json'))['version'])" 2>/dev/null || echo '?')
echo ""
echo "✅ ShellFrame v${VERSION} installed!"
echo ""
echo "  Launch:    shellframe"
echo "  Spotlight: search \"ShellFrame\""
echo "  Launchpad: look for ShellFrame icon"
echo ""
if [ "$(uname)" = "Darwin" ]; then
  echo "  ⚙️  Run \`sfctl permissions\` once to pre-grant macOS Privacy +"
  echo "      firewall access — avoids the 'permission popup' stalls."
  echo ""
fi
