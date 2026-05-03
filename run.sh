#!/bin/bash
# shellframe: GUI terminal wrapper with image paste support
cd "$(dirname "$0")"

# Use existing venv or create one. On macOS prefer Homebrew python over
# Apple's, because Apple's Python framework rewraps itself as Python.app
# at runtime and steals the TCC bundle identity (kills global hotkey).
if [ ! -d ".venv" ]; then
  echo "First run: setting up virtual environment..."
  PY=python3
  if [ "$(uname)" = "Darwin" ]; then
    for c in /opt/homebrew/bin/python3 /usr/local/bin/python3; do
      [ -x "$c" ] && PY="$c" && break
    done
  fi
  "$PY" -m venv .venv
  .venv/bin/pip install -q pywebview pyte
  echo "Done!"
fi

if [[ "$(sysctl -in hw.optional.arm64 2>/dev/null)" == "1" ]]; then
  exec arch -arm64 .venv/bin/python main.py "$@"
fi
exec .venv/bin/python main.py "$@"
