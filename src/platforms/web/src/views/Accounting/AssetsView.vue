<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, computed, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useAccountingStore } from '@/stores/accounting'
import {
    getAccounts,
    createAccount,
    getBalanceTrend,
    type AccountItem,
    type BalanceTrendScope,
    type ScopedBalanceTrendItem,
} from '@/api/accounting'
import { appendOperationLog } from '@/utils/accountingLocal'
import {
    Plus, Eye, EyeOff, Loader2, Banknote, CreditCard, Landmark, X, ChevronRight,
    Wallet, TrendingUp, ArrowDownLeft, ArrowUpRight
} from 'lucide-vue-next'
import * as echarts from 'echarts'
import { toIsoLocal } from './statsRange'
import netWorthBg from '@/assets/net-worth-ocean.svg'

const router = useRouter()


const store = useAccountingStore()
const accounts = ref<AccountItem[]>([])
const loading = ref(false)
const showAmount = ref(true)
const showAddAccount = ref(false)
const chartRef = ref<HTMLElement | null>(null)
const netTrendRows = ref<ScopedBalanceTrendItem[]>([])
const netTrendLoading = ref(false)
const pageRef = ref<HTMLElement | null>(null)
const refreshing = ref(false)
const pullStartY = ref<number | null>(null)
const pullDistance = ref(0)
const isPulling = ref(false)
const pullThreshold = 72
let trendChart: echarts.ECharts | null = null
let trendObserver: ResizeObserver | null = null
let delayedRenderTimer: ReturnType<typeof setTimeout> | null = null

// New account form
const newAccName = ref('')
const newAccType = ref('储蓄卡')
const newAccBalance = ref(0)
const creatingAcc = ref(false)

const accountTypes = ['网络支付', '信用卡', '储蓄卡', '投资账户', '现金', '充值卡', '应收账户', '应付账户']

// Grouped accounts
const grouped = computed(() => {
    const groups: Record<string, AccountItem[]> = {}
    for (const acc of accounts.value) {
        if (!groups[acc.type]) groups[acc.type] = []
        groups[acc.type]!.push(acc)
    }
    return groups
})

const groupTotal = (items: AccountItem[]) =>
    items.reduce((sum, a) => sum + a.balance, 0)

const includedAccounts = computed(() => {
    return accounts.value.filter(account => account.include_in_assets)
})

const totalAssets = computed(() => {
    let assets = 0, debts = 0
    for (const acc of includedAccounts.value) {
        if (acc.balance >= 0) assets += acc.balance
        else debts += acc.balance
    }
    return { assets, debts, net: assets + debts }
})

const formatMoney = (n: number) =>
    new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(n)

const typeIcon = (type: string) => {
    switch (type) {
        case '现金': return Banknote
        case '信用卡': return CreditCard
        case '储蓄卡': return Landmark
        case '网络支付': return Wallet
        case '投资账户': return TrendingUp
        case '充值卡': return CreditCard
        case '应收账户': return ArrowDownLeft
        case '应付账户': return ArrowUpRight
        default: return Landmark
    }
}

const typeColor = (type: string) => {
    switch (type) {
        case '网络支付': return 'bg-teal-500'
        case '信用卡': return 'bg-amber-500'
        case '储蓄卡': return 'bg-emerald-500'
        case '投资账户': return 'bg-rose-500'
        case '现金': return 'bg-rose-400'
        case '充值卡': return 'bg-amber-400'
        case '应收账户': return 'bg-indigo-500'
        case '应付账户': return 'bg-gray-500'
        default: return 'bg-gray-400'
    }
}

const pullHint = computed(() => {
    if (refreshing.value) return '刷新中...'
    return pullDistance.value >= pullThreshold ? '松开刷新' : '下拉刷新'
})

const renderTrendChart = () => {
    if (!chartRef.value || chartRef.value.clientWidth <= 0 || chartRef.value.clientHeight <= 0) return false
    if (!trendChart) trendChart = echarts.init(chartRef.value)

    const labels = netTrendRows.value.map(item => item.period.replace('-', '/'))
    const values = netTrendRows.value.map(item => item.balance)

    trendChart.setOption({
        grid: { top: 4, right: 6, bottom: 2, left: 6 },
        xAxis: {
            type: 'category',
            data: labels.length > 0 ? labels : [''],
            boundaryGap: false,
            axisLine: { show: false },
            axisTick: { show: false },
            axisLabel: { show: false },
        },
        yAxis: {
            type: 'value',
            axisLine: { show: false },
            axisTick: { show: false },
            splitLine: { show: false },
            axisLabel: { show: false },
        },
        series: [{
            type: 'line',
            smooth: true,
            symbol: 'none',
            data: values.length > 0 ? values : [0],
            lineStyle: { width: 2, color: '#f8fafc' },
            areaStyle: {
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                    { offset: 0, color: 'rgba(255,255,255,0.42)' },
                    { offset: 1, color: 'rgba(255,255,255,0.03)' },
                ]),
            },
        }],
    })
    trendChart.resize()
    return true
}

const renderTrendChartSafely = async () => {
    await nextTick()
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))

    if (renderTrendChart()) return

    if (delayedRenderTimer) clearTimeout(delayedRenderTimer)
    delayedRenderTimer = setTimeout(() => {
        renderTrendChart()
    }, 120)
}

const loadNetTrend = async () => {
    if (!store.currentBookId) {
        netTrendRows.value = []
        return
    }

    netTrendLoading.value = true
    try {
        const end = new Date()
        const start = new Date(end)
        start.setMonth(end.getMonth() - 11)
        start.setDate(1)

        const endExclusive = new Date(end)
        endExclusive.setDate(endExclusive.getDate() + 1)

        const res = await getBalanceTrend(
            store.currentBookId,
            toIsoLocal(start),
            toIsoLocal(endExclusive),
            'month',
            'net',
        )
        netTrendRows.value = res.data
    } catch (error) {
        console.error('load net trend failed', error)
        netTrendRows.value = []
    } finally {
        netTrendLoading.value = false
    }

    await renderTrendChartSafely()
}

const goBalanceTrend = (
    scope: BalanceTrendScope,
    options: {
        accountType?: string
        accountId?: number
    } = {},
) => {
    const end = new Date()
    const start = new Date(end)
    start.setMonth(end.getMonth() - 11)
    start.setDate(1)

    const endExclusive = new Date(end)
    endExclusive.setDate(endExclusive.getDate() + 1)

    const query: Record<string, string> = {
        scope,
        start: toIsoLocal(start),
        end: toIsoLocal(endExclusive),
    }

    if (options.accountType) {
        query.account_type = options.accountType
    }
    if (options.accountId) {
        query.account_id = String(options.accountId)
    }

    router.push({ name: 'BalanceTrendDetail', query })
}



const loadData = async () => {
    if (!store.currentBookId) return
    loading.value = true
    try {
        const res = await getAccounts(store.currentBookId)
        accounts.value = res.data
        await loadNetTrend()
    } finally {
        loading.value = false
    }
}

const handleCreateAccount = async () => {
    if (!newAccName.value.trim() || !store.currentBookId) return
    creatingAcc.value = true
    try {
        const res = await createAccount(store.currentBookId, {
            name: newAccName.value.trim(),
            type: newAccType.value,
            balance: newAccBalance.value,
        })
        accounts.value.push(res.data)
        appendOperationLog(
            store.currentBookId,
            '新增账户',
            `${res.data.name} · ${res.data.type} · ¥${res.data.balance.toFixed(2)}`,
        )
        await loadNetTrend()
        newAccName.value = ''
        newAccBalance.value = 0
        showAddAccount.value = false
    } finally {
        creatingAcc.value = false
    }
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

onMounted(async () => {
    if (!store.currentBookId) await store.fetchBooks()
    await loadData()

    if (typeof ResizeObserver !== 'undefined' && chartRef.value) {
        trendObserver = new ResizeObserver(() => trendChart?.resize())
        trendObserver.observe(chartRef.value)
    }
})

onBeforeUnmount(() => {
    trendObserver?.disconnect()
    trendObserver = null

    if (delayedRenderTimer) {
        clearTimeout(delayedRenderTimer)
        delayedRenderTimer = null
    }

    trendChart?.dispose()
    trendChart = null
})
</script>

<template>
  <div
    ref="pageRef"
    class="pb-4"
    @touchstart="handleTouchStart"
    @touchmove="handleTouchMove"
    @touchend="handleTouchEnd"
    @touchcancel="handleTouchEnd"
  >
    <div class="overflow-hidden transition-[height] duration-150" :style="{ height: `${Math.round(pullDistance)}px` }">
      <div class="h-full flex items-end justify-center pb-2 text-xs text-slate-500 gap-1">
        <Loader2 v-if="refreshing" class="w-3 h-3 animate-spin" />
        <span>{{ pullHint }}</span>
      </div>
    </div>

    <!-- Header -->
    <div class="flex items-center justify-between px-4 pt-4 pb-2">
      <h2 class="text-lg font-bold text-theme-primary">净资产</h2>
      <button @click="showAddAccount = true" class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-slate-800 transition">
        <Plus class="w-5 h-5 text-theme-muted" />
      </button>
    </div>

    <!-- Net Worth Card -->
    <div
      class="mx-4 rounded-3xl p-5 text-white shadow-lg relative overflow-hidden cursor-pointer"
      :style="{
        backgroundColor: '#0b5d8f',
        backgroundImage: `linear-gradient(135deg, rgba(6, 30, 78, 0.55), rgba(8, 95, 150, 0.38)), url('${netWorthBg}')`,
        backgroundSize: 'cover',
        backgroundPosition: 'center',
      }"
      @click="goBalanceTrend('net')"
    >
      <div class="absolute inset-0 opacity-10 bg-[radial-gradient(circle_at_20%_20%,rgba(255,255,255,0.35),transparent_45%),radial-gradient(circle_at_80%_70%,rgba(255,255,255,0.18),transparent_45%)]" />
      <div class="relative z-10">
        <div class="flex items-center justify-between gap-3 mb-1">
          <div class="flex items-center gap-2">
            <span class="text-4xl font-bold tracking-tight drop-shadow-[0_2px_8px_rgba(0,0,0,0.35)]">
              {{ showAmount ? `¥${formatMoney(totalAssets.net)}` : '****' }}
            </span>
            <button @click.stop="showAmount = !showAmount" class="opacity-85 hover:opacity-100 transition">
              <EyeOff v-if="showAmount" class="w-5 h-5" />
              <Eye v-else class="w-5 h-5" />
            </button>
          </div>

          <button
            @click.stop="goBalanceTrend('net')"
            class="px-3 py-1.5 rounded-full border border-white/50 text-xs bg-white/15 hover:bg-white/20 transition"
          >
            余额趋势
          </button>
        </div>

        <div class="flex gap-3 text-sm mt-2">
          <button
            @click.stop="goBalanceTrend('assets')"
            class="px-3 py-1.5 rounded-xl bg-white/18 hover:bg-white/28 transition"
          >
            资产 {{ showAmount ? `¥${formatMoney(totalAssets.assets)}` : '****' }}
          </button>
          <button
            @click.stop="goBalanceTrend('liabilities')"
            class="px-3 py-1.5 rounded-xl bg-white/18 hover:bg-white/28 transition"
          >
            负债 {{ showAmount ? (totalAssets.debts < 0 ? `-¥${formatMoney(Math.abs(totalAssets.debts))}` : '¥0') : '****' }}
          </button>
        </div>
      </div>

      <div ref="chartRef" class="h-[76px] mt-4 relative z-10"></div>
      <div v-if="netTrendLoading" class="absolute inset-0 z-20 flex items-center justify-center bg-black/10">
        <Loader2 class="w-4 h-4 animate-spin text-white" />
      </div>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="p-8 text-center text-theme-muted">
      <Loader2 class="w-5 h-5 animate-spin mx-auto mb-2 text-indigo-400" />
    </div>

    <!-- Account Groups -->
    <template v-else>
      <div v-for="(items, type) in grouped" :key="type" class="mx-4 mt-4 rounded-2xl bg-white dark:bg-slate-800 shadow-sm border border-gray-100 dark:border-slate-700 overflow-hidden">
        <!-- Group Header -->
        <div class="flex items-center justify-between px-4 py-3 border-b border-gray-50 dark:border-slate-700/50">
          <button
            @click="goBalanceTrend('account_type', { accountType: type as string })"
            class="text-sm text-theme-muted font-medium hover:text-teal-600 dark:hover:text-teal-400 transition"
          >
            {{ type }}
          </button>
          <button
            @click="goBalanceTrend('account_type', { accountType: type as string })"
            class="text-sm text-theme-muted hover:text-teal-600 dark:hover:text-teal-400 transition"
          >
            {{ showAmount ? `¥${formatMoney(groupTotal(items))}` : '****' }}
          </button>
        </div>
        <!-- Account Items -->
        <div
          v-for="acc in items"
          :key="acc.id"
          @click="router.push(`/accounting/account/${acc.id}`)"
          class="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50 dark:hover:bg-slate-700/50 transition"
        >
          <div :class="['w-9 h-9 rounded-xl flex items-center justify-center', typeColor(type as string)]">
            <component :is="typeIcon(type as string)" class="w-4 h-4 text-white" />
          </div>
          <span class="flex-1 font-medium text-theme-primary text-sm">{{ acc.name }}</span>
          <button
            @click.stop="goBalanceTrend('account', { accountId: acc.id })"
            class="text-teal-500 font-semibold text-sm hover:text-teal-600 transition"
          >
            {{ showAmount ? `¥${formatMoney(acc.balance)}` : '****' }}
          </button>
          <ChevronRight class="w-4 h-4 text-theme-muted" />
        </div>
      </div>

      <div v-if="accounts.length === 0" class="mx-4 mt-4 p-8 text-center text-theme-muted text-sm rounded-2xl bg-white dark:bg-slate-800 shadow-sm border border-gray-100 dark:border-slate-700">
        暂无账户，点击右上角 + 添加
      </div>
    </template>

    <!-- Add Account Modal -->
    <div
      v-if="showAddAccount"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      @click.self="showAddAccount = false"
    >
      <div class="bg-white dark:bg-slate-800 rounded-2xl p-6 w-[320px] shadow-xl">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-lg font-semibold text-theme-primary">添加账户</h3>
          <button @click="showAddAccount = false"><X class="w-5 h-5 text-theme-muted" /></button>
        </div>
        <form @submit.prevent="handleCreateAccount" class="space-y-3">
          <div>
            <label class="text-xs text-theme-muted font-medium">账户名称</label>
            <input v-model="newAccName" type="text" placeholder="如：招商银行-陈" class="w-full mt-1 px-3 py-2.5 rounded-xl border border-gray-200 dark:border-slate-600 bg-gray-50 dark:bg-slate-700 text-theme-primary text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500" autofocus />
          </div>
          <div>
            <label class="text-xs text-theme-muted font-medium">类型</label>
            <select v-model="newAccType" class="w-full mt-1 px-3 py-2.5 rounded-xl border border-gray-200 dark:border-slate-600 bg-gray-50 dark:bg-slate-700 text-theme-primary text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500">
              <option v-for="t in accountTypes" :key="t" :value="t">{{ t }}</option>
            </select>
          </div>
          <div>
            <label class="text-xs text-theme-muted font-medium">余额</label>
            <input v-model.number="newAccBalance" type="number" step="0.01" class="w-full mt-1 px-3 py-2.5 rounded-xl border border-gray-200 dark:border-slate-600 bg-gray-50 dark:bg-slate-700 text-theme-primary text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500" />
          </div>
          <button
            type="submit"
            :disabled="creatingAcc || !newAccName.trim()"
            class="w-full py-2.5 bg-indigo-500 hover:bg-indigo-600 text-white font-medium rounded-xl transition disabled:opacity-50"
          >
            <Loader2 v-if="creatingAcc" class="w-4 h-4 animate-spin mx-auto" />
            <span v-else>添加</span>
          </button>
        </form>
      </div>
    </div>
  </div>
</template>
