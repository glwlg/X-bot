<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { useAccountingStore } from '@/stores/accounting'
import { getBudgets, createOrUpdateBudget, getRecordsSummary, type Budget } from '@/api/accounting'
import { Loader2, Plus, ArrowLeft, Target } from 'lucide-vue-next'

const store = useAccountingStore()
const loading = ref(false)

const now = new Date()
const selectedYear = ref(now.getFullYear())
const selectedMonth = ref(now.getMonth() + 1)

const budgets = ref<Budget[]>([])
const amountSpent = ref(0)
const globalBudgetAmount = ref(0) // total_amount when category is null

const showEditDialog = ref(false)
const inputAmount = ref<number | ''>('')
const saving = ref(false)

const formatMoney = (n: number) =>
    new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(n)

const monthLabel = computed(() => `${selectedYear.value}-${String(selectedMonth.value).padStart(2, '0')}`)

const loadData = async () => {
    if (!store.currentBookId) return
    loading.value = true
    try {
        const [sumRes, budgetRes] = await Promise.all([
            getRecordsSummary(store.currentBookId, selectedYear.value, selectedMonth.value),
            getBudgets(store.currentBookId, monthLabel.value)
        ])
        
        amountSpent.value = sumRes.data.expense || 0
        budgets.value = budgetRes.data
        
        const globalBudget = budgets.value.find(b => !b.category_id)
        globalBudgetAmount.value = globalBudget ? globalBudget.total_amount : 0
        
    } catch (e) {
        console.error('Failed to load budgets', e)
    } finally {
        loading.value = false
    }
}

const prevMonth = () => {
    if (selectedMonth.value === 1) {
        selectedMonth.value = 12
        selectedYear.value--
    } else {
        selectedMonth.value--
    }
}

const nextMonth = () => {
    if (selectedMonth.value === 12) {
        selectedMonth.value = 1
        selectedYear.value++
    } else {
        selectedMonth.value++
    }
}

const handleSaveBudget = async () => {
    if (!store.currentBookId || !inputAmount.value) return
    saving.value = true
    try {
        await createOrUpdateBudget(store.currentBookId, {
            month: monthLabel.value,
            total_amount: Number(inputAmount.value),
            category_id: null
        })
        showEditDialog.value = false
        await loadData()
    } finally {
        saving.value = false
    }
}

const remainingBudget = computed(() => {
    return Math.max(0, globalBudgetAmount.value - amountSpent.value)
})

const budgetPercentage = computed(() => {
    if (globalBudgetAmount.value === 0) return 0
    return Math.min(100, Math.round((amountSpent.value / globalBudgetAmount.value) * 100))
})

watch([selectedYear, selectedMonth], () => loadData())

onMounted(async () => {
    if (!store.currentBookId) await store.fetchBooks()
    if (store.currentBookId) await loadData()
})
</script>

<template>
  <div class="pb-10 pt-4">
    <!-- Month Navigation -->
    <div class="flex items-center justify-between px-6 mb-6">
      <button @click="prevMonth" class="p-2 border border-gray-200 dark:border-slate-700 rounded-full hover:bg-gray-50 dark:hover:bg-slate-800 transition">
        <ArrowLeft class="w-4 h-4 text-theme-secondary" />
      </button>
      <div class="text-center font-bold text-lg text-theme-primary">
        {{ selectedYear }}年{{ selectedMonth }}月
      </div>
      <button @click="nextMonth" class="p-2 border border-gray-200 dark:border-slate-700 rounded-full hover:bg-gray-50 dark:hover:bg-slate-800 transition flex items-center justify-center transform rotate-180">
        <ArrowLeft class="w-4 h-4 text-theme-secondary" />
      </button>
    </div>

    <div v-if="loading" class="p-12 text-center text-theme-muted">
      <Loader2 class="w-5 h-5 animate-spin mx-auto mb-2 text-teal-400" />
      加载中...
    </div>

    <template v-else>
      <!-- Overall Budget Card -->
      <div class="mx-4 bg-white dark:bg-slate-800 rounded-3xl p-6 shadow-sm border border-gray-100 dark:border-slate-700 relative overflow-hidden">
        <div class="flex justify-between items-start mb-6 z-10 relative">
          <div>
            <p class="text-sm font-medium text-theme-secondary mb-1">当月剩余预算</p>
            <div class="text-3xl font-bold text-theme-primary flex items-baseline gap-1">
              <span class="text-xl">¥</span>{{ formatMoney(remainingBudget) }}
            </div>
          </div>
          <button @click="showEditDialog = true; inputAmount = globalBudgetAmount || ''" class="flex flex-col items-center justify-center w-10 h-10 rounded-full bg-teal-50 dark:bg-teal-900/30 text-teal-500 hover:bg-teal-100 dark:hover:bg-teal-900/50 transition">
            <Target class="w-5 h-5" />
          </button>
        </div>

        <div class="space-y-3 z-10 relative">
          <!-- Progress Bar -->
          <div class="w-full h-3 bg-gray-100 dark:bg-slate-700 rounded-full overflow-hidden">
            <div 
              class="h-full rounded-full transition-all duration-500"
              :class="budgetPercentage > 90 ? 'bg-rose-500' : 'bg-teal-500'"
              :style="{ width: `${budgetPercentage}%` }"
            ></div>
          </div>
          
          <div class="flex justify-between text-xs font-medium text-theme-muted">
            <p>已支出 ¥{{ formatMoney(amountSpent) }}</p>
            <p>总预算 ¥{{ formatMoney(globalBudgetAmount) }}</p>
          </div>
        </div>
        
        <!-- Decoration -->
        <div class="absolute -right-10 -bottom-10 w-32 h-32 bg-teal-500/5 rounded-full blur-2xl z-0"></div>
      </div>

      <p v-if="globalBudgetAmount === 0" class="text-center text-theme-muted text-sm mt-10">
        暂未设置本月总预算，点击右上角设置。
      </p>
      
      <!-- Category Budgets placeholder -->
      <div class="mx-4 mt-6">
        <div class="flex justify-between items-center mb-4 px-2">
            <h3 class="font-bold text-theme-primary text-sm">分类预算 (待实现)</h3>
            <button class="text-teal-500 p-1"><Plus class="w-4 h-4" /></button>
        </div>
      </div>
    </template>

    <!-- Set Budget Dialog -->
    <div v-if="showEditDialog" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="showEditDialog = false">
      <div class="bg-white dark:bg-slate-800 rounded-2xl p-6 w-[320px] shadow-xl">
        <h3 class="text-lg font-semibold text-theme-primary mb-4">设置 {{ selectedMonth }} 月总预算</h3>
        <input
            v-model.number="inputAmount"
            type="number"
            placeholder="输入预算金额(¥)"
            class="w-full px-4 py-3 rounded-xl border border-gray-200 dark:border-slate-600 bg-gray-50 dark:bg-slate-700 text-theme-primary focus:outline-none focus:ring-2 focus:ring-teal-500 mb-4"
            autofocus
        />
        <div class="flex gap-3">
          <button @click="showEditDialog = false" type="button" class="flex-1 py-2.5 border border-gray-200 dark:border-slate-600 rounded-xl text-theme-secondary font-medium hover:bg-gray-50 dark:hover:bg-slate-700 transition">取消</button>
          <button @click="handleSaveBudget" :disabled="saving || !inputAmount" type="button" class="flex-1 py-2.5 bg-teal-500 hover:bg-teal-600 text-white font-medium rounded-xl transition disabled:opacity-50">
            <Loader2 v-if="saving" class="w-4 h-4 animate-spin mx-auto" />
            <span v-else>保存</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
