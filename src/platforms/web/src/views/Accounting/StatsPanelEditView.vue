<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAccountingStore } from '@/stores/accounting'
import { getCategories, getRangeSummary, type PeriodSummaryItem } from '@/api/accounting'
import { Check, ChevronLeft, ChevronRight } from 'lucide-vue-next'
import {
    appendOperationLog,
    createStatsPanelDraft,
    getStatsPanel,
    type StatsPanelConfig,
    type StatsPanelFilter,
    type StatsPanelMetric,
    type StatsPanelSubject,
    upsertStatsPanel,
} from '@/utils/accountingLocal'
import {
    createDefaultCustomRangeState,
    formatDateInput,
    getRangeWindow,
    rangeOptions,
    toIsoLocal,
} from './statsRange'

const router = useRouter()
const route = useRoute()
const store = useAccountingStore()
const now = new Date()

const panel = ref<StatsPanelConfig>(createStatsPanelDraft())
const categories = ref<string[]>(['全部分类'])
const previewData = ref<PeriodSummaryItem[]>([])
const previewLoading = ref(false)
const customRange = ref(createDefaultCustomRangeState(now))

const showMetricDialog = ref(false)
const showSubjectDialog = ref(false)
const showFilterDialog = ref(false)

const metricOptions: Array<{ value: StatsPanelMetric; label: string }> = [
    { value: 'sum', label: '交易金额总额' },
    { value: 'avg', label: '交易金额平均值' },
    { value: 'max', label: '交易金额最大值' },
    { value: 'min', label: '交易金额最小值' },
    { value: 'count', label: '交易数量' },
]

const subjectOptions: Array<{ value: StatsPanelSubject; label: string; desc: string }> = [
    { value: 'dynamic', label: '动态日期', desc: '根据筛选条件动态确定统计日期对象' },
    { value: 'year', label: '年', desc: '从记账至今的所有年' },
    { value: 'quarter', label: '季', desc: '交易所在季，1-4季' },
    { value: 'month', label: '月', desc: '交易所在月，1-12月' },
    { value: 'week', label: '周', desc: '交易所在周几，周一至周日' },
    { value: 'day', label: '日', desc: '交易所在日，1-31日' },
    { value: 'amount', label: '金额', desc: '交易金额范围' },
    { value: 'category', label: '分类', desc: '交易分类' },
    { value: 'account', label: '账户', desc: '交易账户，包含转入转出账户' },
    { value: 'project', label: '项目', desc: '交易项目' },
]

const filterOptions: Array<{ value: StatsPanelFilter; label: string }> = [
    { value: 'type', label: '类型' },
    { value: 'date_range', label: '日期范围' },
    { value: 'category', label: '分类' },
    { value: 'account', label: '账户' },
    { value: 'project', label: '项目' },
]

const metricLabelMap: Record<StatsPanelMetric, string> = {
    sum: '交易金额总额',
    avg: '交易金额平均值',
    max: '交易金额最大值',
    min: '交易金额最小值',
    count: '交易数量',
}

const subjectLabelMap: Record<StatsPanelSubject, string> = {
    dynamic: '动态日期',
    year: '年',
    quarter: '季',
    month: '月',
    week: '周',
    day: '日',
    amount: '金额',
    category: '分类',
    account: '账户',
    project: '项目',
}

const panelId = computed(() => {
    const raw = route.params.id
    return typeof raw === 'string' ? raw : ''
})

const isEditing = computed(() => Boolean(panelId.value))

const panelRangeWindow = computed(() => getRangeWindow(panel.value.default_range, customRange.value, now))

const filterLabel = computed(() => {
    const labels = panel.value.filters.map(item => {
        const hit = filterOptions.find(opt => opt.value === item)
        return hit?.label || item
    })
    return labels.join('，') || '未设置'
})

const previewSeries = computed(() => {
    return previewData.value.map(item => ({
        period: item.period,
        amount: panel.value.default_type === '支出' ? item.expense : item.income,
        count: panel.value.default_type === '支出' ? (item.expense_count || 0) : (item.income_count || 0),
    }))
})

const previewValue = computed(() => {
    const values = previewSeries.value.map(item => item.amount)
    const counts = previewSeries.value.map(item => item.count)

    if (panel.value.metric === 'count') {
        return counts.reduce((sum, n) => sum + n, 0)
    }

    if (values.length === 0) return 0
    if (panel.value.metric === 'sum') return values.reduce((sum, n) => sum + n, 0)
    if (panel.value.metric === 'avg') return values.reduce((sum, n) => sum + n, 0) / values.length
    if (panel.value.metric === 'max') return Math.max(...values)
    if (panel.value.metric === 'min') return Math.min(...values)
    return 0
})

const previewBars = computed(() => {
    const sliced = previewSeries.value.slice(-6)
    const max = Math.max(...sliced.map(item => item.amount), 1)
    return sliced.map(item => ({
        period: item.period,
        amount: item.amount,
        height: Math.max(8, Math.round((item.amount / max) * 120)),
    }))
})

const formatMoney = (n: number) =>
    new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(n)

const formatPeriod = (period: string) => {
    return period.replace(/^\d{4}-/, '')
}

const loadCategories = async () => {
    if (!store.currentBookId) return
    try {
        const res = await getCategories(store.currentBookId)
        const names = res.data
            .filter(item => item.type === panel.value.default_type)
            .map(item => item.name)
        categories.value = ['全部分类', '未分类', ...new Set(names)]
        if (!categories.value.includes(panel.value.default_category)) {
            panel.value.default_category = '全部分类'
        }
    } catch {
        categories.value = ['全部分类', '未分类']
    }
}

const loadPreview = async () => {
    if (!store.currentBookId) return
    previewLoading.value = true
    try {
        const res = await getRangeSummary(
            store.currentBookId,
            toIsoLocal(panelRangeWindow.value.start),
            toIsoLocal(panelRangeWindow.value.end),
            panelRangeWindow.value.granularity,
            panel.value.default_category,
        )
        previewData.value = res.data
    } catch (error) {
        console.error('preview load failed', error)
        previewData.value = []
    } finally {
        previewLoading.value = false
    }
}

const loadPanel = async () => {
    if (!panelId.value) {
        panel.value = createStatsPanelDraft()
        return
    }
    const found = await getStatsPanel(store.currentBookId, panelId.value)
    if (!found) {
        panel.value = createStatsPanelDraft()
        return
    }
    panel.value = {
        ...found,
        filters: [...found.filters],
    }
}

const toggleFilter = (value: StatsPanelFilter) => {
    if (panel.value.filters.includes(value)) {
        panel.value.filters = panel.value.filters.filter(item => item !== value)
        return
    }
    panel.value.filters = [...panel.value.filters, value]
}

const savePanel = async () => {
    if (!store.currentBookId) return
    const name = panel.value.name.trim()
    if (!name) {
        alert('统计名称不能为空')
        return
    }

    const next: StatsPanelConfig = {
        ...panel.value,
        name,
        description: panel.value.description.trim(),
        filters: panel.value.filters.length > 0 ? [...panel.value.filters] : ['type', 'date_range'],
        is_custom: panel.value.is_custom || !isEditing.value,
    }

    await upsertStatsPanel(store.currentBookId, next)
    appendOperationLog(store.currentBookId, isEditing.value ? '更新统计模板' : '新增统计模板', next.name)
    router.replace({ name: 'StatsPanelManage' })
}

watch(
    () => panel.value.default_type,
    () => {
        loadCategories()
        loadPreview()
    }
)

watch(
    () => [
        panel.value.default_range,
        panel.value.default_category,
        panel.value.metric,
        panel.value.subject,
        panel.value.filters.join(','),
    ],
    () => {
        loadPreview()
    }
)

watch(
    () => customRange.value,
    () => {
        if (panel.value.default_range === 'day_range') {
            loadPreview()
        }
    },
    { deep: true }
)

onMounted(async () => {
    if (!store.currentBookId) {
        await store.fetchBooks()
    }
    await loadPanel()

    const queryStartRaw = route.query.start
    const queryEndRaw = route.query.end
    const startRaw = Array.isArray(queryStartRaw) ? queryStartRaw[0] : queryStartRaw
    const endRaw = Array.isArray(queryEndRaw) ? queryEndRaw[0] : queryEndRaw
    if (startRaw && endRaw) {
        const start = new Date(startRaw)
        const end = new Date(endRaw)
        if (!Number.isNaN(start.getTime()) && !Number.isNaN(end.getTime())) {
            panel.value.default_range = 'day_range'
            customRange.value.dayStart = formatDateInput(start)
            customRange.value.dayEnd = formatDateInput(new Date(end.getTime() - 86400000))
        }
    }

    await loadCategories()
    await loadPreview()
})
</script>

<template>
  <div class="h-screen flex flex-col bg-slate-100 dark:bg-slate-900 absolute inset-0 z-50">
    <header class="bg-indigo-500 dark:bg-indigo-700 text-white shadow-sm relative z-10 safe-top">
      <div class="flex items-center justify-between h-14 px-4">
        <button @click="router.back()" class="p-2 -ml-2 text-white/90">
          <ChevronLeft class="w-6 h-6" />
        </button>
        <h1 class="text-2xl font-medium">{{ isEditing ? '编辑统计' : '添加统计' }}</h1>
        <button @click="savePanel" class="p-2 text-white/95">
          <Check class="w-6 h-6" />
        </button>
      </div>
    </header>

    <main class="flex-1 overflow-y-auto p-4 safe-bottom space-y-4">
      <p class="text-sm text-rose-400 leading-relaxed">
        本页面是用来定义统计的结构和过滤条件，不是筛选数据。点我了解如何自定义统计。
      </p>

      <div class="rounded-3xl bg-white dark:bg-slate-800 border border-slate-100 dark:border-slate-700 p-4 shadow-sm">
        <h2 class="text-2xl font-semibold text-theme-primary mb-3">预览</h2>

        <div class="flex items-center gap-2 mb-3">
          <select
            v-model="panel.default_range"
            class="h-11 rounded-full border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 px-4 text-xl"
          >
            <option v-for="item in rangeOptions" :key="item.key" :value="item.key">{{ item.label }}</option>
          </select>

          <select
            v-model="panel.default_type"
            class="h-11 rounded-full border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 px-4 text-xl"
          >
            <option value="支出">支出</option>
            <option value="收入">收入</option>
          </select>
        </div>

        <div v-if="panel.default_range === 'day_range'" class="grid grid-cols-2 gap-2 mb-3">
          <input v-model="customRange.dayStart" type="date" class="h-9 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 px-3 text-sm" />
          <input v-model="customRange.dayEnd" type="date" class="h-9 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 px-3 text-sm" />
        </div>

        <p class="text-5xl font-semibold mb-4" :class="panel.default_type === '支出' ? 'text-rose-500' : 'text-indigo-500'">
          {{ panel.default_type === '支出' ? '-' : '+' }}
          {{ panel.metric === 'count' ? previewValue : `¥${formatMoney(previewValue)}` }}
        </p>

        <div class="h-40 rounded-2xl bg-slate-50 dark:bg-slate-900 border border-slate-100 dark:border-slate-700 px-3 py-4 flex items-end gap-3">
          <div v-if="previewLoading" class="w-full text-center text-sm text-theme-muted">加载预览中...</div>
          <template v-else>
            <div v-for="bar in previewBars" :key="bar.period" class="flex-1 flex flex-col items-center gap-1">
              <div
                class="w-full rounded-t-md"
                :class="panel.default_type === '支出' ? 'bg-rose-400' : 'bg-indigo-500'"
                :style="{ height: `${bar.height}px` }"
              ></div>
              <p class="text-xs text-theme-muted">{{ formatPeriod(bar.period) }}</p>
            </div>
          </template>
        </div>
      </div>

      <div class="rounded-3xl bg-white dark:bg-slate-800 border border-slate-100 dark:border-slate-700 p-4 shadow-sm divide-y divide-slate-100 dark:divide-slate-700">
        <h2 class="text-2xl font-semibold text-theme-primary pb-3">编辑</h2>

        <div class="py-4 flex items-center justify-between gap-3">
          <span class="text-2xl text-theme-primary">统计名称</span>
          <input v-model="panel.name" class="text-right text-xl bg-transparent outline-none max-w-[58%] text-theme-muted" />
        </div>

        <button @click="showMetricDialog = true" class="w-full py-4 flex items-center justify-between gap-3 text-left">
          <span class="text-2xl text-theme-primary">统计数值</span>
          <span class="text-xl text-theme-muted">{{ metricLabelMap[panel.metric] }} <ChevronRight class="w-4 h-4 inline" /></span>
        </button>

        <button @click="showSubjectDialog = true" class="w-full py-4 flex items-center justify-between gap-3 text-left">
          <span class="text-2xl text-theme-primary">统计对象</span>
          <span class="text-xl text-theme-muted">{{ subjectLabelMap[panel.subject] }} <ChevronRight class="w-4 h-4 inline" /></span>
        </button>

        <button @click="showFilterDialog = true" class="w-full py-4 flex items-center justify-between gap-3 text-left">
          <span class="text-2xl text-theme-primary">过滤条件</span>
          <span class="text-xl text-theme-muted">{{ filterLabel }} <ChevronRight class="w-4 h-4 inline" /></span>
        </button>

        <div class="py-4 flex items-center justify-between gap-3">
          <span class="text-2xl text-theme-primary">默认分类</span>
          <select v-model="panel.default_category" class="text-right text-xl bg-transparent outline-none text-theme-muted max-w-[58%]">
            <option v-for="name in categories" :key="name" :value="name">{{ name }}</option>
          </select>
        </div>

        <div class="py-4 flex items-center justify-between gap-3">
          <span class="text-2xl text-theme-primary">统计描述</span>
          <input v-model="panel.description" class="text-right text-xl bg-transparent outline-none max-w-[58%] text-theme-muted" placeholder="描述一下统计的功能" />
        </div>
      </div>
    </main>

    <div v-if="showMetricDialog" class="fixed inset-0 z-[80] bg-black/45 flex items-center justify-center p-4" @click.self="showMetricDialog = false">
      <div class="w-full max-w-md bg-white dark:bg-slate-800 rounded-3xl overflow-hidden">
        <div class="px-5 py-4 border-b border-slate-100 dark:border-slate-700 text-3xl text-theme-primary">统计数值</div>
        <button
          v-for="option in metricOptions"
          :key="option.value"
          @click="panel.metric = option.value; showMetricDialog = false"
          class="w-full text-left px-5 py-4 border-b border-slate-100 dark:border-slate-700 last:border-b-0 text-2xl"
        >
          {{ option.label }}
        </button>
      </div>
    </div>

    <div v-if="showSubjectDialog" class="fixed inset-0 z-[80] bg-black/45 flex items-center justify-center p-4" @click.self="showSubjectDialog = false">
      <div class="w-full max-w-md max-h-[80vh] overflow-y-auto bg-white dark:bg-slate-800 rounded-3xl">
        <div class="px-5 py-4 border-b border-slate-100 dark:border-slate-700 text-3xl text-theme-primary">统计对象</div>
        <button
          v-for="option in subjectOptions"
          :key="option.value"
          @click="panel.subject = option.value; showSubjectDialog = false"
          class="w-full text-left px-5 py-4 border-b border-slate-100 dark:border-slate-700 last:border-b-0"
        >
          <p class="text-2xl text-theme-primary">{{ option.label }}</p>
          <p class="text-sm text-theme-muted mt-1">{{ option.desc }}</p>
        </button>
      </div>
    </div>

    <div v-if="showFilterDialog" class="fixed inset-0 z-[80] bg-black/45 flex items-center justify-center p-4" @click.self="showFilterDialog = false">
      <div class="w-full max-w-md bg-white dark:bg-slate-800 rounded-3xl overflow-hidden">
        <div class="px-5 py-4 border-b border-slate-100 dark:border-slate-700 text-3xl text-theme-primary">过滤条件</div>
        <button
          v-for="option in filterOptions"
          :key="option.value"
          @click="toggleFilter(option.value)"
          class="w-full text-left px-5 py-4 border-b border-slate-100 dark:border-slate-700 last:border-b-0 flex items-center justify-between"
        >
          <span class="text-2xl text-theme-primary">{{ option.label }}</span>
          <span class="text-xl text-indigo-500">{{ panel.filters.includes(option.value) ? '已选' : '未选' }}</span>
        </button>
        <div class="p-4">
          <button @click="showFilterDialog = false" class="w-full h-11 rounded-xl bg-indigo-500 text-white text-base">完成</button>
        </div>
      </div>
    </div>
  </div>
</template>
