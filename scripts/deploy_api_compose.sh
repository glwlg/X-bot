#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.yml"
SERVICE_NAME="ikaros-api"
ACTION="${1:-up}"

usage() {
    cat <<'EOF'
Usage:
  scripts/deploy_api_compose.sh [up|down|restart|logs|ps|build]

Examples:
  scripts/deploy_api_compose.sh up
  scripts/deploy_api_compose.sh logs
  scripts/deploy_api_compose.sh down
EOF
}

main() {
    local -a compose_cmd
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        compose_cmd=(docker compose)
    elif command -v docker-compose >/dev/null 2>&1; then
        compose_cmd=(docker-compose)
    else
        echo "Failed to locate docker compose or docker-compose." >&2
        exit 1
    fi

    case "${ACTION}" in
        up)
            cd "${PROJECT_DIR}"
            "${compose_cmd[@]}" -f "${COMPOSE_FILE}" up --build -d "${SERVICE_NAME}"
            ;;
        down)
            cd "${PROJECT_DIR}"
            "${compose_cmd[@]}" -f "${COMPOSE_FILE}" down
            ;;
        restart)
            cd "${PROJECT_DIR}"
            "${compose_cmd[@]}" -f "${COMPOSE_FILE}" up --build -d --force-recreate "${SERVICE_NAME}"
            ;;
        logs)
            cd "${PROJECT_DIR}"
            "${compose_cmd[@]}" -f "${COMPOSE_FILE}" logs -f "${SERVICE_NAME}"
            ;;
        ps)
            cd "${PROJECT_DIR}"
            "${compose_cmd[@]}" -f "${COMPOSE_FILE}" ps
            ;;
        build)
            cd "${PROJECT_DIR}"
            "${compose_cmd[@]}" -f "${COMPOSE_FILE}" build "${SERVICE_NAME}"
            ;;
        -h|--help|help)
            usage
            ;;
        *)
            echo "Unknown action: ${ACTION}" >&2
            usage >&2
            exit 1
            ;;
    esac
}

main "$@"
