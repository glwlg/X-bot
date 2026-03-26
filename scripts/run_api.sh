#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
BUILD_SCRIPT="${PROJECT_DIR}/scripts/build_web.sh"

HOST="0.0.0.0"
PORT="8764"
SKIP_BUILD=0
RELOAD=0

usage() {
    cat <<'EOF'
Usage:
  scripts/run_api.sh [options]

Options:
  --host HOST           Bind host (default: 0.0.0.0)
  --port PORT           Bind port (default: 8764)
  --skip-build          Skip frontend build
  --reload              Enable uvicorn reload mode
  -h, --help            Show this help text
EOF
}

resolve_home_dir() {
    if [[ -n "${HOME:-}" && -d "${HOME}" ]]; then
        printf '%s\n' "${HOME}"
        return 0
    fi

    if command -v getent >/dev/null 2>&1; then
        local passwd_entry
        passwd_entry="$(getent passwd "$(id -u)" 2>/dev/null || true)"
        if [[ -n "${passwd_entry}" ]]; then
            printf '%s\n' "${passwd_entry}" | cut -d: -f6
            return 0
        fi
    fi

    return 1
}

resolve_uv_bin() {
    if [[ -n "${IKAROS_UV_BIN:-}" ]]; then
        printf '%s\n' "${IKAROS_UV_BIN}"
        return 0
    fi

    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return 0
    fi

    local home_dir
    home_dir="$(resolve_home_dir || true)"
    if [[ -n "${home_dir}" && -x "${home_dir}/.local/bin/uv" ]]; then
        printf '%s\n' "${home_dir}/.local/bin/uv"
        return 0
    fi

    return 1
}

main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --host)
                if [[ $# -lt 2 || -z "${2:-}" ]]; then
                    echo "--host requires a value." >&2
                    exit 1
                fi
                HOST="${2:-}"
                shift
                ;;
            --port)
                if [[ $# -lt 2 || -z "${2:-}" ]]; then
                    echo "--port requires a value." >&2
                    exit 1
                fi
                PORT="${2:-}"
                shift
                ;;
            --skip-build)
                SKIP_BUILD=1
                ;;
            --reload)
                RELOAD=1
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

    local home_dir uv_bin

    home_dir="$(resolve_home_dir || true)"
    if [[ -n "${home_dir}" ]]; then
        export HOME="${home_dir}"
        export PATH="${home_dir}/.local/bin:${PATH}"
    fi

    uv_bin="$(resolve_uv_bin)" || {
        echo "Failed to locate uv. Install uv or set IKAROS_UV_BIN." >&2
        exit 1
    }

    if [[ "${SKIP_BUILD}" -eq 0 ]]; then
        if [[ ! -x "${BUILD_SCRIPT}" ]]; then
            chmod +x "${BUILD_SCRIPT}"
        fi
        "${BUILD_SCRIPT}"
    fi

    export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
    export PYTHONPATH="${PROJECT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"

    local -a argv
    argv=(run python -m uvicorn --app-dir "${PROJECT_DIR}/src" api.main:app --host "${HOST}" --port "${PORT}")
    if [[ "${RELOAD}" -eq 1 ]]; then
        argv+=(--reload)
    fi

    cd "${PROJECT_DIR}"
    exec "${uv_bin}" "${argv[@]}"
}

main "$@"
