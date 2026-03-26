#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"

LABEL="com.ikaros.ikaros"
RUNNER_PATH="${PROJECT_DIR}/scripts/run_ikaros.sh"
PRINT_ONLY=0
NO_START=0
LOG_DIR="${PROJECT_DIR}/data/logs"

usage() {
    cat <<'EOF'
Usage:
  scripts/install_launchd_service.sh [options]

Options:
  --label NAME          launchd label (default: com.ikaros.ikaros)
  --runner PATH         Runner script to execute (default: scripts/run_ikaros.sh)
  --print-plist         Print the rendered plist instead of installing it
  --no-start            Install the plist but do not bootstrap it
  -h, --help            Show this help text
EOF
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

normalize_runner_path() {
    local input
    input="$1"
    if [[ "${input}" == /* ]]; then
        printf '%s\n' "${input}"
        return 0
    fi

    local candidate
    candidate="${PROJECT_DIR}/${input#./}"
    (
        cd "$(dirname "${candidate}")" >/dev/null 2>&1
        printf '%s/%s\n' "$(pwd)" "$(basename "${candidate}")"
    )
}

plist_path() {
    printf '%s\n' "${HOME}/Library/LaunchAgents/${LABEL}.plist"
}

render_plist() {
    mkdir -p "${LOG_DIR}"
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${RUNNER_PATH}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${PROJECT_DIR}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>${HOME}</string>
    <key>PATH</key>
    <string>${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/${LABEL}.out.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/${LABEL}.err.log</string>
</dict>
</plist>
EOF
}

install_plist() {
    local destination tmpfile
    destination="$(plist_path)"
    tmpfile="$(mktemp)"

    render_plist >"${tmpfile}"
    mkdir -p "$(dirname "${destination}")"
    install -m 0644 "${tmpfile}" "${destination}"
    rm -f "${tmpfile}"
}

bootstrap_service() {
    local destination domain
    destination="$(plist_path)"
    domain="gui/$(id -u)"

    launchctl bootout "${domain}" "${destination}" >/dev/null 2>&1 || true
    launchctl bootstrap "${domain}" "${destination}"
    launchctl kickstart -k "${domain}/${LABEL}" >/dev/null 2>&1 || true
}

print_next_steps() {
    local destination domain
    destination="$(plist_path)"
    domain="gui/$(id -u)"

    echo "Installed plist: ${destination}"
    if [[ "${NO_START}" -eq 1 ]]; then
        echo "Next: launchctl bootstrap ${domain} ${destination}"
        return 0
    fi

    echo "Check status: launchctl print ${domain}/${LABEL}"
    echo "Logs: tail -f ${LOG_DIR}/${LABEL}.out.log ${LOG_DIR}/${LABEL}.err.log"
}

main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --label)
                if [[ $# -lt 2 || -z "${2:-}" ]]; then
                    echo "--label requires a value." >&2
                    exit 1
                fi
                LABEL="${2:-}"
                shift
                ;;
            --runner)
                if [[ $# -lt 2 || -z "${2:-}" ]]; then
                    echo "--runner requires a value." >&2
                    exit 1
                fi
                RUNNER_PATH="${2:-}"
                shift
                ;;
            --print-plist)
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

    require_command launchctl
    require_command install

    RUNNER_PATH="$(normalize_runner_path "${RUNNER_PATH}")"
    if [[ ! -x "${RUNNER_PATH}" ]]; then
        chmod +x "${RUNNER_PATH}"
    fi

    if [[ "${PRINT_ONLY}" -eq 1 ]]; then
        render_plist
        exit 0
    fi

    install_plist

    if [[ "${NO_START}" -eq 0 ]]; then
        bootstrap_service
    fi

    print_next_steps
}

main "$@"
