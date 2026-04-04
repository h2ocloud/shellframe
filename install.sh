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
.venv/bin/pip install -q pywebview

# CLI launcher
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/shellframe" << 'LAUNCHER'
#!/bin/bash
exec ~/.local/apps/shellframe/.venv/bin/python ~/.local/apps/shellframe/main.py "$@"
LAUNCHER
chmod +x "$BIN_DIR/shellframe"

# macOS .app
if [ "$(uname)" = "Darwin" ]; then
  mkdir -p ~/Applications
  ln -sf "$INSTALL_DIR/ShellFrame.app" ~/Applications/ShellFrame.app
  echo "  Mac app: ~/Applications/ShellFrame.app (Spotlight: ShellFrame)"
fi

echo ""
echo "ShellFrame installed!"
echo "  CLI:  shellframe"
echo "  Path: $INSTALL_DIR"
echo ""
echo "Make sure ~/.local/bin is in your PATH."
