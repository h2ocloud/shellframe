#!/bin/bash
# shellframe: GUI terminal wrapper with image paste support
cd "$(dirname "$0")"

# Use existing venv or create one
if [ ! -d ".venv" ]; then
  echo "First run: setting up virtual environment..."
  python3 -m venv .venv
  .venv/bin/pip install -q pywebview
  echo "Done!"
fi

if [[ "$(sysctl -in hw.optional.arm64 2>/dev/null)" == "1" ]]; then
  exec arch -arm64 .venv/bin/python main.py "$@"
fi
exec .venv/bin/python main.py "$@"
