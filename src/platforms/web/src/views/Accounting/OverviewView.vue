<script setup lang="ts">
import { ref, onMounted, computed, watch, nextTick } from 'vue'
import { useAccountingStore } from '@/stores/accounting'
import {
    getRecordsSummary, getDailySummary, getRecords, createBook, getBudgets,
    type MonthlySummary, type DailySummaryItem, type RecordItem, type Book, type Budget
} from '@/api/accounting'
import {
    ChevronDown, ChevronRight, Plus, Loader2
} from 'lucide-vue-next'
import AddRecordDialog from '@/components/accounting/AddRecordDialog.vue'
import * as echarts from 'echarts'

const store = useAccountingStore()

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

// Create book
const showCreateBook = ref(false)
const newBookName = ref('')
const creatingBook = ref(false)

const chartRef = ref<HTMLElement | null>(null)
let chartInstance: echarts.ECharts | null = null

const monthLabel = computed(() => `${currentMonth.value}月`)

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

watch(() => store.currentBookId, () => {
    if (store.currentBookId) loadData()
})

onMounted(async () => {
    await store.fetchBooks()
    if (store.currentBookId) {
        await loadData()
    }
})
</script>

<template>
  <div class="pb-20">
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
                ? 'bg-teal-50 dark:bg-teal-900/30 text-teal-600 dark:text-teal-400 font-medium'
                : 'text-theme-primary hover:bg-gray-50 dark:hover:bg-slate-700'
            ]"
          >
            {{ book.name }}
          </button>
          <div class="border-t border-gray-100 dark:border-slate-700 mt-1 pt-1">
            <button
              @click="showCreateBook = true; showBookDropdown = false"
              class="w-full text-left px-4 py-2 text-sm text-teal-500 hover:bg-teal-50 dark:hover:bg-teal-900/20 flex items-center gap-2"
            >
              <Plus class="w-3.5 h-3.5" /> 新建账本
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Create Book Prompt (when no books) -->
    <div v-if="store.books.length === 0 && !store.loading" class="px-4 py-12 text-center">
      <div class="w-20 h-20 mx-auto mb-4 rounded-full bg-teal-50 dark:bg-teal-900/30 flex items-center justify-center">
        <Plus class="w-8 h-8 text-teal-500" />
      </div>
      <h3 class="text-lg font-semibold text-theme-primary mb-2">还没有账本</h3>
      <p class="text-theme-muted text-sm mb-4">创建一个账本开始记账吧</p>
      <button
        @click="showCreateBook = true"
        class="px-6 py-2.5 bg-teal-500 hover:bg-teal-600 text-white font-medium rounded-xl transition shadow-sm"
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
            class="w-full px-4 py-3 rounded-xl border border-gray-200 dark:border-slate-600 bg-gray-50 dark:bg-slate-700 text-theme-primary focus:outline-none focus:ring-2 focus:ring-teal-500 mb-4"
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
              class="flex-1 py-2.5 bg-teal-500 hover:bg-teal-600 text-white font-medium rounded-xl transition disabled:opacity-50"
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
            <ChevronRight class="w-4 h-4 text-teal-500" />
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
          <ChevronRight class="w-4 h-4 text-teal-500" />
        </RouterLink>

        <div v-if="loading" class="p-8 text-center text-theme-muted">
          <Loader2 class="w-5 h-5 animate-spin mx-auto mb-2 text-teal-400" />
          加载中...
        </div>

        <div v-else-if="recentRecords.length === 0" class="p-8 text-center text-theme-muted text-sm">
          暂无记录，点击右下角 + 开始记账
        </div>

        <ul v-else class="divide-y divide-gray-50 dark:divide-slate-700/50">
          <li v-for="rec in recentRecords" :key="rec.id" class="px-4 py-3">
            <div class="flex items-start justify-between">
              <!-- Left -->
              <div class="flex items-start gap-3">
                <div class="w-2 h-2 mt-2 rounded-full bg-teal-400 flex-shrink-0" />
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
                  rec.type === '收入' ? 'text-teal-500' : 'text-theme-primary'
                ]">
                  {{ rec.type === '收入' ? '+' : '' }}¥{{ formatMoney(rec.amount) }}
                </p>
                <p v-if="rec.account" class="text-[10px] text-theme-muted mt-0.5 px-1.5 py-0.5 rounded bg-gray-50 dark:bg-slate-700 inline-block">
                  {{ rec.account }}
                </p>
              </div>
            </div>
          </li>
        </ul>
      </div>

      <div class="mx-4 mt-4 rounded-2xl bg-white dark:bg-slate-800 shadow-sm border border-gray-100 dark:border-slate-700 p-4">
        <RouterLink to="/accounting/budgets" class="flex items-center justify-between mb-4 cursor-pointer hover:opacity-80 transition">
          <h3 class="font-semibold text-theme-primary">{{ monthLabel }}预算</h3>
          <ChevronRight class="w-4 h-4 text-teal-500" />
        </RouterLink>
        <div class="flex items-center justify-around">
          <div class="text-center">
            <p class="text-xs text-theme-muted">支出</p>
            <p class="text-lg font-bold text-theme-primary">{{ formatMoney(summary.expense) }}</p>
          </div>
          <!-- Ring -->
          <RouterLink to="/accounting/budgets" class="w-24 h-24 rounded-full border-[8px] flex items-center justify-center relative cursor-pointer hover:opacity-80 transition"
            :class="[(summary.expense / (currentBudget?.total_amount || 1)) > 0.9 ? 'border-rose-400' : (currentBudget ? 'border-teal-400' : 'border-gray-100 dark:border-slate-700')]">
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
    <button
      v-if="store.currentBookId"
      @click="showAddDialog = true"
      class="fixed bottom-20 right-6 w-14 h-14 rounded-full bg-teal-500 hover:bg-teal-600 text-white shadow-lg hover:shadow-xl flex items-center justify-center transition-all z-30 active:scale-95"
    >
      <Plus class="w-7 h-7" />
    </button>

    <!-- Add Record Dialog -->
    <AddRecordDialog
      v-if="showAddDialog"
      :book-id="store.currentBookId!"
      @close="showAddDialog = false"
      @saved="onRecordAdded"
    />
  </div>
</template>
