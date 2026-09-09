#!/bin/bash
# One-time install of the Tesseract OCR engine (github.com/tesseract-ocr/tesseract).
#
# The collector reads scanned pages with Tesseract when it is available, and
# falls back to macOS's built-in Vision OCR when it is not. Installing Tesseract
# needs your password, so this has to be run by you rather than by the app.
set -e

if command -v tesseract >/dev/null 2>&1; then
  echo "Tesseract is already installed: $(tesseract --version | head -1)"
  exit 0
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is not installed. Installing it first (this will ask for your password)."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$candidate" ] && eval "$($candidate shellenv)"
  done
fi

brew install tesseract
echo
echo "Installed: $(tesseract --version | head -1)"
echo "Restart the collector to pick it up."
