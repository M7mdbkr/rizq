#!/bin/bash
# Development launcher. Uses the Electron runtime already installed for Alfred.
set -u
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ELECTRON_BIN="${RIZQ_ELECTRON_BIN:-$HOME/Projects/alfred/node_modules/.bun/electron@40.2.1/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron}"
if [ ! -x "$ELECTRON_BIN" ]; then
  echo "Electron was not found. See desktop/electron/README.md."
  read -r -p "Press Enter to close..." _
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. See desktop/electron/README.md."
  read -r -p "Press Enter to close..." _
  exit 1
fi
exec "$ELECTRON_BIN" "$PROJECT_DIR/desktop/electron"
