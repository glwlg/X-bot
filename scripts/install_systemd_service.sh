#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
RUNNER_PATH="${PROJECT_DIR}/scripts/run_manager.sh"

SERVICE_NAME="x-bot"
INSTALL_MODE="system"
TARGET_USER=""
PRINT_ONLY=0
NO_START=0

usage() {
    cat <<'EOF'
Usage:
  scripts/install_systemd_service.sh [options]

Options:
  --system               Install a system service under /etc/systemd/system (default)
  --user                 Install a user service under ~/.config/systemd/user
  --target-user USER     Override the inferred runtime user for --system mode
  --service-name NAME    Override the unit name (default: x-bot)
  --print-unit           Print the rendered unit file instead of installing it
  --no-start             Install the unit but do not enable/start it
  -h, --help             Show this help text
EOF
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

run_root_command() {
    if [[ "${INSTALL_MODE}" == "system" && "${EUID}" -ne 0 ]]; then
        sudo "$@"
        return 0
    fi
    "$@"
}

infer_target_user() {
    if [[ -n "${TARGET_USER}" ]]; then
        printf '%s\n' "${TARGET_USER}"
        return 0
    fi

    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
        printf '%s\n' "${SUDO_USER}"
        return 0
    fi

    local owner_name
    owner_name="$(stat -c %U "${PROJECT_DIR}")"
    if [[ -n "${owner_name}" && "${owner_name}" != "root" && "${owner_name}" != "UNKNOWN" ]]; then
        printf '%s\n' "${owner_name}"
        return 0
    fi

    local current_user
    current_user="$(id -un)"
    if [[ "${current_user}" != "root" ]]; then
        printf '%s\n' "${current_user}"
        return 0
    fi

    echo "Failed to infer a non-root runtime user. Pass --target-user USER." >&2
    exit 1
}

render_system_unit() {
    local runtime_user runtime_uid runtime_gid
    runtime_user="$(infer_target_user)"
    runtime_uid="$(id -u "${runtime_user}")"
    runtime_gid="$(id -g "${runtime_user}")"

    cat <<EOF
[Unit]
Description=X-Bot Manager
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=${runtime_uid}
Group=${runtime_gid}
WorkingDirectory=${PROJECT_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${RUNNER_PATH}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

render_user_unit() {
    cat <<EOF
[Unit]
Description=X-Bot Manager
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${PROJECT_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${RUNNER_PATH}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
}

render_unit() {
    if [[ "${INSTALL_MODE}" == "user" ]]; then
        render_user_unit
        return 0
    fi
    render_system_unit
}

unit_path() {
    if [[ "${INSTALL_MODE}" == "user" ]]; then
        printf '%s\n' "${HOME}/.config/systemd/user/${SERVICE_NAME}.service"
        return 0
    fi
    printf '%s\n' "/etc/systemd/system/${SERVICE_NAME}.service"
}

install_unit_file() {
    local destination tmpfile
    destination="$(unit_path)"
    tmpfile="$(mktemp)"

    render_unit >"${tmpfile}"

    if [[ "${INSTALL_MODE}" == "user" ]]; then
        mkdir -p "$(dirname "${destination}")"
        install -m 0644 "${tmpfile}" "${destination}"
        rm -f "${tmpfile}"
        return 0
    fi

    run_root_command mkdir -p "$(dirname "${destination}")"
    run_root_command install -m 0644 "${tmpfile}" "${destination}"
    rm -f "${tmpfile}"
}

reload_systemd() {
    if [[ "${INSTALL_MODE}" == "user" ]]; then
        systemctl --user daemon-reload
        return 0
    fi
    run_root_command systemctl daemon-reload
}

enable_and_start_unit() {
    if [[ "${NO_START}" -eq 1 ]]; then
        return 0
    fi

    if [[ "${INSTALL_MODE}" == "user" ]]; then
        systemctl --user enable --now "${SERVICE_NAME}"
        return 0
    fi

    run_root_command systemctl enable --now "${SERVICE_NAME}"
}

print_next_steps() {
    local destination
    destination="$(unit_path)"

    echo "Installed unit: ${destination}"

    if [[ "${NO_START}" -eq 1 ]]; then
        if [[ "${INSTALL_MODE}" == "user" ]]; then
            echo "Next: systemctl --user enable --now ${SERVICE_NAME}"
        else
            echo "Next: sudo systemctl enable --now ${SERVICE_NAME}"
        fi
        return 0
    fi

    if [[ "${INSTALL_MODE}" == "user" ]]; then
        echo "Check status: systemctl --user status ${SERVICE_NAME}"
        echo "Stream logs: journalctl --user -u ${SERVICE_NAME} -f"
        return 0
    fi

    echo "Check status: sudo systemctl status ${SERVICE_NAME}"
    echo "Stream logs: journalctl -u ${SERVICE_NAME} -f"
}

main() {
    require_command systemctl
    require_command install
    require_command stat
    require_command id

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --system)
                INSTALL_MODE="system"
                ;;
            --user)
                INSTALL_MODE="user"
                ;;
            --target-user)
                if [[ $# -lt 2 || -z "${2:-}" ]]; then
                    echo "--target-user requires a value." >&2
                    exit 1
                fi
                TARGET_USER="${2:-}"
                shift
                ;;
            --service-name)
                if [[ $# -lt 2 || -z "${2:-}" ]]; then
                    echo "--service-name requires a value." >&2
                    exit 1
                fi
                SERVICE_NAME="${2:-}"
                shift
                ;;
            --print-unit)
                PRINT_ONLY=1
                ;;
            --no-start)
                NO_START=1
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

    if [[ "${INSTALL_MODE}" == "system" && "${EUID}" -ne 0 ]]; then
        require_command sudo
    fi

    if [[ ! -x "${RUNNER_PATH}" ]]; then
        chmod +x "${RUNNER_PATH}"
    fi

    if [[ "${PRINT_ONLY}" -eq 1 ]]; then
        render_unit
        exit 0
    fi

    install_unit_file
    reload_systemd
    enable_and_start_unit
    print_next_steps
}

main "$@"
