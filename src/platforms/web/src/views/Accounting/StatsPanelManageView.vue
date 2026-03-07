<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAccountingStore } from '@/stores/accounting'
import { ChevronLeft, ChevronRight, Plus, Trash2 } from 'lucide-vue-next'
import {
    appendOperationLog,
    loadStatsPanels,
    removeStatsPanel,
    setStatsPanelEnabled,
    type StatsPanelConfig,
} from '@/utils/accountingLocal'

const router = useRouter()
const store = useAccountingStore()

const loading = ref(false)
const panels = ref<StatsPanelConfig[]>([])

const presetPanels = computed(() => panels.value.filter(panel => !panel.is_custom))
const customPanels = computed(() => panels.value.filter(panel => panel.is_custom))

const refreshPanels = async () => {
    panels.value = await loadStatsPanels(store.currentBookId)
}

const togglePanel = async (panel: StatsPanelConfig) => {
    if (!store.currentBookId) return
    const nextEnabled = !panel.enabled
    panels.value = await setStatsPanelEnabled(store.currentBookId, panel.id, nextEnabled)
    appendOperationLog(store.currentBookId, nextEnabled ? '启用统计面板' : '停用统计面板', panel.name)
}

const goEditPanel = (panel: StatsPanelConfig) => {
    router.push({ name: 'StatsPanelEdit', params: { id: panel.id } })
}

const goAddPanel = () => {
    router.push({ name: 'StatsPanelEdit' })
}

const deletePanel = async (panel: StatsPanelConfig) => {
    if (!store.currentBookId || !panel.is_custom) return
    if (!confirm(`确认删除统计模板「${panel.name}」吗？`)) return
    panels.value = await removeStatsPanel(store.currentBookId, panel.id)
    appendOperationLog(store.currentBookId, '删除自定义统计', panel.name)
}

onMounted(async () => {
    if (!store.currentBookId) {
        loading.value = true
        try {
            await store.fetchBooks()
        } finally {
            loading.value = false
        }
    }
    await refreshPanels()
})
</script>

<template>
  <div class="h-screen flex flex-col bg-slate-100 dark:bg-slate-900 absolute inset-0 z-50">
    <header class="bg-indigo-500 dark:bg-indigo-700 text-white shadow-sm relative z-10 safe-top">
      <div class="flex items-center justify-between h-14 px-4">
        <button @click="router.back()" class="p-2 -ml-2 text-white/90">
          <ChevronLeft class="w-6 h-6" />
        </button>
        <h1 class="text-2xl font-medium">统计</h1>
        <button @click="goAddPanel" class="inline-flex items-center gap-1 text-base font-medium text-white/95">
          <Plus class="w-4 h-4" />
          添加统计
        </button>
      </div>
    </header>

    <main class="flex-1 overflow-y-auto p-4 safe-bottom space-y-4">
      <div v-if="loading" class="rounded-2xl bg-white dark:bg-slate-800 border border-slate-100 dark:border-slate-700 p-6 text-sm text-theme-muted text-center">
        正在加载账本...
      </div>

      <template v-else>
        <div class="rounded-2xl bg-white dark:bg-slate-800 border border-slate-100 dark:border-slate-700 p-4">
          <h2 class="text-base font-semibold text-theme-primary mb-3">预设模板</h2>
          <div class="space-y-3">
            <div
              v-for="panel in presetPanels"
              :key="panel.id"
              class="rounded-2xl border border-slate-100 dark:border-slate-700 px-4 py-3 bg-slate-50/70 dark:bg-slate-900/70"
            >
              <div class="flex items-center justify-between gap-3">
                <button @click="goEditPanel(panel)" class="text-left flex-1">
                  <p class="text-xl text-theme-primary">{{ panel.name }}</p>
                  <p class="text-sm text-theme-muted mt-1">{{ panel.description }}</p>
                </button>

                <div class="flex items-center gap-2">
                  <button @click="goEditPanel(panel)" class="p-1 rounded hover:bg-slate-100 dark:hover:bg-slate-700">
                    <ChevronRight class="w-4 h-4 text-slate-500" />
                  </button>
                  <button
                    @click="togglePanel(panel)"
                    class="w-12 h-7 rounded-full p-0.5 transition-colors"
                    :class="panel.enabled ? 'bg-indigo-500' : 'bg-slate-300 dark:bg-slate-600'"
                  >
                    <div
                      class="h-6 w-6 rounded-full bg-white transition-transform"
                      :class="panel.enabled ? 'translate-x-5' : 'translate-x-0'"
                    ></div>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="rounded-2xl bg-white dark:bg-slate-800 border border-slate-100 dark:border-slate-700 p-4">
          <h2 class="text-base font-semibold text-theme-primary mb-3">自定义统计</h2>

          <div v-if="customPanels.length === 0" class="rounded-xl border border-dashed border-slate-300 dark:border-slate-600 p-5 text-sm text-theme-muted text-center">
            暂无自定义统计，点击右上角“添加统计”创建。
          </div>

          <div v-else class="space-y-3">
            <div
              v-for="panel in customPanels"
              :key="panel.id"
              class="rounded-2xl border border-slate-100 dark:border-slate-700 px-4 py-3 bg-slate-50/70 dark:bg-slate-900/70"
            >
              <div class="flex items-center justify-between gap-3">
                <button @click="goEditPanel(panel)" class="text-left flex-1">
                  <p class="text-xl text-theme-primary">{{ panel.name }}</p>
                  <p class="text-sm text-theme-muted mt-1">{{ panel.description || '自定义统计模板' }}</p>
                </button>

                <div class="flex items-center gap-2">
                  <button @click="deletePanel(panel)" class="p-1 rounded hover:bg-rose-100 dark:hover:bg-rose-900/20">
                    <Trash2 class="w-4 h-4 text-rose-500" />
                  </button>
                  <button @click="goEditPanel(panel)" class="p-1 rounded hover:bg-slate-100 dark:hover:bg-slate-700">
                    <ChevronRight class="w-4 h-4 text-slate-500" />
                  </button>
                  <button
                    @click="togglePanel(panel)"
                    class="w-12 h-7 rounded-full p-0.5 transition-colors"
                    :class="panel.enabled ? 'bg-indigo-500' : 'bg-slate-300 dark:bg-slate-600'"
                  >
                    <div
                      class="h-6 w-6 rounded-full bg-white transition-transform"
                      :class="panel.enabled ? 'translate-x-5' : 'translate-x-0'"
                    ></div>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>
    </main>
  </div>
</template>
