#!/usr/bin/env bash

set -euo pipefail

CONTAINER_NAME="${MEDIAMTX_CONTAINER_NAME:-ikaros-mediamtx}"
IMAGE="${MEDIAMTX_IMAGE:-bluenviron/mediamtx:1}"
IKAROS_HOME="${IKAROS_HOME:-${HOME}/.ikaros}"
CONFIG_DIR="${MEDIAMTX_CONFIG_DIR:-${IKAROS_HOME}/config/mediamtx}"
CONFIG_FILE="${MEDIAMTX_CONFIG_FILE:-${CONFIG_DIR}/mediamtx.yml}"
ENV_FILE="${MEDIAMTX_ENV_FILE:-${CONFIG_DIR}/ikaros-mediamtx.env}"
API_AUTH_URL="${MEDIAMTX_AUTH_URL:-http://127.0.0.1:8764/api/v1/cameras/mediamtx/auth}"
ACTION="${1:-up}"

MTX_API_PORT=""
MTX_RTSP_PORT=""
MTX_HLS_PORT=""
MTX_WEBRTC_PORT=""
MTX_WEBRTC_UDP_PORT=""
RESERVED_TCP_PORTS=""

usage() {
    cat <<'EOF'
Usage:
  scripts/deploy_mediamtx.sh [up|down|restart|logs|status|config]

Environment:
  MEDIAMTX_CONTAINER_NAME  Container name (default: ikaros-mediamtx)
  MEDIAMTX_IMAGE           Docker image (default: bluenviron/mediamtx:1)
  MEDIAMTX_AUTH_URL        Ikaros API auth callback URL
  MEDIAMTX_CONFIG_FILE     Config file path
  MEDIAMTX_ENV_FILE        Environment file read by ikaros-api
  MEDIAMTX_API_PORT        Preferred Control API port (default: 9997)
  MEDIAMTX_RTSP_PORT       Preferred RTSP port (default: 8554)
  MEDIAMTX_HLS_PORT        Preferred HLS port (default: 8888)
  MEDIAMTX_WEBRTC_PORT     Preferred WebRTC HTTP port (default: 8889)
  MEDIAMTX_WEBRTC_UDP_PORT Preferred WebRTC UDP port (default: 8189)
  MEDIAMTX_PROXY_PREFIX    Optional same-origin proxy prefix (example: /_mediamtx)
  MEDIAMTX_PUBLIC_BASE_URL Optional public origin for proxy URLs (example: https://cam.example.com)
  MEDIAMTX_PUBLIC_HOST     Optional public host for generated base URLs
  MEDIAMTX_HLS_BASE_URL    Explicit public HLS base URL override
  MEDIAMTX_WEBRTC_BASE_URL Explicit public WebRTC base URL override

Notes:
  - The container uses host networking so RTSP/HLS/WebRTC ports are visible on the host.
  - Ikaros API should usually run on http://127.0.0.1:8764 for the default auth URL.
  - For HTTPS domain access, prefer MEDIAMTX_PROXY_PREFIX=/_mediamtx and reverse proxy it.
EOF
}

require_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        echo "Missing required command: docker" >&2
        exit 1
    fi
}

tcp_port_in_use() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -H -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]${port}$"
        return $?
    fi
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
        return $?
    fi
    return 1
}

udp_port_in_use() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -H -lun 2>/dev/null | awk '{print $5}' | grep -Eq "[:.]${port}$"
        return $?
    fi
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iUDP:"${port}" >/dev/null 2>&1
        return $?
    fi
    return 1
}

is_reserved_tcp_port() {
    local port="$1"
    [[ " ${RESERVED_TCP_PORTS} " == *" ${port} "* ]]
}

normalize_port() {
    local value="$1"
    local fallback="$2"
    if [[ "${value}" =~ ^[0-9]+$ ]] && (( value >= 1 && value <= 65035 )); then
        printf '%s\n' "${value}"
    else
        printf '%s\n' "${fallback}"
    fi
}

normalize_path_prefix() {
    local value="$1"
    value="${value#/}"
    value="${value%/}"
    if [[ -z "${value}" ]]; then
        return 1
    fi
    printf '/%s\n' "${value}"
}

normalize_url_base() {
    local value="$1"
    value="${value%/}"
    if [[ -z "${value}" ]]; then
        return 1
    fi
    printf '%s\n' "${value}"
}

read_existing_env_value() {
    local key="$1"
    if [[ ! -f "${ENV_FILE}" ]]; then
        return 1
    fi
    sed -n -E "s/^(export[[:space:]]+)?${key}=//p" "${ENV_FILE}" | tail -n 1
}

find_free_tcp_port() {
    local preferred="$1"
    local port
    for port in $(seq "${preferred}" "$((preferred + 500))"); do
        if ! is_reserved_tcp_port "${port}" && ! tcp_port_in_use "${port}"; then
            printf '%s\n' "${port}"
            return 0
        fi
    done
    echo "Failed to find a free TCP port starting at ${preferred}" >&2
    exit 1
}

find_free_udp_port() {
    local preferred="$1"
    local port
    for port in $(seq "${preferred}" "$((preferred + 500))"); do
        if ! udp_port_in_use "${port}"; then
            printf '%s\n' "${port}"
            return 0
        fi
    done
    echo "Failed to find a free UDP port starting at ${preferred}" >&2
    exit 1
}

reserve_tcp_port() {
    local preferred="$1"
    find_free_tcp_port "${preferred}"
}

remember_tcp_port() {
    local port="$1"
    RESERVED_TCP_PORTS="${RESERVED_TCP_PORTS} ${port}"
}

resolve_ports() {
    RESERVED_TCP_PORTS=""
    MTX_API_PORT="$(reserve_tcp_port "$(normalize_port "${MEDIAMTX_API_PORT:-9997}" 9997)")"
    remember_tcp_port "${MTX_API_PORT}"
    MTX_RTSP_PORT="$(reserve_tcp_port "$(normalize_port "${MEDIAMTX_RTSP_PORT:-8554}" 8554)")"
    remember_tcp_port "${MTX_RTSP_PORT}"
    MTX_HLS_PORT="$(reserve_tcp_port "$(normalize_port "${MEDIAMTX_HLS_PORT:-8888}" 8888)")"
    remember_tcp_port "${MTX_HLS_PORT}"
    MTX_WEBRTC_PORT="$(reserve_tcp_port "$(normalize_port "${MEDIAMTX_WEBRTC_PORT:-8889}" 8889)")"
    remember_tcp_port "${MTX_WEBRTC_PORT}"
    MTX_WEBRTC_UDP_PORT="$(find_free_udp_port "$(normalize_port "${MEDIAMTX_WEBRTC_UDP_PORT:-8189}" 8189)")"
}

write_env_file() {
    local hls_base proxy_prefix public_base public_host public_scheme webrtc_base
    hls_base="${MEDIAMTX_HLS_BASE_URL:-}"
    webrtc_base="${MEDIAMTX_WEBRTC_BASE_URL:-}"
    proxy_prefix="${MEDIAMTX_PROXY_PREFIX:-}"
    public_base="${MEDIAMTX_PUBLIC_BASE_URL:-}"
    public_host="${MEDIAMTX_PUBLIC_HOST:-}"
    public_scheme="${MEDIAMTX_PUBLIC_SCHEME:-http}"

    if [[ -n "${proxy_prefix}" ]]; then
        proxy_prefix="$(normalize_path_prefix "${proxy_prefix}")"
        if [[ -n "${public_base}" ]]; then
            public_base="$(normalize_url_base "${public_base}")"
            [[ -z "${hls_base}" ]] && hls_base="${public_base}${proxy_prefix}/hls"
            [[ -z "${webrtc_base}" ]] && webrtc_base="${public_base}${proxy_prefix}/webrtc"
        elif [[ -n "${public_host}" ]]; then
            [[ -z "${hls_base}" ]] && hls_base="${public_scheme}://${public_host}${proxy_prefix}/hls"
            [[ -z "${webrtc_base}" ]] && webrtc_base="${public_scheme}://${public_host}${proxy_prefix}/webrtc"
        else
            [[ -z "${hls_base}" ]] && hls_base="${proxy_prefix}/hls"
            [[ -z "${webrtc_base}" ]] && webrtc_base="${proxy_prefix}/webrtc"
        fi
    elif [[ -n "${public_host}" ]]; then
        [[ -z "${hls_base}" ]] && hls_base="${public_scheme}://${public_host}:${MTX_HLS_PORT}"
        [[ -z "${webrtc_base}" ]] && webrtc_base="${public_scheme}://${public_host}:${MTX_WEBRTC_PORT}"
    else
        [[ -z "${hls_base}" ]] && hls_base="$(read_existing_env_value "MEDIAMTX_HLS_BASE_URL" || true)"
        [[ -z "${webrtc_base}" ]] && webrtc_base="$(read_existing_env_value "MEDIAMTX_WEBRTC_BASE_URL" || true)"
    fi

    cat >"${ENV_FILE}" <<EOF
# Generated by scripts/deploy_mediamtx.sh. Read by ikaros-api camera module.
MEDIAMTX_API_URL=http://127.0.0.1:${MTX_API_PORT}
MEDIAMTX_RTSP_PORT=${MTX_RTSP_PORT}
MEDIAMTX_HLS_PORT=${MTX_HLS_PORT}
MEDIAMTX_WEBRTC_PORT=${MTX_WEBRTC_PORT}
MEDIAMTX_WEBRTC_UDP_PORT=${MTX_WEBRTC_UDP_PORT}
EOF

    [[ -n "${hls_base}" ]] && printf 'MEDIAMTX_HLS_BASE_URL=%s\n' "${hls_base}" >>"${ENV_FILE}"
    [[ -n "${webrtc_base}" ]] && printf 'MEDIAMTX_WEBRTC_BASE_URL=%s\n' "${webrtc_base}" >>"${ENV_FILE}"
}

write_config() {
    mkdir -p "${CONFIG_DIR}" "$(dirname "${CONFIG_FILE}")" "$(dirname "${ENV_FILE}")"
    resolve_ports
    write_env_file
    cat >"${CONFIG_FILE}" <<EOF
# Generated by scripts/deploy_mediamtx.sh for Ikaros camera streaming.

logLevel: info

authMethod: http
authHTTPAddress: ${API_AUTH_URL}
authHTTPExclude:
  - action: api
  - action: metrics
  - action: pprof

api: yes
apiAddress: 127.0.0.1:${MTX_API_PORT}

rtsp: yes
rtspTransports: [tcp]
rtspAddress: :${MTX_RTSP_PORT}

hls: yes
hlsAddress: :${MTX_HLS_PORT}
hlsAllowOrigins: ['*']
hlsVariant: lowLatency
hlsSegmentCount: 7
hlsSegmentDuration: 1s
hlsPartDuration: 200ms
hlsMuxerCloseAfter: 60s

webrtc: yes
webrtcAddress: :${MTX_WEBRTC_PORT}
webrtcAllowOrigins: ['*']
webrtcLocalUDPAddress: :${MTX_WEBRTC_UDP_PORT}
webrtcLocalTCPAddress: ''

rtmp: no
srt: no

paths:
  all_others:
EOF
    echo "Wrote MediaMTX config: ${CONFIG_FILE}"
    echo "Wrote Ikaros MediaMTX env: ${ENV_FILE}"
    echo "Selected ports: api=${MTX_API_PORT}, rtsp=${MTX_RTSP_PORT}, hls=${MTX_HLS_PORT}, webrtc=${MTX_WEBRTC_PORT}, webrtc_udp=${MTX_WEBRTC_UDP_PORT}"
}

start_container() {
    require_docker
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
    write_config
    docker run -d \
        --name "${CONTAINER_NAME}" \
        --restart unless-stopped \
        --network host \
        -v "${CONFIG_FILE}:/mediamtx.yml:ro" \
        "${IMAGE}" /mediamtx.yml
    echo "MediaMTX started: ${CONTAINER_NAME}"
    echo "Status: docker ps --filter name=${CONTAINER_NAME}"
    echo "Logs:   scripts/deploy_mediamtx.sh logs"
    echo "Env:    ${ENV_FILE}"
}

stop_container() {
    require_docker
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
    echo "MediaMTX stopped: ${CONTAINER_NAME}"
}

case "${ACTION}" in
    up)
        start_container
        ;;
    down)
        stop_container
        ;;
    restart)
        stop_container
        start_container
        ;;
    logs)
        require_docker
        docker logs -f "${CONTAINER_NAME}"
        ;;
    status)
        require_docker
        docker ps --filter "name=${CONTAINER_NAME}"
        ;;
    config)
        write_config
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
