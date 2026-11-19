#!/bin/sh
# Bootstrap agent-guard on this machine:
#   1. a private Python runtime (uv-managed, so Homebrew upgrades can't break it)
#   2. gitleaks for secret scanning (via Homebrew, if available)
#   3. `guard install`, which wires every detected agent and the global git hooks
# Safe to re-run. Undo with: guard uninstall
set -eu
REPO=$(cd "$(dirname "$0")" && pwd)
STATE="${AGENT_GUARD_HOME:-$HOME/.agent-guard}"
VENV="$STATE/venv"

command -v uv >/dev/null 2>&1 || { echo "agent-guard needs uv: brew install uv" >&2; exit 1; }

if ! "$VENV/bin/python" -c "import yaml" >/dev/null 2>&1; then
  echo "Creating runtime in $VENV"
  uv venv --quiet --allow-existing --managed-python --python 3.13 "$VENV"
  uv pip install --quiet --python "$VENV/bin/python" "pyyaml>=6" "pytest>=8"
fi

if ! command -v gitleaks >/dev/null 2>&1 && command -v brew >/dev/null 2>&1; then
  echo "Installing gitleaks (secret scanner) with Homebrew"
  brew install --quiet gitleaks || echo "gitleaks install failed; built-in secret patterns still work" >&2
fi

PYTHONPATH="$REPO/src" "$VENV/bin/python" -s -m agent_guard install "$@"
