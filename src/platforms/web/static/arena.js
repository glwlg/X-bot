// --- 常量配置 ---
const ASSET_BASE = "/assets/game"
const ASSETS = {
    fullMap: "bg-full-map",
    manager: "actor-manager",
    worker: "actor-worker",
    player: "actor-player",
}

// 固定物理世界大小，Phaser 会自动缩放适配窗口，保证导航坐标绝对准确 (不会因为屏幕大小偏移穿墙)
const WORLD_W = 980
const WORLD_H = 460

// --- 基于星露谷式像素大图的纯手工无障碍寻路网格 (Waypoint Graph) ---
// 定义场景中绝对不会撞墙穿模的安全行走点位。角色只能在这些点之间连接移动。
const GRAPH = {
    // 左侧（办公室）
    "office_desk": { x: 180, y: 220, links: ["office_door", "office_couch"] },
    "office_couch": { x: 120, y: 380, links: ["office_desk"] },
    "office_door": { x: 300, y: 360, links: ["office_desk", "hall_left"] },

    // 中间区块（大厅）
    "hall_left": { x: 400, y: 360, links: ["office_door", "hall_mid"] },
    "hall_mid": { x: 540, y: 360, links: ["hall_left", "hall_right", "desk_top", "desk_bot"] },
    "desk_top": { x: 540, y: 220, links: ["hall_mid", "desk_tl", "desk_tr"] },
    "desk_tl": { x: 440, y: 220, links: ["desk_top"] },
    "desk_tr": { x: 640, y: 220, links: ["desk_top"] },
    "desk_bot": { x: 540, y: 440, links: ["hall_mid"] },

    // 右侧区块（交付区）
    "hall_right": { x: 680, y: 360, links: ["hall_mid", "delivery_entrance"] },
    "delivery_entrance": { x: 820, y: 360, links: ["hall_right", "delivery_desk"] },
    "delivery_desk": { x: 820, y: 260, links: ["delivery_entrance"] },
}

// 广度优先搜索 (BFS) 构建两点最短路径
function findPath(startId, endId) {
    if (startId === endId) return [startId]
    let queue = [[startId]]
    let visited = new Set([startId])

    while (queue.length > 0) {
        let path = queue.shift()
        let node = path[path.length - 1]

        if (!GRAPH[node]) continue
        for (const nxt of GRAPH[node].links) {
            if (nxt === endId) return [...path, nxt]
            if (!visited.has(nxt)) {
                visited.add(nxt)
                queue.push([...path, nxt])
            }
        }
    }
    return [] // 无路可去
}

// --- 游戏行为核心类 (Game Unit) ---
class GameUnit {
    constructor(scene, skinKey, name, startNodeId) {
        this.scene = scene
        this.currentNode = startNodeId
        let node = GRAPH[startNodeId] || { x: WORLD_W / 2, y: WORLD_H / 2 }

        // 阴影
        this.shadow = scene.add.ellipse(0, 48, 48, 20, 0x000000, 0.3)

        // 角色立绘 (单帧静太图)
        this.sprite = scene.add.sprite(0, 0, skinKey)
        // 动态等比缩放，使得人物视觉高度大约占 110px
        const targetHeight = 110
        const scale = targetHeight / (this.sprite.height || 500)
        this.sprite.setScale(scale)

        // 名字标签
        this.nameText = scene.add.text(0, 72, name, {
            fontFamily: '"Noto Sans SC", sans-serif',
            fontSize: "11px",
            color: "#0f172a",
            stroke: "#f8fafc",
            strokeThickness: 3,
        }).setOrigin(0.5)

        // 创建容器
        this.container = scene.add.container(node.x, node.y, [this.shadow, this.sprite, this.nameText])
        this.container.setDepth(20)

        // 运动属性
        this.path = []
        this.speed = 1.0 + Math.random() * 0.4 // 随机一点差异速度
        this.idleTimer = Math.random() * 2000
        this.isMoving = false
        this.animTime = Math.random() * 1000
        this.homeNode = startNodeId // 专属工作站/基地
    }

    destroy() {
        this.container.destroy()
    }

    setTarget(targetNodeId) {
        if (!GRAPH[targetNodeId]) return
        this.path = findPath(this.currentNode, targetNodeId)
        if (this.path.length > 0) this.path.shift() // 剔除当前起点
    }

    update(delta) {
        this.animTime += delta

        if (this.path.length > 0) {
            this.isMoving = true
            let nextNodeId = this.path[0]
            let targetCoords = GRAPH[nextNodeId]

            let dx = targetCoords.x - this.container.x
            let dy = targetCoords.y - this.container.y
            let dist = Math.sqrt(dx * dx + dy * dy)

            if (dist <= this.speed) {
                // 到达节点
                this.container.x = targetCoords.x
                this.container.y = targetCoords.y
                this.currentNode = nextNodeId
                this.path.shift()
            } else {
                // 缓慢步进
                this.container.x += (dx / dist) * this.speed
                this.container.y += (dy / dist) * this.speed

                // 翻转朝向
                if (Math.abs(dx) > 0.5) {
                    this.sprite.setFlipX(dx > 0)
                }
            }
        } else {
            this.isMoving = false
            // 空闲漫游逻辑 (Wandering)
            this.idleTimer -= delta
            if (this.idleTimer <= 0) {
                // 在工位附近轻微“巡逻 / 散步”
                if (Math.random() < 0.4) {
                    // 在直接相连的相邻点随机走走
                    let links = GRAPH[this.currentNode].links
                    let randNext = links[Math.floor(Math.random() * links.length)]
                    this.setTarget(randNext)
                } else {
                    // 回到默认原岗位
                    if (this.currentNode !== this.homeNode) {
                        this.setTarget(this.homeNode)
                    }
                }
                this.idleTimer = 3000 + Math.random() * 6000
            }
        }

        // Y轴轻微正弦颠簸：增加像素画风格的弹跳复古生命力
        if (this.isMoving) {
            this.sprite.y = Math.sin(this.animTime / 150) * 2.5
        } else {
            this.sprite.y = 0
        }

        // 更新深度确保透视关系（下面的人遮住上面的人）
        this.container.setDepth(20 + this.container.y / 1000)
    }
}

// --- 全局引用 ---
let manager = null
let player = null
let workersMap = new Map()
let arenaScene = null


// --- Phaser 场景定义 ---
class ArenaScene extends window.Phaser.Scene {
    preload() {
        this.load.image(ASSETS.fullMap, `${ASSET_BASE}/full_map.png`)
        this.load.image(ASSETS.manager, `${ASSET_BASE}/manager.png`)
        this.load.image(ASSETS.worker, `${ASSET_BASE}/worker.png`)
        this.load.image(ASSETS.player, `${ASSET_BASE}/player.png`)
    }

    create() {
        // 绘制全景大画布
        const bg = this.add.image(WORLD_W / 2, WORLD_H / 2, ASSETS.fullMap)
        bg.setDisplaySize(WORLD_W, WORLD_H)
        bg.setDepth(0)



        manager = new GameUnit(this, ASSETS.manager, "管理者", "office_desk")
        player = new GameUnit(this, ASSETS.player, "玩家", "delivery_desk")
    }

    update(time, delta) {
        if (manager) manager.update(delta)
        if (player) player.update(delta)
        for (let w of workersMap.values()) {
            w.update(delta)
        }
    }
}

function initPhaser() {
    new window.Phaser.Game({
        type: window.Phaser.AUTO,
        parent: "phaser-stage",
        width: WORLD_W,
        height: WORLD_H,
        transparent: true,
        render: {
            pixelArt: true,
            roundPixels: true,
            antialias: false,
        },
        scale: {
            // 全图拉伸自适应父级 DIV 容器，解决缩放导致的逻辑寻路位移乱窜
            mode: window.Phaser.Scale.FIT,
            autoCenter: window.Phaser.Scale.CENTER_BOTH
        },
        scene: ArenaScene
    })
}

// --- 后端快照流对齐 (Stream Polling) ---

async function fetchSnapshot() {
    if (!arenaScene || !manager) return
    try {
        const res = await fetch("/api/v1/snapshot")
        if (!res.ok) return
        const data = await res.json()

        // 处理执行者 workers
        const activeWorkers = data.workers || [{ id: "worker-main", name: "执行者", status: "ready" }]
        const keepIds = new Set(activeWorkers.map(w => w.id || "worker-main"))

        // 移除不存在的 Worker
        for (let [id, unit] of workersMap.entries()) {
            if (!keepIds.has(id)) {
                unit.destroy()
                workersMap.delete(id)
            }
        }

        // 新增/更新 Worker
        const DESK_KEYS = ["desk_tl", "desk_tr", "desk_bot"]
        activeWorkers.forEach((w, index) => {
            let id = w.id || "worker-main"
            let unit = workersMap.get(id)
            if (!unit) {
                let startNode = DESK_KEYS[index % DESK_KEYS.length]
                unit = new GameUnit(arenaScene, ASSETS.worker, w.name || "员工", startNode)
                workersMap.set(id, unit)
            }

            // 更新状态颜色区分
            let status = String(w.status).toLowerCase()
            if (status === "busy") unit.sprite.setTint(0xfbbf24) // 忙碌黄
            else if (status === "paused") unit.sprite.setTint(0x94a3b8) // 挂机灰
            else unit.sprite.clearTint()

            unit.nameText.setText(`[${status}]`)
        })

    } catch (err) {
        console.warn("Snapshot fetch failed", err)
    }
}

window.addEventListener("DOMContentLoaded", () => {
    initPhaser()
    setInterval(fetchSnapshot, 2000)
    fetchSnapshot()
})
