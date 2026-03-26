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
        tone: 'linear-gradient(90deg, rgba(244, 184, 205, 0.26) 0%, rgba(255, 255, 255, 0.95) 54%, rgba(214, 220, 229, 0.28) 100%)',
        iconClass: 'border-slate-200/90 bg-[linear-gradient(180deg,_rgba(255,255,255,0.96),_rgba(239,243,248,0.92))] text-rose-500 shadow-[0_12px_30px_rgba(210,198,209,0.16)]',
        badgeClass: 'border-rose-100 bg-rose-50/90 text-rose-500',
    },
    {
        title: '绑定',
        label: '渠道',
        to: '/bindings',
        icon: Link2,
        tone: 'linear-gradient(90deg, rgba(168, 211, 159, 0.24) 0%, rgba(255, 255, 255, 0.95) 52%, rgba(216, 222, 231, 0.22) 100%)',
        iconClass: 'border-slate-200/90 bg-[linear-gradient(180deg,_rgba(255,255,255,0.96),_rgba(239,243,248,0.92))] text-emerald-500 shadow-[0_12px_30px_rgba(198,209,198,0.16)]',
        badgeClass: 'border-emerald-100 bg-emerald-50/90 text-emerald-600',
    },
    {
        title: 'RSS',
        label: '订阅',
        to: '/modules/rss',
        icon: Radio,
        tone: 'linear-gradient(90deg, rgba(244, 184, 205, 0.18) 0%, rgba(255, 255, 255, 0.96) 52%, rgba(170, 215, 163, 0.18) 100%)',
        iconClass: 'border-slate-200/90 bg-[linear-gradient(180deg,_rgba(255,255,255,0.96),_rgba(239,243,248,0.92))] text-rose-400 shadow-[0_12px_30px_rgba(214,206,201,0.16)]',
        badgeClass: 'border-rose-100 bg-rose-50/90 text-rose-400',
    },
    {
        title: '调度',
        label: '任务',
        to: '/modules/scheduler',
        icon: Cable,
        tone: 'linear-gradient(90deg, rgba(236, 165, 188, 0.22) 0%, rgba(255, 255, 255, 0.95) 54%, rgba(214, 220, 229, 0.24) 100%)',
        iconClass: 'border-slate-200/90 bg-[linear-gradient(180deg,_rgba(255,255,255,0.96),_rgba(239,243,248,0.92))] text-rose-500 shadow-[0_12px_30px_rgba(210,199,200,0.16)]',
        badgeClass: 'border-rose-100 bg-rose-50/90 text-rose-500',
    },
    {
        title: '心跳',
        label: '监控',
        to: '/modules/monitor',
        icon: HeartPulse,
        tone: 'linear-gradient(90deg, rgba(240, 151, 181, 0.22) 0%, rgba(255, 249, 252, 0.96) 50%, rgba(214, 220, 229, 0.24) 100%)',
        iconClass: 'border-slate-200/90 bg-[linear-gradient(180deg,_rgba(255,255,255,0.96),_rgba(239,243,248,0.92))] text-pink-500 shadow-[0_12px_30px_rgba(212,198,210,0.16)]',
        badgeClass: 'border-pink-100 bg-pink-50/90 text-pink-500',
    },
    {
        title: '自选股',
        label: '行情',
        to: '/modules/watchlist',
        icon: TrendingUp,
        tone: 'linear-gradient(90deg, rgba(176, 220, 167, 0.22) 0%, rgba(255, 255, 255, 0.96) 50%, rgba(244, 184, 205, 0.16) 100%)',
        iconClass: 'border-slate-200/90 bg-[linear-gradient(180deg,_rgba(255,255,255,0.96),_rgba(239,243,248,0.92))] text-rose-500 shadow-[0_12px_30px_rgba(200,209,198,0.16)]',
        badgeClass: 'border-rose-100 bg-rose-50/90 text-rose-500',
    },
    {
        title: '记账',
        label: '资产',
        to: '/accounting',
        icon: WalletCards,
        tone: 'linear-gradient(90deg, rgba(223, 228, 236, 0.30) 0%, rgba(255, 255, 255, 0.96) 52%, rgba(244, 184, 205, 0.18) 100%)',
        iconClass: 'border-slate-200/90 bg-[linear-gradient(180deg,_rgba(255,255,255,0.96),_rgba(239,243,248,0.92))] text-slate-500 shadow-[0_12px_30px_rgba(210,203,211,0.16)]',
        badgeClass: 'border-slate-200 bg-slate-50/90 text-slate-500',
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
            tone: 'linear-gradient(90deg, rgba(168, 211, 159, 0.24) 0%, rgba(255, 255, 255, 0.95) 52%, rgba(214, 220, 229, 0.22) 100%)',
            iconClass: 'border-slate-200/90 bg-[linear-gradient(180deg,_rgba(255,255,255,0.96),_rgba(239,243,248,0.92))] text-emerald-500 shadow-[0_12px_30px_rgba(198,209,198,0.16)]',
            badgeClass: 'border-emerald-100 bg-emerald-50/90 text-emerald-600',
        })
        cards.push({
            title: '诊断',
            label: '状态',
            to: '/admin/diagnostics',
            icon: Gauge,
            tone: 'linear-gradient(90deg, rgba(222, 228, 236, 0.30) 0%, rgba(255, 255, 255, 0.96) 50%, rgba(176, 220, 167, 0.18) 100%)',
            iconClass: 'border-slate-200/90 bg-[linear-gradient(180deg,_rgba(255,255,255,0.96),_rgba(239,243,248,0.92))] text-lime-600 shadow-[0_12px_30px_rgba(204,213,199,0.16)]',
            badgeClass: 'border-lime-100 bg-lime-50/90 text-lime-600',
        })
    }

    if (authStore.isAdmin) {
        cards.unshift({
            title: '初始化',
            label: 'Setup',
            to: '/admin/setup',
            icon: Rocket,
            tone: 'linear-gradient(90deg, rgba(244, 184, 205, 0.24) 0%, rgba(255, 255, 255, 0.95) 52%, rgba(214, 220, 229, 0.24) 100%)',
            iconClass: 'border-slate-200/90 bg-[linear-gradient(180deg,_rgba(255,255,255,0.96),_rgba(239,243,248,0.92))] text-rose-500 shadow-[0_12px_30px_rgba(208,198,205,0.16)]',
            badgeClass: 'border-rose-100 bg-rose-50/90 text-rose-500',
        })
        cards.push({
            title: '配置',
            label: '模型',
            to: '/admin/runtime',
            icon: Settings2,
            tone: 'linear-gradient(90deg, rgba(222, 228, 236, 0.30) 0%, rgba(255, 255, 255, 0.95) 52%, rgba(244, 184, 205, 0.18) 100%)',
            iconClass: 'border-slate-200/90 bg-[linear-gradient(180deg,_rgba(255,255,255,0.96),_rgba(239,243,248,0.92))] text-rose-500 shadow-[0_12px_30px_rgba(208,199,208,0.16)]',
            badgeClass: 'border-rose-100 bg-rose-50/90 text-rose-500',
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
      <div class="rounded-[32px] border border-slate-200/80 bg-[radial-gradient(circle_at_14%_18%,_rgba(244,184,205,0.26),_transparent_30%),radial-gradient(circle_at_86%_80%,_rgba(133,181,128,0.10),_transparent_24%),linear-gradient(135deg,_rgba(255,255,255,0.96),_rgba(236,240,246,0.90)_48%,_rgba(251,243,247,0.95)_100%)] p-6 shadow-[inset_0_1px_0_rgba(255,255,255,0.82),0_18px_40px_rgba(205,193,205,0.12)] md:p-8">
        <div class="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div class="text-xs uppercase tracking-[0.28em] text-rose-400/80">Workspace</div>
            <h2 class="mt-3 text-4xl font-semibold tracking-tight text-stone-950">Ikaros</h2>
          </div>
          <div class="inline-flex items-center rounded-full border border-emerald-100 bg-[linear-gradient(180deg,_rgba(255,255,255,0.92),_rgba(240,246,241,0.88))] px-4 py-2 text-sm font-medium text-emerald-700 shadow-sm">
            Web Channel
          </div>
        </div>

        <div class="mt-8 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <div
            v-for="card in statusCards"
            :key="card.label"
            class="rounded-[24px] border border-slate-200/80 bg-[linear-gradient(180deg,_rgba(255,255,255,0.86),_rgba(245,247,250,0.82))] p-4 shadow-[0_16px_36px_rgba(201,194,202,0.10)] backdrop-blur"
          >
            <div class="text-xs uppercase tracking-[0.24em] text-rose-400/75">{{ card.label }}</div>
            <div class="mt-3 text-2xl font-semibold text-stone-950">{{ card.value }}</div>
          </div>
        </div>
      </div>

      <div class="rounded-[32px] border border-white/45 bg-[linear-gradient(180deg,_rgba(86,78,92,0.98),_rgba(120,114,127,0.92))] p-6 text-slate-50 shadow-[0_24px_70px_rgba(96,87,101,0.24)]">
        <div class="flex items-center gap-2 text-xs uppercase tracking-[0.24em] text-slate-200/55">
          <ShieldCheck class="h-4 w-4 text-emerald-300" />
          Focus
        </div>
        <div class="mt-5 grid gap-3">
          <RouterLink
            v-for="card in focusCards"
            :key="card.to"
            :to="card.to"
            class="flex items-center justify-between rounded-2xl border border-white/18 bg-[linear-gradient(90deg,_rgba(255,255,255,0.08),_rgba(226,230,236,0.04))] px-4 py-3 text-sm font-medium text-slate-50 transition hover:bg-white/12"
          >
            <span>{{ card.title }}</span>
            <component :is="card.icon" class="h-4 w-4 text-emerald-200" />
          </RouterLink>
        </div>
      </div>
    </section>

    <section class="space-y-3">
      <div class="text-xs uppercase tracking-[0.24em] text-rose-400/80">Workspace</div>
      <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <RouterLink
          v-for="card in workspaceCards"
          :key="card.to"
          :to="card.to"
          class="group overflow-hidden rounded-[28px] border border-slate-200/80 bg-[linear-gradient(180deg,_rgba(255,255,255,0.96),_rgba(248,246,248,0.98))] p-6 shadow-[0_18px_42px_rgba(207,198,206,0.10)] transition hover:-translate-y-0.5 hover:border-rose-200 hover:shadow-[0_24px_60px_rgba(189,178,188,0.18)]"
        >
          <div class="relative">
            <div class="absolute inset-x-0 top-0 h-24 rounded-3xl" :style="{ background: card.tone }" />
            <div class="relative">
              <div
                class="inline-flex h-12 w-12 items-center justify-center rounded-2xl border shadow-sm"
                :class="card.iconClass"
              >
                <component :is="card.icon" class="h-5 w-5" />
              </div>
              <div class="mt-5 flex items-end justify-between gap-3">
                <h3 class="text-2xl font-semibold text-slate-950">{{ card.title }}</h3>
                <span
                  class="rounded-full border px-3 py-1 text-xs uppercase tracking-[0.2em]"
                  :class="card.badgeClass"
                >
                  {{ card.label }}
                </span>
              </div>
            </div>
          </div>
        </RouterLink>
      </div>
    </section>

    <section v-if="adminCards.length" class="space-y-3">
      <div class="text-xs uppercase tracking-[0.24em] text-rose-400/80">Admin</div>
      <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <RouterLink
          v-for="card in adminCards"
          :key="card.to"
          :to="card.to"
          class="group overflow-hidden rounded-[28px] border border-slate-200/80 bg-[linear-gradient(180deg,_rgba(255,255,255,0.96),_rgba(248,246,248,0.98))] p-6 shadow-[0_18px_42px_rgba(207,198,206,0.10)] transition hover:-translate-y-0.5 hover:border-rose-200 hover:shadow-[0_24px_60px_rgba(189,178,188,0.18)]"
        >
          <div class="relative">
            <div class="absolute inset-x-0 top-0 h-24 rounded-3xl" :style="{ background: card.tone }" />
            <div class="relative">
              <div
                class="inline-flex h-12 w-12 items-center justify-center rounded-2xl border shadow-sm"
                :class="card.iconClass"
              >
                <component :is="card.icon" class="h-5 w-5" />
              </div>
              <div class="mt-5 flex items-end justify-between gap-3">
                <h3 class="text-2xl font-semibold text-slate-950">{{ card.title }}</h3>
                <span
                  class="rounded-full border px-3 py-1 text-xs uppercase tracking-[0.2em]"
                  :class="card.badgeClass"
                >
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
