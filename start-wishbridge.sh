#!/usr/bin/env bash
# Open the Wishtree WishBridge app in your browser (Linux / macOS).
# On a server without a desktop: ./start-wishbridge.sh --no-browser
# (then open an SSH tunnel: ssh -L 8501:localhost:8501 <server>, and browse to http://localhost:8501)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
if [ ! -x .venv/bin/wishbridge ]; then
  echo "WishBridge is not installed in this folder yet. Run ./install.sh first."
  exit 1
fi
exec .venv/bin/wishbridge ui "$@"
