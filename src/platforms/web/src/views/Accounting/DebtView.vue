<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAccountingStore } from '@/stores/accounting'
import { getDebts, createDebt, repayDebt, type Debt } from '@/api/accounting'
import { 
    ChevronLeft, Plus, Users, Calendar, AlertCircle, User
} from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()
const store = useAccountingStore()

// State
const debts = ref<Debt[]>([])
const loading = ref(false)
const activeTab = ref(route.query.type as string || '借入')
const showCreateDialog = ref(false)
const showRepayDialog = ref(false)

// Forms
const createForm = ref({
    type: activeTab.value,
    contact: '',
    amount: '',
    due_date: '',
    remark: ''
})

const repayForm = ref({
    debtId: 0,
    amount: ''
})
const repayMax = ref(0)
const repayContact = ref('')

const tabs = ['借入', '借出', '报销']

const filteredDebts = computed(() => {
    return debts.value.filter(d => d.type === activeTab.value)
})

const loadData = async () => {
    if (!store.currentBookId) return
    loading.value = true
    try {
        const res = await getDebts(store.currentBookId)
        debts.value = res.data
    } catch (e) {
        console.error(e)
    } finally {
        loading.value = false
    }
}

const handleTabChange = (tab: string) => {
    activeTab.value = tab
    createForm.value.type = tab
}

const openCreate = () => {
    createForm.value = {
        type: activeTab.value,
        contact: '',
        amount: '',
        due_date: '',
        remark: ''
    }
    showCreateDialog.value = true
}

const saveDebt = async () => {
    if (!store.currentBookId) return
    if (!createForm.value.contact || !createForm.value.amount) return
    
    try {
        await createDebt(store.currentBookId, {
            type: createForm.value.type,
            contact: createForm.value.contact,
            amount: Number(createForm.value.amount),
            due_date: createForm.value.due_date || undefined,
            remark: createForm.value.remark || undefined
        })
        showCreateDialog.value = false
        loadData()
    } catch (e) {
        console.error(e)
    }
}

const openRepay = (debt: Debt) => {
    repayForm.value = {
        debtId: debt.id,
        amount: ''
    }
    repayMax.value = debt.remaining_amount
    repayContact.value = debt.contact
    showRepayDialog.value = true
}

const submitRepay = async () => {
    if (!store.currentBookId || !repayForm.value.debtId || !repayForm.value.amount) return
    
    try {
        await repayDebt(store.currentBookId, repayForm.value.debtId, {
            amount: Number(repayForm.value.amount)
        })
        showRepayDialog.value = false
        loadData()
    } catch (e) {
        console.error(e)
    }
}

onMounted(() => {
    loadData()
})

const formatDate = (dateString: string | null) => {
    if (!dateString) return '未设置'
    return new Date(dateString).toLocaleDateString('zh-CN')
}

const isOverdue = (dateString: string | null) => {
    if (!dateString) return false
    return new Date(dateString) < new Date()
}
</script>

<template>
  <div class="h-screen flex flex-col bg-slate-50 dark:bg-slate-900 absolute inset-0 z-50">
    <!-- Header -->
    <header class="bg-white dark:bg-slate-800 shadow-sm relative z-10 safe-top">
      <div class="flex items-center justify-between h-14 px-4">
        <button @click="router.back()" class="p-2 -ml-2 text-slate-600 dark:text-slate-300">
          <ChevronLeft class="w-6 h-6" />
        </button>
        <h1 class="text-lg font-bold text-slate-800 dark:text-white">往来管理</h1>
        <button @click="openCreate" class="p-2 -mr-2 text-indigo-600 dark:text-indigo-400">
          <Plus class="w-6 h-6" />
        </button>
      </div>
      
      <!-- Tabs -->
      <div class="flex border-t border-slate-100 dark:border-slate-700">
        <button
          v-for="tab in tabs"
          :key="tab"
          @click="handleTabChange(tab)"
          class="flex-1 py-3 text-sm font-medium transition-colors relative"
          :class="activeTab === tab ? 'text-indigo-600 dark:text-indigo-400' : 'text-slate-500 hover:text-slate-700'"
        >
          {{ tab }}
          <div v-if="activeTab === tab" class="absolute bottom-0 left-1/2 -translate-x-1/2 w-8 h-1 bg-indigo-500 rounded-t-full"></div>
        </button>
      </div>
    </header>

    <!-- Content -->
    <main class="flex-1 overflow-y-auto p-4 safe-bottom">
      <div v-if="loading" class="flex justify-center py-8">
        <div class="w-8 h-8 rounded-full border-4 border-indigo-500/30 border-t-indigo-500 animate-spin"></div>
      </div>
      
      <div v-else-if="filteredDebts.length === 0" class="flex flex-col items-center justify-center py-20 text-slate-400">
        <Users class="w-16 h-16 mb-4 text-slate-300" />
        <p>暂无{{ activeTab }}记录</p>
      </div>

      <div v-else class="space-y-4">
        <div 
            v-for="debt in filteredDebts" 
            :key="debt.id"
            class="bg-white dark:bg-slate-800 rounded-2xl p-4 shadow-sm border border-slate-100 dark:border-slate-700"
            :class="{'opacity-75': debt.is_settled}"
        >
            <div class="flex justify-between items-start mb-3">
                <div class="flex items-center gap-2">
                    <div class="w-10 h-10 rounded-full bg-slate-100 dark:bg-slate-700flex items-center justify-center text-slate-500">
                        <User class="w-5 h-5" />
                    </div>
                    <div>
                        <h3 class="font-bold text-slate-800 dark:text-white">{{ debt.contact }}</h3>
                        <p class="text-xs text-slate-500">{{ formatDate(debt.created_at) }}</p>
                    </div>
                </div>
                <div class="text-right">
                    <div class="text-sm font-medium text-slate-500">总计: ¥{{ debt.total_amount }}</div>
                    <div class="text-lg font-bold" :class="debt.type === '借入' ? 'text-rose-500' : 'text-indigo-500'">
                        {{ debt.is_settled ? '已结清' : `待还: ¥${debt.remaining_amount}` }}
                    </div>
                </div>
            </div>
            
            <div class="flex gap-2 text-xs mt-3 bg-slate-50 dark:bg-slate-700/50 p-2 rounded-lg items-center">
                <Calendar class="w-4 h-4 text-slate-400" />
                <span class="text-slate-600 dark:text-slate-300">约定还款: {{ formatDate(debt.due_date) }}</span>
                <span v-if="!debt.is_settled && isOverdue(debt.due_date)" class="text-rose-500 ml-auto flex items-center gap-1">
                    <AlertCircle class="w-3 h-3" /> 已逾期
                </span>
            </div>
            
            <div v-if="!debt.is_settled" class="mt-4 pt-3 border-t border-slate-100 dark:border-slate-700 flex justify-end">
                <button @click="openRepay(debt)" class="px-4 py-1.5 bg-indigo-500 text-white rounded-lg text-sm font-medium hover:bg-indigo-600 transition shadow-sm">
                    {{ debt.type === '借入' ? '还款' : '收款' }}
                </button>
            </div>
        </div>
      </div>
    </main>

    <!-- Create Dialog -->
    <div v-if="showCreateDialog" class="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4">
      <div class="bg-white dark:bg-slate-800 rounded-2xl w-full max-w-sm overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        <div class="p-4 border-b border-slate-100 dark:border-slate-700">
          <h2 class="text-lg font-bold text-center">新增{{ createForm.type }}</h2>
        </div>
        <div class="p-4 space-y-4">
          <div>
            <label class="block text-sm text-slate-500 mb-1">往来人</label>
            <input v-model="createForm.contact" type="text" class="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-3 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500" placeholder="姓名/机构">
          </div>
          <div>
            <label class="block text-sm text-slate-500 mb-1">金额</label>
            <input v-model="createForm.amount" type="number" class="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-3 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500" placeholder="0.00">
          </div>
          <div>
            <label class="block text-sm text-slate-500 mb-1">约定日期 (选填)</label>
            <input v-model="createForm.due_date" type="date" class="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-3 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500">
          </div>
          <div>
            <label class="block text-sm text-slate-500 mb-1">备注 (选填)</label>
            <input v-model="createForm.remark" type="text" class="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-3 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500" placeholder="添加备注...">
          </div>
        </div>
        <div class="p-4 flex gap-3 border-t border-slate-100 dark:border-slate-700">
          <button @click="showCreateDialog = false" class="flex-1 py-3 bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 rounded-xl font-medium">取消</button>
          <button @click="saveDebt" class="flex-1 py-3 bg-indigo-500 text-white rounded-xl font-medium shadow-lg shadow-indigo-500/30">保存</button>
        </div>
      </div>
    </div>

    <!-- Repay Dialog -->
    <div v-if="showRepayDialog" class="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4">
      <div class="bg-white dark:bg-slate-800 rounded-2xl w-full max-w-sm overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        <div class="p-4 border-b border-slate-100 dark:border-slate-700">
          <h2 class="text-lg font-bold text-center">记录{{ activeTab === '借入' ? '还款' : '收款' }}</h2>
          <p class="text-center text-sm text-slate-500 mt-1">往来人: {{ repayContact }}</p>
        </div>
        <div class="p-4 space-y-4">
          <div>
            <label class="block text-sm text-slate-500 mb-1">本次金额</label>
            <div class="relative">
                <input v-model="repayForm.amount" type="number" class="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl pl-4 pr-20 py-3 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500" placeholder="0.00">
                <button @click="repayForm.amount = String(repayMax)" class="absolute right-3 top-1/2 -translate-y-1/2 text-sm text-indigo-500 font-medium">全部</button>
            </div>
            <p class="text-xs text-slate-500 mt-1">最多可输入: ¥{{ repayMax }}</p>
          </div>
        </div>
        <div class="p-4 flex gap-3 border-t border-slate-100 dark:border-slate-700">
          <button @click="showRepayDialog = false" class="flex-1 py-3 bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 rounded-xl font-medium">取消</button>
          <button @click="submitRepay" class="flex-1 py-3 bg-indigo-500 text-white rounded-xl font-medium shadow-lg shadow-indigo-500/30">确认</button>
        </div>
      </div>
    </div>
  </div>
</template>
