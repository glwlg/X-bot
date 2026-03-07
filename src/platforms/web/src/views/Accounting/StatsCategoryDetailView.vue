<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAccountingStore } from '@/stores/accounting'
import { getCategories, getCategorySummaryByRange, type CategorySummaryItem } from '@/api/accounting'
import { ChevronLeft, Loader2 } from 'lucide-vue-next'
import * as echarts from 'echarts'
import { getStatsPanel } from '@/utils/accountingLocal'
import {
    createDefaultCustomRangeState,
    formatDateInput,
    getRangeWindow,
    rangeOptions,
    toIsoLocal,
    type RangePreset,
} from './statsRange'

type StatType = '支出' | '收入'

const router = useRouter()
const route = useRoute()
const store = useAccountingStore()
const now = new Date()

const loading = ref(false)
const statType = ref<StatType>('支出')
const rangePreset = ref<RangePreset>('last_12_months')
const customRange = ref(createDefaultCustomRangeState(now))
const selectedCategory = ref('全部分类')
const categories = ref<string[]>(['全部分类'])
const categoryData = ref<CategorySummaryItem[]>([])

const chartRef = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null
let delayedRenderTimer: ReturnType<typeof setTimeout> | null = null
let loadVersion = 0

const detailRangeOptions = rangeOptions.filter(item =>
    !['year_range', 'quarter_range', 'month_range', 'week_range'].includes(item.key)
)

const timeWindow = computed(() => getRangeWindow(rangePreset.value, customRange.value, now))

const formatMoney = (n: number) =>
    new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(n)

const totalAmount = computed(() => categoryData.value.reduce((sum, item) => sum + item.amount, 0))

const indigoColors = [
    '#14b8a6', '#06b6d4', '#0ea5e9', '#6366f1', '#8b5cf6',
    '#d946ef', '#f43f5e', '#f97316', '#eab308', '#22c55e',
]

const renderChart = () => {
    if (!chartRef.value || chartRef.value.clientWidth <= 0 || chartRef.value.clientHeight <= 0) return false
    if (!chart) chart = echarts.init(chartRef.value)

    const data = categoryData.value.map((item, index) => ({
        name: item.category,
        value: item.amount,
        itemStyle: { color: indigoColors[index % indigoColors.length] },
    }))

    chart.setOption({
        tooltip: { trigger: 'item', formatter: '{b}: ¥{c} ({d}%)' },
        series: [{
            type: 'pie',
            radius: ['52%', '80%'],
            center: ['50%', '50%'],
            label: { show: false },
            data: data.length > 0 ? data : [{ name: '暂无', value: 0, itemStyle: { color: '#e5e7eb' } }],
        }],
        graphic: [{
            type: 'text',
            left: 'center',
            top: '42%',
            style: { text: statType.value, fill: '#9ca3af', fontSize: 12 },
        }, {
            type: 'text',
            left: 'center',
            top: '52%',
            style: { text: `¥${formatMoney(totalAmount.value)}`, fill: '#111827', fontSize: 16, fontWeight: 'bold' },
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
            .filter(item => item.type === statType.value)
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
        const res = await getCategorySummaryByRange(
            store.currentBookId,
            toIsoLocal(timeWindow.value.start),
            toIsoLocal(timeWindow.value.end),
            statType.value,
            selectedCategory.value,
        )
        if (current !== loadVersion) return
        categoryData.value = res.data
    } catch (error) {
        if (current !== loadVersion) return
        console.error('category detail load failed', error)
        categoryData.value = []
    } finally {
        if (current === loadVersion) loading.value = false
    }

    if (current !== loadVersion) return
    await renderChartSafely()
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

watch([rangePreset, () => customRange.value, selectedCategory], () => {
    loadData()
}, { deep: true })

const initializeFromQuery = async () => {
    const panelIdRaw = route.query.panel_id
    const panelId = Array.isArray(panelIdRaw) ? panelIdRaw[0] : panelIdRaw
    if (panelId) {
        const panel = await getStatsPanel(store.currentBookId, panelId)
        if (panel) {
            statType.value = panel.default_type
            rangePreset.value = panel.default_range
            selectedCategory.value = panel.default_category || '全部分类'
        }
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
  <div class="h-screen flex flex-col bg-slate-50 dark:bg-slate-900 absolute inset-0 z-50">
    <header class="bg-white dark:bg-slate-800 shadow-sm relative z-10 safe-top">
      <div class="flex items-center justify-between h-14 px-4">
        <button @click="router.back()" class="p-2 -ml-2 text-slate-600 dark:text-slate-300">
          <ChevronLeft class="w-6 h-6" />
        </button>
        <h1 class="text-lg font-bold text-slate-800 dark:text-white">分类统计详情</h1>
        <div class="w-8"></div>
      </div>
    </header>

    <main class="flex-1 overflow-y-auto p-4 safe-bottom">
      <div class="rounded-2xl bg-white dark:bg-slate-800 border border-slate-100 dark:border-slate-700 shadow-sm p-4 space-y-3">
        <div class="grid grid-cols-2 gap-2">
          <select v-model="rangePreset" class="px-3 py-2 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 text-sm">
            <option v-for="item in detailRangeOptions" :key="item.key" :value="item.key">{{ item.label }}</option>
          </select>
          <select v-model="selectedCategory" class="px-3 py-2 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 text-sm">
            <option v-for="name in categories" :key="name" :value="name">{{ name }}</option>
          </select>
        </div>

        <div v-if="rangePreset === 'day_range'" class="grid grid-cols-2 gap-2">
          <input v-model="customRange.dayStart" type="date" class="px-3 py-2 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 text-sm" />
          <input v-model="customRange.dayEnd" type="date" class="px-3 py-2 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 text-sm" />
        </div>

        <div class="flex gap-2">
          <button
            @click="statType = '支出'"
            :class="['px-3 py-1 rounded-full text-xs font-medium transition', statType === '支出' ? 'bg-indigo-500 text-white' : 'bg-gray-100 dark:bg-slate-700 text-theme-secondary']"
          >支出</button>
          <button
            @click="statType = '收入'"
            :class="['px-3 py-1 rounded-full text-xs font-medium transition', statType === '收入' ? 'bg-indigo-500 text-white' : 'bg-gray-100 dark:bg-slate-700 text-theme-secondary']"
          >收入</button>
        </div>

        <p class="text-xs text-theme-muted">{{ timeWindow.label }} · {{ statType }} · {{ selectedCategory }}</p>

        <div class="relative">
          <div ref="chartRef" class="w-full h-[260px]"></div>
          <div v-if="loading" class="absolute inset-0 bg-white/40 dark:bg-slate-800/40 flex items-center justify-center rounded-xl">
            <Loader2 class="w-5 h-5 animate-spin text-indigo-400" />
          </div>
        </div>

        <ul class="space-y-2">
          <li v-for="(cat, i) in categoryData" :key="cat.category" class="flex items-center gap-3">
            <div :style="{ backgroundColor: indigoColors[i % indigoColors.length] }" class="w-3 h-3 rounded-full flex-shrink-0" />
            <span class="flex-1 text-sm text-theme-primary">{{ cat.category }}</span>
            <span class="text-sm font-medium text-theme-primary">¥{{ formatMoney(cat.amount) }}</span>
          </li>
        </ul>
      </div>
    </main>
  </div>
</template>
