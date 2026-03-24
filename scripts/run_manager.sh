#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"

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
    if [[ -n "${X_BOT_UV_BIN:-}" ]]; then
        printf '%s\n' "${X_BOT_UV_BIN}"
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
    local home_dir uv_bin

    home_dir="$(resolve_home_dir || true)"
    if [[ -n "${home_dir}" ]]; then
        export HOME="${home_dir}"
        export PATH="${home_dir}/.local/bin:${PATH}"
    fi

    uv_bin="$(resolve_uv_bin)" || {
        echo "Failed to locate uv. Install uv or set X_BOT_UV_BIN." >&2
        exit 1
    }

    export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"

    cd "${PROJECT_DIR}"
    exec "${uv_bin}" run python src/main.py
}

main "$@"
