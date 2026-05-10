<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import {
    ArrowRight,
    CalendarDays,
    CheckCircle2,
    Globe2,
    HeartPulse,
    KeyRound,
    Link2,
    MessageSquareText,
    Radio,
    RefreshCw,
    Shield,
    TrendingUp,
    UsersRound,
    WalletCards,
    Zap,
} from 'lucide-vue-next'

import { getAdminAudit, getDiagnostics } from '@/api/admin'
import request from '@/api/request'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()
const diagnostics = ref<Record<string, any> | null>(null)
const auditItems = ref<Array<Record<string, any>>>([])
const healthStatus = ref<'loading' | 'ok' | 'error'>('loading')
const diagnosticsStatus = ref<'idle' | 'loading' | 'ok' | 'forbidden' | 'error'>('idle')
const lastUpdatedAt = ref<Date | null>(null)

const roleLabel = computed(() => {
    if (authStore.isAdmin) return '管理员'
    if (authStore.isOperator) return '运营员'
    return '观察者'
})

const workspaceCards = computed(() => [
    {
        title: 'Chat 对话',
        description: 'AI 对话门户，支持文本与多模态交互。',
        to: '/chat',
        icon: MessageSquareText,
    },
    {
        title: 'Bind 绑定',
        description: '管理外部服务连接与 API Hooks。',
        to: '/bindings',
        icon: Link2,
    },
    {
        title: 'Keys 凭据',
        description: '管理发布凭据与多账户投递目标。',
        to: '/credentials',
        icon: KeyRound,
    },
    {
        title: 'RSS 订阅源',
        description: '自动化新闻抓取与情报订阅。',
        to: '/modules/rss',
        icon: Radio,
    },
    {
        title: 'Scheduling 任务调度',
        description: '临时任务管理与自动化执行。',
        to: '/modules/scheduler',
        icon: CalendarDays,
    },
    {
        title: 'Heartbeat 心跳监控',
        description: '实时监控系统健康与心跳指标。',
        to: '/modules/monitor',
        icon: HeartPulse,
    },
    {
        title: 'Stocks 市场追踪',
        description: '市场分析与金融预测追踪。',
        to: '/modules/watchlist',
        icon: TrendingUp,
    },
    {
        title: 'Accounting 智能记账',
        description: '记录收支、资产账户与预算统计。',
        to: '/accounting',
        icon: WalletCards,
    },
])

const platformEntries = computed(() =>
    Object.entries(diagnostics.value?.runtime_config?.platforms || {}).map(([name, enabled]) => ({
        name,
        enabled: Boolean(enabled),
        configured: Boolean(diagnostics.value?.platform_env?.[name]?.configured),
    }))
)

const enabledPlatforms = computed(() =>
    platformEntries.value.filter(item => item.enabled)
)

const configuredPlatforms = computed(() =>
    platformEntries.value.filter(item => item.configured)
)

const statCards = computed(() => [
    {
        label: '渠道',
        alias: 'Channel',
        value: diagnostics.value ? String(enabledPlatforms.value.length) : '未知',
        detail: diagnostics.value
            ? `已启用 ${enabledPlatforms.value.length} / ${platformEntries.value.length} 个渠道，已配置 ${configuredPlatforms.value.length} 个`
            : '需要诊断权限获取渠道状态',
        icon: Globe2,
        tone: 'blue',
    },
    {
        label: '角色',
        alias: 'Role',
        value: roleLabel.value,
        detail: authStore.user?.email || '当前登录用户',
        icon: UsersRound,
        tone: 'teal',
    },
    {
        label: '模块数',
        alias: 'Modules',
        value: String(workspaceCards.value.length),
        detail: '当前控制台可见工作空间入口',
        icon: WalletCards,
        tone: 'violet',
    },
    {
        label: '安全策略',
        alias: 'Security',
        value: 'RBAC',
        detail: `当前权限：${authStore.user?.role || 'viewer'}`,
        icon: Shield,
        tone: 'blue',
    },
])

const formatAuditTime = (value: unknown) => {
    const raw = String(value || '').trim()
    if (!raw) return '-'
    const date = new Date(raw)
    if (Number.isNaN(date.getTime())) return raw
    return date.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
    })
}

const recentActivities = computed(() =>
    auditItems.value.slice(0, 5).map(item => ({
        title: String(item.action || '审计记录'),
        detail: String(item.summary || item.target || item.actor || '无摘要'),
        time: formatAuditTime(item.ts),
        tone: String(item.status || '').toLowerCase() === 'success' ? 'green' : 'amber',
        icon: Shield,
    }))
)

const configFiles = computed(() => diagnostics.value?.config_files || {})

const systemRows = computed(() => [
    {
        label: 'API 服务',
        value: healthStatus.value === 'ok' ? '健康检查通过' : healthStatus.value === 'loading' ? '检查中' : '健康检查失败',
        ok: healthStatus.value === 'ok',
    },
    {
        label: '诊断接口',
        value: diagnosticsStatus.value === 'ok'
            ? '已获取诊断数据'
            : diagnosticsStatus.value === 'forbidden'
                ? '当前账号无诊断权限'
                : diagnosticsStatus.value === 'loading'
                    ? '加载中'
                    : '未获取诊断数据',
        ok: diagnosticsStatus.value === 'ok',
    },
    {
        label: '配置文件',
        value: diagnostics.value
            ? `env ${configFiles.value.env_exists ? '存在' : '缺失'} / memory ${configFiles.value.memory_exists ? '存在' : '缺失'}`
            : '未知',
        ok: Boolean(configFiles.value.env_exists && configFiles.value.memory_exists),
    },
    {
        label: 'Memory',
        value: diagnostics.value?.memory?.provider || '未知',
        ok: Boolean(diagnostics.value?.memory?.provider),
    },
    {
        label: '平台配置',
        value: diagnostics.value
            ? `${configuredPlatforms.value.length} / ${platformEntries.value.length} 已配置`
            : '未知',
        ok: diagnostics.value ? configuredPlatforms.value.length >= enabledPlatforms.value.length : false,
    },
])

const systemStatusText = computed(() => {
    if (healthStatus.value === 'loading' || diagnosticsStatus.value === 'loading') return '检查中'
    if (healthStatus.value !== 'ok') return '异常'
    if (diagnosticsStatus.value === 'forbidden') return '基础正常，诊断无权限'
    if (diagnosticsStatus.value !== 'ok') return '基础正常，诊断未知'
    const hasBadRows = systemRows.value.some(row => !row.ok)
    return hasBadRows ? '需检查' : '正常'
})

const lastUpdatedText = computed(() => {
    if (!lastUpdatedAt.value) return '尚未更新'
    return lastUpdatedAt.value.toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
    })
})

const gitHeadShort = computed(() => {
    const head = String(diagnostics.value?.version?.git_head || '').trim()
    return head ? head.slice(0, 12) : '未知'
})

const loadDashboardStatus = async () => {
    healthStatus.value = 'loading'
    diagnosticsStatus.value = authStore.isOperator ? 'loading' : 'forbidden'
    try {
        await request.get('/health')
        healthStatus.value = 'ok'
    } catch {
        healthStatus.value = 'error'
    }

    if (!authStore.isOperator) {
        diagnostics.value = null
        auditItems.value = []
        lastUpdatedAt.value = new Date()
        return
    }

    try {
        const [diagResponse, auditResponse] = await Promise.all([getDiagnostics(), getAdminAudit()])
        diagnostics.value = diagResponse.data
        auditItems.value = auditResponse.data.items || []
        diagnosticsStatus.value = 'ok'
    } catch (error: any) {
        diagnostics.value = null
        auditItems.value = []
        diagnosticsStatus.value = error?.response?.status === 403 ? 'forbidden' : 'error'
    } finally {
        lastUpdatedAt.value = new Date()
    }
}

onMounted(loadDashboardStatus)
</script>

<template>
  <div class="dashboard-page">
    <section class="welcome-panel">
      <div>
        <h1>欢迎回来，{{ roleLabel }}</h1>
        <p>IKAROS Ethereal Sentinel 控制台已加载；系统状态来自实时健康检查与诊断接口。</p>
      </div>
      <div class="welcome-status">
        <div class="status-pill" :class="{ warning: systemStatusText !== '正常' }">
          <span />
          系统状态：{{ systemStatusText }}
        </div>
        <button type="button" @click="loadDashboardStatus">
          最后更新：{{ lastUpdatedText }}
          <RefreshCw class="h-4 w-4" :class="{ 'animate-spin': healthStatus === 'loading' || diagnosticsStatus === 'loading' }" />
        </button>
      </div>
    </section>

    <section class="stat-grid">
      <RouterLink
        v-for="card in statCards"
        :key="card.label"
        to="/admin/diagnostics"
        class="stat-card"
        :class="`tone-${card.tone}`"
      >
        <div class="stat-icon">
          <component :is="card.icon" class="h-8 w-8" />
        </div>
        <div class="stat-copy">
          <div class="stat-label">{{ card.label }} <span>({{ card.alias }})</span></div>
          <div class="stat-value">{{ card.value }}</div>
          <p>{{ card.detail }}</p>
        </div>
        <ArrowRight class="stat-arrow h-5 w-5" />
      </RouterLink>
    </section>

    <section class="dashboard-grid">
      <div class="workspace-panel">
        <div class="section-heading">
          <h2>工作空间 <span>/ Workspace</span></h2>
        </div>
        <div class="workspace-grid">
          <RouterLink
            v-for="card in workspaceCards"
            :key="card.to"
            :to="card.to"
            class="workspace-card"
          >
            <div class="workspace-icon">
              <component :is="card.icon" class="h-7 w-7" />
            </div>
            <div>
              <h3>{{ card.title }}</h3>
              <p>{{ card.description }}</p>
            </div>
            <ArrowRight class="workspace-arrow h-5 w-5" />
          </RouterLink>
        </div>
      </div>

      <aside class="side-stack">
        <section class="side-panel activity-panel">
          <div class="side-heading">
            <h2>近期活动 <span>/ Recent Activity</span></h2>
            <a href="#">查看全部</a>
          </div>
          <div class="activity-list">
            <article v-for="item in recentActivities" :key="item.title" class="activity-item">
              <span class="activity-dot" :class="item.tone" />
              <div class="activity-icon">
                <component :is="item.icon" class="h-4 w-4" />
              </div>
              <div class="activity-copy">
                <h3>{{ item.title }}</h3>
                <p>{{ item.detail }}</p>
              </div>
              <time>{{ item.time }}</time>
            </article>
            <div v-if="!recentActivities.length" class="empty-note">
              {{ authStore.isOperator ? '暂无管理员审计记录。' : '当前账号无权限查看审计记录。' }}
            </div>
          </div>
        </section>

        <section class="side-panel status-panel">
          <div class="side-heading">
            <h2>系统状态 <span>/ System Status</span></h2>
            <a href="#">查看详情</a>
          </div>
          <div class="status-grid">
            <div class="status-list">
              <div v-for="row in systemRows" :key="row.label" class="status-row">
                <span :class="{ warning: !row.ok }">
                  <CheckCircle2 v-if="row.ok" class="h-3.5 w-3.5" />
                  <Zap v-else class="h-3.5 w-3.5" />
                </span>
                <strong>{{ row.label }}</strong>
                <em>{{ row.value }}</em>
              </div>
            </div>
            <div class="diagnostic-card">
              <div class="load-title">诊断摘要</div>
              <div class="diagnostic-metrics">
                <div><span>平台</span><strong>{{ diagnostics ? `${enabledPlatforms.length}/${platformEntries.length}` : '未知' }}</strong></div>
                <div><span>Memory</span><strong>{{ diagnostics?.memory?.provider || '未知' }}</strong></div>
                <div><span>Git</span><strong>{{ gitHeadShort }}</strong></div>
              </div>
              <p>
                {{ diagnosticsStatus === 'ok'
                  ? '数据来自 /admin/diagnostics。'
                  : diagnosticsStatus === 'forbidden'
                    ? '当前账号没有诊断权限。'
                    : '暂未获取诊断数据。' }}
              </p>
            </div>
          </div>
        </section>
      </aside>
    </section>
  </div>
</template>

<style scoped>
.dashboard-page {
  display: grid;
  gap: 24px;
}

.welcome-panel,
.stat-card,
.workspace-panel,
.side-panel {
  border: 1px solid var(--panel-border);
  border-radius: 14px;
  background: #fff;
  box-shadow: var(--shadow-card);
}

.welcome-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  min-height: 128px;
  padding: 28px 30px;
}

.welcome-panel h1 {
  margin: 0;
  color: var(--text-strong);
  font-size: 25px;
  font-weight: 800;
}

.welcome-panel p {
  margin: 16px 0 0;
  color: var(--text-body);
  font-size: 15px;
}

.welcome-status {
  display: grid;
  justify-items: end;
  gap: 14px;
  color: var(--text-muted);
  font-size: 14px;
}

.status-pill,
.welcome-status button {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  border: 0;
  border-radius: 10px;
  background: #f8fafc;
  color: var(--text-body);
  padding: 11px 16px;
}

.status-pill span {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--success);
}

.status-pill.warning span {
  background: var(--warning);
}

.welcome-status button {
  background: transparent;
  padding: 0;
  color: var(--text-muted);
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 22px;
}

.stat-card {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr) 20px;
  align-items: center;
  gap: 18px;
  min-height: 132px;
  padding: 22px 24px;
  color: inherit;
  text-decoration: none;
}

.stat-icon,
.workspace-icon,
.activity-icon {
  display: grid;
  place-items: center;
  border-radius: 18px;
  background: var(--brand-blue-soft);
  color: var(--brand-blue);
}

.stat-icon {
  width: 76px;
  height: 76px;
  border-radius: 50%;
}

.tone-teal .stat-icon {
  background: #e7f8f5;
  color: #0f9f8f;
}

.tone-violet .stat-icon {
  background: #f1e8ff;
  color: #7c3aed;
}

.stat-label {
  color: var(--text-body);
  font-size: 15px;
  font-weight: 800;
}

.stat-label span {
  color: var(--text-subtle);
  font-weight: 600;
}

.stat-value {
  margin-top: 6px;
  color: var(--text-strong);
  font-size: 28px;
  font-weight: 800;
  line-height: 1;
}

.stat-copy p {
  margin: 8px 0 0;
  color: var(--text-muted);
  font-size: 14px;
}

.stat-arrow,
.workspace-arrow {
  justify-self: end;
  color: var(--text-subtle);
}

.dashboard-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 590px;
  gap: 22px;
}

.workspace-panel {
  padding: 22px 24px;
}

.section-heading h2,
.side-heading h2 {
  margin: 0;
  color: var(--text-strong);
  font-size: 20px;
  font-weight: 800;
}

.section-heading span,
.side-heading span {
  color: var(--text-muted);
  font-weight: 600;
}

.workspace-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 18px;
  margin-top: 18px;
}

.workspace-card {
  position: relative;
  display: grid;
  align-content: start;
  gap: 22px;
  min-height: 244px;
  padding: 24px 22px;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: #fff;
  color: inherit;
  text-decoration: none;
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}

.workspace-card:hover {
  border-color: #9ec5ff;
  box-shadow: 0 18px 38px rgba(47, 124, 246, 0.08);
}

.workspace-icon {
  width: 56px;
  height: 56px;
  border-radius: 14px;
}

.workspace-card h3 {
  margin: 0;
  color: var(--text-strong);
  font-size: 18px;
  font-weight: 800;
}

.workspace-card p {
  margin: 12px 0 0;
  color: var(--text-muted);
  font-size: 15px;
  line-height: 1.65;
}

.workspace-arrow {
  position: absolute;
  right: 22px;
  bottom: 22px;
}

.side-stack {
  display: grid;
  gap: 18px;
}

.side-panel {
  padding: 20px 24px;
}

.side-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

.side-heading a {
  color: var(--brand-blue);
  font-size: 14px;
  font-weight: 700;
  text-decoration: none;
}

.activity-list {
  display: grid;
  gap: 14px;
  margin-top: 16px;
}

.activity-item {
  display: grid;
  grid-template-columns: 9px 36px minmax(0, 1fr) 70px;
  align-items: center;
  gap: 14px;
}

.activity-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--brand-blue);
}

.activity-dot.green {
  background: var(--success);
}

.activity-dot.amber {
  background: var(--warning);
}

.activity-icon {
  width: 36px;
  height: 36px;
  border-radius: 9px;
  color: #667085;
  background: #f2f4f7;
}

.activity-copy h3 {
  margin: 0;
  color: var(--text-strong);
  font-size: 15px;
  font-weight: 800;
}

.activity-copy p,
.activity-item time {
  margin: 4px 0 0;
  color: var(--text-muted);
  font-size: 13px;
}

.activity-item time {
  margin: 0;
  text-align: right;
}

.empty-note {
  border: 1px dashed var(--panel-border);
  border-radius: 10px;
  background: #fbfdff;
  color: var(--text-muted);
  padding: 18px;
  text-align: center;
}

.status-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 252px;
  gap: 22px;
  margin-top: 18px;
}

.status-list {
  display: grid;
  gap: 16px;
}

.status-row {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) minmax(120px, auto);
  align-items: center;
  gap: 10px;
  font-size: 14px;
}

.status-row span {
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--success);
  color: #fff;
}

.status-row span.warning {
  background: var(--warning);
}

.status-row strong {
  color: var(--text-strong);
}

.status-row em {
  color: var(--text-muted);
  font-style: normal;
  text-align: right;
}

.load-card,
.diagnostic-card {
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  padding: 14px 16px;
}

.load-title {
  color: var(--text-strong);
  font-size: 14px;
  font-weight: 800;
}

.load-card svg {
  width: 100%;
  height: 82px;
  margin-top: 8px;
}

.load-card path:first-child {
  fill: none;
  stroke: var(--brand-blue);
  stroke-width: 3;
}

.load-card path:last-child {
  fill: rgba(47, 124, 246, 0.10);
  stroke: none;
}

.load-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 6px;
}

.load-metrics div {
  display: grid;
  gap: 3px;
}

.load-metrics span {
  color: var(--text-muted);
  font-size: 12px;
}

.load-metrics strong {
  color: var(--brand-blue);
  font-size: 24px;
  line-height: 1;
}

.diagnostic-metrics {
  display: grid;
  gap: 12px;
  margin-top: 14px;
}

.diagnostic-metrics div {
  display: grid;
  gap: 4px;
}

.diagnostic-metrics span {
  color: var(--text-muted);
  font-size: 12px;
}

.diagnostic-metrics strong {
  overflow: hidden;
  color: var(--brand-blue);
  font-size: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.diagnostic-card p {
  margin: 14px 0 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.5;
}

@media (max-width: 1600px) {
  .dashboard-grid {
    grid-template-columns: minmax(0, 1fr) 470px;
  }

  .workspace-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .status-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 1180px) {
  .stat-grid,
  .dashboard-grid {
    grid-template-columns: 1fr;
  }

  .workspace-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .welcome-panel,
  .stat-card {
    grid-template-columns: 1fr;
  }

  .welcome-panel {
    align-items: flex-start;
    flex-direction: column;
    padding: 22px;
  }

  .welcome-status {
    justify-items: start;
  }

  .stat-grid,
  .workspace-grid {
    grid-template-columns: 1fr;
  }

  .workspace-card {
    min-height: 190px;
  }

  .activity-item {
    grid-template-columns: 9px 36px minmax(0, 1fr);
  }

  .activity-item time {
    grid-column: 3;
    text-align: left;
  }
}
</style>
