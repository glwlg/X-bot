const state = {
  workers: [],
  selectedWorkerId: "",
}

const byId = (id) => document.getElementById(id)

const SYSTEM_LABELS = {
  core_chat_execution_mode: "核心对话执行模式",
  core_chat_worker_backend: "核心对话默认后端",
  heartbeat_enabled: "心跳功能开关",
  heartbeat_mode: "心跳运行模式",
  web_dashboard_enabled: "看板开关",
  web_dashboard_host: "看板监听地址",
  web_dashboard_port: "看板监听端口",
}

function setText(id, value) {
  const node = byId(id)
  if (node) {
    node.textContent = value
  }
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options)
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(`${response.status} ${response.statusText} ${detail}`.trim())
  }
  return response.json()
}

function authHeader() {
  const token = byId("token")?.value.trim() || ""
  if (!token) {
    return {}
  }
  return { "X-XBOT-Token": token }
}

function localizeExecutionMode(value) {
  const raw = String(value || "").toLowerCase()
  if (raw === "worker_only") {
    return "仅执行者"
  }
  if (raw === "worker_preferred") {
    return "优先执行者"
  }
  if (raw === "orchestrator") {
    return "核心编排"
  }
  return String(value || "-")
}

function localizeBackend(value) {
  const raw = String(value || "").toLowerCase()
  if (raw === "core-agent") {
    return "核心代理"
  }
  if (raw === "codex") {
    return "编码引擎"
  }
  if (raw === "gemini-cli") {
    return "双子引擎"
  }
  if (raw === "shell") {
    return "系统终端"
  }
  return String(value || "-")
}

function localizeWorkerStatus(value) {
  const raw = String(value || "ready").toLowerCase()
  if (raw === "busy") {
    return "忙碌"
  }
  if (raw === "paused") {
    return "暂停"
  }
  return "就绪"
}

function localizeBool(value) {
  return value ? "开启" : "关闭"
}

function localizeHeartbeatMode(value) {
  const raw = String(value || "").toLowerCase()
  if (raw === "observe") {
    return "观察模式"
  }
  if (raw === "live") {
    return "实时模式"
  }
  return String(value || "-")
}

function renderSystem(system) {
  const host = byId("system-grid")
  host.innerHTML = ""
  const effective = system?.effective || {}
  const snapshot = system?.snapshot || {}

  const rows = [
    [SYSTEM_LABELS.core_chat_execution_mode, localizeExecutionMode(effective.core_chat_execution_mode)],
    [SYSTEM_LABELS.core_chat_worker_backend, localizeBackend(effective.core_chat_worker_backend)],
    [SYSTEM_LABELS.heartbeat_enabled, localizeBool(Boolean(effective.heartbeat_enabled))],
    [SYSTEM_LABELS.heartbeat_mode, localizeHeartbeatMode(effective.heartbeat_mode)],
    [SYSTEM_LABELS.web_dashboard_enabled, localizeBool(Boolean(effective.web_dashboard_enabled))],
    [SYSTEM_LABELS.web_dashboard_host, effective.web_dashboard_host || "-"],
    [SYSTEM_LABELS.web_dashboard_port, String(effective.web_dashboard_port || "-")],
    ["系统快照版本", String(system?.snapshot_version || "1")],
    ["快照执行模式", localizeExecutionMode(snapshot.core_chat_execution_mode)],
    ["快照默认后端", localizeBackend(snapshot.core_chat_worker_backend)],
  ]

  rows.forEach(([key, value]) => {
    const row = document.createElement("article")
    row.className = "kv"
    row.innerHTML = `<p class="k">${esc(key)}</p><p class="v">${esc(value)}</p>`
    host.append(row)
  })
  setText("system-status", "已加载")
}

function renderManager(manager) {
  byId("manager-soul").value = String(manager?.soul_content || "")
  setText("manager-status", `已加载 ${String(manager?.updated_at || "")}`)
}

function renderWorkerCards(workers) {
  const host = byId("worker-cards")
  host.innerHTML = ""
  const list = Array.isArray(workers) ? workers : []

  if (!list.length) {
    host.innerHTML = '<p class="small">暂无执行者。</p>'
    setText("workers-note", "执行者：0")
    return
  }

  list.forEach((worker) => {
    const card = document.createElement("article")
    card.className = "worker-card"
    card.innerHTML = `
      <h3>${esc(worker.name || worker.id || "执行者")}</h3>
      <p>编号：${esc(worker.id || "")}</p>
      <p>后端：${esc(localizeBackend(worker.backend))}</p>
      <p>状态：${esc(localizeWorkerStatus(worker.status))}</p>
    `
    host.append(card)
  })
  setText("workers-note", `执行者：${list.length}`)
}

function renderWorkerSelect(workers) {
  const select = byId("worker-select")
  select.innerHTML = ""
  const list = Array.isArray(workers) ? workers : []
  if (!list.length) {
    state.selectedWorkerId = ""
    return
  }

  list.forEach((worker) => {
    const option = document.createElement("option")
    option.value = String(worker.id || "")
    option.textContent = `${worker.name || worker.id}（${worker.id}）`
    select.append(option)
  })

  const preferred = state.selectedWorkerId || "worker-main"
  const exists = list.some((item) => String(item.id) === preferred)
  state.selectedWorkerId = exists ? preferred : String(list[0].id || "")
  select.value = state.selectedWorkerId
}

function fillWorkerForm(worker) {
  if (!worker) {
    return
  }
  byId("worker-name").value = String(worker.name || worker.id || "")
  byId("worker-backend").value = String(worker.backend || "core-agent")
  byId("worker-runtime-status").value = String(worker.status || "ready")
  const deleteBtn = byId("worker-delete")
  deleteBtn.disabled = String(worker.id || "") === "worker-main"
}

async function loadWorkerSoul(workerId) {
  if (!workerId) {
    byId("worker-soul").value = ""
    return
  }
  const data = await fetchJson(`/api/v1/config/workers/${encodeURIComponent(workerId)}/soul`)
  byId("worker-soul").value = String(data.content || "")
}

async function hydrateFromBootstrap() {
  const data = await fetchJson("/api/v1/config/bootstrap")
  renderSystem(data.system || {})
  renderManager(data.manager || {})

  state.workers = Array.isArray(data.workers?.items) ? data.workers.items : []
  renderWorkerCards(state.workers)
  renderWorkerSelect(state.workers)

  const selected = state.workers.find((item) => String(item.id) === state.selectedWorkerId)
  fillWorkerForm(selected)
  await loadWorkerSoul(state.selectedWorkerId)
  setText("worker-status", "已加载")
}

function wireTabs() {
  const tabButtons = Array.from(document.querySelectorAll(".tab"))
  const contents = {
    system: byId("tab-system"),
    manager: byId("tab-manager"),
    worker: byId("tab-worker"),
  }

  tabButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const tab = String(button.dataset.tab || "system")
      tabButtons.forEach((item) => item.classList.toggle("active", item === button))
      Object.entries(contents).forEach(([name, node]) => {
        node.classList.toggle("active", name === tab)
      })
    })
  })
}

async function refreshWorkersOnly() {
  const data = await fetchJson("/api/v1/config/workers")
  state.workers = Array.isArray(data.workers?.items) ? data.workers.items : []
  renderWorkerCards(state.workers)
  renderWorkerSelect(state.workers)
  const selected = state.workers.find((item) => String(item.id) === state.selectedWorkerId)
  fillWorkerForm(selected)
}

async function handleManagerReload() {
  setText("manager-status", "重新加载中...")
  try {
    const data = await fetchJson("/api/v1/config/manager")
    renderManager(data.manager || {})
  } catch (err) {
    setText("manager-status", `加载失败：${String(err.message || err)}`)
  }
}

async function handleManagerSubmit(event) {
  event.preventDefault()
  setText("manager-status", "保存中...")
  try {
    const body = { soul_content: byId("manager-soul").value }
    const data = await fetchJson("/api/v1/config/manager", {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        ...authHeader(),
      },
      body: JSON.stringify(body),
    })
    renderManager(data.manager || {})
  } catch (err) {
    setText("manager-status", `保存失败：${String(err.message || err)}`)
  }
}

async function handleWorkerCreate(event) {
  event.preventDefault()
  setText("worker-status", "创建中...")
  try {
    const body = {
      name: byId("new-worker-name").value.trim() || "执行者",
      backend: byId("new-worker-backend").value,
    }
    const data = await fetchJson("/api/v1/config/workers", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeader(),
      },
      body: JSON.stringify(body),
    })
    state.selectedWorkerId = String(data.worker?.id || "")
    byId("new-worker-name").value = ""
    await refreshWorkersOnly()
    await loadWorkerSoul(state.selectedWorkerId)
    setText("worker-status", `已创建：${state.selectedWorkerId}`)
  } catch (err) {
    setText("worker-status", `创建失败：${String(err.message || err)}`)
  }
}

async function handleWorkerReload() {
  setText("worker-status", "重新加载中...")
  try {
    await refreshWorkersOnly()
    await loadWorkerSoul(state.selectedWorkerId)
    setText("worker-status", "已加载")
  } catch (err) {
    setText("worker-status", `加载失败：${String(err.message || err)}`)
  }
}

async function handleWorkerSubmit(event) {
  event.preventDefault()
  if (!state.selectedWorkerId) {
    return
  }
  setText("worker-status", "保存中...")
  try {
    const workerId = state.selectedWorkerId
    const updateBody = {
      name: byId("worker-name").value.trim(),
      backend: byId("worker-backend").value,
      status: byId("worker-runtime-status").value,
    }
    await fetchJson(`/api/v1/config/workers/${encodeURIComponent(workerId)}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        ...authHeader(),
      },
      body: JSON.stringify(updateBody),
    })

    await fetchJson(`/api/v1/config/workers/${encodeURIComponent(workerId)}/soul`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        ...authHeader(),
      },
      body: JSON.stringify({ content: byId("worker-soul").value }),
    })

    await refreshWorkersOnly()
    setText("worker-status", `已保存：${workerId}`)
  } catch (err) {
    setText("worker-status", `保存失败：${String(err.message || err)}`)
  }
}

async function handleWorkerDelete() {
  const workerId = state.selectedWorkerId
  if (!workerId || workerId === "worker-main") {
    return
  }
  const ok = window.confirm(`确定删除执行者 ${workerId} 吗？`)
  if (!ok) {
    return
  }

  setText("worker-status", "删除中...")
  try {
    await fetchJson(`/api/v1/config/workers/${encodeURIComponent(workerId)}`, {
      method: "DELETE",
      headers: {
        ...authHeader(),
      },
    })
    state.selectedWorkerId = "worker-main"
    await refreshWorkersOnly()
    await loadWorkerSoul(state.selectedWorkerId)
    setText("worker-status", `已删除：${workerId}`)
  } catch (err) {
    setText("worker-status", `删除失败：${String(err.message || err)}`)
  }
}

function wireWorkerSelect() {
  byId("worker-select").addEventListener("change", async (event) => {
    state.selectedWorkerId = String(event.target.value || "")
    const selected = state.workers.find((item) => String(item.id) === state.selectedWorkerId)
    fillWorkerForm(selected)
    try {
      await loadWorkerSoul(state.selectedWorkerId)
      setText("worker-status", `已加载：${state.selectedWorkerId}`)
    } catch (err) {
      setText("worker-status", `加载失败：${String(err.message || err)}`)
    }
  })
}

function init() {
  wireTabs()

  byId("manager-reload").addEventListener("click", handleManagerReload)
  byId("manager-form").addEventListener("submit", handleManagerSubmit)

  byId("worker-create-form").addEventListener("submit", handleWorkerCreate)
  byId("worker-edit-form").addEventListener("submit", handleWorkerSubmit)
  byId("worker-reload").addEventListener("click", handleWorkerReload)
  byId("worker-delete").addEventListener("click", handleWorkerDelete)
  wireWorkerSelect()

  hydrateFromBootstrap().catch((err) => {
    setText("system-status", `加载失败：${String(err.message || err)}`)
    setText("manager-status", `加载失败：${String(err.message || err)}`)
    setText("worker-status", `加载失败：${String(err.message || err)}`)
  })
}

window.addEventListener("DOMContentLoaded", init)
