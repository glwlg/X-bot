<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAccountingStore } from '@/stores/accounting'
import { getCategories, getRangeSummary, type PeriodSummaryItem } from '@/api/accounting'
import { ChevronLeft, ChevronRight, Loader2 } from 'lucide-vue-next'
import * as echarts from 'echarts'
import { getStatsPanel, type StatsPanelMetric, type StatsPanelSubject } from '@/utils/accountingLocal'
import {
    createDefaultCustomRangeState,
    formatDateInput,
    getRangeWindow,
    rangeOptions,
    shiftWindow,
    toIsoLocal,
    type RangePreset,
    type Granularity,
} from './statsRange'

type StatType = '支出' | '收入'

const router = useRouter()
const route = useRoute()
const store = useAccountingStore()
const now = new Date()

const loading = ref(false)
const statType = ref<StatType>('支出')
const rangePreset = ref<RangePreset>('all_time')
const customRange = ref(createDefaultCustomRangeState(now))
const selectedCategory = ref('全部分类')
const categories = ref<string[]>(['全部分类'])
const periodData = ref<PeriodSummaryItem[]>([])
const shiftedStart = ref<Date | null>(null)
const shiftedEnd = ref<Date | null>(null)
const panelTitle = ref('年度统计')
const panelMetric = ref<StatsPanelMetric>('sum')
const panelSubject = ref<StatsPanelSubject>('dynamic')

const chartRef = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null
let delayedRenderTimer: ReturnType<typeof setTimeout> | null = null
let loadVersion = 0

const pad2 = (n: number) => String(n).padStart(2, '0')
const toDateLabel = (d: Date) => `${d.getFullYear()}/${pad2(d.getMonth() + 1)}/${pad2(d.getDate())}`

const baseWindow = computed(() => getRangeWindow(rangePreset.value, customRange.value, now))

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

const detailRangeOptions = rangeOptions.filter(item =>
    !['year_range', 'quarter_range', 'month_range', 'week_range'].includes(item.key)
)

const granularity = computed<Granularity>(() => {
    const bySubject = resolveGranularityForSubject(panelSubject.value, currentWindow.value.granularity)
    if (panelSubject.value !== 'dynamic') {
        return bySubject
    }

    const raw = route.query.granularity
    if (Array.isArray(raw)) {
        if (raw[0] === 'day' || raw[0] === 'week' || raw[0] === 'month' || raw[0] === 'quarter' || raw[0] === 'year') {
            return raw[0]
        }
    } else if (raw === 'day' || raw === 'week' || raw === 'month' || raw === 'quarter' || raw === 'year') {
        return raw
    }
    return currentWindow.value.granularity
})

const queryValue = (key: string) => {
    const raw = route.query[key]
    if (Array.isArray(raw)) return raw[0] ?? ''
    return raw ?? ''
}

const activePanelId = computed(() => queryValue('panel_id') || 'preset-yearly')

const resolveGranularityForSubject = (
    subject: StatsPanelSubject,
    fallback: Granularity,
): Granularity => {
    if (subject === 'year') return 'year'
    if (subject === 'quarter') return 'quarter'
    if (subject === 'month') return 'month'
    if (subject === 'week') return 'week'
    if (subject === 'day') return 'day'
    return fallback
}

const granularityLabel = computed(() => {
    if (granularity.value === 'day') return '日'
    if (granularity.value === 'week') return '周'
    if (granularity.value === 'month') return '月'
    if (granularity.value === 'quarter') return '季'
    return '年'
})

const formatMoney = (n: number) =>
    new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(n)

const totalAmount = computed(() => {
    const amounts = periodData.value.map(item => statType.value === '支出' ? item.expense : item.income)
    const counts = periodData.value.map(item => statType.value === '支出' ? (item.expense_count || 0) : (item.income_count || 0))

    if (panelMetric.value === 'count') {
        return counts.reduce((sum, n) => sum + n, 0)
    }
    if (amounts.length === 0) return 0
    if (panelMetric.value === 'sum') return amounts.reduce((sum, n) => sum + n, 0)
    if (panelMetric.value === 'avg') return amounts.reduce((sum, n) => sum + n, 0) / amounts.length
    if (panelMetric.value === 'max') return Math.max(...amounts)
    if (panelMetric.value === 'min') return Math.min(...amounts)
    return 0
})

const metricLabel = computed(() => {
    if (panelMetric.value === 'sum') return '总额'
    if (panelMetric.value === 'avg') return '平均值'
    if (panelMetric.value === 'max') return '最大值'
    if (panelMetric.value === 'min') return '最小值'
    return '数量'
})

const totalCount = computed(() => {
    return periodData.value.reduce((sum, item) => {
        return sum + (statType.value === '支出' ? (item.expense_count || 0) : (item.income_count || 0))
    }, 0)
})

const formatPeriodLabel = (period: string) => {
    if (granularity.value === 'day') return period.slice(5)
    if (granularity.value === 'week') return period.replace(/^\d{4}-/, '')
    if (granularity.value === 'month') return period.replace('-', '/')
    return period
}

const periodRows = computed(() => {
    const total = totalAmount.value || 1
    return periodData.value
        .map((item) => {
            const amount = panelMetric.value === 'count'
                ? (statType.value === '支出' ? (item.expense_count || 0) : (item.income_count || 0))
                : (statType.value === '支出' ? item.expense : item.income)
            const count = statType.value === '支出' ? (item.expense_count || 0) : (item.income_count || 0)
            return {
                period: item.period,
                amount,
                count,
                ratio: (amount / total) * 100,
            }
        })
        .sort((a, b) => b.period.localeCompare(a.period))
})

const renderChart = () => {
    if (!chartRef.value || chartRef.value.clientWidth <= 0 || chartRef.value.clientHeight <= 0) return false
    if (!chart) chart = echarts.init(chartRef.value)

    const xAxisData = periodData.value.map(item => formatPeriodLabel(item.period))
    const seriesData = periodData.value.map(item => {
        if (panelMetric.value === 'count') {
            return statType.value === '支出' ? (item.expense_count || 0) : (item.income_count || 0)
        }
        return statType.value === '支出' ? item.expense : item.income
    })
    const avg = seriesData.length > 0 ? seriesData.reduce((sum, n) => sum + n, 0) / seriesData.length : 0

    chart.setOption({
        grid: { top: 18, right: 14, bottom: 28, left: 56 },
        xAxis: {
            type: 'category',
            data: xAxisData,
            axisLine: { show: false },
            axisTick: { show: false },
            axisLabel: { color: '#9ca3af', fontSize: 11 },
        },
        yAxis: {
            type: 'value',
            axisLine: { show: false },
            axisTick: { show: false },
            splitLine: { lineStyle: { color: '#f1f5f9' } },
            axisLabel: {
                color: '#9ca3af',
                fontSize: 10,
                formatter: (v: number) => v >= 10000 ? `${(v / 10000).toFixed(0)}w` : `${v}`,
            },
        },
        series: [{
            type: 'bar',
            data: seriesData,
            barWidth: 26,
            itemStyle: {
                color: statType.value === '支出' ? '#fb7185' : '#14b8a6',
                borderRadius: [4, 4, 0, 0],
            },
            markLine: {
                symbol: 'none',
                label: {
                    formatter: `平均 ${formatMoney(avg)}`,
                    color: '#94a3b8',
                },
                lineStyle: {
                    type: 'dashed',
                    color: '#fbcfe8',
                },
                data: [{ yAxis: avg }],
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

const loadCategories = async () => {
    if (!store.currentBookId) return
    try {
        const res = await getCategories(store.currentBookId)
        const names = res.data
            .filter(item => statType.value === '支出' || statType.value === '收入' ? item.type === statType.value : true)
            .map(item => item.name)
        categories.value = ['全部分类', '未分类', ...new Set(names)]
        if (!categories.value.includes(selectedCategory.value)) {
            selectedCategory.value = '全部分类'
        }
    } catch {
        categories.value = ['全部分类', '未分类']
    }
}

const loadData = async () => {
    if (!store.currentBookId) return
    const current = ++loadVersion
    loading.value = true
    try {
        const res = await getRangeSummary(
            store.currentBookId,
            toIsoLocal(currentWindow.value.start),
            toIsoLocal(currentWindow.value.end),
            granularity.value,
            selectedCategory.value,
        )
        if (current !== loadVersion) return
        periodData.value = res.data
    } catch (error) {
        if (current !== loadVersion) return
        console.error('trend detail load failed', error)
        periodData.value = []
    } finally {
        if (current === loadVersion) loading.value = false
    }

    if (current !== loadVersion) return
    await renderChartSafely()
}

const setType = (nextType: StatType) => {
    statType.value = nextType
    router.replace({ query: { ...route.query, type: nextType } })
}

const goEditPanel = () => {
    router.push({
        name: 'StatsPanelEdit',
        params: { id: activePanelId.value },
        query: {
            start: toIsoLocal(currentWindow.value.start),
            end: toIsoLocal(currentWindow.value.end),
        },
    })
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

watch(
    () => route.query.type,
    (value) => {
        const raw = Array.isArray(value) ? value[0] : value
        if (raw === '收入' || raw === '支出') {
            statType.value = raw
        }
    },
    { immediate: true }
)

watch(statType, async () => {
    await loadCategories()
    await loadData()
})

watch([rangePreset, () => customRange.value, selectedCategory, granularity], async () => {
    resetShiftOnPresetChange()
    await loadData()
}, { deep: true })

watch([shiftedStart, shiftedEnd], () => {
    if (shiftedStart.value && shiftedEnd.value) {
        loadData()
    }
})

const initializeFromQuery = async () => {
    const panel = await getStatsPanel(store.currentBookId, activePanelId.value)
    if (panel) {
        panelTitle.value = panel.name
        panelMetric.value = panel.metric
        panelSubject.value = panel.subject
        statType.value = panel.default_type
        rangePreset.value = panel.default_range
        selectedCategory.value = panel.default_category || '全部分类'
    } else {
        panelTitle.value = '年度统计'
        panelMetric.value = 'sum'
        panelSubject.value = 'dynamic'
    }

    const queryStartRaw = route.query.start
    const queryEndRaw = route.query.end
    const startRaw = Array.isArray(queryStartRaw) ? queryStartRaw[0] : queryStartRaw
    const endRaw = Array.isArray(queryEndRaw) ? queryEndRaw[0] : queryEndRaw
    if (!startRaw || !endRaw) return

    const start = new Date(startRaw)
    const end = new Date(endRaw)
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return

    rangePreset.value = 'day_range'
    customRange.value.dayStart = formatDateInput(start)
    customRange.value.dayEnd = formatDateInput(new Date(end.getTime() - 86400000))
}

onMounted(async () => {
    if (!store.currentBookId) await store.fetchBooks()
    await initializeFromQuery()
    await loadCategories()
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
  <div class="h-screen flex flex-col bg-slate-100 dark:bg-slate-900 absolute inset-0 z-50">
    <header class="bg-indigo-500 dark:bg-indigo-700 text-white shadow-sm relative z-10 safe-top">
      <div class="flex items-center justify-between h-14 px-4">
        <button @click="router.back()" class="p-2 -ml-2 text-white/90">
          <ChevronLeft class="w-6 h-6" />
        </button>
        <h1 class="text-2xl font-medium">{{ panelTitle }}</h1>
        <button @click="goEditPanel" class="text-sm font-medium text-white/95">编辑统计</button>
      </div>
    </header>

    <main class="flex-1 overflow-y-auto p-4 safe-bottom space-y-4">
      <div class="rounded-2xl bg-white dark:bg-slate-800 border border-slate-100 dark:border-slate-700 p-3 shadow-sm space-y-3">
        <div class="flex items-center gap-2">
          <button @click="moveRange(-1)" class="w-9 h-9 rounded-xl bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 flex items-center justify-center">
            <ChevronLeft class="w-5 h-5" />
          </button>

          <select
            v-model="rangePreset"
            class="flex-1 h-9 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 px-3 text-sm"
          >
            <option v-for="item in detailRangeOptions" :key="item.key" :value="item.key">{{ item.label }}</option>
          </select>

          <button @click="moveRange(1)" class="w-9 h-9 rounded-xl bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 flex items-center justify-center">
            <ChevronRight class="w-5 h-5" />
          </button>

          <select
            v-model="statType"
            class="h-9 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 px-3 text-sm min-w-[82px]"
          >
            <option value="支出">支出</option>
            <option value="收入">收入</option>
          </select>
        </div>

        <div class="flex items-center gap-2">
          <select
            v-model="selectedCategory"
            class="h-9 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 px-3 text-sm flex-1"
          >
            <option v-for="name in categories" :key="name" :value="name">{{ name }}</option>
          </select>
          <button
            type="button"
            @click="setType(statType === '支出' ? '收入' : '支出')"
            class="h-9 px-3 rounded-xl text-sm border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900"
          >
            切换 {{ statType === '支出' ? '收入' : '支出' }}
          </button>
        </div>

        <div v-if="rangePreset === 'day_range'" class="grid grid-cols-2 gap-2">
          <input v-model="customRange.dayStart" type="date" class="h-9 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 px-3 text-sm" />
          <input v-model="customRange.dayEnd" type="date" class="h-9 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 px-3 text-sm" />
        </div>

        <p class="text-xs text-theme-muted">{{ currentWindow.label }} · 按{{ granularityLabel }}</p>
      </div>

      <div class="rounded-2xl bg-white dark:bg-slate-800 border border-slate-100 dark:border-slate-700 p-4 shadow-sm">
        <div class="flex items-baseline gap-2 mb-3">
          <p class="text-4xl font-semibold" :class="statType === '支出' ? 'text-rose-500' : 'text-indigo-500'">
            <template v-if="panelMetric === 'count'">
              {{ totalAmount }}
            </template>
            <template v-else>
              {{ statType === '支出' ? '-' : '+' }}¥{{ formatMoney(totalAmount) }}
            </template>
          </p>
          <p class="text-sm text-indigo-500">{{ metricLabel }} · 总笔数 {{ totalCount }}</p>
        </div>

        <div class="relative">
          <div ref="chartRef" class="w-full h-[250px]"></div>
          <div v-if="loading" class="absolute inset-0 bg-white/40 dark:bg-slate-800/40 flex items-center justify-center rounded-xl">
            <Loader2 class="w-5 h-5 animate-spin text-indigo-400" />
          </div>
        </div>
      </div>

      <div class="space-y-3">
        <div
          v-for="row in periodRows"
          :key="row.period"
          class="rounded-2xl bg-white dark:bg-slate-800 border border-slate-100 dark:border-slate-700 p-4 shadow-sm flex items-center justify-between"
        >
          <div class="flex items-center gap-3">
            <div class="w-3 h-3 rounded-full" :class="statType === '支出' ? 'bg-rose-400' : 'bg-indigo-500'"></div>
            <div>
              <p class="text-xl text-theme-primary">{{ formatPeriodLabel(row.period) }}</p>
              <p class="text-sm text-theme-muted">{{ row.ratio.toFixed(1) }}% · {{ row.count }}笔</p>
            </div>
          </div>
          <p class="text-4xl font-semibold" :class="statType === '支出' ? 'text-rose-500' : 'text-indigo-500'">
            <template v-if="panelMetric === 'count'">
              {{ row.amount }}
            </template>
            <template v-else>
              {{ statType === '支出' ? '-' : '+' }}¥{{ formatMoney(row.amount) }}
            </template>
          </p>
        </div>
      </div>
    </main>
  </div>
</template>
