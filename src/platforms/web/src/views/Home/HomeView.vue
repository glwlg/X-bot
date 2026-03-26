<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import {
    Bot,
    Cable,
    Gauge,
    HeartPulse,
    Link2,
    Radio,
    Rocket,
    Settings2,
    ShieldCheck,
    TrendingUp,
    WalletCards,
} from 'lucide-vue-next'

import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()

const workspaceCards = computed(() => [
    {
        title: 'Chat',
        label: '对话',
        to: '/chat',
        icon: Bot,
        tone: 'from-cyan-500/18 via-sky-500/10 to-transparent',
    },
    {
        title: '绑定',
        label: '渠道',
        to: '/bindings',
        icon: Link2,
        tone: 'from-emerald-500/18 via-teal-400/10 to-transparent',
    },
    {
        title: 'RSS',
        label: '订阅',
        to: '/modules/rss',
        icon: Radio,
        tone: 'from-orange-500/18 via-amber-400/10 to-transparent',
    },
    {
        title: '调度',
        label: '任务',
        to: '/modules/scheduler',
        icon: Cable,
        tone: 'from-blue-500/18 via-indigo-500/10 to-transparent',
    },
    {
        title: '心跳',
        label: '监控',
        to: '/modules/monitor',
        icon: HeartPulse,
        tone: 'from-violet-500/18 via-fuchsia-400/10 to-transparent',
    },
    {
        title: '自选股',
        label: '行情',
        to: '/modules/watchlist',
        icon: TrendingUp,
        tone: 'from-rose-500/18 via-pink-400/10 to-transparent',
    },
    {
        title: '记账',
        label: '资产',
        to: '/accounting',
        icon: WalletCards,
        tone: 'from-amber-500/20 via-orange-400/10 to-transparent',
    },
])

const adminCards = computed(() => {
    const cards = []

    if (authStore.isOperator) {
        cards.push({
            title: '用户',
            label: '账号',
            to: '/admin/users',
            icon: ShieldCheck,
            tone: 'from-emerald-500/18 via-teal-500/10 to-transparent',
        })
        cards.push({
            title: '诊断',
            label: '状态',
            to: '/admin/diagnostics',
            icon: Gauge,
            tone: 'from-lime-500/18 via-emerald-500/10 to-transparent',
        })
    }

    if (authStore.isAdmin) {
        cards.unshift({
            title: '初始化',
            label: 'Setup',
            to: '/admin/setup',
            icon: Rocket,
            tone: 'from-sky-500/18 via-cyan-400/10 to-transparent',
        })
        cards.push({
            title: '配置',
            label: '模型',
            to: '/admin/runtime',
            icon: Settings2,
            tone: 'from-fuchsia-500/18 via-pink-400/10 to-transparent',
        })
    }

    return cards
})

const focusCards = computed(() => {
    const cards = [
        { title: 'Chat', to: '/chat', icon: Bot },
        { title: '绑定', to: '/bindings', icon: Link2 },
        { title: '记账', to: '/accounting', icon: WalletCards },
    ]

    if (authStore.isOperator) {
        cards.push({ title: '诊断', to: '/admin/diagnostics', icon: Gauge })
    }

    if (authStore.isAdmin) {
        cards.unshift({ title: '初始化', to: '/admin/setup', icon: Rocket })
        cards.push({ title: '配置', to: '/admin/runtime', icon: Settings2 })
    }

    return cards
})

const statusCards = computed(() => [
    { label: 'Channel', value: 'Web' },
    { label: 'Role', value: authStore.user?.role || 'viewer' },
    { label: 'Modules', value: String(workspaceCards.value.length) },
    { label: 'Security', value: 'RBAC' },
])
</script>

<template>
  <div class="space-y-6 p-6 md:p-8">
    <section class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
      <div class="rounded-[32px] border border-slate-200 bg-[linear-gradient(135deg,_rgba(8,145,178,0.12),_rgba(255,255,255,0.94)_40%,_rgba(15,23,42,0.03)_100%)] p-6 md:p-8">
        <div class="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div class="text-xs uppercase tracking-[0.28em] text-slate-400">Workspace</div>
            <h2 class="mt-3 text-4xl font-semibold tracking-tight text-slate-950">Ikaros</h2>
          </div>
          <div class="inline-flex items-center rounded-full border border-cyan-200 bg-white/85 px-4 py-2 text-sm font-medium text-cyan-700 shadow-sm">
            Web Channel
          </div>
        </div>

        <div class="mt-8 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <div
            v-for="card in statusCards"
            :key="card.label"
            class="rounded-[24px] border border-white/70 bg-white/85 p-4 shadow-sm"
          >
            <div class="text-xs uppercase tracking-[0.24em] text-slate-400">{{ card.label }}</div>
            <div class="mt-3 text-2xl font-semibold text-slate-950">{{ card.value }}</div>
          </div>
        </div>
      </div>

      <div class="rounded-[32px] border border-slate-200 bg-slate-950 p-6 text-slate-100 shadow-sm">
        <div class="flex items-center gap-2 text-xs uppercase tracking-[0.24em] text-slate-500">
          <ShieldCheck class="h-4 w-4 text-emerald-400" />
          Focus
        </div>
        <div class="mt-5 grid gap-3">
          <RouterLink
            v-for="card in focusCards"
            :key="card.to"
            :to="card.to"
            class="flex items-center justify-between rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-sm font-medium text-slate-100 transition hover:bg-white/10"
          >
            <span>{{ card.title }}</span>
            <component :is="card.icon" class="h-4 w-4 text-cyan-300" />
          </RouterLink>
        </div>
      </div>
    </section>

    <section class="space-y-3">
      <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Workspace</div>
      <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <RouterLink
          v-for="card in workspaceCards"
          :key="card.to"
          :to="card.to"
          class="group overflow-hidden rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm transition hover:-translate-y-0.5 hover:shadow-[0_24px_60px_rgba(15,23,42,0.12)]"
        >
          <div class="relative">
            <div :class="`absolute inset-x-0 top-0 h-24 rounded-3xl bg-gradient-to-r ${card.tone}`" />
            <div class="relative">
              <div class="inline-flex h-12 w-12 items-center justify-center rounded-2xl border border-slate-200 bg-white text-slate-900 shadow-sm">
                <component :is="card.icon" class="h-5 w-5" />
              </div>
              <div class="mt-5 flex items-end justify-between gap-3">
                <h3 class="text-2xl font-semibold text-slate-950">{{ card.title }}</h3>
                <span class="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs uppercase tracking-[0.2em] text-slate-500">
                  {{ card.label }}
                </span>
              </div>
            </div>
          </div>
        </RouterLink>
      </div>
    </section>

    <section v-if="adminCards.length" class="space-y-3">
      <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Admin</div>
      <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <RouterLink
          v-for="card in adminCards"
          :key="card.to"
          :to="card.to"
          class="group overflow-hidden rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm transition hover:-translate-y-0.5 hover:shadow-[0_24px_60px_rgba(15,23,42,0.12)]"
        >
          <div class="relative">
            <div :class="`absolute inset-x-0 top-0 h-24 rounded-3xl bg-gradient-to-r ${card.tone}`" />
            <div class="relative">
              <div class="inline-flex h-12 w-12 items-center justify-center rounded-2xl border border-slate-200 bg-white text-slate-900 shadow-sm">
                <component :is="card.icon" class="h-5 w-5" />
              </div>
              <div class="mt-5 flex items-end justify-between gap-3">
                <h3 class="text-2xl font-semibold text-slate-950">{{ card.title }}</h3>
                <span class="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs uppercase tracking-[0.2em] text-slate-500">
                  {{ card.label }}
                </span>
              </div>
            </div>
          </div>
        </RouterLink>
      </div>
    </section>
  </div>
</template>
