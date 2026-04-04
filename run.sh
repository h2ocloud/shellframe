#!/bin/bash
# cli-gui: GUI terminal wrapper with image paste support
cd "$(dirname "$0")"

# Use existing venv or create one
if [ ! -d ".venv" ]; then
  echo "First run: setting up virtual environment..."
  python3 -m venv .venv
  .venv/bin/pip install -q pywebview
  echo "Done!"
fi

exec .venv/bin/python main.py "$@"
