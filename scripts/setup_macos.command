#!/bin/zsh
set -euo pipefail

if [[ "$(uname -m)" != "arm64" ]]; then
  print "This download is built for Apple Silicon Macs."
  read -r "REPLY?Press Return to close. "
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -d "$SCRIPT_DIR/video-rabbit-holes/scripts" ]]; then
  SOURCE_DIR="$SCRIPT_DIR/video-rabbit-holes"
else
  SOURCE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
fi
APP_BUNDLE="${1:-}"
CODEX_HOME="$HOME/.codex"
RUNTIME="$CODEX_HOME/video-research-runtime"
VENV="$RUNTIME/venv"

BREW=""
for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  if [[ -x "$candidate" ]]; then
    BREW="$candidate"
    break
  fi
done
if [[ -z "$BREW" ]] && command -v brew >/dev/null 2>&1; then
  BREW="$(command -v brew)"
fi

PYTHON=""
for candidate in "$(command -v python3.12 2>/dev/null || true)" \
                 /opt/homebrew/opt/python@3.12/bin/python3.12 \
                 /usr/local/opt/python@3.12/bin/python3.12; do
  if [[ -n "$candidate" && -x "$candidate" ]] && "$candidate" -c 'import tkinter' >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done

if [[ -z "$PYTHON" ]]; then
  if [[ -z "$BREW" ]]; then
    print "Install Homebrew from https://brew.sh, then open Video Workbench again."
    print "Homebrew lets this setup add Python 3.12 and its macOS Tk support."
    read -r "REPLY?Press Return to close. "
    exit 1
  fi
  "$BREW" install python@3.12 python-tk@3.12
  PYTHON="$("$BREW" --prefix python@3.12)/bin/python3.12"
fi

if ! "$PYTHON" -c 'import tkinter' >/dev/null 2>&1; then
  if [[ -z "$BREW" ]]; then
    print "Python 3.12 was found, but its Tk interface is missing. Install Homebrew and retry."
    read -r "REPLY?Press Return to close. "
    exit 1
  fi
  "$BREW" install python-tk@3.12
fi

mkdir -p "$RUNTIME"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON" -m venv "$VENV"
fi
VENV_PYTHON="$VENV/bin/python"
REQ_HASH="$(/usr/bin/shasum -a 256 "$SOURCE_DIR/requirements.txt" | /usr/bin/awk '{print $1}')"
INSTALLED_HASH="$(cat "$RUNTIME/requirements-macos.sha256" 2>/dev/null || true)"
if [[ "$REQ_HASH" != "$INSTALLED_HASH" ]]; then
  "$VENV_PYTHON" -m pip install --upgrade pip
  "$VENV_PYTHON" -m pip install -r "$SOURCE_DIR/requirements.txt"
  print -r -- "$REQ_HASH" > "$RUNTIME/requirements-macos.sha256"
fi

"$VENV_PYTHON" -m playwright install chromium

print ""
read -r "INSTALL_CODEX?Install or update the optional Codex skill in ~/.agents/skills? [Y/n] "
if [[ -z "${INSTALL_CODEX:-}" || "$INSTALL_CODEX" == [Yy]* ]]; then
  "$VENV_PYTHON" "$SOURCE_DIR/scripts/install.py"
else
  print "Codex skill install skipped. You can add it later in Video Workbench Settings."
fi

touch "$RUNTIME/setup-complete"
print ""
print "Setup is complete."
if [[ -n "$APP_BUNDLE" && -d "$APP_BUNDLE" ]]; then
  open -a "$APP_BUNDLE"
else
  print "Open Video Workbench.app from Applications."
fi
read -r "REPLY?Press Return to close. "
