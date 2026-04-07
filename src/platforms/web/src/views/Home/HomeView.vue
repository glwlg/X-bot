<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import {
    Cable,
    Gauge,
    HeartPulse,
    Link2,
    MessageSquareText,
    Radio,
    Settings2,
    ShieldCheck,
    TrendingUp,
    WalletCards,
    Zap,
} from 'lucide-vue-next'

import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()

const roleLabel = computed(() => {
    if (authStore.isAdmin) return '管理员'
    if (authStore.isOperator) return '运营员'
    return '观察者'
})

const workspaceCards = computed(() => [
    {
        title: 'Chat',
        description: 'Primary AI interaction portal with conversational memory.',
        to: '/chat',
        icon: MessageSquareText,
        tone: 'pink',
    },
    {
        title: 'Bind',
        description: 'Manage external service connections and API hooks.',
        to: '/bindings',
        icon: Link2,
        tone: 'cyan',
    },
    {
        title: 'RSS',
        description: 'Automated news harvesting and ethereal intelligence feeds.',
        to: '/modules/rss',
        icon: Radio,
        tone: 'silver',
    },
    {
        title: 'Scheduling',
        description: 'Temporal management and automated task execution.',
        to: '/modules/scheduler',
        icon: Cable,
        tone: 'silver',
    },
    {
        title: 'Heartbeat',
        description: 'Real-time system health and pulse monitoring metrics.',
        to: '/modules/monitor',
        icon: HeartPulse,
        tone: 'silver',
    },
    {
        title: 'Stocks',
        description: 'Market analysis and ethereal financial forecasting.',
        to: '/modules/watchlist',
        icon: TrendingUp,
        tone: 'silver',
    },
    {
        title: 'Billing',
        description: 'Usage quotas and resource allocation accounting.',
        to: '/accounting',
        icon: WalletCards,
        tone: 'silver',
    },
])

const adminCards = computed(() => {
    const cards = []

    if (authStore.isAdmin) {
        cards.push({
            title: 'Runtime',
            description: 'Configure admin identity, channels, docs and runtime controls.',
            to: '/admin/runtime',
            icon: Zap,
            tone: 'pink',
        })
    }

    if (authStore.isOperator) {
        cards.push({
            title: 'Users',
            description: 'Control operator identities, roles and channel access.',
            to: '/admin/users',
            icon: ShieldCheck,
            tone: 'cyan',
        })
        cards.push({
            title: 'Diagnostics',
            description: 'Inspect service state, queues and runtime health telemetry.',
            to: '/admin/diagnostics',
            icon: Gauge,
            tone: 'silver',
        })
    }

    if (authStore.isAdmin) {
        cards.push({
            title: 'Models',
            description: 'Manage providers, bindings, model pools and execution selection.',
            to: '/admin/models',
            icon: Settings2,
            tone: 'pink',
        })
    }

    return cards
})

const spotlightActions = computed(() => {
    const actions = []

    if (authStore.isAdmin) {
        actions.push({
            title: '运行配置',
            label: '运行配置',
            to: '/admin/runtime',
            icon: Zap,
            tone: 'pink',
        })
    }

    actions.push({
        title: '助手对话',
        label: '助手对话',
        to: '/chat',
        icon: MessageSquareText,
        tone: 'pink',
    })

    actions.push({
        title: '记账',
        label: '记账',
        to: '/accounting',
        icon: WalletCards,
        tone: 'silver',
    })

    if (authStore.isAdmin) {
        actions.push({
            title: '模型配置',
            label: '模型配置',
            to: '/admin/models',
            icon: Settings2,
            tone: 'pink',
        })
    } else if (authStore.isOperator) {
        actions.push({
            title: '诊断',
            label: '诊断',
            to: '/admin/diagnostics',
            icon: Gauge,
            tone: 'cyan',
        })
    }

    return actions
})

const statusCards = computed(() => [
    { label: '渠道', value: 'Web', tone: 'cyan' },
    { label: '角色', value: roleLabel.value, tone: 'pink' },
    { label: '模块', value: String(workspaceCards.value.length).padStart(2, '0'), tone: 'silver' },
    { label: '安全', value: 'RBAC', tone: 'cyan' },
])
</script>

<template>
  <div class="home-dashboard">
    <section class="home-hero">
      <div class="home-hero-copy">
        <div class="home-ai-pulse" />
        <div class="home-hero-kicker font-body">工作空间 <span>- Ikaros</span></div>
        <p class="home-hero-text">
          系统核心已激活。Sentinel 正在监控 {{ workspaceCards.length }} 个带有 RBAC 高级安全权限的活动模块。
        </p>

        <div class="home-status-grid">
          <article
            v-for="card in statusCards"
            :key="card.label"
            class="home-status-pill"
            :class="`tone-${card.tone}`"
          >
            <div class="home-status-label">{{ card.label }}</div>
            <div class="home-status-value font-display">{{ card.value }}</div>
          </article>
        </div>
      </div>

      <aside class="home-spotlight">
        <div class="home-spotlight-head">
          <div class="home-spotlight-kicker">重点操作</div>
          <div class="home-spotlight-badge">+</div>
        </div>

        <div class="home-spotlight-list">
          <RouterLink
            v-for="action in spotlightActions"
            :key="action.to"
            :to="action.to"
            class="home-spotlight-item"
            :class="`tone-${action.tone}`"
          >
            <div class="home-spotlight-item-left">
              <div class="home-spotlight-icon">
                <component :is="action.icon" class="h-5 w-5" />
              </div>
              <span>{{ action.title }}</span>
            </div>
            <span class="home-spotlight-label">{{ action.label }}</span>
          </RouterLink>
        </div>
      </aside>
    </section>

    <section class="home-section">
      <div class="home-section-head">
        <h2 class="home-section-title font-display">工作空间</h2>
        <div class="home-section-rule" />
      </div>

      <div class="home-card-grid">
        <RouterLink
          v-for="card in workspaceCards"
          :key="card.to"
          :to="card.to"
          class="home-card"
          :class="`tone-${card.tone}`"
        >
          <div class="home-card-icon">
            <component :is="card.icon" class="h-6 w-6" />
          </div>

          <div class="home-card-copy">
            <h3 class="home-card-title font-display">{{ card.title }}</h3>
            <p class="home-card-description">{{ card.description }}</p>
          </div>
        </RouterLink>
      </div>
    </section>

    <section v-if="adminCards.length" class="home-section">
      <div class="home-section-head">
        <h2 class="home-section-title font-display">管理员</h2>
        <div class="home-section-rule" />
      </div>

      <div class="home-card-grid admin-grid">
        <RouterLink
          v-for="card in adminCards"
          :key="card.to"
          :to="card.to"
          class="home-card"
          :class="`tone-${card.tone}`"
        >
          <div class="home-card-icon">
            <component :is="card.icon" class="h-6 w-6" />
          </div>

          <div class="home-card-copy">
            <h3 class="home-card-title font-display">{{ card.title }}</h3>
            <p class="home-card-description">{{ card.description }}</p>
          </div>
        </RouterLink>
      </div>
    </section>
  </div>
</template>

<style scoped>
.home-dashboard {
  --surface: #10131a;
  --surface-low: #191c22;
  --surface-high: #272a31;
  --primary: #ffcbd5;
  --secondary: #c6c6c6;
  --tertiary: #66eaff;
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 4.25rem;
  padding: 1.6rem 0 4rem;
  color: #f5f7fb;
}

.home-dashboard::before {
  content: '';
  position: absolute;
  top: 1rem;
  left: 6%;
  width: 30rem;
  height: 16rem;
  background: radial-gradient(circle, rgba(102, 234, 255, 0.14) 0%, rgba(255, 203, 213, 0.12) 36%, transparent 72%);
  filter: blur(88px);
  opacity: 0.56;
  pointer-events: none;
}

.home-hero {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 330px;
  gap: 2.5rem;
  align-items: start;
}

.home-hero-copy {
  position: relative;
  padding-top: 2.75rem;
}

.home-ai-pulse {
  position: absolute;
  top: -1.2rem;
  left: -3rem;
  width: 28rem;
  height: 16rem;
  background: radial-gradient(circle, rgba(102, 234, 255, 0.18) 0%, rgba(255, 203, 213, 0.14) 35%, transparent 72%);
  filter: blur(82px);
  opacity: 0.58;
  pointer-events: none;
}

.home-hero-kicker {
  position: relative;
  z-index: 1;
  font-size: 1.05rem;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.88);
}

.home-hero-kicker span {
  color: var(--primary);
}

.home-hero-text {
  position: relative;
  z-index: 1;
  margin: 1.15rem 0 0;
  max-width: 52rem;
  font-size: 1.4rem;
  line-height: 1.75;
  color: rgba(255, 255, 255, 0.76);
}

.home-status-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1.35rem;
  margin-top: 2.75rem;
}

.home-status-pill {
  min-height: 7rem;
  padding: 1.25rem 1.45rem;
  border-radius: 2rem;
  background: rgba(25, 28, 34, 0.92);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.04), 0 24px 48px rgba(0, 0, 0, 0.18);
}

.home-status-label {
  font-size: 0.82rem;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: rgba(255, 255, 255, 0.46);
}

.home-status-value {
  margin-top: 0.68rem;
  font-size: 2.45rem;
  line-height: 1;
  font-weight: 700;
}

.home-spotlight {
  padding: 1.7rem;
  border-radius: 2.4rem;
  background: rgba(25, 28, 34, 0.9);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.04), 0 32px 80px rgba(0, 0, 0, 0.24);
  backdrop-filter: blur(28px);
}

.home-spotlight-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.home-spotlight-kicker {
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--primary);
}

.home-spotlight-badge {
  display: grid;
  place-items: center;
  width: 1.6rem;
  height: 1.6rem;
  border-radius: 999px;
  background: rgba(255, 203, 213, 0.12);
  color: var(--primary);
  font-weight: 700;
}

.home-spotlight-list {
  display: grid;
  gap: 0.85rem;
  margin-top: 1.15rem;
}

.home-spotlight-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 1rem 1.1rem;
  border-radius: 1.35rem;
  background: rgba(16, 19, 26, 0.72);
  color: #fff;
  text-decoration: none;
  transition: transform 0.2s ease, background-color 0.2s ease;
}

.home-spotlight-item:hover {
  transform: translateY(-2px);
  background: rgba(39, 42, 49, 0.96);
}

.home-spotlight-item-left {
  display: flex;
  align-items: center;
  gap: 0.9rem;
  font-size: 1.08rem;
  font-weight: 700;
}

.home-spotlight-icon {
  display: grid;
  place-items: center;
  width: 2.75rem;
  height: 2.75rem;
  border-radius: 1.1rem;
  background: rgba(39, 42, 49, 0.95);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.04);
}

.home-spotlight-label {
  color: rgba(255, 255, 255, 0.6);
  font-size: 1rem;
}

.home-section {
  display: flex;
  flex-direction: column;
  gap: 1.65rem;
}

.home-section-head {
  display: flex;
  align-items: center;
  gap: 1.2rem;
}

.home-section-title {
  margin: 0;
  font-size: 3rem;
  line-height: 1;
}

.home-section-rule {
  flex: 1;
  height: 1px;
  background: linear-gradient(90deg, rgba(255, 203, 213, 0.32), rgba(255, 203, 213, 0));
  opacity: 0.7;
}

.home-card-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1.5rem;
}

.home-card {
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 18.25rem;
  padding: 1.9rem;
  border-radius: 2rem;
  background: rgba(25, 28, 34, 0.94);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.04), 0 30px 60px rgba(0, 0, 0, 0.18);
  color: #fff;
  text-decoration: none;
  transition: transform 0.24s ease, background-color 0.24s ease, box-shadow 0.24s ease;
}

.home-card:hover {
  transform: translateY(-4px);
  background: rgba(31, 35, 43, 0.98);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.05), 0 34px 68px rgba(0, 0, 0, 0.24);
}

.home-card::after {
  content: '';
  position: absolute;
  inset: auto -20% -28% 38%;
  height: 52%;
  background: radial-gradient(circle, rgba(102, 234, 255, 0.08), transparent 68%);
  pointer-events: none;
}

.home-card-icon {
  display: grid;
  place-items: center;
  width: 4rem;
  height: 4rem;
  border-radius: 1.35rem;
  background: rgba(39, 42, 49, 0.96);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.04);
}

.home-card-copy {
  margin-top: auto;
}

.home-card-title {
  margin: 0;
  font-size: 2.05rem;
  line-height: 1.05;
}

.home-card-description {
  margin: 0.95rem 0 0;
  font-size: 1rem;
  line-height: 1.6;
  color: rgba(255, 255, 255, 0.62);
}

.tone-pink .home-status-value {
  color: var(--primary);
  text-shadow: 0 0 18px rgba(255, 203, 213, 0.16);
}

.tone-cyan .home-status-value {
  color: var(--tertiary);
  text-shadow: 0 0 18px rgba(102, 234, 255, 0.18);
}

.tone-silver .home-status-value {
  color: #f2f4f7;
}

.tone-pink .home-spotlight-icon,
.tone-pink .home-card-icon {
  color: var(--primary);
  background: rgba(255, 203, 213, 0.12);
}

.tone-cyan .home-spotlight-icon,
.tone-cyan .home-card-icon {
  color: var(--tertiary);
  background: rgba(102, 234, 255, 0.12);
}

.tone-silver .home-spotlight-icon,
.tone-silver .home-card-icon {
  color: var(--secondary);
  background: rgba(198, 198, 198, 0.1);
}

.tone-pink.home-card::after {
  background: radial-gradient(circle, rgba(255, 203, 213, 0.12), transparent 68%);
}

.tone-cyan.home-card::after {
  background: radial-gradient(circle, rgba(102, 234, 255, 0.12), transparent 68%);
}

.admin-grid .home-card {
  min-height: 16.5rem;
}

@media (max-width: 1460px) {
  .home-card-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 1180px) {
  .home-hero {
    grid-template-columns: 1fr;
  }

  .home-status-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .home-card-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .home-dashboard {
    gap: 3rem;
    padding-bottom: 2rem;
  }

  .home-hero-copy {
    padding-top: 1.5rem;
  }

  .home-hero-text {
    font-size: 1.08rem;
  }

  .home-section-title {
    font-size: 2.2rem;
  }

  .home-status-grid,
  .home-card-grid {
    grid-template-columns: 1fr;
  }

  .home-card {
    min-height: 15rem;
  }
}
</style>
