<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, computed, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useAccountingStore } from '@/stores/accounting'
import {
    getRecordsSummary, getDailySummary, getRecords, createBook, getBudgets, autoCreateRecordFromImage,
    type MonthlySummary, type DailySummaryItem, type RecordItem, type Book, type Budget
} from '@/api/accounting'
import {
    ChevronDown, ChevronRight, Plus, Loader2, Zap
} from 'lucide-vue-next'
import AddRecordDialog from '@/components/accounting/AddRecordDialog.vue'
import * as echarts from 'echarts'

const store = useAccountingStore()
const router = useRouter()

const now = new Date()
const currentYear = ref(now.getFullYear())
const currentMonth = ref(now.getMonth() + 1)

const summary = ref<MonthlySummary>({ income: 0, expense: 0, balance: 0 })
const dailyData = ref<DailySummaryItem[]>([])
const recentRecords = ref<RecordItem[]>([])
const currentBudget = ref<Budget | null>(null)
const loading = ref(false)
const showAddDialog = ref(false)
const showBookDropdown = ref(false)
const showClipboardPrompt = ref(false)
const showIOSClipboardHint = ref(false)
const clipboardImageFile = ref<File | null>(null)
const clipboardPreviewUrl = ref('')
const uploadImageInputRef = ref<HTMLInputElement | null>(null)
const clipboardSubmitting = ref(false)
const clipboardStage = ref<'uploading' | 'recognizing' | 'writing'>('uploading')
const clipboardError = ref('')
const successTip = ref('')
let successTipTimer: ReturnType<typeof setTimeout> | null = null
const pageRef = ref<HTMLElement | null>(null)
const refreshing = ref(false)
const pullStartY = ref<number | null>(null)
const pullDistance = ref(0)
const isPulling = ref(false)
const pullThreshold = 72

// Create book
const showCreateBook = ref(false)
const newBookName = ref('')
const creatingBook = ref(false)

const chartRef = ref<HTMLElement | null>(null)
let chartInstance: echarts.ECharts | null = null

const monthLabel = computed(() => `${currentMonth.value}月`)

const pullHint = computed(() => {
    if (refreshing.value) return '刷新中...'
    return pullDistance.value >= pullThreshold ? '松开刷新' : '下拉刷新'
})

const formatMoney = (n: number) =>
    new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(n)

const formatDate = (iso: string) => {
    const d = new Date(iso)
    return `${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`
}

const loadData = async () => {
    if (!store.currentBookId) return
    loading.value = true
    try {
        const formattedMonth = `${currentYear.value}-${String(currentMonth.value).padStart(2, '0')}`
        const [sumRes, dailyRes, recRes, budgetRes] = await Promise.all([
            getRecordsSummary(store.currentBookId, currentYear.value, currentMonth.value),
            getDailySummary(store.currentBookId, currentYear.value, currentMonth.value),
            getRecords(store.currentBookId, 5),
            getBudgets(store.currentBookId, formattedMonth)
        ])
        summary.value = sumRes.data
        dailyData.value = dailyRes.data
        recentRecords.value = recRes.data
        const globalB = budgetRes.data.find(b => !b.category_id)
        currentBudget.value = globalB || null
        
        await nextTick()
        renderChart()
    } catch (e) {
        console.error('Failed to load data', e)
    } finally {
        loading.value = false
    }
}

const renderChart = () => {
    if (!chartRef.value) return
    if (!chartInstance) {
        chartInstance = echarts.init(chartRef.value)
    }
    const days = dailyData.value.map(d => {
        const parts = d.date.split('-')
        return `${parts[1]}.${parts[2]}`
    })
    const expenses = dailyData.value.map(d => d.expense)

    chartInstance.setOption({
        grid: { top: 10, right: 10, bottom: 25, left: 40 },
        xAxis: {
            type: 'category',
            data: days.length > 0 ? days : generateDayLabels(),
            axisLine: { show: false },
            axisTick: { show: false },
            axisLabel: { color: '#9ca3af', fontSize: 11 },
        },
        yAxis: {
            type: 'value',
            show: false,
        },
        series: [{
            type: 'line',
            data: expenses,
            smooth: true,
            symbol: 'none',
            lineStyle: { color: '#14b8a6', width: 2 },
            areaStyle: {
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                    { offset: 0, color: 'rgba(20,184,166,0.3)' },
                    { offset: 1, color: 'rgba(20,184,166,0.02)' },
                ]),
            },
        }],
    })
}

const generateDayLabels = () => {
    const daysInMonth = new Date(currentYear.value, currentMonth.value, 0).getDate()
    const labels = []
    const step = Math.max(1, Math.floor(daysInMonth / 7))
    for (let i = 1; i <= daysInMonth; i += step) {
        labels.push(`${currentMonth.value}.${i}`)
    }
    return labels
}

const handleCreateBook = async () => {
    if (!newBookName.value.trim()) return
    creatingBook.value = true
    try {
        const res = await createBook(newBookName.value.trim())
        store.books.push(res.data)
        store.setCurrentBook(res.data.id)
        newBookName.value = ''
        showCreateBook.value = false
        await loadData()
    } finally {
        creatingBook.value = false
    }
}

const switchBook = async (book: Book) => {
    store.setCurrentBook(book.id)
    showBookDropdown.value = false
    await loadData()
}

const onRecordAdded = () => {
    showAddDialog.value = false
    loadData()
}

const readClipboardImage = async (): Promise<File | null> => {
    if (typeof window === 'undefined' || !window.isSecureContext) {
        return null
    }

    if (!navigator.clipboard || typeof navigator.clipboard.read !== 'function') {
        return null
    }

    try {
        const items = await navigator.clipboard.read()
        for (const item of items) {
            const imageType = item.types.find(type => type.startsWith('image/'))
            if (!imageType) continue
            const blob = await item.getType(imageType)
            const ext = imageType.split('/')[1] || 'png'
            return new File([blob], `clipboard-${Date.now()}.${ext}`, { type: imageType })
        }
        return null
    } catch (error) {
        console.debug('read clipboard image failed', error)
        return null
    }
}

const showImageAutoAccountingPrompt = (file: File) => {
    clipboardImageFile.value = file
    releaseClipboardPreview()
    clipboardPreviewUrl.value = URL.createObjectURL(file)
    clipboardError.value = ''
    clipboardSubmitting.value = false
    clipboardStage.value = 'uploading'
    showClipboardPrompt.value = true
}

const openUploadImagePicker = () => {
    uploadImageInputRef.value?.click()
}

const handleUploadImageChange = (event: Event) => {
    const input = event.target as HTMLInputElement | null
    const file = input?.files?.[0] ?? null
    if (input) {
        input.value = ''
    }
    if (!file || !file.type.startsWith('image/')) {
        return
    }
    showIOSClipboardHint.value = false
    showImageAutoAccountingPrompt(file)
}

const releaseClipboardPreview = () => {
    if (clipboardPreviewUrl.value) {
        URL.revokeObjectURL(clipboardPreviewUrl.value)
    }
    clipboardPreviewUrl.value = ''
}

const closeClipboardPrompt = () => {
    showClipboardPrompt.value = false
    clipboardSubmitting.value = false
    clipboardError.value = ''
    clipboardImageFile.value = null
    releaseClipboardPreview()
}

const showSuccessTip = (text: string) => {
    successTip.value = text
    if (successTipTimer) {
        clearTimeout(successTipTimer)
    }
    successTipTimer = setTimeout(() => {
        successTip.value = ''
        successTipTimer = null
    }, 1800)
}

const handleImageFabClick = async () => {
    if (!store.currentBookId || clipboardSubmitting.value) return

    const image = await readClipboardImage()
    if (image) {
        showImageAutoAccountingPrompt(image)
        return
    }

    showIOSClipboardHint.value = true
}

const startClipboardAutoAccounting = async () => {
    if (!store.currentBookId || !clipboardImageFile.value || clipboardSubmitting.value) return

    clipboardSubmitting.value = true
    clipboardError.value = ''
    clipboardStage.value = 'uploading'
    await new Promise(resolve => window.setTimeout(resolve, 180))

    try {
        clipboardStage.value = 'recognizing'
        const res = await autoCreateRecordFromImage(store.currentBookId, clipboardImageFile.value)

        const recordId = Number(res.data?.record_id || 0)
        if (!Number.isFinite(recordId) || recordId <= 0) {
            throw new Error('未返回有效记录ID')
        }

        clipboardStage.value = 'writing'
        await new Promise(resolve => window.setTimeout(resolve, 160))

        closeClipboardPrompt()
        showSuccessTip('记账成功')
        await loadData()
        await router.push({ name: 'RecordDetail', params: { id: recordId } })
    } catch (error: any) {
        clipboardError.value = error?.response?.data?.detail || error?.message || '自动记账失败，请手动记账'
    } finally {
        clipboardSubmitting.value = false
    }
}

const useManualRecordFromClipboardPrompt = () => {
    closeClipboardPrompt()
    showAddDialog.value = true
}

const useManualRecordFromIOSHint = () => {
    showIOSClipboardHint.value = false
    showAddDialog.value = true
}

const uploadImageFromIOSHint = () => {
    showIOSClipboardHint.value = false
    openUploadImagePicker()
}

const getScrollParent = () => {
    let node: HTMLElement | null = pageRef.value?.parentElement || null
    while (node) {
        const style = window.getComputedStyle(node)
        const scrollable = /(auto|scroll)/.test(style.overflowY)
        if (scrollable && node.scrollHeight > node.clientHeight) {
            return node
        }
        node = node.parentElement
    }
    return null
}

const resetPull = () => {
    pullDistance.value = 0
    pullStartY.value = null
    isPulling.value = false
}

const triggerRefresh = async () => {
    if (refreshing.value) return
    refreshing.value = true
    try {
        if (!store.currentBookId) {
            await store.fetchBooks()
        }
        await loadData()
    } finally {
        refreshing.value = false
        resetPull()
    }
}

const handleTouchStart = (event: TouchEvent) => {
    if (refreshing.value) return
    const scrollParent = getScrollParent()
    if (scrollParent && scrollParent.scrollTop > 0) return
    pullStartY.value = event.touches[0]?.clientY ?? null
    isPulling.value = true
}

const handleTouchMove = (event: TouchEvent) => {
    if (!isPulling.value || pullStartY.value === null) return
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

const handleTouchEnd = () => {
    if (!isPulling.value) return
    if (pullDistance.value >= pullThreshold) {
        void triggerRefresh()
        return
    }
    resetPull()
}

watch(() => store.currentBookId, () => {
    if (store.currentBookId) loadData()
})

onMounted(async () => {
    await store.fetchBooks()
    if (store.currentBookId) {
        await loadData()
    }
})

onBeforeUnmount(() => {
    if (successTipTimer) {
        clearTimeout(successTipTimer)
        successTipTimer = null
    }
    releaseClipboardPreview()
})
</script>

<template>
  <div
    ref="pageRef"
    class="pb-20"
    @touchstart="handleTouchStart"
    @touchmove="handleTouchMove"
    @touchend="handleTouchEnd"
    @touchcancel="handleTouchEnd"
  >
    <input
      ref="uploadImageInputRef"
      type="file"
      accept="image/*"
      class="hidden"
      @change="handleUploadImageChange"
    >

    <div class="overflow-hidden transition-[height] duration-150" :style="{ height: `${Math.round(pullDistance)}px` }">
      <div class="h-full flex items-end justify-center pb-2 text-xs text-slate-500 gap-1">
        <Loader2 v-if="refreshing" class="w-3 h-3 animate-spin" />
        <span>{{ pullHint }}</span>
      </div>
    </div>

    <!-- Book Selector -->
    <div class="px-4 pt-4 pb-2 flex items-center justify-between">
      <div class="relative">
        <button
          @click="showBookDropdown = !showBookDropdown"
          class="flex items-center gap-1 text-lg font-bold text-theme-primary"
        >
          {{ store.books.find(b => b.id === store.currentBookId)?.name || '选择账本' }}
          <ChevronDown class="w-4 h-4" />
        </button>

        <!-- Dropdown -->
        <div
          v-if="showBookDropdown"
          class="absolute top-full left-0 mt-1 bg-white dark:bg-slate-800 border border-gray-200 dark:border-slate-700 rounded-xl shadow-lg py-1 min-w-[160px] z-30"
        >
          <button
            v-for="book in store.books"
            :key="book.id"
            @click="switchBook(book)"
            :class="[
              'w-full text-left px-4 py-2 text-sm transition',
              book.id === store.currentBookId
                ? 'bg-indigo-50 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400 font-medium'
                : 'text-theme-primary hover:bg-gray-50 dark:hover:bg-slate-700'
            ]"
          >
            {{ book.name }}
          </button>
          <div class="border-t border-gray-100 dark:border-slate-700 mt-1 pt-1">
            <button
              @click="showCreateBook = true; showBookDropdown = false"
              class="w-full text-left px-4 py-2 text-sm text-indigo-500 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 flex items-center gap-2"
            >
              <Plus class="w-3.5 h-3.5" /> 新建账本
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Create Book Prompt (when no books) -->
    <div v-if="store.books.length === 0 && !store.loading" class="px-4 py-12 text-center">
      <div class="w-20 h-20 mx-auto mb-4 rounded-full bg-indigo-50 dark:bg-indigo-900/30 flex items-center justify-center">
        <Plus class="w-8 h-8 text-indigo-500" />
      </div>
      <h3 class="text-lg font-semibold text-theme-primary mb-2">还没有账本</h3>
      <p class="text-theme-muted text-sm mb-4">创建一个账本开始记账吧</p>
      <button
        @click="showCreateBook = true"
        class="px-6 py-2.5 bg-indigo-500 hover:bg-indigo-600 text-white font-medium rounded-xl transition shadow-sm"
      >
        创建第一个账本
      </button>
    </div>

    <!-- Create Book Modal -->
    <div
      v-if="showCreateBook"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      @click.self="showCreateBook = false"
    >
      <div class="bg-white dark:bg-slate-800 rounded-2xl p-6 w-[320px] shadow-xl">
        <h3 class="text-lg font-semibold text-theme-primary mb-4">新建账本</h3>
        <form @submit.prevent="handleCreateBook">
          <input
            v-model="newBookName"
            type="text"
            placeholder="输入账本名称…"
            class="w-full px-4 py-3 rounded-xl border border-gray-200 dark:border-slate-600 bg-gray-50 dark:bg-slate-700 text-theme-primary focus:outline-none focus:ring-2 focus:ring-indigo-500 mb-4"
            autofocus
          />
          <div class="flex gap-3">
            <button
              type="button"
              @click="showCreateBook = false"
              class="flex-1 py-2.5 border border-gray-200 dark:border-slate-600 rounded-xl text-theme-secondary font-medium hover:bg-gray-50 dark:hover:bg-slate-700 transition"
            >
              取消
            </button>
            <button
              type="submit"
              :disabled="creatingBook || !newBookName.trim()"
              class="flex-1 py-2.5 bg-indigo-500 hover:bg-indigo-600 text-white font-medium rounded-xl transition disabled:opacity-50"
            >
              <Loader2 v-if="creatingBook" class="w-4 h-4 animate-spin mx-auto" />
              <span v-else>创建</span>
            </button>
          </div>
        </form>
      </div>
    </div>

    <!-- Content (when has books) -->
    <template v-if="store.currentBookId">
      <!-- Monthly Summary Card -->
      <div class="mx-4 mt-2 rounded-2xl bg-white dark:bg-slate-800 shadow-sm border border-gray-100 dark:border-slate-700 overflow-hidden">
        <div class="p-4">
          <RouterLink to="/accounting/stats" class="flex items-center justify-between mb-2 cursor-pointer hover:opacity-80 transition">
            <span class="text-sm text-theme-muted">{{ monthLabel }}支出</span>
            <ChevronRight class="w-4 h-4 text-indigo-500" />
          </RouterLink>
          <div class="flex items-baseline justify-between">
            <div>
              <span class="text-3xl font-bold text-rose-500">¥{{ formatMoney(summary.expense) }}</span>
            </div>
            <span class="text-sm text-theme-muted">结余 {{ formatMoney(summary.balance) }}</span>
          </div>
        </div>
        <!-- Chart -->
        <div ref="chartRef" class="w-full h-[120px]"></div>
      </div>

      <!-- Recent Transactions -->
      <div class="mx-4 mt-4 rounded-2xl bg-white dark:bg-slate-800 shadow-sm border border-gray-100 dark:border-slate-700">
        <RouterLink to="/accounting/records" class="flex items-center justify-between p-4 pb-2 cursor-pointer hover:opacity-80 transition">
          <h3 class="font-semibold text-theme-primary">最近交易</h3>
          <ChevronRight class="w-4 h-4 text-indigo-500" />
        </RouterLink>

        <div v-if="loading" class="p-8 text-center text-theme-muted">
          <Loader2 class="w-5 h-5 animate-spin mx-auto mb-2 text-indigo-400" />
          加载中...
        </div>

        <div v-else-if="recentRecords.length === 0" class="p-8 text-center text-theme-muted text-sm">
          暂无记录，点击右下角 + 开始记账
        </div>

        <ul v-else class="divide-y divide-gray-50 dark:divide-slate-700/50">
          <li v-for="rec in recentRecords" :key="rec.id" class="px-4 py-3">
            <RouterLink :to="`/accounting/records/${rec.id}`" class="flex items-start justify-between hover:opacity-80 transition">
              <!-- Left -->
              <div class="flex items-start gap-3">
                <div class="w-2 h-2 mt-2 rounded-full bg-indigo-400 flex-shrink-0" />
                <div>
                  <p class="font-medium text-theme-primary text-sm">{{ rec.category || rec.payee || rec.remark || '未分类' }}</p>
                  <p class="text-xs text-theme-muted mt-0.5">
                    {{ formatDate(rec.record_time) }}
                    <template v-if="rec.remark"> · {{ rec.remark }}</template>
                  </p>
                </div>
              </div>
              <!-- Right -->
              <div class="text-right flex-shrink-0">
                <p :class="[
                  'font-semibold text-sm',
                  rec.type === '收入' ? 'text-indigo-500' : 'text-theme-primary'
                ]">
                  {{ rec.type === '收入' ? '+' : '' }}¥{{ formatMoney(rec.amount) }}
                </p>
                <p v-if="rec.account" class="text-[10px] text-theme-muted mt-0.5 px-1.5 py-0.5 rounded bg-gray-50 dark:bg-slate-700 inline-block">
                  {{ rec.account }}
                </p>
              </div>
            </RouterLink>
          </li>
        </ul>
      </div>

      <div class="mx-4 mt-4 rounded-2xl bg-white dark:bg-slate-800 shadow-sm border border-gray-100 dark:border-slate-700 p-4">
        <RouterLink to="/accounting/budgets" class="flex items-center justify-between mb-4 cursor-pointer hover:opacity-80 transition">
          <h3 class="font-semibold text-theme-primary">{{ monthLabel }}预算</h3>
          <ChevronRight class="w-4 h-4 text-indigo-500" />
        </RouterLink>
        <div class="flex items-center justify-around">
          <div class="text-center">
            <p class="text-xs text-theme-muted">支出</p>
            <p class="text-lg font-bold text-theme-primary">{{ formatMoney(summary.expense) }}</p>
          </div>
          <!-- Ring -->
          <RouterLink to="/accounting/budgets" class="w-24 h-24 rounded-full border-[8px] flex items-center justify-center relative cursor-pointer hover:opacity-80 transition"
            :class="[(summary.expense / (currentBudget?.total_amount || 1)) > 0.9 ? 'border-rose-400' : (currentBudget ? 'border-indigo-400' : 'border-gray-100 dark:border-slate-700')]">
            <div class="text-center">
              <p class="text-[10px] text-theme-muted">剩余</p>
              <p :class="['text-sm font-bold', currentBudget ? ((currentBudget.total_amount - summary.expense) < 0 ? 'text-rose-500' : 'text-theme-primary') : 'text-theme-primary']">
                {{ currentBudget ? formatMoney(currentBudget.total_amount - summary.expense) : '点击添加' }}
              </p>
              <p v-if="currentBudget" class="text-[10px] text-theme-muted">总额{{ formatMoney(currentBudget.total_amount) }}</p>
            </div>
          </RouterLink>
          <div class="text-center">
            <p class="text-xs text-theme-muted">剩余日均</p>
            <p class="text-lg font-bold text-theme-primary">
              {{ currentBudget && (currentBudget.total_amount - summary.expense) > 0 ? formatMoney((currentBudget.total_amount - summary.expense) / (new Date(currentYear, currentMonth, 0).getDate() - now.getDate() + 1)) : '0' }}
            </p>
          </div>
        </div>
      </div>
    </template>

    <!-- FAB -->
    <div
      v-if="store.currentBookId"
      class="fixed bottom-20 right-6 z-30 flex items-center gap-3"
      style="-webkit-touch-callout: none; -webkit-user-select: none; user-select: none;"
    >
      <button
        @click="handleImageFabClick"
        @contextmenu.prevent
        class="w-12 h-12 rounded-full bg-amber-400 hover:bg-amber-500 text-slate-900 shadow-lg hover:shadow-xl flex items-center justify-center transition-all active:scale-95"
        aria-label="图片识别记账"
      >
        <Zap class="w-5 h-5" />
      </button>
      <button
        @click="showAddDialog = true"
        @contextmenu.prevent
        class="w-14 h-14 rounded-full bg-indigo-500 hover:bg-indigo-600 text-white shadow-lg hover:shadow-xl flex items-center justify-center transition-all active:scale-95"
        aria-label="手动记账"
      >
        <Plus class="w-7 h-7" />
      </button>
    </div>

    <div
      v-if="successTip"
      class="fixed top-20 left-1/2 -translate-x-1/2 z-50 px-4 py-2 rounded-xl bg-slate-900 text-white text-sm shadow-lg"
    >
      {{ successTip }}
    </div>

    <div
      v-if="showClipboardPrompt"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4"
      @click.self="!clipboardSubmitting && closeClipboardPrompt()"
    >
      <div class="w-full max-w-[360px] rounded-2xl bg-white dark:bg-slate-800 border border-gray-100 dark:border-slate-700 shadow-xl p-4">
        <template v-if="!clipboardSubmitting">
          <h3 class="text-base font-semibold text-theme-primary mb-2">已选择图片</h3>
          <p class="text-sm text-theme-muted">是否直接让 AI 识别并记账？</p>

          <img
            v-if="clipboardPreviewUrl"
            :src="clipboardPreviewUrl"
            alt="clipboard preview"
            class="mt-3 w-full max-h-44 object-contain rounded-xl border border-gray-100 dark:border-slate-700 bg-slate-50 dark:bg-slate-900"
          />

          <p v-if="clipboardError" class="mt-3 text-sm text-rose-500">{{ clipboardError }}</p>

          <div class="mt-4 flex gap-2">
            <button
              @click="closeClipboardPrompt"
              class="flex-1 h-10 rounded-xl border border-gray-200 dark:border-slate-600 text-theme-secondary"
            >
              取消
            </button>
            <button
              @click="useManualRecordFromClipboardPrompt"
              class="flex-1 h-10 rounded-xl border border-indigo-200 text-indigo-500"
            >
              手动记账
            </button>
            <button
              @click="startClipboardAutoAccounting"
              class="flex-1 h-10 rounded-xl bg-indigo-500 text-white"
            >
              识别并记账
            </button>
          </div>
        </template>

        <template v-else>
          <h3 class="text-base font-semibold text-theme-primary mb-3">正在处理</h3>
          <div class="space-y-2 text-sm">
            <div class="flex items-center justify-between rounded-xl px-3 py-2 bg-slate-50 dark:bg-slate-900">
              <span>上传中</span>
              <Loader2 v-if="clipboardStage === 'uploading'" class="w-4 h-4 animate-spin text-indigo-500" />
              <span v-else class="text-emerald-500">完成</span>
            </div>
            <div class="flex items-center justify-between rounded-xl px-3 py-2 bg-slate-50 dark:bg-slate-900">
              <span>AI识别中</span>
              <Loader2 v-if="clipboardStage === 'recognizing'" class="w-4 h-4 animate-spin text-indigo-500" />
              <span v-else-if="clipboardStage === 'writing'" class="text-emerald-500">完成</span>
              <span v-else class="text-theme-muted">等待</span>
            </div>
            <div class="flex items-center justify-between rounded-xl px-3 py-2 bg-slate-50 dark:bg-slate-900">
              <span>写入账本</span>
              <Loader2 v-if="clipboardStage === 'writing'" class="w-4 h-4 animate-spin text-indigo-500" />
              <span v-else class="text-theme-muted">等待</span>
            </div>
          </div>
        </template>
      </div>
    </div>

    <div
      v-if="showIOSClipboardHint"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4"
      @click.self="showIOSClipboardHint = false"
    >
      <div class="w-full max-w-[360px] rounded-2xl bg-white dark:bg-slate-800 border border-gray-100 dark:border-slate-700 shadow-xl p-4">
        <h3 class="text-base font-semibold text-theme-primary mb-2">未检测到可读取的剪贴板图片</h3>
        <p class="text-sm text-theme-muted">你可以上传截图识别，或切换为手动记账。</p>
        <div class="mt-4 flex gap-2 justify-end">
          <button
            @click="uploadImageFromIOSHint"
            class="px-4 h-10 rounded-xl border border-indigo-200 text-indigo-500"
          >
            上传图片识别
          </button>
          <button
            @click="showIOSClipboardHint = false"
            class="px-4 h-10 rounded-xl border border-gray-200 dark:border-slate-600 text-theme-secondary"
          >
            取消
          </button>
          <button
            @click="useManualRecordFromIOSHint"
            class="px-4 h-10 rounded-xl bg-indigo-500 text-white"
          >
            手动记账
          </button>
        </div>
      </div>
    </div>

    <!-- Add Record Dialog -->
    <AddRecordDialog
      v-if="showAddDialog"
      :book-id="store.currentBookId!"
      @close="showAddDialog = false"
      @saved="onRecordAdded"
    />
  </div>
</template>
