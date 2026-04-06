#!/bin/bash
set -euo pipefail

# ShellFrame installer
# Usage: curl -fsSL https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh | bash

INSTALL_DIR="${HOME}/.local/apps/shellframe"
BIN_DIR="${HOME}/.local/bin"

echo "Installing ShellFrame..."

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
  APP_LINK="${HOME}/Applications/ShellFrame.app"
  mkdir -p ~/Applications
  # Remove stale symlink if exists
  [ -L "$APP_LINK" ] && rm "$APP_LINK"
  ln -sf "$INSTALL_DIR/ShellFrame.app" "$APP_LINK"
  echo "  Mac app: ~/Applications/ShellFrame.app (Spotlight: ShellFrame)"
fi

# Ensure ~/.local/bin is in PATH
if ! echo "$PATH" | tr ':' '\n' | grep -q "$BIN_DIR"; then
  SHELL_RC=""
  case "$(basename "$SHELL")" in
    zsh)  SHELL_RC="$HOME/.zshrc" ;;
    bash) SHELL_RC="$HOME/.bashrc" ;;
    fish) SHELL_RC="$HOME/.config/fish/config.fish" ;;
  esac
  if [ -n "$SHELL_RC" ] && ! grep -q '.local/bin' "$SHELL_RC" 2>/dev/null; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
    echo "  Added ~/.local/bin to PATH in $(basename "$SHELL_RC")"
  fi
fi

echo ""
echo "ShellFrame v$(python3 -c "import json; print(json.load(open('version.json'))['version'])" 2>/dev/null || echo '?') installed!"
echo ""
echo "  Launch:  shellframe"
echo "  or open ShellFrame from Spotlight"
echo ""
