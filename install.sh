#!/bin/bash
set -euo pipefail

# ShellFrame installer
# Usage: curl -fsSL https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh | bash

INSTALL_DIR="${HOME}/.local/apps/shellframe"
BIN_DIR="${HOME}/.local/bin"

echo "Installing ShellFrame..."

# ── Helper ──────────────────────────────────────────────────
install_if_missing() {
  local cmd="$1" pkg_brew="$2" pkg_apt="${3:-$2}" pkg_dnf="${4:-$2}"
  if command -v "$cmd" &>/dev/null; then return 0; fi
  echo "  Installing $cmd..."
  if [ "$(uname)" = "Darwin" ]; then
    if command -v brew &>/dev/null; then
      brew install -q "$pkg_brew"
    else
      echo "  Error: Homebrew is required. Install it first:"
      echo "    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
      exit 1
    fi
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
  # Directory exists with files but no .git — user downloaded a zip or cp'd files.
  # Convert into a git clone in-place: init, add remote, fetch, reset --hard.
  echo "Upgrading non-git install at $INSTALL_DIR to a git clone..."
  cd "$INSTALL_DIR"
  git init -q
  git remote add origin "$REPO_URL" 2>/dev/null || git remote set-url origin "$REPO_URL"
  git fetch --depth=1 origin main
  # Preserve user's .venv and any non-tracked files by stashing untracked first
  git stash push -u -m "install.sh-pre-reinit-$(date +%s)" >/dev/null 2>&1 || true
  git reset --hard origin/main
else
  echo "Cloning repository..."
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ── 3. Python venv + pip dependencies ───────────────────────
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
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
exec ~/.local/apps/shellframe/.venv/bin/python ~/.local/apps/shellframe/main.py "$@"
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

  # Stamp Info.plist with current version from version.json
  CURRENT_VER=$(python3 -c "import json; print(json.load(open('$INSTALL_DIR/version.json'))['version'])" 2>/dev/null || echo "0.0.0")
  PLIST="$APP_DEST/Contents/Info.plist"
  if [ -f "$PLIST" ]; then
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $CURRENT_VER" "$PLIST" 2>/dev/null || true
    /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $CURRENT_VER" "$PLIST" 2>/dev/null || true
  fi

  # Clean up old ~/Applications copy if we migrated to /Applications
  [ "$APP_DEST" = "/Applications/ShellFrame.app" ] && rm -rf "${HOME}/Applications/ShellFrame.app" 2>/dev/null

  # Ad-hoc code sign (unsigned apps may be cleared by Gatekeeper)
  codesign --force --deep --sign - "$APP_DEST" 2>/dev/null || true

  # Register with Launch Services for Spotlight indexing
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP_DEST" 2>/dev/null || true

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
