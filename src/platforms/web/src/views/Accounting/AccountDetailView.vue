<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
    getAccountDetail, getAccountRecords, getAccountBalanceTrend,
    updateAccount, adjustAccountBalance, deleteAccount,
    type AccountItem, type RecordItem, type BalanceTrendItem
} from '@/api/accounting'
import {
    ArrowLeft, Trash2, Pencil, X, Loader2, ChevronRight, Plus
} from 'lucide-vue-next'
import * as echarts from 'echarts'

const router = useRouter()
const route = useRoute()
const accountId = Number(route.params.id)

const account = ref<AccountItem | null>(null)
const records = ref<RecordItem[]>([])
const trendData = ref<BalanceTrendItem[]>([])
const loading = ref(false)

// Edit modal
const showEdit = ref(false)
const editName = ref('')
const editType = ref('')
const saving = ref(false)

// Balance adjust modal
const showAdjust = ref(false)
const adjustTarget = ref(0)
const adjustMethod = ref('差额补记收支')
const adjusting = ref(false)

// Delete confirmation
const showDeleteConfirm = ref(false)

const chartRef = ref<HTMLElement | null>(null)
let chartInstance: echarts.ECharts | null = null

const accountTypes = ['现金', '储蓄卡', '信用卡', '支付宝', '微信', '投资', '其他']
const adjustMethods = [
    { value: '差额补记收支', desc: '添加收入或支出，使余额达到指定金额。' },
    { value: '更改当前余额', desc: '设置初始金额为指定金额，设置余额起始时间为当前时间，余额趋势统计将丢失。' },
    { value: '设置初始余额', desc: '设置初始金额为指定金额。' },
]

const formatMoney = (n: number) =>
    new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(n)

const formatDate = (iso: string) => {
    const d = new Date(iso)
    return `${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`
}

const loadData = async () => {
    loading.value = true
    try {
        const [detailRes, recordsRes, trendRes] = await Promise.all([
            getAccountDetail(accountId),
            getAccountRecords(accountId, 20),
            getAccountBalanceTrend(accountId, 30),
        ])
        account.value = detailRes.data
        records.value = recordsRes.data
        trendData.value = trendRes.data
        await nextTick()
        renderChart()
    } finally {
        loading.value = false
    }
}

const renderChart = () => {
    if (!chartRef.value) return
    if (!chartInstance) chartInstance = echarts.init(chartRef.value)

    const dates = trendData.value.map(t => {
        const parts = t.date.split('-')
        return `${parts[1]}.${parts[2]}`
    })
    const balances = trendData.value.map(t => t.balance)

    chartInstance.setOption({
        grid: { top: 20, right: 15, bottom: 25, left: 50 },
        xAxis: {
            type: 'category',
            data: dates.length > 0 ? dates : [''],
            axisLine: { show: false },
            axisTick: { show: false },
            axisLabel: { color: '#9ca3af', fontSize: 10 },
        },
        yAxis: {
            type: 'value',
            axisLine: { show: false },
            axisTick: { show: false },
            splitLine: { lineStyle: { color: '#f3f4f6' } },
            axisLabel: {
                color: '#9ca3af',
                fontSize: 10,
                formatter: (v: number) => v >= 10000 ? `${(v / 10000).toFixed(0)}w` : `${v}`,
            },
        },
        series: [{
            type: 'line',
            data: balances,
            smooth: true,
            symbol: 'none',
            lineStyle: { color: '#14b8a6', width: 2 },
            areaStyle: {
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                    { offset: 0, color: 'rgba(20,184,166,0.25)' },
                    { offset: 1, color: 'rgba(20,184,166,0.02)' },
                ]),
            },
        }],
    })
}

const openEdit = () => {
    if (!account.value) return
    editName.value = account.value.name
    editType.value = account.value.type
    showEdit.value = true
}

const handleSaveEdit = async () => {
    if (!account.value) return
    saving.value = true
    try {
        await updateAccount(accountId, {
            name: editName.value,
            type: editType.value,
        })
        await loadData()
        showEdit.value = false
    } finally {
        saving.value = false
    }
}

const openAdjust = () => {
    if (!account.value) return
    adjustTarget.value = account.value.balance
    adjustMethod.value = '差额补记收支'
    showAdjust.value = true
}

const handleAdjust = async () => {
    adjusting.value = true
    try {
        await adjustAccountBalance(accountId, {
            target_balance: adjustTarget.value,
            method: adjustMethod.value,
        })
        await loadData()
        showAdjust.value = false
    } catch (e: any) {
        alert(e.response?.data?.detail || '调整失败')
    } finally {
        adjusting.value = false
    }
}

const handleDelete = async () => {
    try {
        await deleteAccount(accountId)
        router.push('/accounting/assets')
    } catch (e: any) {
        alert(e.response?.data?.detail || '删除失败')
    }
}

onMounted(loadData)
</script>

<template>
  <div class="pb-4">
    <!-- Custom Header (override AccountingLayout) -->
    <div class="sticky top-0 z-20 bg-gradient-to-r from-teal-500 to-teal-400 px-4 py-3 flex items-center justify-between shadow-sm">
      <button @click="router.push('/accounting/assets')" class="flex items-center gap-1.5 text-white/90 hover:text-white transition">
        <ArrowLeft class="w-4 h-4" />
        <span class="font-semibold">{{ account?.name || '账户详情' }}</span>
      </button>
      <button @click="showDeleteConfirm = true" class="text-white/80 hover:text-white transition">
        <Trash2 class="w-5 h-5" />
      </button>
    </div>

    <!-- Loading -->
    <div v-if="loading && !account" class="p-12 text-center">
      <Loader2 class="w-5 h-5 animate-spin mx-auto mb-2 text-teal-400" />
    </div>

    <template v-if="account">
      <!-- Balance Card -->
      <div class="mx-4 mt-4 rounded-2xl bg-white dark:bg-slate-800 shadow-sm border border-gray-100 dark:border-slate-700 p-5">
        <div class="flex items-center justify-between mb-1">
          <span class="text-sm text-theme-muted">当前余额</span>
        </div>
        <div class="flex items-center gap-3">
          <span :class="['text-3xl font-bold', account.balance >= 0 ? 'text-teal-500' : 'text-rose-500']">
            ¥{{ formatMoney(account.balance) }}
          </span>
          <button @click="openAdjust" class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-slate-700 transition">
            <Pencil class="w-4 h-4 text-theme-muted" />
          </button>
        </div>
        <p class="text-xs text-theme-muted mt-2">当前余额 = 初始余额 + 余额起始时间后的交易金额的和</p>
      </div>

      <!-- Trend Chart -->
      <div class="mx-4 mt-4 rounded-2xl bg-white dark:bg-slate-800 shadow-sm border border-gray-100 dark:border-slate-700 p-4">
        <div class="flex items-center justify-between mb-2">
          <h3 class="font-semibold text-theme-primary">余额趋势</h3>
          <div class="flex gap-1 text-xs">
            <span class="px-2 py-0.5 rounded bg-teal-50 dark:bg-teal-900/30 text-teal-500 font-medium">日</span>
            <span class="px-2 py-0.5 rounded text-theme-muted">月</span>
          </div>
        </div>
        <div ref="chartRef" class="w-full h-[160px]"></div>
      </div>

      <!-- Recent Records -->
      <div class="mx-4 mt-4 rounded-2xl bg-white dark:bg-slate-800 shadow-sm border border-gray-100 dark:border-slate-700">
        <div class="flex items-center justify-between p-4 pb-2">
          <h3 class="font-semibold text-theme-primary">最近交易</h3>
          <Plus class="w-5 h-5 text-theme-muted" />
        </div>

        <div v-if="records.length === 0" class="p-6 text-center text-theme-muted text-sm">暂无交易记录</div>

        <ul v-else class="divide-y divide-gray-50 dark:divide-slate-700/50">
          <li v-for="rec in records" :key="rec.id" class="px-4 py-3">
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-3">
                <div class="w-2 h-2 rounded-full bg-teal-400 flex-shrink-0" />
                <div>
                  <p class="font-medium text-theme-primary text-sm">{{ rec.category || rec.remark || '余额变更' }}</p>
                  <p class="text-xs text-theme-muted">{{ formatDate(rec.record_time) }}</p>
                </div>
              </div>
              <div class="text-right">
                <span :class="['font-semibold text-sm', rec.type === '收入' ? 'text-teal-500' : 'text-rose-500']">
                  {{ rec.type === '收入' ? '+' : '-' }}¥{{ formatMoney(rec.amount) }}
                </span>
              </div>
            </div>
          </li>
        </ul>

        <button class="w-full p-3 text-sm text-teal-500 font-medium border-t border-gray-50 dark:border-slate-700/50">
          所有交易
        </button>
      </div>

      <!-- Account Settings -->
      <div class="mx-4 mt-4 rounded-2xl bg-white dark:bg-slate-800 shadow-sm border border-gray-100 dark:border-slate-700 overflow-hidden">
        <div class="px-4 py-3 flex items-center justify-between border-b border-gray-50 dark:border-slate-700/50">
          <span class="text-sm text-theme-primary">计入资产</span>
          <div class="w-12 h-7 rounded-full bg-teal-500 relative cursor-pointer">
            <div class="absolute right-0.5 top-0.5 w-6 h-6 rounded-full bg-white shadow" />
          </div>
        </div>
        <div @click="openEdit" class="px-4 py-3 flex items-center justify-between border-b border-gray-50 dark:border-slate-700/50 cursor-pointer hover:bg-gray-50 dark:hover:bg-slate-700 transition">
          <span class="text-sm text-theme-primary">类型</span>
          <div class="flex items-center gap-1">
            <span class="text-sm text-theme-muted">{{ account.type }}</span>
            <ChevronRight class="w-4 h-4 text-theme-muted" />
          </div>
        </div>
        <div @click="openEdit" class="px-4 py-3 flex items-center justify-between border-b border-gray-50 dark:border-slate-700/50 cursor-pointer hover:bg-gray-50 dark:hover:bg-slate-700 transition">
          <span class="text-sm text-theme-primary">名称</span>
          <div class="flex items-center gap-1">
            <span class="text-sm text-theme-muted">{{ account.name }}</span>
            <ChevronRight class="w-4 h-4 text-theme-muted" />
          </div>
        </div>
        <div class="px-4 py-3 flex items-center justify-between">
          <span class="text-sm text-theme-primary">货币</span>
          <span class="text-sm text-theme-muted">人民币</span>
        </div>
      </div>
    </template>

    <!-- Edit Account Modal -->
    <div v-if="showEdit" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="showEdit = false">
      <div class="bg-white dark:bg-slate-800 rounded-2xl p-6 w-[320px] shadow-xl">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-lg font-semibold text-theme-primary">编辑账户</h3>
          <button @click="showEdit = false"><X class="w-5 h-5 text-theme-muted" /></button>
        </div>
        <form @submit.prevent="handleSaveEdit" class="space-y-3">
          <div>
            <label class="text-xs text-theme-muted font-medium">名称</label>
            <input v-model="editName" type="text" class="w-full mt-1 px-3 py-2.5 rounded-xl border border-gray-200 dark:border-slate-600 bg-gray-50 dark:bg-slate-700 text-theme-primary text-sm focus:outline-none focus:ring-2 focus:ring-teal-500" />
          </div>
          <div>
            <label class="text-xs text-theme-muted font-medium">类型</label>
            <select v-model="editType" class="w-full mt-1 px-3 py-2.5 rounded-xl border border-gray-200 dark:border-slate-600 bg-gray-50 dark:bg-slate-700 text-theme-primary text-sm focus:outline-none focus:ring-2 focus:ring-teal-500">
              <option v-for="t in accountTypes" :key="t" :value="t">{{ t }}</option>
            </select>
          </div>
          <button type="submit" :disabled="saving" class="w-full py-2.5 bg-teal-500 hover:bg-teal-600 text-white font-medium rounded-xl transition disabled:opacity-50">
            <Loader2 v-if="saving" class="w-4 h-4 animate-spin mx-auto" />
            <span v-else>保存</span>
          </button>
        </form>
      </div>
    </div>

    <!-- Balance Adjust Modal -->
    <div v-if="showAdjust" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="showAdjust = false">
      <div class="bg-white dark:bg-slate-800 rounded-2xl p-6 w-[340px] shadow-xl">
        <h3 class="text-lg font-semibold text-theme-primary mb-4">余额校正</h3>
        <input
          v-model.number="adjustTarget"
          type="number"
          step="0.01"
          class="w-full px-4 py-3 rounded-xl border border-gray-200 dark:border-slate-600 bg-gray-50 dark:bg-slate-700 text-theme-primary text-lg font-bold focus:outline-none focus:ring-2 focus:ring-teal-500 mb-4"
        />
        <div class="space-y-3 mb-5">
          <label
            v-for="m in adjustMethods"
            :key="m.value"
            class="flex items-start gap-3 cursor-pointer"
          >
            <div :class="['w-5 h-5 mt-0.5 rounded border-2 flex-shrink-0 flex items-center justify-center transition',
              adjustMethod === m.value
                ? 'border-teal-500 bg-teal-500'
                : 'border-gray-300 dark:border-slate-600'
            ]">
              <svg v-if="adjustMethod === m.value" class="w-3.5 h-3.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" /></svg>
            </div>
            <div @click="adjustMethod = m.value">
              <p class="text-sm font-medium text-theme-primary">{{ m.value }}</p>
              <p class="text-xs text-theme-muted">{{ m.desc }}</p>
            </div>
          </label>
        </div>
        <div class="flex gap-3">
          <button
            @click="showAdjust = false"
            class="flex-1 py-2.5 border border-gray-200 dark:border-slate-600 rounded-xl text-theme-secondary font-medium hover:bg-gray-50 dark:hover:bg-slate-700 transition"
          >取消</button>
          <button
            @click="handleAdjust"
            :disabled="adjusting"
            class="flex-1 py-2.5 text-teal-500 font-bold rounded-xl transition hover:bg-teal-50 dark:hover:bg-teal-900/20 disabled:opacity-50"
          >
            <Loader2 v-if="adjusting" class="w-4 h-4 animate-spin mx-auto" />
            <span v-else>完成</span>
          </button>
        </div>
      </div>
    </div>

    <!-- Delete Confirm -->
    <div v-if="showDeleteConfirm" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="showDeleteConfirm = false">
      <div class="bg-white dark:bg-slate-800 rounded-2xl p-6 w-[300px] shadow-xl text-center">
        <h3 class="text-lg font-semibold text-theme-primary mb-2">删除账户</h3>
        <p class="text-sm text-theme-muted mb-5">确定要删除「{{ account?.name }}」吗？此操作不可撤销。</p>
        <div class="flex gap-3">
          <button @click="showDeleteConfirm = false" class="flex-1 py-2.5 border border-gray-200 dark:border-slate-600 rounded-xl text-theme-secondary font-medium">取消</button>
          <button @click="handleDelete" class="flex-1 py-2.5 bg-rose-500 hover:bg-rose-600 text-white font-medium rounded-xl transition">删除</button>
        </div>
      </div>
    </div>
  </div>
</template>
