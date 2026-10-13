#!/usr/bin/env bash
# One-click setup for Wishtree WishBridge on Linux and macOS.
#
# Installs what is missing (Python 3.10+, Java, the Databricks CLI), signs in to
# Databricks, installs Databricks Labs LakeBridge and its converters, installs
# WishBridge into .venv next to this script, and adds a launcher
# (Linux: app-menu entry; macOS: double-clickable file on the Desktop).
# Safe to run again: steps that are already done are skipped.
#
#   ./install.sh                                   # asks for the workspace URL if needed
#   ./install.sh https://dbc-1234.cloud.databricks.com
#   DRY_RUN=1 ./install.sh                         # only report what would be done
#   DATABRICKS_PROFILE=MYPROFILE ./install.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="${DATABRICKS_PROFILE:-DEFAULT}"
WORKSPACE_URL="${1:-}"
DRY_RUN="${DRY_RUN:-0}"
OS="$(uname -s)"

step() { printf '\n\033[36m== %s\033[0m\n' "$1"; }
ok()   { printf '   \033[32m[ok]\033[0m   %s\n' "$1"; }
todo() { printf '   \033[33m[todo]\033[0m %s\n' "$1"; }
fail() { printf '   \033[31m[fail]\033[0m %s\n' "$1"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
network_hint() {
  echo "   If this was a certificate / SSL error, a firewall is inspecting traffic: run setup on another"
  echo "   network or ask IT to trust its certificate."
}

# Install a package with the platform's package manager.
#   pkg <brew name> <apt name> <dnf name> <label>
pkg() {
  local brew_name="$1" apt_name="$2" dnf_name="$3" label="$4"
  if [ "$DRY_RUN" = "1" ]; then todo "would install $label"; return; fi
  echo "   installing $label ..."
  if [ "$OS" = "Darwin" ]; then
    have brew || fail "Homebrew is needed to install $label on macOS: https://brew.sh"
    brew install $brew_name
  elif have apt-get; then
    sudo apt-get update -y && sudo apt-get install -y $apt_name
  elif have dnf; then
    sudo dnf install -y $dnf_name
  else
    fail "Install $label with your package manager, then run setup again"
  fi
}

python_ok() {
  have python3 && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null
}

echo "Wishtree WishBridge setup ($OS)"
[ "$DRY_RUN" = "1" ] && echo "(dry run - nothing will be changed)"

# 1. Prerequisites -------------------------------------------------------------
step "1/6 Prerequisites"
if python_ok; then ok "$(python3 --version)"; else
  pkg "python@3.12" "python3 python3-venv python3-pip" "python3 python3-pip" "Python 3"
  [ "$DRY_RUN" = "1" ] || python_ok || fail "Python 3.10+ not found - install it and run setup again"
fi
# python3-venv is a separate package on Debian/Ubuntu
if python_ok && ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
  pkg "python@3.12" "python3-venv" "python3" "Python venv support"
fi
if have java; then ok "Java found"; else
  pkg "--cask temurin@17" "openjdk-17-jre-headless" "java-17-openjdk-headless" "Java 17"
  [ "$DRY_RUN" = "1" ] || have java || fail "Java not found - install Java 11+ and run setup again"
fi
if have databricks; then ok "$(databricks --version)"; else
  if [ "$DRY_RUN" = "1" ]; then todo "would install the Databricks CLI"
  elif [ "$OS" = "Darwin" ] && have brew; then brew tap databricks/tap && brew install databricks
  else
    # Official installer from Databricks (installs to /usr/local/bin; may ask for sudo)
    curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh || { network_hint; fail "Databricks CLI install failed"; }
  fi
  [ "$DRY_RUN" = "1" ] || have databricks || fail "Databricks CLI not found - see https://docs.databricks.com/dev-tools/cli/install"
fi

# 2. Databricks login ----------------------------------------------------------
step "2/6 Databricks login (profile $PROFILE)"
if have databricks && databricks auth profiles 2>/dev/null | awk -v p="$PROFILE" '$1 == p && $NF == "YES" {found=1} END {exit !found}'; then
  ok "signed in"
elif [ "$DRY_RUN" = "1" ]; then todo "would sign in to Databricks (browser opens)"
else
  [ -n "$WORKSPACE_URL" ] || read -r -p "   Databricks workspace URL (e.g. https://dbc-1234.cloud.databricks.com): " WORKSPACE_URL
  databricks auth login --host "$WORKSPACE_URL" --profile "$PROFILE" || fail "sign-in failed"
  ok "signed in"
fi
export DATABRICKS_CONFIG_PROFILE="$PROFILE"

# 3. LakeBridge ----------------------------------------------------------------
step "3/6 Databricks Labs LakeBridge"
if [ -d "$HOME/.databricks/labs/lakebridge" ]; then ok "installed"
elif [ "$DRY_RUN" = "1" ]; then todo "would run: databricks labs install lakebridge"
else
  echo "   installing (press Enter to accept the defaults if asked) ..."
  databricks labs install lakebridge || { network_hint; fail "LakeBridge install failed"; }
  ok "installed"
fi

# 4. Converters ----------------------------------------------------------------
step "4/6 LakeBridge converters"
T="$HOME/.databricks/labs/remorph-transpilers"
if [ -f "$T/databricks-morph-plugin/lib/config.yml" ] && [ -f "$T/bladebridge/lib/config.yml" ]; then
  ok "Morph and BladeBridge installed"
elif [ "$DRY_RUN" = "1" ]; then todo "would install the converters"
else
  databricks labs lakebridge install-transpile --interactive false || { network_hint; fail "converter install failed"; }
  ok "converters installed"
fi

# 5. WishBridge ----------------------------------------------------------------
step "5/6 WishBridge"
VENV="$ROOT/.venv"
if [ "$DRY_RUN" = "1" ]; then
  if [ -x "$VENV/bin/wishbridge" ]; then ok "installed - would refresh it"; else todo "would create .venv and install WishBridge"; fi
else
  [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -e "$ROOT[ai,ui]" || { network_hint; fail "pip install failed"; }
  ok "$("$VENV/bin/wishbridge" --version)"
fi

# 6. Launcher ------------------------------------------------------------------
step "6/6 Launcher"
chmod +x "$ROOT/start-wishbridge.sh" 2>/dev/null || true
if [ "$DRY_RUN" = "1" ]; then todo "would add a launcher"
elif [ "$OS" = "Darwin" ]; then
  LAUNCHER="$HOME/Desktop/Wishtree WishBridge.command"
  printf '#!/bin/bash\nexec "%s/start-wishbridge.sh"\n' "$ROOT" > "$LAUNCHER"
  chmod +x "$LAUNCHER"
  ok "created $LAUNCHER (double-click it in Finder)"
else
  APPS="$HOME/.local/share/applications"
  mkdir -p "$APPS"
  cat > "$APPS/wishtree-wishbridge.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Wishtree WishBridge
Comment=Migration to Databricks
Exec="$ROOT/start-wishbridge.sh"
Path=$ROOT
Terminal=true
Categories=Development;
EOF
  ok "added 'Wishtree WishBridge' to the application menu (or run ./start-wishbridge.sh)"
fi

echo
if [ "$DRY_RUN" = "1" ]; then echo "Dry run finished."; else echo "WishBridge is ready. Start it from the launcher, or run ./start-wishbridge.sh"; fi
