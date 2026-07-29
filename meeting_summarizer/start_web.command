#!/usr/bin/env bash
#
# macOS equivalent of start_web.bat — runs the web UI from source.
# Make it double-clickable once with:  chmod +x start_web.command
set -euo pipefail
cd "$(dirname "$0")"

echo "Killing old process on port 8000..."
lsof -ti :8000 | xargs kill -9 2>/dev/null || true

if [ ! -d venv ]; then
  echo "Creating venv (first run, this takes a few minutes)..."
  python3 -m venv venv
  ./venv/bin/python -m pip install --upgrade pip wheel
  if [ "$(uname -m)" = "x86_64" ]; then
    # Last PyTorch release with macOS Intel wheels.
    ./venv/bin/python -m pip install "torch==2.2.2" "numpy<2"
  else
    ./venv/bin/python -m pip install "torch>=2.2.0"
  fi
  ./venv/bin/python -m pip install -r requirements-mac.txt
fi

echo "Starting Meeting Summarizer Web..."
./venv/bin/python web.py
