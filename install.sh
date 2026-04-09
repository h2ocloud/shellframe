#!/bin/bash
set -euo pipefail

# ShellFrame installer
# Usage: curl -fsSL https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh | bash

INSTALL_DIR="${HOME}/.local/apps/shellframe"
BIN_DIR="${HOME}/.local/bin"

echo "Installing ShellFrame..."

# Check prerequisites
if ! command -v git &>/dev/null; then
  echo "Error: git is required. Install it first:"
  echo "  macOS:  xcode-select --install"
  echo "  Linux:  sudo apt install git"
  exit 1
fi

# Auto-install system dependencies
install_if_missing() {
  local cmd="$1" pkg_brew="$2" pkg_apt="$3" pkg_dnf="$4"
  if command -v "$cmd" &>/dev/null; then return 0; fi
  echo "$cmd not found. Installing..."
  if [ "$(uname)" = "Darwin" ]; then
    if command -v brew &>/dev/null; then
      brew install "$pkg_brew"
    else
      echo "  Error: Homebrew required to install $cmd. Install Homebrew first:"
      echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
      return 1
    fi
  elif command -v apt-get &>/dev/null; then
    sudo apt-get update -q && sudo apt-get install -y -q $pkg_apt
  elif command -v dnf &>/dev/null; then
    sudo dnf install -y $pkg_dnf
  elif command -v pacman &>/dev/null; then
    sudo pacman -S --noconfirm "$cmd"
  else
    echo "  Error: Could not install $cmd. Install it manually and re-run."
    return 1
  fi
}

# Python 3 — core runtime
if ! command -v python3 &>/dev/null; then
  if [ "$(uname)" = "Darwin" ]; then
    if command -v brew &>/dev/null; then
      brew install python@3.12
    else
      echo "  Installing Xcode CLI tools (includes Python 3)..."
      xcode-select --install 2>/dev/null || true
      echo "  Run this installer again after Xcode tools finish installing."
      exit 1
    fi
  else
    install_if_missing python3 python@3.12 "python3 python3-venv" python3
  fi
fi

# tmux — session persistence (sessions survive ShellFrame restart)
install_if_missing tmux tmux tmux tmux

# Clone or update
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "Updating existing installation..."
  cd "$INSTALL_DIR" && git pull --ff-only
else
  echo "Cloning repository..."
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone https://github.com/h2ocloud/shellframe.git "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# Set up Python venv
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi
echo "Installing dependencies..."
.venv/bin/pip install -q -r requirements.txt

# CLI launchers
mkdir -p "$BIN_DIR"

# shellframe — main app
cat > "$BIN_DIR/shellframe" << 'LAUNCHER'
#!/bin/bash
# Source user profile for full PATH (nvm, etc.)
if [ -f "$HOME/.zprofile" ]; then source "$HOME/.zprofile" 2>/dev/null; fi
if [ -f "$HOME/.zshrc" ]; then source "$HOME/.zshrc" 2>/dev/null; fi
exec ~/.local/apps/shellframe/.venv/bin/python ~/.local/apps/shellframe/main.py "$@"
LAUNCHER
chmod +x "$BIN_DIR/shellframe"

# sfctl — remote control for AI agents
ln -sf "$INSTALL_DIR/sfctl.py" "$BIN_DIR/sfctl"

# macOS .app (for Spotlight / Launchpad)
if [ "$(uname)" = "Darwin" ]; then
  APP_DEST="${HOME}/Applications/ShellFrame.app"
  mkdir -p ~/Applications
  # Copy .app bundle (not symlink — Spotlight won't index symlinks to dot-folders)
  rm -rf "$APP_DEST"
  cp -R "$INSTALL_DIR/ShellFrame.app" "$APP_DEST"
  # Register with Launch Services for Spotlight indexing
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP_DEST" 2>/dev/null
  echo "  Mac app: ~/Applications/ShellFrame.app (Spotlight: ShellFrame)"
fi

# Ensure ~/.local/bin is in PATH (always check shell RC, not current env)
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

echo ""
echo "ShellFrame v$(python3 -c "import json; print(json.load(open('version.json'))['version'])" 2>/dev/null || echo '?') installed!"
echo ""
echo "  Launch:  shellframe"
echo "  or open ShellFrame from Spotlight"
echo ""
