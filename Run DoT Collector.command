#!/bin/bash
# Double-click this file to start the DoT Blocking Order Collector.

cd "$(dirname "$0")" || exit 1

say() { printf '%s\n' "$*"; }
die() { say ""; say "$*"; say ""; say "Press any key to close this window."; read -r -n 1; exit 1; }

find_python() {
  for candidate in \
    "$(command -v python3 2>/dev/null)" \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
    /usr/bin/python3
  do
    [ -n "$candidate" ] && [ -x "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  done
  return 1
}

if [ ! -x .venv/bin/python ]; then
  say "First run - setting up. This takes a couple of minutes."
  PYTHON=$(find_python) || die "Could not find Python 3. Install it from python.org, then try again."
  say "Using $PYTHON"
  "$PYTHON" -m venv .venv || die "Could not create the virtual environment in .venv"
  .venv/bin/python -m pip install --upgrade pip >/dev/null 2>&1
  .venv/bin/python -m pip install -r requirements.txt || die "Could not install the dependencies."
  say "Setup complete."
fi

exec .venv/bin/python serve.py
