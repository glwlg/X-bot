<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChevronLeft, Users } from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()

const queryValue = (key: string) => {
    const raw = route.query[key]
    if (Array.isArray(raw)) return raw[0] ?? ''
    return raw ?? ''
}

const rangeLabel = computed(() => queryValue('label') || '当前范围')
</script>

<template>
  <div class="h-screen flex flex-col bg-slate-50 dark:bg-slate-900 absolute inset-0 z-50">
    <header class="bg-white dark:bg-slate-800 shadow-sm relative z-10 safe-top">
      <div class="flex items-center justify-between h-14 px-4">
        <button @click="router.back()" class="p-2 -ml-2 text-slate-600 dark:text-slate-300">
          <ChevronLeft class="w-6 h-6" />
        </button>
        <h1 class="text-lg font-bold text-slate-800 dark:text-white">多人统计详情</h1>
        <div class="w-8"></div>
      </div>
    </header>

    <main class="flex-1 overflow-y-auto p-4 safe-bottom">
      <div class="rounded-2xl bg-white dark:bg-slate-800 border border-slate-100 dark:border-slate-700 shadow-sm p-6 text-center">
        <div class="mx-auto w-14 h-14 rounded-full bg-indigo-100 dark:bg-indigo-900/40 flex items-center justify-center">
          <Users class="w-7 h-7 text-indigo-600" />
        </div>
        <h2 class="text-base font-semibold text-theme-primary mt-4">多人协作统计</h2>
        <p class="text-sm text-theme-muted mt-1">范围：{{ rangeLabel }}</p>
        <p class="text-sm text-theme-muted mt-3">
          当前账本为个人模式，暂无成员维度数据。
          后续接入共享账本后，这里将展示成员分摊和贡献明细。
        </p>
      </div>
    </main>
  </div>
</template>
