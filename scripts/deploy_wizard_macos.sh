#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
MAIN_WIZARD="${SCRIPT_DIR}/deploy_wizard.sh"

usage() {
    cat <<'EOF'
Usage:
  scripts/deploy_wizard_macos.sh

Description:
  macOS one-click deployment entry for Ikaros.
  It wraps scripts/deploy_wizard.sh and only allows running on macOS.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ "$(uname -s 2>/dev/null || true)" != "Darwin" ]]; then
    echo "scripts/deploy_wizard_macos.sh is only for macOS." >&2
    echo "On Linux use: ./scripts/deploy_wizard.sh" >&2
    exit 1
fi

if [[ ! -x "${MAIN_WIZARD}" ]]; then
    chmod +x "${MAIN_WIZARD}"
fi

echo "Launching macOS deployment wizard ..."
exec "${MAIN_WIZARD}" "$@"
