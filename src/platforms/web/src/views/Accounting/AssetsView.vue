<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAccountingStore } from '@/stores/accounting'
import { getAccounts, createAccount, type AccountItem } from '@/api/accounting'
import { appendOperationLog } from '@/utils/accountingLocal'
import {
    Plus, Eye, EyeOff, Loader2, Banknote, CreditCard, Landmark, X, ChevronRight,
    Wallet, TrendingUp, ArrowDownLeft, ArrowUpRight
} from 'lucide-vue-next'

const router = useRouter()


const store = useAccountingStore()
const accounts = ref<AccountItem[]>([])
const loading = ref(false)
const showAmount = ref(true)
const showAddAccount = ref(false)

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

const totalAssets = computed(() => {
    let assets = 0, debts = 0
    for (const acc of accounts.value) {
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



const loadData = async () => {
    if (!store.currentBookId) return
    loading.value = true
    try {
        const res = await getAccounts(store.currentBookId)
        accounts.value = res.data
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
        newAccName.value = ''
        newAccBalance.value = 0
        showAddAccount.value = false
    } finally {
        creatingAcc.value = false
    }
}

onMounted(async () => {
    if (!store.currentBookId) await store.fetchBooks()
    await loadData()
})
</script>

<template>
  <div class="pb-4">
    <!-- Header -->
    <div class="flex items-center justify-between px-4 pt-4 pb-2">
      <h2 class="text-lg font-bold text-theme-primary">净资产</h2>
      <button @click="showAddAccount = true" class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-slate-800 transition">
        <Plus class="w-5 h-5 text-theme-muted" />
      </button>
    </div>

    <!-- Net Worth Card -->
    <div class="mx-4 rounded-2xl bg-gradient-to-br from-cyan-600 via-teal-500 to-blue-600 p-5 text-white shadow-lg relative overflow-hidden">
      <div class="absolute top-0 right-0 w-40 h-40 bg-white/5 rounded-full -translate-y-1/2 translate-x-1/2" />
      <div class="relative z-10">
        <div class="flex items-center gap-3 mb-1">
          <span class="text-3xl font-bold">
            {{ showAmount ? `¥${formatMoney(totalAssets.net)}` : '****' }}
          </span>
          <button @click="showAmount = !showAmount" class="opacity-80 hover:opacity-100 transition">
            <EyeOff v-if="showAmount" class="w-5 h-5" />
            <Eye v-else class="w-5 h-5" />
          </button>
        </div>
        <div class="flex gap-6 text-sm opacity-90">
          <span>资产 {{ showAmount ? `¥${formatMoney(totalAssets.assets)}` : '****' }}</span>
          <span>负债 {{ showAmount ? (totalAssets.debts < 0 ? `-¥${formatMoney(Math.abs(totalAssets.debts))}` : '¥0') : '****' }}</span>
        </div>
      </div>
      <!-- Mini chart placeholder -->
      <div ref="chartRef" class="h-[60px] mt-3 opacity-60"></div>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="p-8 text-center text-theme-muted">
      <Loader2 class="w-5 h-5 animate-spin mx-auto mb-2 text-teal-400" />
    </div>

    <!-- Account Groups -->
    <template v-else>
      <div v-for="(items, type) in grouped" :key="type" class="mx-4 mt-4 rounded-2xl bg-white dark:bg-slate-800 shadow-sm border border-gray-100 dark:border-slate-700 overflow-hidden">
        <!-- Group Header -->
        <div class="flex items-center justify-between px-4 py-3 border-b border-gray-50 dark:border-slate-700/50">
          <span class="text-sm text-theme-muted font-medium">{{ type }}</span>
          <span class="text-sm text-theme-muted">{{ showAmount ? `¥${formatMoney(groupTotal(items))}` : '****' }}</span>
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
          <span class="text-teal-500 font-semibold text-sm">
            {{ showAmount ? `¥${formatMoney(acc.balance)}` : '****' }}
          </span>
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
            <input v-model="newAccName" type="text" placeholder="如：招商银行-陈" class="w-full mt-1 px-3 py-2.5 rounded-xl border border-gray-200 dark:border-slate-600 bg-gray-50 dark:bg-slate-700 text-theme-primary text-sm focus:outline-none focus:ring-2 focus:ring-teal-500" autofocus />
          </div>
          <div>
            <label class="text-xs text-theme-muted font-medium">类型</label>
            <select v-model="newAccType" class="w-full mt-1 px-3 py-2.5 rounded-xl border border-gray-200 dark:border-slate-600 bg-gray-50 dark:bg-slate-700 text-theme-primary text-sm focus:outline-none focus:ring-2 focus:ring-teal-500">
              <option v-for="t in accountTypes" :key="t" :value="t">{{ t }}</option>
            </select>
          </div>
          <div>
            <label class="text-xs text-theme-muted font-medium">余额</label>
            <input v-model.number="newAccBalance" type="number" step="0.01" class="w-full mt-1 px-3 py-2.5 rounded-xl border border-gray-200 dark:border-slate-600 bg-gray-50 dark:bg-slate-700 text-theme-primary text-sm focus:outline-none focus:ring-2 focus:ring-teal-500" />
          </div>
          <button
            type="submit"
            :disabled="creatingAcc || !newAccName.trim()"
            class="w-full py-2.5 bg-teal-500 hover:bg-teal-600 text-white font-medium rounded-xl transition disabled:opacity-50"
          >
            <Loader2 v-if="creatingAcc" class="w-4 h-4 animate-spin mx-auto" />
            <span v-else>添加</span>
          </button>
        </form>
      </div>
    </div>
  </div>
</template>
