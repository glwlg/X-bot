#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
WEB_DIR="${PROJECT_DIR}/src/platforms/web"
TARGET_DIR="${PROJECT_DIR}/src/api/static/dist"

FORCE_INSTALL=0

usage() {
    cat <<'EOF'
Usage:
  scripts/build_web.sh [options]

Options:
  --install             Force reinstall frontend dependencies before build
  -h, --help            Show this help text
EOF
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --install)
                FORCE_INSTALL=1
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                echo "Unknown option: $1" >&2
                usage >&2
                exit 1
                ;;
        esac
        shift
    done

    require_command npm

    cd "${WEB_DIR}"

    if [[ "${FORCE_INSTALL}" -eq 1 || ! -d "${WEB_DIR}/node_modules" ]]; then
        if [[ -f "${WEB_DIR}/package-lock.json" ]]; then
            npm ci
        else
            npm install
        fi
    fi

    npm run build
    echo "Built frontend assets into ${TARGET_DIR}"
}

main "$@"
