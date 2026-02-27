const ASSET_BASE = "/assets/game"

const ASSETS = {
  bgTile: "bg-tile",
  office: ["office-lv1", "office-lv2", "office-lv3"],
  hall: ["hall-lv1", "hall-lv2", "hall-lv3"],
  delivery: ["delivery-lv1", "delivery-lv2", "delivery-lv3"],
  officeDecor: ["office-decor-console", "office-decor-terminal", "office-decor-plant"],
  hallDecor: ["hall-decor-rack", "hall-decor-light", "hall-decor-banner"],
  deliveryDecor: ["delivery-decor-kiosk", "delivery-decor-sign", "delivery-decor-trophy"],
  manager: "actor-manager",
  worker: "actor-worker",
  player: "actor-player",
  crate: "task-crate",
  cart: "task-cart",
  queueBoard: "queue-board",
  doneBoard: "done-board",
  mailDesk: "mail-desk",
}

const state = {
  lastSeq: 0,
  stream: null,
  reconnectTimer: null,
  refreshTimer: null,
  workers: [],
  game: null,
  scene: null,
  layout: null,
  managerUnit: null,
  playerUnit: null,
  workerUnits: new Map(),
  taskUnits: new Map(),
  worldObjects: [],
  lastSnapshot: null,
  fallbackLoaded: false,
  soundEnabled: false,
  audioCtx: null,
  simulatedMinutes: 8 * 60,
  minutesPerSecond: 2.1,
  levelState: {
    office: 1,
    hall: 1,
    delivery: 1,
  },
  levelDecorKey: "",
  focusTimer: null,
  lastGuideAt: 0,
  selectedZoneKey: "office",
  deliveryRateHistory: [100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
}

const byId = (id) => document.getElementById(id)
const timelineList = byId("timeline-list")

function setText(id, value) {
  const node = byId(id)
  if (node) {
    node.textContent = value
  }
}

function setStyle(id, key, value) {
  const node = byId(id)
  if (node) {
    node.style[key] = value
  }
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value))
}

function appendDeliveryRate(rate) {
  state.deliveryRateHistory.push(rate)
  while (state.deliveryRateHistory.length > 18) {
    state.deliveryRateHistory.shift()
  }
}

function renderDeliveryTrend() {
  const line = byId("service-trend-line")
  if (!line) {
    return
  }

  const points = []
  const count = state.deliveryRateHistory.length
  const maxX = 160
  const maxY = 44

  state.deliveryRateHistory.forEach((value, index) => {
    const ratio = count <= 1 ? 0 : index / (count - 1)
    const x = Math.floor(ratio * maxX)
    const y = Math.floor(maxY - (clamp(value, 0, 100) / 100) * (maxY - 6) - 2)
    points.push(`${x},${y}`)
  })
  line.setAttribute("points", points.join(" "))
}

function actionFromSource(source) {
  const raw = String(source || "").toLowerCase()
  if (raw.includes("heartbeat")) {
    return "巡检中"
  }
  if (raw.includes("chat") || raw.includes("message")) {
    return "对话中"
  }
  if (raw.includes("command") || raw.includes("tool")) {
    return "工具中"
  }
  return "执行中"
}

function actionColorFromSource(source) {
  const raw = String(source || "").toLowerCase()
  if (raw.includes("heartbeat")) {
    return "#8ef4d7"
  }
  if (raw.includes("chat") || raw.includes("message")) {
    return "#a5d8ff"
  }
  if (raw.includes("command") || raw.includes("tool")) {
    return "#ffe09d"
  }
  return "#9ad6ff"
}

function actionTintFromSource(source) {
  const raw = String(source || "").toLowerCase()
  if (raw.includes("heartbeat")) {
    return 0x8ef4d7
  }
  if (raw.includes("chat") || raw.includes("message")) {
    return 0xa5d8ff
  }
  if (raw.includes("command") || raw.includes("tool")) {
    return 0xffe09d
  }
  return 0x9ad6ff
}

function zoneLabel(zoneKey) {
  if (zoneKey === "office") {
    return "经理办公室"
  }
  if (zoneKey === "hall") {
    return "工作大厅"
  }
  return "交付区"
}

function zoneSpriteForKey(zoneKey) {
  if (!state.layout?.refs) {
    return null
  }
  if (zoneKey === "office") {
    return state.layout.refs.officeSprite || null
  }
  if (zoneKey === "hall") {
    return state.layout.refs.hallSprite || null
  }
  return state.layout.refs.deliverySprite || null
}

function applyZoneHighlight() {
  const office = zoneSpriteForKey("office")
  const hall = zoneSpriteForKey("hall")
  const delivery = zoneSpriteForKey("delivery")

  ;[office, hall, delivery].forEach((sprite) => {
    if (!sprite) {
      return
    }
    sprite.clearTint()
    sprite.setAlpha(0.9)
    sprite.setScale(1)
  })

  const selected = zoneSpriteForKey(state.selectedZoneKey)
  if (!selected) {
    return
  }
  selected.setTint(0xcde7ff)
  selected.setAlpha(1)
  selected.setScale(1.012)
}

function zoneMetrics(snapshot, zoneKey) {
  const source = snapshot || {}
  const totals = source?.dispatch?.totals || {}
  const manager = source?.manager || {}
  const workers = Array.isArray(source?.workers) ? source.workers : state.workers
  const tasks = Array.isArray(source?.dispatch?.tasks) ? source.dispatch.tasks : []

  const usersTotal = Number(manager.users_total || 0)
  const usersDue = Number(manager.users_due || 0)
  const pending = Number(totals.pending || 0)
  const running = Number(totals.running || 0)
  const done = Number(totals.done || 0)
  const failed = Number(totals.failed || 0)
  const cancelled = Number(totals.cancelled || 0)
  const workerCount = workers.length || 0

  if (zoneKey === "office") {
    const load = usersTotal > 0 ? clamp(Math.round((usersDue / usersTotal) * 100), 0, 100) : 0
    const tip = load > 65 ? "巡检压力偏高，建议提升调度节奏" : "巡检节奏稳定，调度流畅"
    return {
      load,
      summary: `待巡检 ${usersDue} / 总用户 ${usersTotal}`,
      tip,
    }
  }

  if (zoneKey === "hall") {
    const load = workerCount > 0 ? clamp(Math.round((running / workerCount) * 100), 0, 100) : 0
    const hotWorkers = workers.filter((item) => String(item.status || "").toLowerCase() === "busy").length
    const tip = load > 85 ? "大厅拥塞，建议扩充执行者或减轻任务峰值" : "大厅运转平衡，执行链路正常"
    return {
      load,
      summary: `执行中 ${running} / 执行者 ${workerCount}（忙碌 ${hotWorkers}）`,
      tip,
    }
  }

  const closed = done + failed + cancelled
  const delivered = tasks.filter((task) => String(task.delivered_at || "").trim()).length
  const backlog = Math.max(0, closed - delivered)
  const load = closed > 0 ? clamp(Math.round((backlog / closed) * 100), 0, 100) : pending > 0 ? 35 : 0
  const tip = load > 55 ? "交付积压增加，建议优先回传已完成任务" : "交付稳定，玩家接待满意"
  return {
    load,
    summary: `待回传 ${backlog} / 已关闭 ${closed}`,
    tip,
  }
}

function updateFacilityPanel(snapshot) {
  const zoneKey = state.selectedZoneKey
  const status =
    zoneKey === "office"
      ? state.levelState.office
      : zoneKey === "hall"
        ? state.levelState.hall
        : state.levelState.delivery
  const metrics = zoneMetrics(snapshot || state.lastSnapshot, zoneKey)

  setText("facility-level", `状态 ${zoneStatusLabel(status)}`)
  setText("facility-name", `选中：${zoneLabel(zoneKey)}`)
  setText("facility-metrics", `${metrics.summary} · 负载 ${metrics.load}%`)
  setText("facility-tip", metrics.tip)

  setStyle("facility-load-fill", "width", `${metrics.load}%`)
  if (metrics.load >= 80) {
    setStyle("facility-load-fill", "background", "linear-gradient(90deg, #ff8888, #ffb0b0)")
  } else if (metrics.load >= 55) {
    setStyle("facility-load-fill", "background", "linear-gradient(90deg, #f0c76d, #ffd98f)")
  } else {
    setStyle("facility-load-fill", "background", "linear-gradient(90deg, #6bcaf8, #89ddff)")
  }
}

function setSelectedZone(zoneKey, snapshot) {
  state.selectedZoneKey = zoneKey
  applyZoneHighlight()
  updateFacilityPanel(snapshot || state.lastSnapshot)
}

function fmtClock(minutes) {
  const safe = ((Math.floor(minutes) % (24 * 60)) + 24 * 60) % (24 * 60)
  const hh = String(Math.floor(safe / 60)).padStart(2, "0")
  const mm = String(safe % 60).padStart(2, "0")
  return `${hh}:${mm}`
}

function fmtTime(value) {
  if (!value) {
    return "--"
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return String(value)
  }
  return date.toLocaleTimeString("zh-CN", { hour12: false })
}

function localizeWorkerStatus(value) {
  const status = String(value || "ready").toLowerCase()
  if (status === "busy") {
    return "忙碌"
  }
  if (status === "paused") {
    return "暂停"
  }
  return "就绪"
}

function localizeEvent(event) {
  const payload = event.payload || {}
  const taskId = String(payload.task_id || "任务")
  const workerId = String(payload.worker_id || "worker-main")

  if (event.type === "manager.tick") {
    return {
      title: "管理者巡检",
      detail: `管理者完成巡检，待巡检用户 ${Number(payload.users_due || 0)} 人。`,
    }
  }
  if (event.type === "heartbeat.tick") {
    const userId = String(payload.user_id || "用户")
    const level = String(payload.level || "NOTICE")
    return {
      title: "心跳检查",
      detail: `${userId} 心跳检查完成，级别 ${level}。`,
    }
  }
  if (event.type === "task.enqueued") {
    return {
      title: "任务派发",
      detail: `${taskId} 已派发给 ${workerId}。`,
    }
  }
  if (event.type === "task.claimed" || event.type === "task.running") {
    return {
      title: "任务执行",
      detail: `${workerId} 正在处理 ${taskId}。`,
    }
  }
  if (event.type === "task.completed") {
    return {
      title: "任务完成",
      detail: `${taskId} 已执行完成。`,
    }
  }
  if (event.type === "task.failed") {
    return {
      title: "任务失败",
      detail: `${taskId} 执行失败。`,
    }
  }
  if (event.type === "task.cancelled") {
    return {
      title: "任务取消",
      detail: `${taskId} 已取消。`,
    }
  }
  if (event.type === "result.delivered") {
    return {
      title: "结果回传",
      detail: `${taskId} 已送达玩家接待区。`,
    }
  }
  if (event.type === "control.action") {
    return {
      title: "配置更新",
      detail: String(event.detail || "配置已更新。"),
    }
  }
  return {
    title: "系统事件",
    detail: String(event.detail || event.type || "未知事件"),
  }
}

async function fetchJson(url) {
  const response = await fetch(url)
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`)
  }
  return response.json()
}

function ensureAudioContext() {
  if (state.audioCtx) {
    return state.audioCtx
  }
  const AudioContextClass = window.AudioContext || window.webkitAudioContext
  if (!AudioContextClass) {
    return null
  }
  state.audioCtx = new AudioContextClass()
  return state.audioCtx
}

function playTone(freq, duration, volume, type) {
  if (!state.soundEnabled) {
    return
  }
  const ctx = ensureAudioContext()
  if (!ctx) {
    return
  }
  const now = ctx.currentTime
  const osc = ctx.createOscillator()
  const gain = ctx.createGain()
  osc.type = type
  osc.frequency.setValueAtTime(freq, now)
  gain.gain.setValueAtTime(0.0001, now)
  gain.gain.exponentialRampToValueAtTime(Math.max(0.0001, volume), now + 0.015)
  gain.gain.exponentialRampToValueAtTime(0.0001, now + duration)
  osc.connect(gain)
  gain.connect(ctx.destination)
  osc.start(now)
  osc.stop(now + duration + 0.03)
}

function playSfx(kind) {
  if (kind === "tick") {
    playTone(420, 0.16, 0.035, "triangle")
    return
  }
  if (kind === "dispatch") {
    playTone(560, 0.13, 0.043, "triangle")
    return
  }
  if (kind === "running") {
    playTone(620, 0.12, 0.04, "square")
    return
  }
  if (kind === "success") {
    playTone(740, 0.13, 0.045, "triangle")
    setTimeout(() => playTone(920, 0.12, 0.035, "triangle"), 90)
    return
  }
  if (kind === "error") {
    playTone(250, 0.22, 0.043, "sawtooth")
    return
  }
  if (kind === "upgrade") {
    playTone(610, 0.14, 0.052, "triangle")
    setTimeout(() => playTone(810, 0.15, 0.052, "triangle"), 110)
  }
}

function updateSoundButton() {
  setText("sound-toggle", state.soundEnabled ? "音效：开" : "音效：关")
}

function pickLevelKey(keys, level) {
  const index = clamp(Math.ceil(level), 1, keys.length) - 1
  return keys[index]
}

function buildLayout(scene) {
  const width = Math.max(640, Math.floor(scene.scale.width))
  const height = Math.max(430, Math.floor(scene.scale.height))

  const marginX = Math.floor(width * 0.04)
  const marginY = Math.floor(height * 0.06)
  const usableW = width - marginX * 2
  const usableH = height - marginY * 2

  const office = {
    x: Math.floor(marginX + usableW * 0.20),
    y: Math.floor(marginY + usableH * 0.40),
    w: Math.floor(usableW * 0.26),
    h: Math.floor(usableH * 0.52),
  }

  const hall = {
    x: Math.floor(marginX + usableW * 0.50),
    y: Math.floor(marginY + usableH * 0.54),
    w: Math.floor(usableW * 0.36),
    h: Math.floor(usableH * 0.50),
  }

  const delivery = {
    x: Math.floor(marginX + usableW * 0.80),
    y: Math.floor(marginY + usableH * 0.40),
    w: Math.floor(usableW * 0.22),
    h: Math.floor(usableH * 0.46),
  }

  const slots = []
  const columns = 4
  const rows = 2
  const slotPadX = 56
  const slotPadY = 68
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < columns; col += 1) {
      const x = Math.floor(
        hall.x - hall.w / 2 + slotPadX + (col * (hall.w - slotPadX * 2)) / Math.max(1, columns - 1),
      )
      const y = Math.floor(hall.y - hall.h / 2 + slotPadY + row * Math.floor(hall.h * 0.36))
      slots.push({ x, y })
    }
  }

  return {
    width,
    height,
    office,
    hall,
    delivery,
    hallSlots: slots,
    anchors: {
      managerDesk: {
        x: Math.floor(office.x),
        y: Math.floor(office.y + office.h * 0.22),
      },
      managerDoor: {
        x: Math.floor(office.x + office.w / 2 - 16),
        y: Math.floor(office.y + office.h / 2 - 16),
      },
      queueBoard: {
        x: Math.floor(hall.x - hall.w / 2 + 36),
        y: Math.floor(hall.y - hall.h / 2 + 44),
      },
      doneBoard: {
        x: Math.floor(hall.x + hall.w / 2 - 36),
        y: Math.floor(hall.y - hall.h / 2 + 44),
      },
      mailDesk: {
        x: Math.floor(delivery.x),
        y: Math.floor(delivery.y + delivery.h * 0.22),
      },
      playerSpot: {
        x: Math.floor(delivery.x + delivery.w * 0.12),
        y: Math.floor(delivery.y - delivery.h * 0.14),
      },
      managerPatrol: [
        {
          x: Math.floor(office.x - office.w * 0.2),
          y: Math.floor(office.y + office.h * 0.12),
        },
        {
          x: Math.floor(office.x + office.w * 0.2),
          y: Math.floor(office.y + office.h * 0.10),
        },
        {
          x: Math.floor(office.x + office.w * 0.15),
          y: Math.floor(office.y - office.h * 0.10),
        },
        {
          x: Math.floor(office.x - office.w * 0.18),
          y: Math.floor(office.y - office.h * 0.10),
        },
      ],
    },
    refs: {},
  }
}

function stopMotion(unit) {
  if (!unit) {
    return
  }
  if (unit.__route && typeof unit.__route.stop === "function") {
    unit.__route.stop()
  }
  if (unit.__floatTween && typeof unit.__floatTween.stop === "function") {
    unit.__floatTween.stop()
  }
}

function clearTaskUnits() {
  Array.from(state.taskUnits.values()).forEach((entry) => {
    if (entry.__tween && typeof entry.__tween.stop === "function") {
      entry.__tween.stop()
    }
    entry.unit.destroy()
  })
  state.taskUnits.clear()
}

function clearWorkerUnits() {
  Array.from(state.workerUnits.values()).forEach((unit) => {
    stopMotion(unit)
    unit.destroy()
  })
  state.workerUnits.clear()
}

function clearWorld() {
  state.worldObjects.forEach((item) => item.destroy())
  state.worldObjects = []
  clearTaskUnits()
  clearWorkerUnits()
  stopMotion(state.managerUnit)
  stopMotion(state.playerUnit)
  if (state.managerUnit) {
    state.managerUnit.destroy()
  }
  if (state.playerUnit) {
    state.playerUnit.destroy()
  }
  state.managerUnit = null
  state.playerUnit = null
}

function drawZoneSprite(scene, center, size, textureKey, depth) {
  const sprite = scene.add.image(center.x, center.y, textureKey)
  sprite.setDisplaySize(size.w, size.h)
  sprite.setDepth(depth)
  return sprite
}

function createDecorSprite(scene, key, x, y, width, height, depth, alpha) {
  const sprite = scene.add.image(x, y, key)
  sprite.setDisplaySize(width, height)
  sprite.setDepth(depth)
  sprite.setAlpha(alpha)
  return sprite
}

function clearZoneDecor() {
  if (!state.layout?.refs?.zoneDecor) {
    return
  }
  const previous = new Set(state.layout.refs.zoneDecor)
  state.worldObjects = state.worldObjects.filter((item) => !previous.has(item))
  state.layout.refs.zoneDecor.forEach((item) => item.destroy())
  state.layout.refs.zoneDecor = []
}

function applyZoneDecor(scene, levels) {
  if (!state.layout) {
    return
  }

  const key = `${levels.office}-${levels.hall}-${levels.delivery}-${state.layout.width}x${state.layout.height}`
  if (state.levelDecorKey === key && Array.isArray(state.layout.refs.zoneDecor)) {
    return
  }

  clearZoneDecor()

  const decor = []
  const office = state.layout.office
  const hall = state.layout.hall
  const delivery = state.layout.delivery

  for (let index = 0; index < levels.office; index += 1) {
    const item = createDecorSprite(
      scene,
      ASSETS.officeDecor[index % ASSETS.officeDecor.length],
      office.x - office.w * 0.28 + index * 28,
      office.y + office.h * 0.25,
      30,
      30,
      9,
      0.95,
    )
    decor.push(item)
  }

  for (let index = 0; index < levels.hall + 2; index += 1) {
    const top = createDecorSprite(
      scene,
      ASSETS.hallDecor[index % ASSETS.hallDecor.length],
      hall.x - hall.w * 0.42 + index * ((hall.w * 0.84) / Math.max(1, levels.hall + 1)),
      hall.y - hall.h * 0.28,
      36,
      24,
      9,
      0.9,
    )
    decor.push(top)
  }

  for (let index = 0; index < levels.delivery; index += 1) {
    const item = createDecorSprite(
      scene,
      ASSETS.deliveryDecor[index % ASSETS.deliveryDecor.length],
      delivery.x - delivery.w * 0.22 + index * 32,
      delivery.y + delivery.h * 0.25,
      30,
      30,
      9,
      0.95,
    )
    decor.push(item)
  }

  decor.forEach((item, index) => {
    scene.tweens.add({
      targets: item,
      y: item.y - 2,
      duration: 1100 + index * 35,
      yoyo: true,
      repeat: -1,
      ease: "Sine.easeInOut",
    })
  })

  state.layout.refs.zoneDecor = decor
  decor.forEach((item) => state.worldObjects.push(item))
  state.levelDecorKey = key
}

function animateUpgradePulse(zoneSprite, center, color) {
  if (!state.scene || !zoneSprite) {
    return
  }

  state.scene.tweens.add({
    targets: zoneSprite,
    scaleX: 1.03,
    scaleY: 1.03,
    duration: 180,
    yoyo: true,
    repeat: 1,
    ease: "Sine.easeInOut",
  })

  for (let index = 0; index < 8; index += 1) {
    const spark = state.scene.add.circle(center.x, center.y, 4, color, 0.92)
    spark.setDepth(35)
    const angle = (Math.PI * 2 * index) / 8
    const radius = 24 + index * 3
    state.scene.tweens.add({
      targets: spark,
      x: center.x + Math.cos(angle) * radius,
      y: center.y + Math.sin(angle) * radius,
      alpha: 0,
      duration: 420,
      ease: "Cubic.easeOut",
      onComplete: () => {
        spark.destroy()
      },
    })
  }
}

function bindZoneInteractions(scene) {
  if (!state.layout?.refs) {
    return
  }

  const entries = [
    ["office", state.layout.refs.officeSprite, "经理办公室"],
    ["hall", state.layout.refs.hallSprite, "工作大厅"],
    ["delivery", state.layout.refs.deliverySprite, "交付区"],
  ]

  entries.forEach(([zoneKey, sprite, label]) => {
    if (!sprite) {
      return
    }
    sprite.removeAllListeners()
    sprite.setInteractive({ useHandCursor: true })
    sprite.on("pointerover", () => {
      if (state.selectedZoneKey !== zoneKey) {
        sprite.setTint(0xcfe9ff)
      }
    })
    sprite.on("pointerout", () => {
      applyZoneHighlight()
    })
    sprite.on("pointerdown", () => {
      setSelectedZone(zoneKey, state.lastSnapshot)
      triggerCameraGuide(zoneKey)
      popupText(label, sprite.x, sprite.y - sprite.displayHeight * 0.32, "#9fd6ff")
      playSfx("tick")
    })
  })

  applyZoneHighlight()
}

function drawWorld(scene) {
  clearWorld()
  state.layout = buildLayout(scene)

  const { office, hall, delivery, anchors } = state.layout

  const bg = scene.add.tileSprite(
    state.layout.width / 2,
    state.layout.height / 2,
    state.layout.width,
    state.layout.height,
    ASSETS.bgTile,
  )
  bg.setDepth(0)
  state.worldObjects.push(bg)
  state.layout.refs.bgTile = bg

  const officeSprite = drawZoneSprite(
    scene,
    { x: office.x, y: office.y },
    { w: office.w, h: office.h },
    pickLevelKey(ASSETS.office, state.levelState.office),
    4,
  )
  const hallSprite = drawZoneSprite(
    scene,
    { x: hall.x, y: hall.y },
    { w: hall.w, h: hall.h },
    pickLevelKey(ASSETS.hall, state.levelState.hall),
    4,
  )
  const deliverySprite = drawZoneSprite(
    scene,
    { x: delivery.x, y: delivery.y },
    { w: delivery.w, h: delivery.h },
    pickLevelKey(ASSETS.delivery, state.levelState.delivery),
    4,
  )
  state.worldObjects.push(officeSprite)
  state.worldObjects.push(hallSprite)
  state.worldObjects.push(deliverySprite)

  state.layout.refs.officeSprite = officeSprite
  state.layout.refs.hallSprite = hallSprite
  state.layout.refs.deliverySprite = deliverySprite

  const queueBoard = scene.add.image(anchors.queueBoard.x, anchors.queueBoard.y, ASSETS.queueBoard)
  queueBoard.setDisplaySize(54, 76)
  queueBoard.setDepth(7)
  state.worldObjects.push(queueBoard)
  state.layout.refs.queueBoard = queueBoard

  const doneBoard = scene.add.image(anchors.doneBoard.x, anchors.doneBoard.y, ASSETS.doneBoard)
  doneBoard.setDisplaySize(54, 76)
  doneBoard.setDepth(7)
  state.worldObjects.push(doneBoard)
  state.layout.refs.doneBoard = doneBoard

  const mailDesk = scene.add.image(anchors.mailDesk.x, anchors.mailDesk.y, ASSETS.mailDesk)
  mailDesk.setDisplaySize(96, 30)
  mailDesk.setDepth(7)
  state.worldObjects.push(mailDesk)
  state.layout.refs.mailDesk = mailDesk

  const route = scene.add.graphics().setDepth(3)
  route.lineStyle(6, 0x4e7fb4, 0.62)
  route.beginPath()
  route.moveTo(anchors.managerDoor.x, anchors.managerDoor.y)
  route.lineTo(anchors.queueBoard.x, anchors.queueBoard.y + 32)
  route.lineTo(anchors.doneBoard.x, anchors.doneBoard.y + 34)
  route.lineTo(anchors.mailDesk.x, anchors.mailDesk.y)
  route.strokePath()
  state.worldObjects.push(route)

  const officeLevel = scene.add
    .text(office.x, office.y - office.h / 2 + 32, zoneStatusLabel(state.levelState.office), {
      fontFamily: '"Chakra Petch"',
      fontSize: "16px",
      color: "#a7d5ff",
      stroke: "#08111f",
      strokeThickness: 3,
    })
    .setOrigin(0.5)
    .setDepth(8)
  state.worldObjects.push(officeLevel)

  const hallLevel = scene.add
    .text(hall.x, hall.y - hall.h / 2 + 32, zoneStatusLabel(state.levelState.hall), {
      fontFamily: '"Chakra Petch"',
      fontSize: "16px",
      color: "#a7d5ff",
      stroke: "#08111f",
      strokeThickness: 3,
    })
    .setOrigin(0.5)
    .setDepth(8)
  state.worldObjects.push(hallLevel)

  const deliveryLevel = scene.add
    .text(delivery.x, delivery.y - delivery.h / 2 + 32, zoneStatusLabel(state.levelState.delivery), {
      fontFamily: '"Chakra Petch"',
      fontSize: "16px",
      color: "#a7d5ff",
      stroke: "#08111f",
      strokeThickness: 3,
    })
    .setOrigin(0.5)
    .setDepth(8)
  state.worldObjects.push(deliveryLevel)

  state.layout.refs.officeLevel = officeLevel
  state.layout.refs.hallLevel = hallLevel
  state.layout.refs.deliveryLevel = deliveryLevel

  const sun = scene.add.circle(0, 0, 15, 0xffd474, 0.95).setDepth(2)
  const moon = scene.add.circle(0, 0, 10, 0xdce8ff, 0.9).setDepth(2)
  const overlay = scene.add.rectangle(
    state.layout.width / 2,
    state.layout.height / 2,
    state.layout.width,
    state.layout.height,
    0x020812,
    0.34,
  )
  overlay.setDepth(25)
  state.worldObjects.push(sun)
  state.worldObjects.push(moon)
  state.worldObjects.push(overlay)
  state.layout.refs.sky = {
    sun,
    moon,
    overlay,
  }

  applyZoneDecor(scene, state.levelState)
  bindZoneInteractions(scene)
  setSelectedZone(state.selectedZoneKey, state.lastSnapshot)

  ensureCoreUnits(scene)
  syncWorkers(scene)
  if (state.lastSnapshot) {
    syncTasks(state.lastSnapshot)
  }
}

function createUnit(scene, textureKey, nameText) {
  const shadow = scene.add.ellipse(0, 16, 24, 10, 0x000000, 0.34)
  const avatar = scene.add.image(0, 0, textureKey)
  avatar.setDisplaySize(44, 64)

  const name = scene.add
    .text(0, 34, nameText, {
      fontFamily: '"Noto Sans SC"',
      fontSize: "11px",
      color: "#d2e5ff",
      stroke: "#06101d",
      strokeThickness: 2,
    })
    .setOrigin(0.5)

  const bubbleBg = scene.add.rectangle(0, -36, 46, 16, 0x0a1728, 0.86)
  bubbleBg.setStrokeStyle(1, 0x95d6ff, 0.9)
  bubbleBg.setVisible(false)
  const bubbleText = scene.add
    .text(0, -36, "", {
      fontFamily: '"Noto Sans SC"',
      fontSize: "10px",
      color: "#95d6ff",
    })
    .setOrigin(0.5)
    .setVisible(false)

  const unit = scene.add.container(0, 0, [shadow, bubbleBg, bubbleText, avatar, name])
  unit.setDepth(22)
  unit.__avatar = avatar
  unit.__name = name
  unit.__bubbleBg = bubbleBg
  unit.__bubbleText = bubbleText
  unit.__route = null
  unit.__floatTween = null
  return unit
}

function setBubble(unit, text, color) {
  if (!unit) {
    return
  }
  const normalized = String(text || "").trim()
  const show = normalized.length > 0
  unit.__bubbleBg.setVisible(show)
  unit.__bubbleText.setVisible(show)
  if (!show) {
    return
  }
  unit.__bubbleText.setText(normalized)
  unit.__bubbleText.setColor(color)
  const width = Math.max(46, unit.__bubbleText.width + 16)
  unit.__bubbleBg.setDisplaySize(width, 16)
}

function createLoopRoute(scene, target, steps) {
  const route = {
    stopped: false,
    tween: null,
    stop() {
      this.stopped = true
      if (this.tween && typeof this.tween.stop === "function") {
        this.tween.stop()
      }
      this.tween = null
    },
  }

  if (!Array.isArray(steps) || !steps.length) {
    return route
  }

  let cursor = 0
  const run = () => {
    if (route.stopped || !target || !target.scene) {
      return
    }
    const step = steps[cursor % steps.length]
    cursor += 1
    route.tween = scene.tweens.add({
      targets: target,
      x: step.x,
      y: step.y,
      duration: step.duration,
      ease: step.ease || "Sine.easeInOut",
      onComplete: run,
    })
  }

  run()
  return route
}

function ensureCoreUnits(scene) {
  if (!state.managerUnit || !state.managerUnit.scene) {
    state.managerUnit = createUnit(scene, ASSETS.manager, "管理者")
  }
  if (!state.playerUnit || !state.playerUnit.scene) {
    state.playerUnit = createUnit(scene, ASSETS.player, "玩家")
  }

  const patrol = state.layout.anchors.managerPatrol
  state.managerUnit.setPosition(patrol[0].x, patrol[0].y)
  state.playerUnit.setPosition(state.layout.anchors.playerSpot.x, state.layout.anchors.playerSpot.y)

  stopMotion(state.managerUnit)
  state.managerUnit.__route = createLoopRoute(
    scene,
    state.managerUnit,
    patrol.map((point) => ({
      x: point.x,
      y: point.y,
      duration: 1000,
      ease: "Sine.easeInOut",
    })),
  )

  if (state.playerUnit.__floatTween && typeof state.playerUnit.__floatTween.stop === "function") {
    state.playerUnit.__floatTween.stop()
  }
  state.playerUnit.__floatTween = scene.tweens.add({
    targets: state.playerUnit,
    y: state.layout.anchors.playerSpot.y - 6,
    duration: 1250,
    yoyo: true,
    repeat: -1,
    ease: "Sine.easeInOut",
  })
}

function workerTint(status) {
  const raw = String(status || "ready").toLowerCase()
  if (raw === "busy") {
    return 0xffcb79
  }
  if (raw === "paused") {
    return 0xb6bfd1
  }
  return 0xffffff
}

function syncWorkers(scene) {
  const workers = Array.isArray(state.workers) && state.workers.length
    ? state.workers
    : [{ id: "worker-main", name: "执行者", status: "ready" }]

  const keep = new Set(workers.map((worker) => String(worker.id || "worker-main")))

  Array.from(state.workerUnits.entries()).forEach(([workerId, unit]) => {
    if (keep.has(workerId)) {
      return
    }
    stopMotion(unit)
    unit.destroy()
    state.workerUnits.delete(workerId)
  })

  workers.forEach((worker, index) => {
    const workerId = String(worker.id || "worker-main")
    let unit = state.workerUnits.get(workerId)

    if (!unit) {
      unit = createUnit(scene, ASSETS.worker, "执行者")
      state.workerUnits.set(workerId, unit)
    }

    const slot = state.layout.hallSlots[index % state.layout.hallSlots.length]
    const name = `${worker.name || workerId}(${localizeWorkerStatus(worker.status)})`
    unit.__name.setText(name)
    unit.__avatar.setTint(workerTint(worker.status))
    unit.setPosition(slot.x, slot.y)
    setBubble(unit, "", "#95d6ff")

    stopMotion(unit)
    if (String(worker.status || "ready").toLowerCase() === "paused") {
      unit.__floatTween = scene.tweens.add({
        targets: unit,
        y: slot.y - 3,
        duration: 900,
        yoyo: true,
        repeat: -1,
        ease: "Sine.easeInOut",
      })
    } else {
      const boardX = state.layout.anchors.queueBoard.x + 38 + (index % 2) * 10
      const boardY = state.layout.anchors.queueBoard.y + 54 + Math.floor(index / 2) * 8
      unit.__route = createLoopRoute(scene, unit, [
        {
          x: boardX,
          y: boardY,
          duration: 1300 + (index % 4) * 130,
          ease: "Sine.easeInOut",
        },
        {
          x: slot.x,
          y: slot.y,
          duration: 1150 + (index % 3) * 100,
          ease: "Sine.easeInOut",
        },
      ])
    }
  })
}

function shortTask(taskId) {
  const text = String(taskId || "任务")
  if (text.length <= 9) {
    return text
  }
  return `${text.slice(0, 4)}..${text.slice(-2)}`
}

function taskTint(task) {
  const status = String(task.status || "pending").toLowerCase()
  const delivered = String(task.delivered_at || "").trim().length > 0
  if (delivered) {
    return 0x8cf4c8
  }
  if (status === "running") {
    return 0x9cd7ff
  }
  if (status === "done") {
    return 0x85e9ba
  }
  if (status === "failed" || status === "cancelled") {
    return 0xffa0a0
  }
  return 0xffd37c
}

function taskTarget(task, index) {
  const status = String(task.status || "pending").toLowerCase()
  const delivered = String(task.delivered_at || "").trim().length > 0
  if (delivered) {
    const col = index % 3
    const row = Math.floor(index / 3)
    return {
      x: state.layout.anchors.mailDesk.x - 26 + col * 22,
      y: state.layout.anchors.mailDesk.y - 12 + row * 14,
    }
  }

  if (status === "running") {
    const workerUnit = state.workerUnits.get(String(task.worker_id || ""))
    if (workerUnit) {
      return {
        x: workerUnit.x + ((index % 2) * 16 - 8),
        y: workerUnit.y - 20 - (Math.floor(index / 2) % 2) * 10,
      }
    }
  }

  if (status === "done" || status === "failed" || status === "cancelled") {
    const col = index % 3
    const row = Math.floor(index / 3)
    return {
      x: state.layout.anchors.doneBoard.x - 16 + col * 14,
      y: state.layout.anchors.doneBoard.y + 32 + row * 12,
    }
  }

  const col = index % 2
  const row = Math.floor(index / 2)
  return {
    x: state.layout.anchors.queueBoard.x - 10 + col * 18,
    y: state.layout.anchors.queueBoard.y + 28 + row * 13,
  }
}

function createTaskUnit(scene, taskId, tint) {
  const sprite = scene.add.image(0, 0, ASSETS.crate)
  sprite.setDisplaySize(32, 20)
  sprite.setTint(tint)
  const label = scene.add
    .text(0, 0, shortTask(taskId), {
      fontFamily: '"Chakra Petch"',
      fontSize: "8px",
      color: "#07101a",
      fontStyle: "bold",
    })
    .setOrigin(0.5)
  const unit = scene.add.container(0, 0, [sprite, label])
  unit.setDepth(18)
  return {
    unit,
    sprite,
    label,
    __tween: null,
  }
}

function syncTasks(snapshot) {
  if (!state.scene || !state.layout) {
    return
  }

  const rows = Array.isArray(snapshot?.dispatch?.tasks) ? snapshot.dispatch.tasks : []
  const tasks = rows.slice(0, 36)
  const keep = new Set(tasks.map((task) => String(task.task_id || "")))

  Array.from(state.taskUnits.entries()).forEach(([taskId, holder]) => {
    if (keep.has(taskId)) {
      return
    }
    if (holder.__tween && typeof holder.__tween.stop === "function") {
      holder.__tween.stop()
    }
    holder.unit.destroy()
    state.taskUnits.delete(taskId)
  })

  const runningByWorker = new Map()
  const actionByWorker = new Map()
  const actionColorByWorker = new Map()
  const actionTintByWorker = new Map()

  tasks.forEach((task, index) => {
    const taskId = String(task.task_id || "")
    if (!taskId) {
      return
    }

    const status = String(task.status || "pending").toLowerCase()
    if (status === "running") {
      const workerId = String(task.worker_id || "worker-main")
      runningByWorker.set(workerId, Number(runningByWorker.get(workerId) || 0) + 1)
      if (!actionByWorker.has(workerId)) {
        actionByWorker.set(workerId, actionFromSource(task.source))
        actionColorByWorker.set(workerId, actionColorFromSource(task.source))
        actionTintByWorker.set(workerId, actionTintFromSource(task.source))
      }
    }

    let holder = state.taskUnits.get(taskId)
    if (!holder) {
      holder = createTaskUnit(state.scene, taskId, taskTint(task))
      state.taskUnits.set(taskId, holder)
    }

    holder.sprite.setTint(taskTint(task))
    holder.label.setText(shortTask(taskId))

    if (holder.__tween && typeof holder.__tween.stop === "function") {
      holder.__tween.stop()
    }
    const target = taskTarget(task, index)
    holder.__tween = state.scene.tweens.add({
      targets: holder.unit,
      x: target.x,
      y: target.y,
      duration: 620,
      ease: "Sine.easeInOut",
    })
  })

  Array.from(state.workerUnits.entries()).forEach(([workerId, unit]) => {
    const count = Number(runningByWorker.get(workerId) || 0)
    if (count > 0) {
      const action = actionByWorker.get(workerId) || "执行中"
      const actionColor = actionColorByWorker.get(workerId) || "#9ad6ff"
      const actionTint = actionTintByWorker.get(workerId)
      if (actionTint) {
        unit.__avatar.setTint(actionTint)
      }
      setBubble(unit, `${action} x${count}`, actionColor)
    } else {
      const worker = state.workers.find((item) => String(item.id || "") === workerId)
      unit.__avatar.setTint(workerTint(worker ? worker.status : "ready"))
      setBubble(unit, "", "#9ad6ff")
    }
  })
}

function popupText(text, x, y, color) {
  if (!state.scene) {
    return
  }
  const note = state.scene.add
    .text(x, y, text, {
      fontFamily: '"Noto Sans SC"',
      fontSize: "12px",
      color,
      stroke: "#07101c",
      strokeThickness: 3,
    })
    .setOrigin(0.5)
    .setDepth(40)

  state.scene.tweens.add({
    targets: note,
    y: y - 20,
    alpha: 0,
    duration: 920,
    onComplete: () => {
      note.destroy()
    },
  })
}

function animateCart(kind, from, to) {
  if (!state.scene || !from || !to) {
    return
  }
  const cart = state.scene.add.image(from.x, from.y, ASSETS.cart)
  cart.setDisplaySize(34, 24)
  cart.setDepth(30)

  if (kind === "success") {
    cart.setTint(0x8cf4c8)
  } else if (kind === "error") {
    cart.setTint(0xffa0a0)
  } else {
    cart.setTint(0x9ad6ff)
  }

  state.scene.tweens.add({
    targets: cart,
    x: to.x,
    y: to.y,
    duration: 760,
    ease: "Cubic.easeInOut",
    onComplete: () => {
      cart.destroy()
    },
  })
}

function updateServicePanel(snapshot) {
  const totals = snapshot?.dispatch?.totals || {}
  const tasks = Array.isArray(snapshot?.dispatch?.tasks) ? snapshot.dispatch.tasks : []

  const done = Number(totals.done || 0)
  const failed = Number(totals.failed || 0)
  const cancelled = Number(totals.cancelled || 0)
  const closed = done + failed + cancelled
  const deliveredCount = tasks.filter((task) => String(task.delivered_at || "").trim()).length
  const undeliveredClosed = Math.max(0, closed - deliveredCount)
  const deliveryRate = closed > 0 ? clamp(Math.round((deliveredCount / closed) * 100), 0, 100) : 100

  appendDeliveryRate(deliveryRate)
  renderDeliveryTrend()

  setText("service-score", `回传率 ${deliveryRate}%`)
  setStyle("service-fill", "width", `${deliveryRate}%`)
  setText("combo-label", `已回传 ${deliveredCount} / 已关闭 ${closed}`)
  setText("combo-reward", `待回传 ${undeliveredClosed}`)

  if (deliveryRate >= 90) {
    setStyle("service-fill", "background", "linear-gradient(90deg, #61d59f, #88e0b8)")
    setText("service-note", "交付链路健康，结果回传稳定")
  } else if (deliveryRate >= 65) {
    setStyle("service-fill", "background", "linear-gradient(90deg, #e4c76e, #ffd98f)")
    setText("service-note", "存在轻微回传积压，建议关注 relay 延迟")
  } else {
    setStyle("service-fill", "background", "linear-gradient(90deg, #ff8888, #ffb0b0)")
    setText("service-note", "回传积压明显，建议优先消化已完成任务")
  }

  const feed = byId("delivery-feed")
  if (!feed) {
    return
  }

  const delivered = tasks
    .filter((task) => String(task.delivered_at || "").trim())
    .sort((left, right) => {
      const leftTs = Date.parse(String(left.delivered_at || "")) || 0
      const rightTs = Date.parse(String(right.delivered_at || "")) || 0
      return rightTs - leftTs
    })
    .slice(0, 6)

  feed.innerHTML = ""
  if (!delivered.length) {
    const item = document.createElement("li")
    item.textContent = "暂无交付记录"
    feed.append(item)
    return
  }

  delivered.forEach((task) => {
    const item = document.createElement("li")
    const taskId = shortTask(task.task_id)
    const workerId = String(task.worker_id || "worker-main")
    item.textContent = `${taskId} · ${workerId} · ${fmtTime(task.delivered_at)}`
    feed.append(item)
  })
}

function buildWorkerStatusEntries(snapshot) {
  const source = snapshot || {}
  const workers = Array.isArray(source?.workers) && source.workers.length
    ? source.workers
    : state.workers
  const tasks = Array.isArray(source?.dispatch?.tasks) ? source.dispatch.tasks : []

  const workerMap = new Map()
  workers.forEach((worker) => {
    const workerId = String(worker.id || "worker-main")
    workerMap.set(workerId, {
      id: workerId,
      name: String(worker.name || workerId),
      status: String(worker.status || "ready"),
      running: 0,
      pending: 0,
      lastSource: "",
      lastStatus: "",
      lastTaskId: "",
      lastTs: 0,
    })
  })

  tasks.forEach((task) => {
    const workerId = String(task.worker_id || "worker-main")
    if (!workerMap.has(workerId)) {
      workerMap.set(workerId, {
        id: workerId,
        name: workerId,
        status: "ready",
        running: 0,
        pending: 0,
        lastSource: "",
        lastStatus: "",
        lastTaskId: "",
        lastTs: 0,
      })
    }

    const entry = workerMap.get(workerId)
    const status = String(task.status || "pending").toLowerCase()
    if (status === "running") {
      entry.running += 1
    } else if (status === "pending") {
      entry.pending += 1
    }

    const ts =
      Date.parse(String(task.updated_at || "")) ||
      Date.parse(String(task.started_at || "")) ||
      Date.parse(String(task.created_at || "")) ||
      0
    if (ts >= entry.lastTs) {
      entry.lastTs = ts
      entry.lastSource = String(task.source || "")
      entry.lastStatus = status
      entry.lastTaskId = String(task.task_id || "")
    }
  })

  return Array.from(workerMap.values()).sort((left, right) => {
    if (right.running !== left.running) {
      return right.running - left.running
    }
    if (right.pending !== left.pending) {
      return right.pending - left.pending
    }
    return left.name.localeCompare(right.name, "zh-CN")
  })
}

function updateWorkerStatusPanel(snapshot) {
  const list = byId("worker-status-list")
  if (!list) {
    return
  }

  const entries = buildWorkerStatusEntries(snapshot)
  const active = entries.filter((item) => item.running > 0).length
  setText("worker-health", `活跃 ${active}/${entries.length}`)

  list.innerHTML = ""
  if (!entries.length) {
    const item = document.createElement("li")
    item.innerHTML = "<span>暂无执行者状态</span>"
    list.append(item)
    return
  }

  entries.forEach((entry) => {
    const item = document.createElement("li")
    if (entry.running > 0) {
      item.classList.add("running")
    } else if (String(entry.status).toLowerCase() === "paused") {
      item.classList.add("paused")
    }

    const statusText = localizeWorkerStatus(entry.status)
    const action = entry.lastSource ? actionFromSource(entry.lastSource) : "待命中"
    const taskText = entry.lastTaskId ? shortTask(entry.lastTaskId) : "无"

    item.innerHTML = `<strong>${esc(entry.name)}</strong><span>状态:${esc(statusText)} · 执行中:${entry.running} · 待领:${entry.pending} · 动作:${esc(action)} · 最近任务:${esc(taskText)}</span>`
    list.append(item)
  })
}

function triggerCameraGuide(zoneName) {
  if (!state.scene || !state.layout) {
    return
  }

  const now = Date.now()
  if (now - state.lastGuideAt < 1100) {
    return
  }
  state.lastGuideAt = now

  if (state.focusTimer) {
    clearTimeout(state.focusTimer)
    state.focusTimer = null
  }

  const camera = state.scene.cameras.main
  const center = {
    x: state.layout.width / 2,
    y: state.layout.height / 2,
  }

  let target = center
  if (zoneName === "office") {
    target = {
      x: state.layout.office.x,
      y: state.layout.office.y,
    }
  } else if (zoneName === "hall") {
    target = {
      x: state.layout.hall.x,
      y: state.layout.hall.y,
    }
  } else if (zoneName === "delivery") {
    target = {
      x: state.layout.delivery.x,
      y: state.layout.delivery.y,
    }
  }

  camera.pan(target.x, target.y, 220, "Sine.easeInOut")
  camera.zoomTo(1.07, 220, "Sine.easeInOut")

  state.focusTimer = setTimeout(() => {
    camera.pan(center.x, center.y, 280, "Sine.easeInOut")
    camera.zoomTo(1, 280, "Sine.easeInOut")
    state.focusTimer = null
  }, 360)
}

function pulseHeartbeat() {
  const pulse = byId("heartbeat-pulse")
  if (!pulse) {
    return
  }
  pulse.classList.remove("boost")
  void pulse.offsetWidth
  pulse.classList.add("boost")
}

function boostManager() {
  if (!state.scene || !state.managerUnit) {
    return
  }
  state.scene.tweens.add({
    targets: state.managerUnit,
    x: state.managerUnit.x + 14,
    duration: 170,
    yoyo: true,
    repeat: 1,
    ease: "Sine.easeInOut",
  })
}

function scheduleSnapshotRefresh(delayMs) {
  if (state.refreshTimer) {
    return
  }
  state.refreshTimer = setTimeout(() => {
    state.refreshTimer = null
    refreshSnapshot()
  }, delayMs)
}

function handleSceneEvent(event) {
  if (!state.layout) {
    return
  }

  const payload = event.payload || {}
  const workerId = String(payload.worker_id || "worker-main")
  const worker = state.workerUnits.get(workerId)
  const workerPos = worker
    ? {
        x: worker.x,
        y: worker.y,
      }
    : {
        x: state.layout.anchors.queueBoard.x + 12,
        y: state.layout.anchors.queueBoard.y + 46,
      }

  if (event.type === "manager.tick") {
    boostManager()
    triggerCameraGuide("office")
    pulseHeartbeat()
    popupText("巡检", state.layout.anchors.managerDesk.x, state.layout.anchors.managerDesk.y - 34, "#9dd9ff")
    playSfx("tick")
    return
  }

  if (event.type === "heartbeat.tick") {
    triggerCameraGuide("office")
    pulseHeartbeat()
    popupText("心跳", state.layout.anchors.managerDesk.x + 40, state.layout.anchors.managerDesk.y - 24, "#8ef4d7")
    playSfx("tick")
    return
  }

  if (event.type === "task.enqueued") {
    boostManager()
    triggerCameraGuide("office")
    animateCart("dispatch", state.layout.anchors.managerDoor, state.layout.anchors.queueBoard)
    popupText("派发", state.layout.anchors.queueBoard.x, state.layout.anchors.queueBoard.y - 36, "#8ed1ff")
    playSfx("dispatch")
    scheduleSnapshotRefresh(520)
    return
  }

  if (event.type === "task.claimed" || event.type === "task.running") {
    triggerCameraGuide("hall")
    animateCart("dispatch", state.layout.anchors.queueBoard, workerPos)
    popupText("接单", workerPos.x, workerPos.y - 28, "#8dd2ff")
    playSfx("running")
    scheduleSnapshotRefresh(520)
    return
  }

  if (event.type === "task.completed") {
    triggerCameraGuide("hall")
    animateCart("success", workerPos, state.layout.anchors.doneBoard)
    popupText("完成", state.layout.anchors.doneBoard.x, state.layout.anchors.doneBoard.y - 36, "#9df2c6")
    playSfx("success")
    scheduleSnapshotRefresh(520)
    return
  }

  if (event.type === "task.failed" || event.type === "task.cancelled") {
    triggerCameraGuide("hall")
    animateCart("error", workerPos, state.layout.anchors.doneBoard)
    popupText("异常", state.layout.anchors.doneBoard.x, state.layout.anchors.doneBoard.y - 36, "#ff9f9f")
    playSfx("error")
    scheduleSnapshotRefresh(520)
    return
  }

  if (event.type === "result.delivered") {
    triggerCameraGuide("delivery")
    animateCart("success", state.layout.anchors.doneBoard, state.layout.anchors.mailDesk)
    popupText("回传", state.layout.anchors.mailDesk.x, state.layout.anchors.mailDesk.y - 32, "#b2dbff")
    playSfx("success")
    scheduleSnapshotRefresh(520)
  }
}

function appendTimeline(event) {
  const localized = localizeEvent(event)
  const item = document.createElement("li")
  const level = String(event.level || "info")
  item.innerHTML = `
    <div class="line-top">
      <span class="line-title line-level-${esc(level)}">${esc(localized.title)}</span>
      <span>${esc(fmtTime(event.at))} #${esc(event.seq || "")}</span>
    </div>
    <div class="line-detail">${esc(localized.detail)}</div>
  `
  timelineList.prepend(item)
  while (timelineList.children.length > 160) {
    timelineList.lastElementChild.remove()
  }
}

function handleEvent(event) {
  state.lastSeq = Math.max(state.lastSeq, Number(event.seq || 0))
  setText("timeline-seq", `序号：${state.lastSeq}`)
  appendTimeline(event)
  handleSceneEvent(event)
}

function computeLevels(snapshot) {
  const totals = snapshot?.dispatch?.totals || {}
  const tasks = Array.isArray(snapshot?.dispatch?.tasks) ? snapshot.dispatch.tasks : []
  const manager = snapshot?.manager || {}
  const usersTotal = Number(manager.users_total || 0)
  const usersDue = Number(manager.users_due || 0)
  const workerCount = Array.isArray(snapshot?.workers) ? snapshot.workers.length : state.workers.length
  const running = Number(totals.running || 0)
  const done = Number(totals.done || 0)
  const failed = Number(totals.failed || 0)
  const cancelled = Number(totals.cancelled || 0)
  const closed = done + failed + cancelled
  const delivered = tasks.filter((item) => String(item.delivered_at || "").trim()).length
  const undeliveredClosed = Math.max(0, closed - delivered)

  const officeLoad = usersTotal > 0 ? usersDue / usersTotal : 0
  const hallLoad = workerCount > 0 ? running / workerCount : 0
  const deliveryLoad = closed > 0 ? undeliveredClosed / closed : 0

  const office = officeLoad >= 0.65 ? 3 : officeLoad >= 0.3 ? 2 : 1
  const hall = hallLoad >= 0.85 ? 3 : hallLoad >= 0.45 ? 2 : 1
  const delivery = deliveryLoad >= 0.55 ? 3 : deliveryLoad >= 0.2 ? 2 : 1

  return { office, hall, delivery }
}

function zoneStatusLabel(value) {
  if (value >= 3) {
    return "拥塞"
  }
  if (value >= 2) {
    return "繁忙"
  }
  return "稳定"
}

function renderLevels(levels) {
  setText("dock-office-level", `办公室状态：${zoneStatusLabel(levels.office)}`)
  setText("dock-hall-level", `大厅状态：${zoneStatusLabel(levels.hall)}`)
  setText("dock-delivery-level", `交付区状态：${zoneStatusLabel(levels.delivery)}`)

  if (state.layout?.refs?.officeLevel) {
    state.layout.refs.officeLevel.setText(zoneStatusLabel(levels.office))
  }
  if (state.layout?.refs?.hallLevel) {
    state.layout.refs.hallLevel.setText(zoneStatusLabel(levels.hall))
  }
  if (state.layout?.refs?.deliveryLevel) {
    state.layout.refs.deliveryLevel.setText(zoneStatusLabel(levels.delivery))
  }

  if (state.layout?.refs?.officeSprite) {
    state.layout.refs.officeSprite.setTexture(pickLevelKey(ASSETS.office, levels.office))
  }
  if (state.layout?.refs?.hallSprite) {
    state.layout.refs.hallSprite.setTexture(pickLevelKey(ASSETS.hall, levels.hall))
  }
  if (state.layout?.refs?.deliverySprite) {
    state.layout.refs.deliverySprite.setTexture(pickLevelKey(ASSETS.delivery, levels.delivery))
  }

  if (state.scene && state.layout) {
    applyZoneDecor(state.scene, levels)
  }
}

function maybeUpgradeLevels(nextLevels) {
  const previous = state.levelState
  if (nextLevels.office !== previous.office) {
    popupText(
      `办公室${zoneStatusLabel(nextLevels.office)}`,
      state.layout.office.x,
      state.layout.office.y - 18,
      "#9fd7ff",
    )
    animateUpgradePulse(state.layout.refs.officeSprite, state.layout.office, 0x9fd7ff)
    playSfx("tick")
  }
  if (nextLevels.hall !== previous.hall) {
    popupText(
      `大厅${zoneStatusLabel(nextLevels.hall)}`,
      state.layout.hall.x,
      state.layout.hall.y - 28,
      "#9fd7ff",
    )
    animateUpgradePulse(state.layout.refs.hallSprite, state.layout.hall, 0x9fd7ff)
    playSfx("tick")
  }
  if (nextLevels.delivery !== previous.delivery) {
    popupText(
      `交付区${zoneStatusLabel(nextLevels.delivery)}`,
      state.layout.delivery.x,
      state.layout.delivery.y - 18,
      "#9fd7ff",
    )
    animateUpgradePulse(state.layout.refs.deliverySprite, state.layout.delivery, 0x9fd7ff)
    playSfx("tick")
  }
  state.levelState = nextLevels
}

function updateStats(snapshot) {
  const totals = snapshot?.dispatch?.totals || {}
  const tasks = Array.isArray(snapshot?.dispatch?.tasks) ? snapshot.dispatch.tasks : []
  const manager = snapshot?.manager || {}
  const usersTotal = Number(manager.users_total || 0)
  const usersDue = Number(manager.users_due || 0)
  const workerCount = Array.isArray(snapshot?.workers) && snapshot.workers.length
    ? snapshot.workers.length
    : state.workers.length
  const pending = Number(totals.pending || 0)
  const running = Number(totals.running || 0)
  const done = Number(totals.done || 0)
  const failed = Number(totals.failed || 0)
  const cancelled = Number(totals.cancelled || 0)
  const delivered = tasks.filter((task) => String(task.delivered_at || "").trim()).length

  setText("chip-users", `用户：${usersTotal}`)
  setText("chip-workers", `执行者：${workerCount}`)
  setText("chip-pending", `待处理：${pending}`)
  setText("manager-tick", `管理者巡检：${fmtTime(manager.last_tick_at)}`)

  setText("stat-pending", String(pending))
  setText("stat-running", String(running))
  setText("stat-done", String(done))
  setText("stat-failed", String(failed))
  setText("stat-due", String(usersDue))

  setText("dock-queue", `待派发：${pending}`)
  setText("dock-running", `执行中：${running}`)
  setText("dock-done", `已关闭：${done + failed + cancelled}`)
  setText("dock-mail", `已回传：${delivered}`)

  if (usersDue > 0) {
    setText("heartbeat-note", `当前有 ${usersDue} 名用户待巡检`)
  } else {
    setText("heartbeat-note", "当前所有用户处于巡检节奏内")
  }

  updateServicePanel(snapshot)
  updateFacilityPanel(snapshot)

  const levels = computeLevels(snapshot)
  if (state.layout) {
    maybeUpgradeLevels(levels)
    renderLevels(levels)
  } else {
    state.levelState = levels
  }
}

function updateDayNight(deltaMs) {
  if (!state.layout?.refs?.sky) {
    return
  }
  state.simulatedMinutes = (state.simulatedMinutes + (deltaMs * state.minutesPerSecond) / 1000) % (24 * 60)
  const progress = state.simulatedMinutes / (24 * 60)
  const sunValue = Math.sin(progress * Math.PI * 2 - Math.PI / 2)
  const isDay = sunValue > 0

  const sky = state.layout.refs.sky
  sky.overlay.setAlpha(
    isDay
      ? clamp(0.08 + (1 - sunValue) * 0.18, 0.08, 0.26)
      : clamp(0.42 + (-sunValue) * 0.24, 0.42, 0.66),
  )

  const orbitX = state.layout.width * progress
  const arc = Math.sin(progress * Math.PI)
  sky.sun.setPosition(orbitX, state.layout.height * 0.18 - arc * 68)
  sky.moon.setPosition(
    (orbitX + state.layout.width * 0.5) % state.layout.width,
    state.layout.height * 0.16 - Math.sin((progress + 0.5) * Math.PI) * 56,
  )
  sky.sun.setAlpha(isDay ? 0.96 : 0.18)
  sky.moon.setAlpha(isDay ? 0.2 : 0.92)

  const label = isDay ? "白天" : "夜晚"
  setText("day-cycle", `昼夜：${label} ${fmtClock(state.simulatedMinutes)}`)

  if (state.layout.refs.bgTile) {
    state.layout.refs.bgTile.tilePositionX += deltaMs * 0.01
    state.layout.refs.bgTile.tilePositionY += deltaMs * 0.004
  }
}

async function loadFallbackWorkers() {
  if (state.fallbackLoaded) {
    return
  }
  state.fallbackLoaded = true

  try {
    const workersData = await fetchJson("/api/v1/config/workers")
    const workers = Array.isArray(workersData?.workers?.items) ? workersData.workers.items : []
    if (workers.length) {
      state.workers = workers
      return
    }
  } catch (_err) {
  }

  try {
    const bootstrap = await fetchJson("/api/v1/config/bootstrap")
    const workers = Array.isArray(bootstrap?.workers?.items) ? bootstrap.workers.items : []
    if (workers.length) {
      state.workers = workers
      return
    }
  } catch (_err) {
  }

  if (!state.workers.length) {
    state.workers = [{ id: "worker-main", name: "执行者", status: "ready", backend: "core-agent" }]
  }
}

async function refreshSnapshot() {
  try {
    const snapshot = await fetchJson("/api/v1/snapshot")
    state.lastSnapshot = snapshot

    const workers = Array.isArray(snapshot?.workers) ? snapshot.workers : []
    if (workers.length) {
      state.workers = workers
    } else if (!state.workers.length) {
      await loadFallbackWorkers()
    }

    if (state.scene && state.layout) {
      syncWorkers(state.scene)
      syncTasks(snapshot)
    }

    updateStats(snapshot)
  } catch (err) {
    setText("chip-status", `快照请求失败：${String(err.message || err)}`)
  }
}

function connectStream() {
  if (state.stream) {
    state.stream.close()
    state.stream = null
  }
  if (state.reconnectTimer) {
    clearTimeout(state.reconnectTimer)
    state.reconnectTimer = null
  }

  const source = new EventSource(`/api/v1/events/stream?after=${state.lastSeq}`)
  state.stream = source
  setText("chip-status", "事件流：连接中")

  source.addEventListener("open", () => {
    setText("chip-status", "事件流：已连接")
  })

  source.addEventListener("timeline", (event) => {
    try {
      const payload = JSON.parse(event.data)
      handleEvent(payload)
    } catch (_err) {
      setText("chip-status", "事件流：解析失败")
    }
  })

  source.addEventListener("error", () => {
    setText("chip-status", "事件流：重连中")
    source.close()
    state.reconnectTimer = setTimeout(connectStream, 1400)
  })
}

function loadAssets(scene) {
  scene.load.svg(ASSETS.bgTile, `${ASSET_BASE}/bg_tile.svg`)

  scene.load.svg(ASSETS.office[0], `${ASSET_BASE}/office_lv1.svg`)
  scene.load.svg(ASSETS.office[1], `${ASSET_BASE}/office_lv2.svg`)
  scene.load.svg(ASSETS.office[2], `${ASSET_BASE}/office_lv3.svg`)

  scene.load.svg(ASSETS.hall[0], `${ASSET_BASE}/hall_lv1.svg`)
  scene.load.svg(ASSETS.hall[1], `${ASSET_BASE}/hall_lv2.svg`)
  scene.load.svg(ASSETS.hall[2], `${ASSET_BASE}/hall_lv3.svg`)

  scene.load.svg(ASSETS.delivery[0], `${ASSET_BASE}/delivery_lv1.svg`)
  scene.load.svg(ASSETS.delivery[1], `${ASSET_BASE}/delivery_lv2.svg`)
  scene.load.svg(ASSETS.delivery[2], `${ASSET_BASE}/delivery_lv3.svg`)

  scene.load.svg(ASSETS.manager, `${ASSET_BASE}/manager.svg`)
  scene.load.svg(ASSETS.worker, `${ASSET_BASE}/worker.svg`)
  scene.load.svg(ASSETS.player, `${ASSET_BASE}/player.svg`)

  scene.load.svg(ASSETS.officeDecor[0], `${ASSET_BASE}/office_decor_console.svg`)
  scene.load.svg(ASSETS.officeDecor[1], `${ASSET_BASE}/office_decor_terminal.svg`)
  scene.load.svg(ASSETS.officeDecor[2], `${ASSET_BASE}/office_decor_plant.svg`)

  scene.load.svg(ASSETS.hallDecor[0], `${ASSET_BASE}/hall_decor_rack.svg`)
  scene.load.svg(ASSETS.hallDecor[1], `${ASSET_BASE}/hall_decor_light.svg`)
  scene.load.svg(ASSETS.hallDecor[2], `${ASSET_BASE}/hall_decor_banner.svg`)

  scene.load.svg(ASSETS.deliveryDecor[0], `${ASSET_BASE}/delivery_decor_kiosk.svg`)
  scene.load.svg(ASSETS.deliveryDecor[1], `${ASSET_BASE}/delivery_decor_sign.svg`)
  scene.load.svg(ASSETS.deliveryDecor[2], `${ASSET_BASE}/delivery_decor_trophy.svg`)

  scene.load.svg(ASSETS.crate, `${ASSET_BASE}/task_crate.svg`)
  scene.load.svg(ASSETS.cart, `${ASSET_BASE}/cart.svg`)

  scene.load.svg(ASSETS.queueBoard, `${ASSET_BASE}/queue_board.svg`)
  scene.load.svg(ASSETS.doneBoard, `${ASSET_BASE}/done_board.svg`)
  scene.load.svg(ASSETS.mailDesk, `${ASSET_BASE}/mail_desk.svg`)
}

function initPhaser() {
  const mount = byId("phaser-stage")
  if (!mount) {
    return
  }
  if (!window.Phaser) {
    mount.innerHTML = '<div class="engine-fallback">游戏引擎加载失败，请检查网络或刷新页面。</div>'
    return
  }

  const width = Math.max(640, Math.floor(mount.clientWidth || 980))
  const height = Math.max(430, Math.floor(mount.clientHeight || 460))
  const dpr = clamp(window.devicePixelRatio || 1, 1, 2)

  state.game = new window.Phaser.Game({
    type: window.Phaser.AUTO,
    parent: "phaser-stage",
    width,
    height,
    resolution: dpr,
    autoRound: true,
    transparent: true,
    render: {
      antialias: false,
      pixelArt: true,
      roundPixels: true,
      powerPreference: "high-performance",
    },
    scale: {
      mode: window.Phaser.Scale.RESIZE,
      autoCenter: window.Phaser.Scale.CENTER_BOTH,
      autoRound: true,
    },
    scene: {
      preload() {
        loadAssets(this)
      },
      create() {
        state.scene = this
        this.cameras.main.setRoundPixels(true)
        if (this.game.canvas) {
          this.game.canvas.style.imageRendering = "pixelated"
          this.game.canvas.style.imageRendering = "crisp-edges"
        }
        drawWorld(this)
        if (state.lastSnapshot) {
          syncWorkers(this)
          syncTasks(state.lastSnapshot)
          renderLevels(computeLevels(state.lastSnapshot))
        }
      },
      update(_time, delta) {
        updateDayNight(delta)
      },
    },
  })

  window.addEventListener("resize", () => {
    if (!state.game) {
      return
    }
    const nextW = Math.max(640, Math.floor(mount.clientWidth || 980))
    const nextH = Math.max(430, Math.floor(mount.clientHeight || 460))
    state.game.scale.resize(nextW, nextH)
    if (state.scene) {
      drawWorld(state.scene)
      if (state.lastSnapshot) {
        syncWorkers(state.scene)
        syncTasks(state.lastSnapshot)
      }
    }
  })
}

function bindControls() {
  const soundButton = byId("sound-toggle")
  if (!soundButton) {
    return
  }

  soundButton.addEventListener("click", () => {
    state.soundEnabled = !state.soundEnabled
    if (state.soundEnabled) {
      const ctx = ensureAudioContext()
      if (ctx && ctx.state === "suspended") {
        ctx.resume()
      }
      playSfx("tick")
    }
    updateSoundButton()
  })
}

function init() {
  bindControls()
  updateSoundButton()
  renderDeliveryTrend()
  updateFacilityPanel(null)
  initPhaser()

  refreshSnapshot()
    .then(() => connectStream())
    .catch(() => connectStream())

  window.setInterval(refreshSnapshot, 5000)
}

window.addEventListener("DOMContentLoaded", init)
