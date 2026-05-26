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

if [[ "$(sysctl -in hw.optional.arm64 2>/dev/null)" == "1" ]]; then
  exec arch -arm64 "$PY_LAUNCHER" main.py "$@"
fi
exec "$PY_LAUNCHER" main.py "$@"
