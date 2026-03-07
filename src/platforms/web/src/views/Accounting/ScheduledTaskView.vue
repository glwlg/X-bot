<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAccountingStore } from '@/stores/accounting'
import { getScheduledTasks, createScheduledTask, deleteScheduledTask, type ScheduledTask } from '@/api/accounting'
import { 
    ChevronLeft, Plus, CalendarClock, Trash2, ArrowRightLeft, ArrowRight
} from 'lucide-vue-next'

const router = useRouter()
const store = useAccountingStore()

// State
const tasks = ref<ScheduledTask[]>([])
const loading = ref(false)
const showCreateDialog = ref(false)

// Form
const createForm = ref({
    name: '',
    frequency: '每月',
    type: '支出',
    amount: '',
    account_id: '' as unknown as number,
    target_account_id: '' as unknown as number,
    category_id: '' as unknown as number,
    payee: '',
    remark: ''
})

const frequencies = ['每天', '每周', '每月', '每年']
const types = ['支出', '收入', '转账']

const loadData = async () => {
    if (!store.currentBookId) return
    loading.value = true
    try {
        const res = await getScheduledTasks(store.currentBookId)
        tasks.value = res.data
    } catch (e) {
        console.error(e)
    } finally {
        loading.value = false
    }
}

const openCreate = () => {
    createForm.value = {
        name: '',
        frequency: '每月',
        type: '支出',
        amount: '',
        account_id: '' as unknown as number,
        target_account_id: '' as unknown as number,
        category_id: '' as unknown as number,
        payee: '',
        remark: ''
    }
    showCreateDialog.value = true
}

const getNextRunDate = (freq: string) => {
    const d = new Date()
    if (freq === '每天') d.setDate(d.getDate() + 1)
    else if (freq === '每周') d.setDate(d.getDate() + 7)
    else if (freq === '每月') d.setMonth(d.getMonth() + 1)
    else if (freq === '每年') d.setFullYear(d.getFullYear() + 1)
    return d.toISOString()
}

const saveTask = async () => {
    if (!store.currentBookId) return
    if (!createForm.value.name || !createForm.value.amount) return
    
    try {
        const next_run = getNextRunDate(createForm.value.frequency)
        await createScheduledTask(store.currentBookId, {
            ...createForm.value,
            amount: Number(createForm.value.amount),
            next_run,
            account_id: createForm.value.account_id || undefined,
            target_account_id: createForm.value.target_account_id || undefined,
            category_id: createForm.value.category_id || undefined,
            payee: createForm.value.payee || undefined,
            remark: createForm.value.remark || undefined
        })
        showCreateDialog.value = false
        loadData()
    } catch (e) {
        console.error(e)
    }
}

const handleDelete = async (id: number) => {
    if (!store.currentBookId) return
    if(!confirm('确定要删除该周期计划吗？')) return
    
    try {
        await deleteScheduledTask(store.currentBookId, id)
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

const getIconColor = (type: string) => {
  if (type === '支出') return 'bg-rose-100 text-rose-500 dark:bg-rose-500/20'
  if (type === '收入') return 'bg-indigo-100 text-indigo-600 dark:bg-indigo-500/20'
  return 'bg-blue-100 text-blue-500 dark:bg-blue-500/20'
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
        <h1 class="text-lg font-bold text-slate-800 dark:text-white">计划管理</h1>
        <button @click="openCreate" class="p-2 -mr-2 text-indigo-600 dark:text-indigo-400">
          <Plus class="w-6 h-6" />
        </button>
      </div>
    </header>

    <!-- Content -->
    <main class="flex-1 overflow-y-auto p-4 safe-bottom">
      <div v-if="loading" class="flex justify-center py-8">
        <div class="w-8 h-8 rounded-full border-4 border-indigo-500/30 border-t-indigo-500 animate-spin"></div>
      </div>
      
      <div v-else-if="tasks.length === 0" class="flex flex-col items-center justify-center py-20 text-slate-400">
        <CalendarClock class="w-16 h-16 mb-4 text-slate-300" />
        <p>暂无周期计划</p>
      </div>

      <div v-else class="space-y-4">
        <div 
            v-for="task in tasks" 
            :key="task.id"
            class="bg-white dark:bg-slate-800 rounded-2xl p-4 shadow-sm border border-slate-100 dark:border-slate-700"
            :class="{'opacity-60': !task.is_active}"
        >
            <div class="flex items-start justify-between">
                <div class="flex items-center gap-3">
                    <div class="w-10 h-10 rounded-xl flex items-center justify-center" :class="getIconColor(task.type)">
                        <ArrowRightLeft v-if="task.type === '转账'" class="w-5 h-5 flex-shrink-0" />
                        <ArrowRight v-else class="w-5 h-5 flex-shrink-0" :class="task.type === '支出' ? 'rotate-45' : '-rotate-45'" />
                    </div>
                    <div>
                        <div class="flex items-center gap-2">
                            <h3 class="font-bold text-slate-800 dark:text-white">{{ task.name }}</h3>
                            <span class="px-2 py-0.5 rounded text-[10px] font-medium bg-slate-100 dark:bg-slate-700 text-slate-500">
                                {{ task.frequency }}
                            </span>
                        </div>
                        <div class="text-xs text-slate-500 mt-0.5 flex items-center gap-1">
                            <span>下次执行: {{ formatDate(task.next_run) }}</span>
                        </div>
                    </div>
                </div>
                
                <div class="text-right">
                    <div class="text-base font-bold" :class="task.type === '支出' ? 'text-rose-500' : 'text-indigo-500'">
                        {{ task.type === '支出' ? '-' : (task.type === '收入' ? '+' : '') }}¥{{ task.amount }}
                    </div>
                    <div class="mt-1 flex justify-end">
                        <button @click="handleDelete(task.id)" class="text-slate-400 hover:text-rose-500 transition border border-transparent hover:border-rose-100 rounded-md p-1">
                            <Trash2 class="w-4 h-4" />
                        </button>
                    </div>
                </div>
            </div>
        </div>
      </div>
    </main>

    <!-- Create Dialog -->
    <div v-if="showCreateDialog" class="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4">
      <div class="bg-white dark:bg-slate-800 rounded-2xl w-full max-w-sm overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        <div class="p-4 border-b border-slate-100 dark:border-slate-700">
          <h2 class="text-lg font-bold text-center">新增周期计划</h2>
        </div>
        <div class="p-4 space-y-4 max-h-[60vh] overflow-y-auto">
          <div>
            <label class="block text-sm text-slate-500 mb-1">计划名称</label>
            <input v-model="createForm.name" type="text" class="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-3 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500" placeholder="房租/工资/还款">
          </div>
          <div class="flex gap-4">
             <div class="flex-1">
                 <label class="block text-sm text-slate-500 mb-1">执行周期</label>
                 <select v-model="createForm.frequency" class="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-3 text-slate-800 dark:text-white focus:outline-none">
                     <option v-for="freq in frequencies" :key="freq" :value="freq">{{ freq }}</option>
                 </select>
             </div>
             <div class="flex-1">
                 <label class="block text-sm text-slate-500 mb-1">交易类型</label>
                 <select v-model="createForm.type" class="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-3 text-slate-800 dark:text-white focus:outline-none">
                     <option v-for="t in types" :key="t" :value="t">{{ t }}</option>
                 </select>
             </div>
          </div>
          <div>
            <label class="block text-sm text-slate-500 mb-1">金额</label>
            <input v-model="createForm.amount" type="number" class="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-3 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500" placeholder="0.00">
          </div>
          <!-- 简单化处理，如果是实际应用，这里应该有完善的选择器，为了避免过度复杂，这里只保留备注等基础信息 -->
          <div>
            <label class="block text-sm text-slate-500 mb-1">备注 (选填)</label>
            <input v-model="createForm.remark" type="text" class="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-3 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500" placeholder="添加备注...">
          </div>
        </div>
        <div class="p-4 flex gap-3 border-t border-slate-100 dark:border-slate-700">
          <button @click="showCreateDialog = false" class="flex-1 py-3 bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 rounded-xl font-medium">取消</button>
          <button @click="saveTask" class="flex-1 py-3 bg-indigo-500 text-white rounded-xl font-medium shadow-lg shadow-indigo-500/30">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>
