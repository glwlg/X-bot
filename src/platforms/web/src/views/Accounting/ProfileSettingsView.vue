<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAccountingStore } from '@/stores/accounting'
import { ChevronLeft, Loader2, Trash2 } from 'lucide-vue-next'
import { createAccount, createRecord } from '@/api/accounting'
import {
    appendOperationLog,
    clearOperationLogs,
    loadExtensionSettings,
    loadGlobalSettings,
    markOperationLogRolledBack,
    loadOperationLogs,
    saveExtensionSettings,
    saveGlobalSettings,
    type ExtensionSettingsState,
    type GlobalSettingsState,
    type OperationLogEntry,
} from '@/utils/accountingLocal'

type SettingsKind = 'global' | 'extensions' | 'logs'

const route = useRoute()
const router = useRouter()
const store = useAccountingStore()

const savedHint = ref('')

const globalSettings = ref<GlobalSettingsState>(loadGlobalSettings())
const extensionSettings = ref<ExtensionSettingsState>(loadExtensionSettings())
const operationLogs = ref<OperationLogEntry[]>([])

const resolveKind = (value: unknown): SettingsKind => {
    const raw = typeof value === 'string' ? value : ''
    if (raw === 'extensions' || raw === 'logs') return raw
    return 'global'
}

const kind = computed<SettingsKind>(() => {
    const raw = Array.isArray(route.params.kind) ? route.params.kind[0] : route.params.kind
    return resolveKind(raw)
})

const titleMap: Record<SettingsKind, string> = {
    global: '全局设置',
    extensions: '扩展组件',
    logs: '操作日志',
}

const pageTitle = computed(() => titleMap[kind.value])

const mainRef = ref<HTMLElement | null>(null)
const refreshingLogs = ref(false)
const rollbackingLogId = ref('')
const pullStartY = ref<number | null>(null)
const pullDistance = ref(0)
const isPulling = ref(false)
const pullThreshold = 72

const pullHint = computed(() => {
    if (refreshingLogs.value) return '刷新中...'
    return pullDistance.value >= pullThreshold ? '松开刷新操作日志' : '下拉刷新操作日志'
})

const refreshLogs = async () => {
    operationLogs.value = await loadOperationLogs(store.currentBookId)
}

const triggerRefreshLogs = async () => {
    if (refreshingLogs.value) return
    refreshingLogs.value = true
    try {
        await new Promise(resolve => window.setTimeout(resolve, 220))
        await refreshLogs()
    } finally {
        refreshingLogs.value = false
        pullDistance.value = 0
        pullStartY.value = null
        isPulling.value = false
    }
}

const rollbackLabel = (log: OperationLogEntry) => {
    if (!log.rollback) return '不可回滚'
    return log.rollback.kind === 'record' ? '回滚交易' : '回滚账户'
}

const handleRollback = async (log: OperationLogEntry) => {
    if (!store.currentBookId || !log.rollback || log.rolled_back) return

    const targetText = log.rollback.kind === 'record' ? '这条交易' : '这个账户'
    if (!confirm(`确认回滚并恢复${targetText}吗？`)) return

    rollbackingLogId.value = log.id
    try {
        if (log.rollback.kind === 'record') {
            await createRecord(store.currentBookId, log.rollback.data)
            appendOperationLog(
                store.currentBookId,
                '回滚删除交易',
                `${log.rollback.data.type} · ¥${log.rollback.data.amount.toFixed(2)} · ${log.rollback.data.category_name || '未分类'}`,
            )
        } else {
            await createAccount(store.currentBookId, log.rollback.data)
            appendOperationLog(
                store.currentBookId,
                '回滚删除账户',
                `${log.rollback.data.name} · ${log.rollback.data.type} · ¥${log.rollback.data.balance.toFixed(2)}`,
            )
        }

        operationLogs.value = await markOperationLogRolledBack(store.currentBookId, log.id)
        showSavedHint('回滚成功')
    } catch (error) {
        console.error('rollback operation failed', error)
        alert('回滚失败，请稍后重试')
    } finally {
        rollbackingLogId.value = ''
    }
}

const handleMainTouchStart = (event: TouchEvent) => {
    if (kind.value !== 'logs' || refreshingLogs.value || !mainRef.value) return
    if (mainRef.value.scrollTop > 0) return
    pullStartY.value = event.touches[0]?.clientY ?? null
    isPulling.value = true
}

const handleMainTouchMove = (event: TouchEvent) => {
    if (kind.value !== 'logs' || !isPulling.value || pullStartY.value === null) return

    const currentY = event.touches[0]?.clientY ?? pullStartY.value
    const delta = currentY - pullStartY.value
    if (delta <= 0) {
        pullDistance.value = 0
        return
    }

    pullDistance.value = Math.min(120, delta * 0.5)
    if (pullDistance.value > 0) {
        event.preventDefault()
    }
}

const handleMainTouchEnd = () => {
    if (kind.value !== 'logs') return
    if (!isPulling.value) return

    const shouldRefresh = pullDistance.value >= pullThreshold
    if (shouldRefresh) {
        triggerRefreshLogs()
        return
    }

    pullDistance.value = 0
    pullStartY.value = null
    isPulling.value = false
}

const showSavedHint = (text: string) => {
    savedHint.value = text
    window.setTimeout(() => {
        if (savedHint.value === text) {
            savedHint.value = ''
        }
    }, 1500)
}

const handleSaveGlobal = () => {
    saveGlobalSettings(globalSettings.value)
    appendOperationLog(store.currentBookId, '保存全局设置', JSON.stringify(globalSettings.value))
    showSavedHint('全局设置已保存')
}

const handleSaveExtensions = () => {
    saveExtensionSettings(extensionSettings.value)
    appendOperationLog(store.currentBookId, '保存扩展设置', JSON.stringify(extensionSettings.value))
    showSavedHint('扩展设置已保存')
}

const handleClearLogs = async () => {
    if (!confirm('确认清空操作日志吗？')) return
    await clearOperationLogs(store.currentBookId)
    await refreshLogs()
}

watch(kind, () => {
    if (kind.value === 'logs') {
        void refreshLogs()
    }
})

onMounted(async () => {
    if (!store.currentBookId) {
        await store.fetchBooks()
    }

    if (kind.value === 'logs') {
        await refreshLogs()
    }
})
</script>

<template>
  <div class="h-screen flex flex-col bg-slate-50 dark:bg-slate-900 absolute inset-0 z-50">
    <header class="bg-white dark:bg-slate-800 shadow-sm relative z-10 safe-top">
      <div class="flex items-center justify-between h-14 px-4">
        <button @click="router.back()" class="p-2 -ml-2 text-slate-600 dark:text-slate-300">
          <ChevronLeft class="w-6 h-6" />
        </button>
        <h1 class="text-lg font-bold text-slate-800 dark:text-white">{{ pageTitle }}</h1>
        <span class="text-xs text-indigo-600 w-20 text-right">{{ savedHint }}</span>
      </div>
    </header>

    <main
      ref="mainRef"
      class="flex-1 overflow-y-auto p-4 safe-bottom"
      @touchstart="handleMainTouchStart"
      @touchmove="handleMainTouchMove"
      @touchend="handleMainTouchEnd"
      @touchcancel="handleMainTouchEnd"
    >
      <div v-if="kind === 'global'" class="space-y-4">
        <div class="bg-white dark:bg-slate-800 rounded-2xl p-4 border border-slate-100 dark:border-slate-700 shadow-sm space-y-4">
          <div>
            <label class="block text-xs text-slate-500 mb-1">货币符号</label>
            <select
              v-model="globalSettings.currency_symbol"
              class="w-full px-3 py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900"
            >
              <option value="¥">人民币 (¥)</option>
              <option value="$">美元 ($)</option>
              <option value="€">欧元 (€)</option>
            </select>
          </div>

          <div>
            <label class="block text-xs text-slate-500 mb-1">金额小数位</label>
            <input
              v-model.number="globalSettings.decimal_places"
              type="number"
              min="0"
              max="4"
              class="w-full px-3 py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900"
            />
          </div>

          <div>
            <label class="block text-xs text-slate-500 mb-1">周起始日</label>
            <select
              v-model="globalSettings.week_start"
              class="w-full px-3 py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900"
            >
              <option value="周一">周一</option>
              <option value="周日">周日</option>
            </select>
          </div>

          <label class="flex items-center justify-between rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 px-3 py-2.5">
            <span class="text-sm text-slate-700 dark:text-slate-200">快速记账模式</span>
            <input v-model="globalSettings.quick_create_enabled" type="checkbox" class="w-4 h-4" />
          </label>
        </div>

        <button
          @click="handleSaveGlobal"
          class="w-full py-3 rounded-xl bg-indigo-500 hover:bg-indigo-600 text-white font-medium"
        >保存设置</button>
      </div>

      <div v-else-if="kind === 'extensions'" class="space-y-4">
        <div class="bg-white dark:bg-slate-800 rounded-2xl p-4 border border-slate-100 dark:border-slate-700 shadow-sm space-y-3">
          <label class="flex items-center justify-between rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 px-3 py-2.5">
            <span class="text-sm text-slate-700 dark:text-slate-200">智能分类建议</span>
            <input v-model="extensionSettings.smart_category_enabled" type="checkbox" class="w-4 h-4" />
          </label>

          <label class="flex items-center justify-between rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 px-3 py-2.5">
            <span class="text-sm text-slate-700 dark:text-slate-200">周期任务提醒</span>
            <input v-model="extensionSettings.recurring_reminder_enabled" type="checkbox" class="w-4 h-4" />
          </label>

          <label class="flex items-center justify-between rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 px-3 py-2.5">
            <span class="text-sm text-slate-700 dark:text-slate-200">往来到期提醒</span>
            <input v-model="extensionSettings.debt_reminder_enabled" type="checkbox" class="w-4 h-4" />
          </label>

          <label class="flex items-center justify-between rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 px-3 py-2.5">
            <span class="text-sm text-slate-700 dark:text-slate-200">快捷导入助手</span>
            <input v-model="extensionSettings.quick_import_enabled" type="checkbox" class="w-4 h-4" />
          </label>
        </div>

        <button
          @click="handleSaveExtensions"
          class="w-full py-3 rounded-xl bg-indigo-500 hover:bg-indigo-600 text-white font-medium"
        >保存扩展设置</button>
      </div>

      <div v-else class="space-y-4">
        <div class="overflow-hidden transition-[height] duration-150" :style="{ height: `${Math.round(pullDistance)}px` }">
          <div class="h-full flex items-end justify-center pb-2 text-xs text-slate-500 gap-1">
            <Loader2 v-if="refreshingLogs" class="w-3 h-3 animate-spin" />
            <span>{{ pullHint }}</span>
          </div>
        </div>

        <div class="bg-white dark:bg-slate-800 rounded-2xl border border-slate-100 dark:border-slate-700 shadow-sm overflow-hidden">
          <div v-if="operationLogs.length === 0" class="p-6 text-center text-sm text-slate-500">暂无操作日志</div>
          <div
            v-for="log in operationLogs"
            :key="log.id"
            class="px-4 py-3 border-b border-slate-100 dark:border-slate-700 last:border-b-0"
          >
            <p class="text-sm font-medium text-slate-800 dark:text-white">{{ log.action }}</p>
            <p class="text-xs text-slate-500 mt-0.5">{{ log.detail }}</p>

            <div class="mt-2 flex items-center justify-between gap-2">
              <p class="text-[11px] text-slate-400">{{ new Date(log.created_at).toLocaleString('zh-CN') }}</p>

              <button
                v-if="log.rollback"
                @click="handleRollback(log)"
                :disabled="log.rolled_back || rollbackingLogId === log.id"
                class="px-2.5 py-1 rounded-lg text-xs border transition disabled:opacity-60"
                :class="log.rolled_back
                  ? 'border-slate-200 text-slate-400 bg-slate-50 dark:border-slate-700 dark:bg-slate-900/50'
                  : 'border-indigo-200 text-indigo-600 bg-indigo-50 hover:bg-indigo-100 dark:border-indigo-700 dark:bg-indigo-900/20'"
              >
                <Loader2 v-if="rollbackingLogId === log.id" class="w-3 h-3 animate-spin" />
                <span v-else>{{ log.rolled_back ? '已回滚' : rollbackLabel(log) }}</span>
              </button>
            </div>

            <p v-if="log.rolled_back && log.rolled_back_at" class="text-[11px] text-indigo-600 mt-1">
              已回滚于 {{ new Date(log.rolled_back_at).toLocaleString('zh-CN') }}
            </p>
          </div>
        </div>

        <button
          @click="handleClearLogs"
          class="w-full py-3 rounded-xl border border-rose-200 text-rose-600 bg-rose-50 hover:bg-rose-100 font-medium flex items-center justify-center gap-1"
        >
          <Trash2 class="w-4 h-4" />
          清空日志
        </button>
      </div>
    </main>
  </div>
</template>
