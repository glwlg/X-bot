<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAccountingStore } from '@/stores/accounting'
import {
    getAccounts,
    getBalanceTrend,
    getYearlySummary,
    type AccountItem,
    type BalanceTrendScope,
    type ScopedBalanceTrendItem,
} from '@/api/accounting'
import { ChevronLeft, ChevronRight, Loader2 } from 'lucide-vue-next'
import * as echarts from 'echarts'
import {
    createDefaultCustomRangeState,
    formatDateInput,
    getRangeWindow,
    rangeOptions,
    shiftWindow,
    toIsoLocal,
    type Granularity,
    type RangePreset,
} from './statsRange'

const router = useRouter()
const route = useRoute()
const store = useAccountingStore()
const now = new Date()

const loading = ref(false)
const scope = ref<BalanceTrendScope>('net')
const activeTab = ref<'trend' | 'share'>('trend')
const rangePreset = ref<RangePreset>('last_12_months')
const customRange = ref(createDefaultCustomRangeState(now))
const shiftedStart = ref<Date | null>(null)
const shiftedEnd = ref<Date | null>(null)
const selectedAccountType = ref('')
const selectedAccountId = ref<number | null>(null)
const accounts = ref<AccountItem[]>([])
const trendRows = ref<ScopedBalanceTrendItem[]>([])
const allTimeStart = ref<Date | null>(null)

const chartRef = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null
let delayedRenderTimer: ReturnType<typeof setTimeout> | null = null
let loadVersion = 0

const scopeOptions: Array<{ key: BalanceTrendScope; label: string }> = [
    { key: 'net', label: '净资产' },
    { key: 'assets', label: '资产' },
    { key: 'liabilities', label: '负债' },
    { key: 'account_type', label: '账户类型' },
    { key: 'account', label: '具体账户' },
]

const detailRangeOptions = rangeOptions.filter(item =>
    !['year_range', 'quarter_range', 'month_range', 'week_range'].includes(item.key)
)

const baseWindow = computed(() => getRangeWindow(
    rangePreset.value,
    customRange.value,
    now,
    allTimeStart.value,
))

const currentWindow = computed(() => {
    if (shiftedStart.value && shiftedEnd.value) {
        return {
            start: shiftedStart.value,
            end: shiftedEnd.value,
            granularity: baseWindow.value.granularity,
            label: `${toDateLabel(shiftedStart.value)} - ${toDateLabel(new Date(shiftedEnd.value.getTime() - 86400000))}`,
        }
    }
    return baseWindow.value
})

const accountTypes = computed(() => {
    return [...new Set(accounts.value.map(account => account.type))]
})

const scopeLabel = computed(() => {
    return scopeOptions.find(item => item.key === scope.value)?.label || '净资产'
})

const shareRows = computed(() => {
    let source = accounts.value
    if (scope.value === 'net' || scope.value === 'assets' || scope.value === 'liabilities') {
        source = accounts.value.filter(account => account.include_in_assets)
    }
    if (scope.value === 'account_type') {
        source = accounts.value.filter(account => account.type === selectedAccountType.value)
    }
    if (scope.value === 'account') {
        source = accounts.value.filter(account => account.id === selectedAccountId.value)
    }

    const normalized = source
        .filter(account => {
            if (scope.value === 'assets') return account.balance > 0
            if (scope.value === 'liabilities') return account.balance < 0
            return true
        })
        .map(account => ({
            id: account.id,
            name: account.name,
            type: account.type,
            balance: account.balance,
        }))

    const base = normalized.reduce((sum, account) => sum + Math.abs(account.balance), 0)
    if (base <= 0) {
        return normalized.map(account => ({ ...account, ratio: 0 }))
    }

    return normalized
        .map(account => ({
            ...account,
            ratio: (Math.abs(account.balance) / base) * 100,
        }))
        .sort((a, b) => Math.abs(b.balance) - Math.abs(a.balance))
})

const shareTotal = computed(() => {
    return shareRows.value.reduce((sum, row) => sum + Math.abs(row.balance), 0)
})

const displayBalance = computed(() => {
    if (trendRows.value.length === 0) return 0
    return trendRows.value[trendRows.value.length - 1]?.balance ?? 0
})

const displayChange = computed(() => {
    if (trendRows.value.length === 0) return 0
    return trendRows.value[trendRows.value.length - 1]?.change ?? 0
})

const orderedTrendRows = computed(() => {
    return [...trendRows.value].sort((a, b) => {
        return new Date(b.period_start).getTime() - new Date(a.period_start).getTime()
    })
})

const granularity = computed<Granularity>(() => currentWindow.value.granularity)

const granularityLabel = computed(() => {
    if (granularity.value === 'day') return '日'
    if (granularity.value === 'week') return '周'
    if (granularity.value === 'month') return '月'
    if (granularity.value === 'quarter') return '季'
    return '年'
})

const pad2 = (n: number) => String(n).padStart(2, '0')
const toDateLabel = (d: Date) => `${d.getFullYear()}/${pad2(d.getMonth() + 1)}/${pad2(d.getDate())}`

const formatMoney = (n: number) =>
    new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(n)

const formatPeriodLabel = (period: string) => {
    if (granularity.value === 'day') return period.slice(5)
    if (granularity.value === 'week') return period.replace(/^\d{4}-/, '')
    if (granularity.value === 'month') return period.replace('-', '/')
    return period
}

const readThemeColor = (cssVar: string, fallback: string) => {
    if (typeof window === 'undefined') return fallback
    const value = getComputedStyle(document.documentElement).getPropertyValue(cssVar).trim()
    return value || fallback
}

const ensureScopeSelection = () => {
    if (scope.value === 'account_type') {
        if (!accountTypes.value.includes(selectedAccountType.value)) {
            selectedAccountType.value = accountTypes.value[0] || ''
        }
    }

    if (scope.value === 'account') {
        const hasSelected = accounts.value.some(account => account.id === selectedAccountId.value)
        if (!hasSelected) {
            selectedAccountId.value = accounts.value[0]?.id ?? null
        }
    }
}

const renderChart = () => {
    if (!chartRef.value || chartRef.value.clientWidth <= 0 || chartRef.value.clientHeight <= 0) return false
    if (!chart) chart = echarts.init(chartRef.value)

    const axisColor = readThemeColor('--color-text-muted', '#94a3b8')
    const splitLineColor = readThemeColor('--color-border-primary', '#e2e8f0')
    const lineColor = '#6366f1'

    const xAxisData = trendRows.value.map(item => formatPeriodLabel(item.period))
    const seriesData = trendRows.value.map(item => item.balance)

    chart.setOption({
        grid: { top: 18, right: 14, bottom: 28, left: 56 },
        xAxis: {
            type: 'category',
            data: xAxisData,
            axisLine: { show: false },
            axisTick: { show: false },
            axisLabel: { color: axisColor, fontSize: 11 },
        },
        yAxis: {
            type: 'value',
            axisLine: { show: false },
            axisTick: { show: false },
            splitLine: { lineStyle: { color: splitLineColor } },
            axisLabel: {
                color: axisColor,
                fontSize: 10,
                formatter: (v: number) => v >= 10000 ? `${(v / 10000).toFixed(0)}w` : `${v}`,
            },
        },
        series: [{
            type: 'line',
            data: seriesData,
            smooth: true,
            symbol: 'none',
            lineStyle: { color: lineColor, width: 3 },
            areaStyle: {
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                    { offset: 0, color: 'rgba(99,102,241,0.22)' },
                    { offset: 1, color: 'rgba(99,102,241,0.03)' },
                ]),
            },
        }],
    })
    chart.resize()
    return true
}

const renderChartSafely = async () => {
    await nextTick()
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))

    if (renderChart()) return

    if (delayedRenderTimer) clearTimeout(delayedRenderTimer)
    delayedRenderTimer = setTimeout(() => {
        renderChart()
    }, 120)
}

const loadAccounts = async () => {
    if (!store.currentBookId) return
    try {
        const res = await getAccounts(store.currentBookId)
        accounts.value = res.data
    } catch (error) {
        console.error('load accounts failed', error)
        accounts.value = []
    }
}

const loadAllTimeStart = async () => {
    if (!store.currentBookId) {
        allTimeStart.value = null
        return
    }
    try {
        const res = await getYearlySummary(store.currentBookId)
        const years = res.data
            .map(item => Number(item.year))
            .filter((year): year is number => Number.isInteger(year) && year > 0)
            .sort((a, b) => a - b)
        const firstYear = years[0] ?? now.getFullYear()
        allTimeStart.value = years.length
            ? new Date(firstYear, 0, 1)
            : new Date(now.getFullYear(), 0, 1)
    } catch (error) {
        console.error('load yearly summary failed', error)
        allTimeStart.value = new Date(now.getFullYear(), 0, 1)
    }
}

const loadData = async () => {
    if (!store.currentBookId) return
    ensureScopeSelection()

    if (scope.value === 'account_type' && !selectedAccountType.value) {
        trendRows.value = []
        await renderChartSafely()
        return
    }
    if (scope.value === 'account' && !selectedAccountId.value) {
        trendRows.value = []
        await renderChartSafely()
        return
    }

    const current = ++loadVersion
    loading.value = true
    try {
        const res = await getBalanceTrend(
            store.currentBookId,
            toIsoLocal(currentWindow.value.start),
            toIsoLocal(currentWindow.value.end),
            granularity.value,
            scope.value,
            scope.value === 'account_type' ? selectedAccountType.value : '',
            scope.value === 'account' ? (selectedAccountId.value ?? undefined) : undefined,
        )
        if (current !== loadVersion) return
        trendRows.value = res.data
    } catch (error) {
        if (current !== loadVersion) return
        console.error('load balance trend failed', error)
        trendRows.value = []
    } finally {
        if (current === loadVersion) loading.value = false
    }

    if (current !== loadVersion) return
    await renderChartSafely()
}

const moveRange = (direction: -1 | 1) => {
    const shifted = shiftWindow(currentWindow.value, direction)
    shiftedStart.value = shifted.start
    shiftedEnd.value = shifted.end
}

const resetShiftOnPresetChange = () => {
    shiftedStart.value = null
    shiftedEnd.value = null
}

const initializeFromQuery = () => {
    const readQuery = (key: string) => {
        const raw = route.query[key]
        if (Array.isArray(raw)) return raw[0] || ''
        return raw || ''
    }

    const queryScope = readQuery('scope')
    if (queryScope === 'net' || queryScope === 'assets' || queryScope === 'liabilities' || queryScope === 'account_type' || queryScope === 'account') {
        scope.value = queryScope
    }

    selectedAccountType.value = readQuery('account_type')

    const accountIdRaw = Number(readQuery('account_id'))
    if (Number.isFinite(accountIdRaw) && accountIdRaw > 0) {
        selectedAccountId.value = accountIdRaw
    }

    const startRaw = readQuery('start')
    const endRaw = readQuery('end')
    if (!startRaw || !endRaw) return

    const start = new Date(startRaw)
    const end = new Date(endRaw)
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return

    rangePreset.value = 'day_range'
    customRange.value.dayStart = formatDateInput(start)
    customRange.value.dayEnd = formatDateInput(new Date(end.getTime() - 86400000))
}

watch(scope, async () => {
    ensureScopeSelection()
    await loadData()
})

watch([rangePreset, selectedAccountType, selectedAccountId], async () => {
    resetShiftOnPresetChange()
    await loadData()
})

watch(() => customRange.value, async () => {
    resetShiftOnPresetChange()
    await loadData()
}, { deep: true })

watch([shiftedStart, shiftedEnd], () => {
    if (shiftedStart.value && shiftedEnd.value) {
        loadData()
    }
})

onMounted(async () => {
    if (!store.currentBookId) {
        await store.fetchBooks()
    }
    await loadAccounts()
    await loadAllTimeStart()
    initializeFromQuery()
    ensureScopeSelection()
    await loadData()

    if (typeof ResizeObserver !== 'undefined' && chartRef.value) {
        resizeObserver = new ResizeObserver(() => chart?.resize())
        resizeObserver.observe(chartRef.value)
    }
    window.addEventListener('resize', renderChartSafely)
})

onBeforeUnmount(() => {
    resizeObserver?.disconnect()
    resizeObserver = null

    if (delayedRenderTimer) {
        clearTimeout(delayedRenderTimer)
        delayedRenderTimer = null
    }

    window.removeEventListener('resize', renderChartSafely)
    chart?.dispose()
    chart = null
})
</script>

<template>
  <div class="h-screen flex flex-col bg-theme-secondary absolute inset-0 z-50">
    <header class="bg-indigo-500 dark:bg-indigo-700 text-white shadow-sm relative z-10 safe-top">
      <div class="flex items-center justify-between h-14 px-4">
        <button @click="router.back()" class="p-2 -ml-2 text-white/90">
          <ChevronLeft class="w-6 h-6" />
        </button>
        <h1 class="text-2xl font-medium">余额趋势</h1>
        <div class="flex items-center gap-1 rounded-full bg-white/20 p-1">
          <button
            @click="activeTab = 'trend'"
            class="px-2.5 py-1 rounded-full text-xs transition"
            :class="activeTab === 'trend' ? 'bg-white text-indigo-600 font-medium' : 'text-white/90'"
          >
            余额趋势
          </button>
          <button
            @click="activeTab = 'share'"
            class="px-2.5 py-1 rounded-full text-xs transition"
            :class="activeTab === 'share' ? 'bg-white text-indigo-600 font-medium' : 'text-white/90'"
          >
            账户占比
          </button>
        </div>
      </div>
    </header>

    <main class="flex-1 overflow-y-auto p-4 safe-bottom space-y-4">
      <div class="rounded-2xl bg-theme-elevated border border-theme-primary p-3 shadow-sm space-y-3">
        <div class="flex items-center gap-2">
          <select
            v-model="scope"
            class="h-9 rounded-xl border border-theme-primary bg-theme-secondary px-3 text-sm min-w-[96px] text-theme-primary"
          >
            <option v-for="item in scopeOptions" :key="item.key" :value="item.key">{{ item.label }}</option>
          </select>

          <button @click="moveRange(-1)" class="w-9 h-9 rounded-xl bg-theme-secondary text-theme-secondary flex items-center justify-center">
            <ChevronLeft class="w-5 h-5" />
          </button>

          <select
            v-model="rangePreset"
            class="flex-1 h-9 rounded-xl border border-theme-primary bg-theme-secondary px-3 text-sm text-theme-primary"
          >
            <option v-for="item in detailRangeOptions" :key="item.key" :value="item.key">{{ item.label }}</option>
          </select>

          <button @click="moveRange(1)" class="w-9 h-9 rounded-xl bg-theme-secondary text-theme-secondary flex items-center justify-center">
            <ChevronRight class="w-5 h-5" />
          </button>
        </div>

        <div v-if="scope === 'account_type'">
          <select
            v-model="selectedAccountType"
            class="w-full h-9 rounded-xl border border-theme-primary bg-theme-secondary px-3 text-sm text-theme-primary"
          >
            <option v-for="type in accountTypes" :key="type" :value="type">{{ type }}</option>
          </select>
        </div>

        <div v-if="scope === 'account'">
          <select
            v-model.number="selectedAccountId"
            class="w-full h-9 rounded-xl border border-theme-primary bg-theme-secondary px-3 text-sm text-theme-primary"
          >
            <option v-for="account in accounts" :key="account.id" :value="account.id">{{ account.name }}</option>
          </select>
        </div>

        <div v-if="rangePreset === 'day_range'" class="grid grid-cols-2 gap-2">
          <input v-model="customRange.dayStart" type="date" class="h-9 rounded-xl border border-theme-primary bg-theme-secondary px-3 text-sm text-theme-primary" />
          <input v-model="customRange.dayEnd" type="date" class="h-9 rounded-xl border border-theme-primary bg-theme-secondary px-3 text-sm text-theme-primary" />
        </div>

        <p class="text-xs text-theme-muted">{{ currentWindow.label }} · 按{{ granularityLabel }}</p>
      </div>

      <template v-if="activeTab === 'trend'">
        <div class="rounded-2xl bg-theme-elevated border border-theme-primary p-4 shadow-sm">
          <div class="flex items-baseline justify-between gap-3 mb-3">
            <p class="text-4xl font-semibold text-indigo-500">¥{{ formatMoney(displayBalance) }}</p>
            <p class="text-sm" :class="displayChange >= 0 ? 'text-indigo-500' : 'text-rose-500'">
              {{ displayChange >= 0 ? '+' : '' }}¥{{ formatMoney(displayChange) }}
            </p>
          </div>

          <div class="relative">
            <div ref="chartRef" class="w-full h-[250px]"></div>
            <div v-if="loading" class="absolute inset-0 bg-black/10 flex items-center justify-center rounded-xl">
              <Loader2 class="w-5 h-5 animate-spin text-indigo-500" />
            </div>
          </div>
        </div>

        <div class="space-y-3">
          <div
            v-for="row in orderedTrendRows"
            :key="`${row.period}-${row.period_end}`"
            class="rounded-2xl bg-theme-elevated border border-theme-primary p-4 shadow-sm"
          >
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-2">
                <div class="w-2.5 h-2.5 rounded-full" :class="row.change >= 0 ? 'bg-indigo-500' : 'bg-rose-400'"></div>
                <p class="text-xl text-theme-primary">{{ formatPeriodLabel(row.period) }}</p>
              </div>
              <p class="text-4xl font-semibold" :class="row.change >= 0 ? 'text-indigo-500' : 'text-rose-500'">
                {{ row.change >= 0 ? '+' : '' }}¥{{ formatMoney(row.change) }}
              </p>
            </div>

            <div class="mt-1 flex items-center justify-between text-sm text-theme-muted">
              <p>+¥{{ formatMoney(row.income) }} · -¥{{ formatMoney(row.expense) }}</p>
              <p class="px-2 py-0.5 rounded-full border border-theme-primary">余额 ¥{{ formatMoney(row.balance) }}</p>
            </div>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="rounded-2xl bg-theme-elevated border border-theme-primary p-4 shadow-sm">
          <div class="flex items-center justify-between mb-3">
            <p class="text-sm text-theme-muted">{{ scopeLabel }} · 账户占比</p>
            <p class="text-sm font-medium text-theme-primary">总额 ¥{{ formatMoney(shareTotal) }}</p>
          </div>

          <div v-if="shareRows.length === 0" class="py-8 text-center text-sm text-theme-muted">暂无可展示账户</div>

          <div v-else class="space-y-3">
            <div
              v-for="row in shareRows"
              :key="`share-${row.id}`"
              class="rounded-xl border border-theme-primary bg-theme-secondary p-3"
            >
              <div class="flex items-center justify-between gap-2 mb-2">
                <div>
                  <p class="text-sm font-medium text-theme-primary">{{ row.name }}</p>
                  <p class="text-xs text-theme-muted mt-0.5">{{ row.type }}</p>
                </div>
                <div class="text-right">
                  <p class="text-sm font-semibold" :class="row.balance >= 0 ? 'text-indigo-500' : 'text-rose-500'">
                    {{ row.balance >= 0 ? '+' : '-' }}¥{{ formatMoney(Math.abs(row.balance)) }}
                  </p>
                  <p class="text-xs text-theme-muted">{{ row.ratio.toFixed(1) }}%</p>
                </div>
              </div>

              <div class="h-2 rounded-full bg-theme-primary overflow-hidden">
                <div
                  class="h-full rounded-full"
                  :class="row.balance >= 0 ? 'bg-indigo-500' : 'bg-rose-400'"
                  :style="{ width: `${Math.min(100, row.ratio)}%` }"
                ></div>
              </div>
            </div>
          </div>
        </div>
      </template>
    </main>
  </div>
</template>
