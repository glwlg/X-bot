<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import {
    Activity,
    Bot,
    Cable,
    Gauge,
    HeartPulse,
    Home,
    LayoutGrid,
    Link2,
    LogOut,
    Radio,
    Rocket,
    Settings2,
    ShieldUser,
    WalletCards
} from 'lucide-vue-next'

import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const authStore = useAuthStore()

const isHomeRoute = computed(() =>
    route.path === '/home' || route.path.startsWith('/home/')
)

const identityPrimary = computed(() =>
    authStore.user?.display_name || authStore.user?.username || authStore.user?.email || '未登录'
)

const showIdentityEmail = computed(() =>
    Boolean(authStore.user?.email) && authStore.user?.email !== identityPrimary.value
)

const handleLogout = async () => {
    await authStore.logout()
    window.location.href = '/login'
}

const primaryNav = computed(() => [
    { label: '总览', to: '/home', icon: Home },
    { label: 'Chat', to: '/chat', icon: Bot },
    { label: '绑定', to: '/bindings', icon: Link2 },
    { label: 'RSS', to: '/modules/rss', icon: Radio },
    { label: '调度', to: '/modules/scheduler', icon: Cable },
    { label: '心跳', to: '/modules/monitor', icon: HeartPulse },
    { label: '自选股', to: '/modules/watchlist', icon: Activity },
    { label: '记账', to: '/accounting', icon: WalletCards },
])

const adminNav = computed(() => {
    const items = []
    if (authStore.isOperator) {
        items.push({ label: '用户', to: '/admin/users', icon: ShieldUser })
        items.push({ label: '诊断', to: '/admin/diagnostics', icon: Gauge })
    }
    if (authStore.isAdmin) {
        items.unshift({ label: '初始化', to: '/admin/setup', icon: Rocket })
        items.push({ label: '运行配置', to: '/admin/runtime', icon: Settings2 })
    }
    return items
})

const currentTitle = computed(() => String(route.meta.title || 'Ikaros'))

const shellTone = computed(() => {
    if (isHomeRoute.value) {
        return {
            root: 'bg-[radial-gradient(circle_at_top,_rgba(14,165,233,0.20),_transparent_28%),linear-gradient(180deg,_#07111f_0%,_#0f172a_38%,_#f8fafc_100%)]',
            aside: 'border-white/10 bg-slate-950/70 shadow-[0_24px_80px_rgba(2,8,23,0.45)]',
            brandIcon: 'bg-cyan-400/20 text-cyan-200',
            subtitle: 'text-slate-400',
            sectionLabel: 'text-[11px] uppercase tracking-[0.24em] text-slate-500',
            identityCard: 'border-white/10 bg-white/5',
            identityEmail: 'text-slate-300',
            identityRole: 'text-slate-400',
            logoutButton: 'border-white/10 bg-white/5 text-slate-200 hover:bg-white/10',
            contentShell: 'border-white/70 bg-[linear-gradient(180deg,_rgba(255,255,255,0.92),_rgba(246,245,247,0.94)_62%,_rgba(255,252,251,0.96))] shadow-[0_28px_80px_rgba(153,142,152,0.18)]',
            header: 'border-white/60 bg-[linear-gradient(180deg,_rgba(255,255,255,0.86),_rgba(245,242,246,0.76))]',
            eyebrow: 'text-rose-400/80',
            title: 'text-stone-950',
        }
    }

    return {
        root: 'bg-[radial-gradient(circle_at_top,_rgba(14,165,233,0.20),_transparent_28%),linear-gradient(180deg,_#07111f_0%,_#0f172a_38%,_#f8fafc_100%)]',
        aside: 'border-white/10 bg-slate-950/70 shadow-[0_24px_80px_rgba(2,8,23,0.45)]',
        brandIcon: 'bg-cyan-400/20 text-cyan-200',
        subtitle: 'text-slate-400',
        sectionLabel: 'text-[11px] uppercase tracking-[0.24em] text-slate-500',
        identityCard: 'border-white/10 bg-white/5',
        identityEmail: 'text-slate-300',
        identityRole: 'text-slate-400',
        logoutButton: 'border-white/10 bg-white/5 text-slate-200 hover:bg-white/10',
        contentShell: 'border-slate-200/70 bg-white/92 shadow-[0_24px_80px_rgba(15,23,42,0.18)]',
        header: 'border-slate-200/70',
        eyebrow: 'text-slate-400',
        title: 'text-slate-900',
    }
})

const isNavActive = (to: string) =>
    route.path === to || route.path.startsWith(`${to}/`)

const primaryNavClass = (to: string) => {
    if (isHomeRoute.value) {
        return isNavActive(to)
            ? 'bg-cyan-400/15 text-white shadow-[inset_0_0_0_1px_rgba(34,211,238,0.28)]'
            : 'text-slate-300 hover:bg-white/5 hover:text-white'
    }

    return isNavActive(to)
        ? 'bg-cyan-400/15 text-white shadow-[inset_0_0_0_1px_rgba(34,211,238,0.28)]'
        : 'text-slate-300 hover:bg-white/5 hover:text-white'
}

const adminNavClass = (to: string) => {
    if (isHomeRoute.value) {
        return route.path === to
            ? 'bg-amber-300/15 text-white shadow-[inset_0_0_0_1px_rgba(252,211,77,0.25)]'
            : 'text-slate-300 hover:bg-white/5 hover:text-white'
    }

    return route.path === to
        ? 'bg-amber-300/15 text-white shadow-[inset_0_0_0_1px_rgba(252,211,77,0.25)]'
        : 'text-slate-300 hover:bg-white/5 hover:text-white'
}
</script>

<template>
  <div class="min-h-screen md:h-screen md:overflow-hidden" :class="shellTone.root">
    <div class="mx-auto flex min-h-screen w-full max-w-[1680px] flex-col gap-4 p-3 md:h-full md:min-h-0 md:flex-row md:p-5">
      <aside
        class="flex w-full flex-col rounded-[28px] border p-4 text-slate-100 backdrop-blur md:sticky md:top-5 md:h-[calc(100vh-2.5rem)] md:w-[300px] md:overflow-y-auto"
        :class="shellTone.aside"
      >
        <div class="flex items-center gap-3 border-b border-white/10 pb-4">
          <div class="flex h-12 w-12 items-center justify-center rounded-2xl" :class="shellTone.brandIcon">
            <LayoutGrid class="h-6 w-6" />
          </div>
          <div>
            <div class="text-lg font-semibold tracking-wide">Ikaros</div>
            <div class="text-xs" :class="shellTone.subtitle">Web Channel Console</div>
          </div>
        </div>

        <div class="mt-6 space-y-6">
          <div class="space-y-2">
            <div class="mb-2" :class="shellTone.sectionLabel">Workspace</div>
            <RouterLink
              v-for="item in primaryNav"
              :key="item.to"
              :to="item.to"
              class="group flex items-center gap-3 rounded-2xl px-3 py-3 transition"
              :class="primaryNavClass(item.to)"
            >
              <component :is="item.icon" class="h-4 w-4" />
              <span class="text-sm font-medium">{{ item.label }}</span>
            </RouterLink>
          </div>

          <div v-if="adminNav.length" class="space-y-2">
            <div class="mb-2" :class="shellTone.sectionLabel">Admin</div>
            <RouterLink
              v-for="item in adminNav"
              :key="item.to"
              :to="item.to"
              class="group flex items-center gap-3 rounded-2xl px-3 py-3 transition"
              :class="adminNavClass(item.to)"
            >
              <component :is="item.icon" class="h-4 w-4" />
              <span class="text-sm font-medium">{{ item.label }}</span>
            </RouterLink>
          </div>
        </div>

        <div class="mt-8 rounded-3xl border p-4 md:mt-auto" :class="shellTone.identityCard">
          <div class="text-xs uppercase tracking-[0.24em]" :class="shellTone.sectionLabel">Identity</div>
          <div class="mt-3 text-base font-semibold text-white">{{ identityPrimary }}</div>
          <div v-if="showIdentityEmail" class="mt-1 break-all text-sm" :class="shellTone.identityEmail">{{ authStore.user?.email }}</div>
          <div class="mt-2 text-sm" :class="shellTone.identityRole">{{ authStore.user?.role || 'viewer' }}</div>
          <button
            type="button"
            class="mt-4 inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm transition"
            :class="shellTone.logoutButton"
            @click="handleLogout"
          >
            <LogOut class="h-4 w-4" />
            退出
          </button>
        </div>
      </aside>

      <div
        class="flex min-h-[calc(100vh-2.5rem)] flex-1 flex-col overflow-hidden rounded-[32px] border md:h-[calc(100vh-2.5rem)] md:min-h-0"
        :class="shellTone.contentShell"
      >
        <header class="border-b px-6 py-5" :class="shellTone.header">
          <div>
            <div class="text-xs uppercase tracking-[0.28em]" :class="shellTone.eyebrow">Command Center</div>
            <h1 class="mt-1 text-2xl font-semibold" :class="shellTone.title">{{ currentTitle }}</h1>
          </div>
        </header>
        <main class="min-h-0 flex-1 overflow-x-hidden overflow-y-auto [scrollbar-gutter:stable]">
          <RouterView />
        </main>
      </div>
    </div>
  </div>
</template>
