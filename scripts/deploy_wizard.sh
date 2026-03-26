#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"
ENV_TEMPLATE="${PROJECT_DIR}/.env.example"
MODELS_FILE="${PROJECT_DIR}/config/models.json"
MODELS_TEMPLATE="${PROJECT_DIR}/config/models.example.json"
LOG_DIR="${PROJECT_DIR}/data/logs"
RUN_DIR="${PROJECT_DIR}/data/run"
API_PORT="8764"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.yml"

usage() {
    cat <<'EOF'
Usage:
  scripts/deploy_wizard.sh

Description:
  Interactive deployment wizard for Ikaros Core and Ikaros API.
  It can:
    - initialize .env / config/models.json from templates
    - optionally configure primary / routing provider, baseUrl, apiKey, and model bindings
    - deploy ikaros and ikaros-api with shell background, systemd / launchd, or docker compose
    - print the Web URL for finishing bootstrap in browser
EOF
}

info() {
    printf '[INFO] %s\n' "$*"
}

warn() {
    printf '[WARN] %s\n' "$*" >&2
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

os_kind() {
    local uname_s
    uname_s="$(uname -s 2>/dev/null || echo unknown)"
    case "${uname_s}" in
        Linux) printf 'linux\n' ;;
        Darwin) printf 'macos\n' ;;
        *) printf 'other\n' ;;
    esac
}

confirm() {
    local prompt="${1}"
    local default="${2:-Y}"
    local answer=""
    local hint="[Y/n]"
    if [[ "${default}" == "N" ]]; then
        hint="[y/N]"
    fi

    while true; do
        read -r -p "${prompt} ${hint} " answer || true
        answer="${answer:-${default}}"
        case "${answer}" in
            Y|y|yes|YES)
                return 0
                ;;
            N|n|no|NO)
                return 1
                ;;
            *)
                echo "Please answer yes or no."
                ;;
        esac
    done
}

prompt_text() {
    local __result_var="$1"
    local prompt="$2"
    local default="${3:-}"
    local value=""

    if [[ -n "${default}" ]]; then
        read -r -p "${prompt} [${default}]: " value || true
        value="${value:-${default}}"
    else
        read -r -p "${prompt}: " value || true
    fi

    printf -v "${__result_var}" '%s' "${value}"
}

prompt_secret() {
    local __result_var="$1"
    local prompt="$2"
    local default="${3:-}"
    local value=""

    if [[ -n "${default}" ]]; then
        read -r -s -p "${prompt} [已配置，回车保持不变]: " value || true
        echo
        value="${value:-${default}}"
    else
        while true; do
            read -r -s -p "${prompt}: " value || true
            echo
            if [[ -n "${value}" ]]; then
                break
            fi
            echo "不能为空。"
        done
    fi

    printf -v "${__result_var}" '%s' "${value}"
}

choose_option() {
    local __result_var="$1"
    local prompt="$2"
    local default_index="$3"
    shift 3
    local options=("$@")
    local answer=""
    local selected=""

    echo
    echo "${prompt}"
    local idx=1
    local entry value label
    for entry in "${options[@]}"; do
        value="${entry%%|*}"
        label="${entry#*|}"
        printf '  %d) %s\n' "${idx}" "${label}"
        idx=$((idx + 1))
    done

    while true; do
        read -r -p "请选择 [${default_index}]: " answer || true
        answer="${answer:-${default_index}}"
        if [[ "${answer}" =~ ^[0-9]+$ ]] && (( answer >= 1 && answer <= ${#options[@]} )); then
            selected="${options[$((answer - 1))]%%|*}"
            printf -v "${__result_var}" '%s' "${selected}"
            return 0
        fi
        echo "请输入有效的序号。"
    done
}

ensure_seed_file() {
    local target="$1"
    local template="$2"
    if [[ -f "${target}" ]]; then
        return 0
    fi
    if [[ ! -f "${template}" ]]; then
        echo "Template file not found: ${template}" >&2
        exit 1
    fi
    mkdir -p "$(dirname "${target}")"
    cp "${template}" "${target}"
    info "Initialized $(basename "${target}") from template."
}

current_model_key() {
    local role="$1"
    python3 - "${MODELS_FILE}" "${role}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
role = sys.argv[2]
if not path.exists():
    print("")
    raise SystemExit(0)
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)
model = data.get("model") or {}
value = model.get(role) if isinstance(model, dict) else ""
print(str(value or "").strip())
PY
}

current_role_field() {
    local role="$1"
    local field="$2"
    python3 - "${MODELS_FILE}" "${role}" "${field}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
role = sys.argv[2].strip()
field = sys.argv[3].strip()
if not path.exists():
    print("")
    raise SystemExit(0)

try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)

model_bindings = data.get("model") or {}
if not isinstance(model_bindings, dict):
    print("")
    raise SystemExit(0)
model_key = str(model_bindings.get(role) or "").strip()
provider_name, _, model_id = model_key.partition("/")

providers = data.get("providers") or {}
provider = providers.get(provider_name) if isinstance(providers, dict) else None
if not isinstance(provider, dict):
    provider = {}

models = provider.get("models") or []
target_model = {}
if isinstance(models, list):
    for item in models:
        if isinstance(item, dict) and str(item.get("id") or "").strip() == model_id:
            target_model = item
            break

if field == "provider_name":
    print(provider_name)
elif field == "model_id":
    print(model_id)
elif field == "base_url":
    print(str(provider.get("baseUrl") or ""))
elif field == "api_key":
    print(str(provider.get("apiKey") or ""))
elif field == "api_style":
    print(str(provider.get("api") or ""))
elif field == "display_name":
    print(str(target_model.get("name") or ""))
elif field == "reasoning":
    print("true" if bool(target_model.get("reasoning")) else "false")
else:
    print("")
PY
}

provider_field() {
    local provider_name="$1"
    local field="$2"
    python3 - "${MODELS_FILE}" "${provider_name}" "${field}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
provider_name = sys.argv[2].strip()
field = sys.argv[3].strip()
if not path.exists() or not provider_name:
    print("")
    raise SystemExit(0)

try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)

providers = data.get("providers") or {}
provider = providers.get(provider_name) if isinstance(providers, dict) else None
if not isinstance(provider, dict):
    print("")
    raise SystemExit(0)

mapping = {
    "base_url": str(provider.get("baseUrl") or ""),
    "api_key": str(provider.get("apiKey") or ""),
    "api_style": str(provider.get("api") or ""),
}
print(mapping.get(field, ""))
PY
}

available_model_keys() {
    python3 - "${MODELS_FILE}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(0)

try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(0)

keys = []
providers = data.get("providers") or {}
if isinstance(providers, dict):
    for provider_name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        for model in provider.get("models") or []:
            if not isinstance(model, dict):
                continue
            model_id = str(model.get("id") or "").strip()
            if model_id:
                keys.append(f"{provider_name}/{model_id}")

pools = data.get("models") or {}
if isinstance(pools, dict):
    for role_pool in pools.values():
        if isinstance(role_pool, dict):
            keys.extend(str(key).strip() for key in role_pool.keys() if str(key).strip())
        elif isinstance(role_pool, list):
            keys.extend(str(item).strip() for item in role_pool if str(item).strip())

bindings = data.get("model") or {}
if isinstance(bindings, dict):
    keys.extend(str(value).strip() for value in bindings.values() if str(value).strip())

deduped = []
for key in keys:
    if key and key not in deduped:
        deduped.append(key)

for key in deduped:
    print(key)
PY
}

model_key_exists() {
    local model_key="$1"
    python3 - "${MODELS_FILE}" "${model_key}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
model_key = sys.argv[2].strip()
if not path.exists() or not model_key:
    raise SystemExit(1)
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)

providers = data.get("providers") or {}
if not isinstance(providers, dict):
    raise SystemExit(1)
provider_name, _, model_id = model_key.partition("/")
provider = providers.get(provider_name)
if not isinstance(provider, dict):
    raise SystemExit(1)
for model in provider.get("models") or []:
    if isinstance(model, dict) and str(model.get("id") or "").strip() == model_id:
        raise SystemExit(0)
raise SystemExit(1)
PY
}

update_model_roles() {
    local primary_provider="$1"
    local primary_model_id="$2"
    local primary_base_url="$3"
    local primary_api_key="$4"
    local primary_api_style="$5"
    local routing_provider="$6"
    local routing_model_id="$7"
    local routing_base_url="$8"
    local routing_api_key="$9"
    local routing_api_style="${10}"
    python3 - "${MODELS_FILE}" \
        "${primary_provider}" "${primary_model_id}" "${primary_base_url}" "${primary_api_key}" "${primary_api_style}" \
        "${routing_provider}" "${routing_model_id}" "${routing_base_url}" "${routing_api_key}" "${routing_api_style}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])

def role_args(offset: int):
    return (
        sys.argv[offset].strip(),
        sys.argv[offset + 1].strip(),
        sys.argv[offset + 2].strip(),
        sys.argv[offset + 3].strip(),
        sys.argv[offset + 4].strip(),
    )

primary_provider, primary_model_id, primary_base_url, primary_api_key, primary_api_style = role_args(2)
routing_provider, routing_model_id, routing_base_url, routing_api_key, routing_api_style = role_args(7)

data = {}
if path.exists():
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Failed to parse models.json: {exc}")

if not isinstance(data, dict):
    data = {}

data.setdefault("model", {})
data.setdefault("models", {})
data.setdefault("providers", {})
data.setdefault("mode", "merge")

if not isinstance(data["model"], dict):
    data["model"] = {}
if not isinstance(data["models"], dict):
    data["models"] = {}
if not isinstance(data["providers"], dict):
    data["providers"] = {}

def upsert(role: str, provider_name: str, model_id: str, base_url: str, api_key: str, api_style: str):
    if not provider_name or not model_id:
        return

    model_key = f"{provider_name}/{model_id}"
    provider_entry = data["providers"].get(provider_name)
    if not isinstance(provider_entry, dict):
        provider_entry = {}

    provider_models = provider_entry.get("models")
    if not isinstance(provider_models, list):
        provider_models = []

    existing = None
    existing_index = -1
    for index, item in enumerate(provider_models):
        if isinstance(item, dict) and str(item.get("id") or "").strip() == model_id:
            existing = dict(item)
            existing_index = index
            break

    payload = dict(existing or {})
    payload["id"] = model_id
    payload["name"] = str(payload.get("name") or model_id)
    payload["reasoning"] = bool(payload.get("reasoning", role == "primary"))
    payload["input"] = payload.get("input") if isinstance(payload.get("input"), list) and payload.get("input") else (
        ["text", "image", "voice"] if role == "primary" else ["text"]
    )
    payload["cost"] = payload.get("cost") if isinstance(payload.get("cost"), dict) else {
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
    }
    payload["contextWindow"] = int(payload.get("contextWindow") or 1000000)
    payload["maxTokens"] = int(payload.get("maxTokens") or 65536)

    if existing_index >= 0:
        provider_models[existing_index] = payload
    else:
        provider_models.append(payload)

    provider_entry["baseUrl"] = base_url
    provider_entry["apiKey"] = api_key
    provider_entry["api"] = api_style or str(provider_entry.get("api") or "openai-completions")
    provider_entry["models"] = provider_models
    data["providers"][provider_name] = provider_entry

    data["model"][role] = model_key
    role_pool = data["models"].get(role)
    if isinstance(role_pool, list):
        role_pool = {str(item): {} for item in role_pool if str(item).strip()}
    if not isinstance(role_pool, dict):
        role_pool = {}
    role_pool.setdefault(model_key, {})
    data["models"][role] = role_pool

for args in (
    ("primary", primary_provider, primary_model_id, primary_base_url, primary_api_key, primary_api_style),
    ("routing", routing_provider, routing_model_id, routing_base_url, routing_api_key, routing_api_style),
):
    role, provider_name, model_id, base_url, api_key, api_style = args
    if not provider_name and not model_id:
        continue
    upsert(role, provider_name, model_id, base_url, api_key, api_style)

path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

print_model_reference() {
    local -a keys=()

    mapfile -t keys < <(available_model_keys)

    echo
    echo "当前 models.json 里的已知模型键："
    if [[ "${#keys[@]}" -gt 0 ]]; then
        local idx=1
        local item
        for item in "${keys[@]}"; do
            printf '  %d) %s\n' "${idx}" "${item}"
            idx=$((idx + 1))
        done
    else
        echo "  (当前 models.json 中还没有可枚举的模型键，将改为手动输入)"
    fi
}

ROLE_MODEL_PROVIDER_NAME=""
ROLE_MODEL_ID=""
ROLE_BASE_URL=""
ROLE_API_KEY=""
ROLE_API_STYLE=""
ROLE_MODEL_KEY=""

configure_role_model() {
    local role="$1"
    local role_label="$2"
    local default_provider="${3:-}"
    local default_model_id="${4:-}"
    local default_base_url="${5:-}"
    local default_api_key="${6:-}"
    local default_api_style="${7:-openai-completions}"
    local provider_name="" model_id="" base_url="" api_key="" api_style=""
    local suggested_base_url="" suggested_api_key="" suggested_api_style=""

    print_model_reference

    prompt_text provider_name "请输入 ${role_label} provider 名称" "${default_provider}"
    provider_name="$(printf '%s' "${provider_name}" | xargs)"
    if [[ -z "${provider_name}" ]]; then
        echo "${role_label} provider 不能为空。" >&2
        exit 1
    fi

    prompt_text model_id "请输入 ${role_label} model_id（最终 key 为 ${provider_name}/...）" "${default_model_id}"
    model_id="$(printf '%s' "${model_id}" | xargs)"
    if [[ -z "${model_id}" ]]; then
        echo "${role_label} model_id 不能为空。" >&2
        exit 1
    fi

    suggested_base_url="$(provider_field "${provider_name}" base_url)"
    if [[ -z "${suggested_base_url}" ]]; then
        suggested_base_url="${default_base_url}"
    fi
    prompt_text base_url "请输入 ${role_label} baseUrl" "${suggested_base_url}"
    base_url="$(printf '%s' "${base_url}" | xargs)"
    if [[ -z "${base_url}" ]]; then
        echo "${role_label} baseUrl 不能为空。" >&2
        exit 1
    fi

    suggested_api_key="$(provider_field "${provider_name}" api_key)"
    if [[ -z "${suggested_api_key}" ]]; then
        suggested_api_key="${default_api_key}"
    fi
    prompt_secret api_key "请输入 ${role_label} apiKey" "${suggested_api_key}"
    if [[ -z "${api_key}" ]]; then
        echo "${role_label} apiKey 不能为空。" >&2
        exit 1
    fi

    suggested_api_style="$(provider_field "${provider_name}" api_style)"
    if [[ -z "${suggested_api_style}" ]]; then
        suggested_api_style="${default_api_style:-openai-completions}"
    fi
    prompt_text api_style "请输入 ${role_label} API 风格" "${suggested_api_style}"
    api_style="$(printf '%s' "${api_style}" | xargs)"
    if [[ -z "${api_style}" ]]; then
        api_style="openai-completions"
    fi

    ROLE_MODEL_PROVIDER_NAME="${provider_name}"
    ROLE_MODEL_ID="${model_id}"
    ROLE_BASE_URL="${base_url}"
    ROLE_API_KEY="${api_key}"
    ROLE_API_STYLE="${api_style}"
    ROLE_MODEL_KEY="${provider_name}/${model_id}"
}

ensure_uv_sync() {
    require_command uv
    info "Running uv sync ..."
    (
        cd "${PROJECT_DIR}"
        uv sync
    )
}

ensure_web_build() {
    info "Building Web frontend ..."
    if [[ ! -x "${SCRIPT_DIR}/build_web.sh" ]]; then
        chmod +x "${SCRIPT_DIR}/build_web.sh"
    fi
    "${SCRIPT_DIR}/build_web.sh" --install
}

stop_background_if_requested() {
    local name="$1"
    local pid_file="$2"

    if [[ ! -f "${pid_file}" ]]; then
        return 0
    fi

    local pid
    pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if [[ -z "${pid}" || ! "${pid}" =~ ^[0-9]+$ ]]; then
        rm -f "${pid_file}"
        return 0
    fi

    if ! kill -0 "${pid}" >/dev/null 2>&1; then
        rm -f "${pid_file}"
        return 0
    fi

    if confirm "${name} 似乎已经在运行（PID ${pid}）。是否重启它？" "Y"; then
        kill "${pid}" >/dev/null 2>&1 || true
        sleep 1
        rm -f "${pid_file}"
        return 0
    fi

    info "Keeping existing ${name} process."
    return 1
}

start_background_service() {
    local name="$1"
    local pid_file="$2"
    local log_file="$3"
    shift 3
    local -a cmd=("$@")

    mkdir -p "${LOG_DIR}" "${RUN_DIR}"

    if ! stop_background_if_requested "${name}" "${pid_file}"; then
        return 0
    fi

    (
        cd "${PROJECT_DIR}"
        nohup "${cmd[@]}" >"${log_file}" 2>&1 &
        echo $! >"${pid_file}"
    )

    local pid
    pid="$(cat "${pid_file}")"
    info "Started ${name} in background (PID ${pid}). Log: ${log_file}"
}

resolve_compose_cmd() {
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        COMPOSE_CMD=(docker compose)
        return 0
    fi
    if command -v docker-compose >/dev/null 2>&1; then
        COMPOSE_CMD=(docker-compose)
        return 0
    fi
    echo "Failed to locate docker compose or docker-compose." >&2
    exit 1
}

run_compose_up() {
    local -a extra_args=("$@")
    resolve_compose_cmd
    (
        cd "${PROJECT_DIR}"
        "${COMPOSE_CMD[@]}" -f "${COMPOSE_FILE}" up --build -d "${extra_args[@]}"
    )
}

infer_display_host() {
    local env_host=""
    if [[ -f "${ENV_FILE}" ]] && command -v python3 >/dev/null 2>&1; then
        env_host="$(python3 - "${ENV_FILE}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
value = ""
for line in path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, raw = line.split("=", 1)
    if key.strip() != "SERVER_IP":
        continue
    raw = raw.strip().strip('"').strip("'")
    value = raw
    break
print(value)
PY
)"
    fi

    if [[ -n "${env_host}" ]]; then
        printf '%s\n' "${env_host}"
        return 0
    fi

    if command -v hostname >/dev/null 2>&1; then
        local ip
        ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
        if [[ -n "${ip}" ]]; then
            printf '%s\n' "${ip}"
            return 0
        fi
    fi

    printf '127.0.0.1\n'
}

install_service_mode() {
    local service_kind="$1"
    local os_name="$2"

    if [[ "${service_kind}" == "ikaros" ]]; then
        if [[ "${os_name}" == "linux" ]]; then
            "${SCRIPT_DIR}/install_systemd_service.sh" --service-name ikaros --runner scripts/run_ikaros.sh
            return 0
        fi
        if [[ "${os_name}" == "macos" ]]; then
            "${SCRIPT_DIR}/install_launchd_service.sh" --label com.ikaros.core --runner scripts/run_ikaros.sh
            return 0
        fi
    fi

    if [[ "${service_kind}" == "api" ]]; then
        if [[ "${os_name}" == "linux" ]]; then
            "${SCRIPT_DIR}/install_systemd_service.sh" --service-name ikaros-api --runner scripts/run_api.sh
            return 0
        fi
        if [[ "${os_name}" == "macos" ]]; then
            "${SCRIPT_DIR}/install_launchd_service.sh" --label com.ikaros.api --runner scripts/run_api.sh
            return 0
        fi
    fi

    echo "Unsupported service install combination: ${service_kind} / ${os_name}" >&2
    exit 1
}

print_next_steps() {
    local display_host="$1"
    local ikaros_mode="$2"
    local api_mode="$3"
    local model_mode="$4"
    local primary_key="$5"
    local routing_key="$6"

    echo
    echo "Deployment summary"
    echo "------------------"
    echo "Ikaros Core : ${ikaros_mode}"
    echo "Ikaros API  : ${api_mode}"
    echo "Models      : ${model_mode}"
    if [[ -n "${primary_key}" ]]; then
        echo "Primary     : ${primary_key}"
    fi
    if [[ -n "${routing_key}" ]]; then
        echo "Routing     : ${routing_key}"
    fi
    echo
    if [[ "${api_mode}" != "skip" ]]; then
        echo "Next:"
        echo "1. Open http://${display_host}:${API_PORT}/login"
        echo "2. Complete the first admin bootstrap"
        echo "3. Enter /admin/setup to finish models / SOUL / USER / channels initialization"
        echo
    else
        echo "API was not deployed in this run, so Web bootstrap is not available yet."
        echo "Deploy ikaros-api later, then open /login to finish initialization."
        echo
    fi

    if [[ "${ikaros_mode}" == "shell_bg" ]]; then
        echo "Core logs: tail -f ${LOG_DIR}/ikaros.out.log"
    elif [[ "${ikaros_mode}" == "systemd" ]]; then
        echo "Core status: sudo systemctl status ikaros"
    elif [[ "${ikaros_mode}" == "launchd" ]]; then
        echo "Core status: launchctl print gui/$(id -u)/com.ikaros.core"
    elif [[ "${ikaros_mode}" == "compose" ]]; then
        echo "Core logs: docker compose logs -f ikaros"
    fi

    if [[ "${api_mode}" == "shell_bg" ]]; then
        echo "API logs:  tail -f ${LOG_DIR}/ikaros-api.out.log"
    elif [[ "${api_mode}" == "systemd" ]]; then
        echo "API status: sudo systemctl status ikaros-api"
    elif [[ "${api_mode}" == "launchd" ]]; then
        echo "API status: launchctl print gui/$(id -u)/com.ikaros.api"
    elif [[ "${api_mode}" == "compose" ]]; then
        echo "API logs:  docker compose logs -f ikaros-api"
    fi
}

main() {
    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
        usage
        exit 0
    fi

    local current_os
    current_os="$(os_kind)"

    echo "Ikaros Deployment Wizard"
    echo "========================"
    echo "Project: ${PROJECT_DIR}"
    echo

    ensure_seed_file "${ENV_FILE}" "${ENV_TEMPLATE}"
    ensure_seed_file "${MODELS_FILE}" "${MODELS_TEMPLATE}"

    local -a core_options api_options
    case "${current_os}" in
        linux)
            core_options=(
                "shell_bg|后台脚本模式（nohup 启动）"
                "systemd|systemd 服务"
                "compose|Docker Compose"
                "skip|暂不部署"
            )
            api_options=(
                "shell_bg|后台脚本模式（nohup 启动）"
                "systemd|systemd 服务"
                "compose|Docker Compose"
                "skip|暂不部署"
            )
            ;;
        macos)
            core_options=(
                "shell_bg|后台脚本模式（nohup 启动）"
                "launchd|launchd 服务"
                "compose|Docker Compose"
                "skip|暂不部署"
            )
            api_options=(
                "shell_bg|后台脚本模式（nohup 启动）"
                "launchd|launchd 服务"
                "compose|Docker Compose"
                "skip|暂不部署"
            )
            ;;
        *)
            core_options=(
                "shell_bg|后台脚本模式（nohup 启动）"
                "compose|Docker Compose"
                "skip|暂不部署"
            )
            api_options=(
                "shell_bg|后台脚本模式（nohup 启动）"
                "compose|Docker Compose"
                "skip|暂不部署"
            )
            ;;
    esac

    local ikaros_mode api_mode model_mode
    choose_option ikaros_mode "选择 Ikaros Core 的部署方式" 1 "${core_options[@]}"
    choose_option api_mode "选择 Ikaros API 的部署方式" 1 "${api_options[@]}"
    choose_option model_mode "Primary / Routing 模型现在怎么处理？" 2 \
        "now|现在写入 config/models.json" \
        "later|稍后到 Web 初始化页配置"

    local primary_key=""
    local routing_key=""
    local primary_provider_name=""
    local primary_model_id=""
    local primary_base_url=""
    local primary_api_key=""
    local primary_api_style=""
    local routing_provider_name=""
    local routing_model_id=""
    local routing_base_url=""
    local routing_api_key=""
    local routing_api_style=""
    if [[ "${model_mode}" == "now" ]]; then
        require_command python3
        configure_role_model \
            "primary" \
            "Primary" \
            "$(current_role_field primary provider_name)" \
            "$(current_role_field primary model_id)" \
            "$(current_role_field primary base_url)" \
            "$(current_role_field primary api_key)" \
            "$(current_role_field primary api_style)"
        primary_provider_name="${ROLE_MODEL_PROVIDER_NAME}"
        primary_model_id="${ROLE_MODEL_ID}"
        primary_base_url="${ROLE_BASE_URL}"
        primary_api_key="${ROLE_API_KEY}"
        primary_api_style="${ROLE_API_STYLE}"
        primary_key="${ROLE_MODEL_KEY}"

        if confirm "Routing 是否复用 Primary 的 provider / baseUrl / apiKey / API 风格？" "Y"; then
            print_model_reference
            prompt_text routing_model_id "请输入 Routing model_id（最终 key 为 ${primary_provider_name}/...）" "$(current_role_field routing model_id)"
            routing_model_id="$(printf '%s' "${routing_model_id}" | xargs)"
            if [[ -z "${routing_model_id}" ]]; then
                echo "Routing model_id 不能为空。" >&2
                exit 1
            fi
            routing_provider_name="${primary_provider_name}"
            routing_base_url="${primary_base_url}"
            routing_api_key="${primary_api_key}"
            routing_api_style="${primary_api_style}"
            routing_key="${routing_provider_name}/${routing_model_id}"
        else
            configure_role_model \
                "routing" \
                "Routing" \
                "$(current_role_field routing provider_name)" \
                "$(current_role_field routing model_id)" \
                "$(current_role_field routing base_url)" \
                "$(current_role_field routing api_key)" \
                "$(current_role_field routing api_style)"
            routing_provider_name="${ROLE_MODEL_PROVIDER_NAME}"
            routing_model_id="${ROLE_MODEL_ID}"
            routing_base_url="${ROLE_BASE_URL}"
            routing_api_key="${ROLE_API_KEY}"
            routing_api_style="${ROLE_API_STYLE}"
            routing_key="${ROLE_MODEL_KEY}"
        fi

        update_model_roles \
            "${primary_provider_name}" "${primary_model_id}" "${primary_base_url}" "${primary_api_key}" "${primary_api_style}" \
            "${routing_provider_name}" "${routing_model_id}" "${routing_base_url}" "${routing_api_key}" "${routing_api_style}"
        info "Updated provider/baseUrl/apiKey plus model.primary and model.routing in ${MODELS_FILE}"
    fi

    local need_uv_sync=0
    local need_web_build=0
    local need_compose=0

    case "${ikaros_mode}" in
        shell_bg|systemd|launchd)
            need_uv_sync=1
            ;;
        compose)
            need_compose=1
            ;;
    esac

    case "${api_mode}" in
        shell_bg|systemd|launchd)
            need_uv_sync=1
            need_web_build=1
            ;;
        compose)
            need_compose=1
            ;;
    esac

    if [[ "${need_uv_sync}" -eq 1 ]]; then
        ensure_uv_sync
    fi

    if [[ "${need_web_build}" -eq 1 ]]; then
        ensure_web_build
    fi

    case "${ikaros_mode}" in
        shell_bg)
            start_background_service \
                "ikaros" \
                "${RUN_DIR}/ikaros.pid" \
                "${LOG_DIR}/ikaros.out.log" \
                "${SCRIPT_DIR}/run_ikaros.sh"
            ;;
        systemd|launchd)
            install_service_mode "ikaros" "${current_os}"
            ;;
    esac

    case "${api_mode}" in
        shell_bg)
            start_background_service \
                "ikaros-api" \
                "${RUN_DIR}/ikaros-api.pid" \
                "${LOG_DIR}/ikaros-api.out.log" \
                "${SCRIPT_DIR}/run_api.sh" --skip-build --host 0.0.0.0 --port "${API_PORT}"
            ;;
        systemd|launchd)
            install_service_mode "api" "${current_os}"
            ;;
    esac

    if [[ "${need_compose}" -eq 1 ]]; then
        case "${ikaros_mode}:${api_mode}" in
            compose:compose)
                run_compose_up ikaros ikaros-api
                ;;
            compose:*)
                run_compose_up ikaros
                ;;
            *:compose)
                run_compose_up --no-deps ikaros-api
                ;;
        esac
    fi

    local display_host
    display_host="$(infer_display_host)"
    print_next_steps "${display_host}" "${ikaros_mode}" "${api_mode}" "${model_mode}" "${primary_key}" "${routing_key}"
}

main "$@"
