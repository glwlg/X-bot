<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, RouterView, RouterLink } from 'vue-router'
import { Home, Wallet, BarChart3, Grid2x2, UserCircle, ArrowLeft } from 'lucide-vue-next'

const route = useRoute()

const tabs = [
    { path: '/accounting/overview', label: '首页', icon: Home },
    { path: '/accounting/assets', label: '资产', icon: Wallet },
    { path: '/accounting/stats', label: '统计', icon: BarChart3 },
    { path: '/accounting/more', label: '更多', icon: Grid2x2 },
    { path: '/accounting/profile', label: '我的', icon: UserCircle },
]

const isActiveTab = (path: string) => route.path === path

const isPWA = typeof window !== 'undefined' && 
    (window.matchMedia('(display-mode: standalone)').matches || ('standalone' in navigator && (navigator as any).standalone))

// Hide layout chrome on sub-pages with own headers
const isSubPage = computed(() => [
    'BalanceTrendDetail',
    'AccountDetail',
    'RecordList',
    'RecordDetail',
    'StatsCategoryDetail',
    'StatsTrendDetail',
    'StatsTeamDetail',
    'StatsPanelManage',
    'StatsPanelEdit',
    'BudgetList',
    'DebtList',
    'ScheduledTaskList',
    'ProfileManage',
    'ProfileSettings',
].includes(route.name as string))
</script>

<template>
  <div class="flex flex-col h-full bg-gradient-to-b from-indigo-50/50 to-white dark:from-slate-900 dark:to-slate-950">
    <!-- Top bar with back button (hidden on sub-pages) -->
    <div
      v-if="!isSubPage"
      class="sticky top-0 z-20 bg-gradient-to-r from-indigo-500 to-indigo-400 dark:from-indigo-700 dark:to-indigo-600 px-4 py-3 flex items-center gap-3 shadow-sm"
    >
      <RouterLink
        v-if="!isPWA"
        to="/home"
        class="flex items-center gap-1.5 text-white/90 hover:text-white transition text-sm font-medium"
      >
        <ArrowLeft class="w-4 h-4" />
        <span>返回</span>
      </RouterLink>
      <span class="text-white font-semibold">智能记账</span>
    </div>

    <!-- Page content -->
    <div class="flex-1 overflow-auto">
      <RouterView />
    </div>

    <!-- Bottom Tab Bar (hidden on sub-pages) -->
    <nav
      v-if="!isSubPage"
      class="sticky bottom-0 z-20 flex border-t border-gray-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-[0_-2px_10px_rgba(0,0,0,0.05)]"
    >
      <RouterLink
        v-for="tab in tabs"
        :key="tab.path"
        :to="tab.path"
        class="flex-1 flex flex-col items-center gap-0.5 py-2 transition-colors"
        :class="isActiveTab(tab.path)
          ? 'text-indigo-500 dark:text-indigo-400'
          : 'text-gray-400 dark:text-slate-500 hover:text-gray-600 dark:hover:text-slate-400'"
      >
        <component :is="tab.icon" class="w-5 h-5" />
        <span class="text-[10px] font-medium">{{ tab.label }}</span>
        <div
          v-if="isActiveTab(tab.path)"
          class="absolute bottom-0 w-8 h-0.5 rounded-full bg-indigo-500 dark:bg-indigo-400"
        />
      </RouterLink>
    </nav>
  </div>
</template>
