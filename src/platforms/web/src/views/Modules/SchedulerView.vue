<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ChevronLeft, Plus, Trash2, Clock, CalendarClock, Pencil } from 'lucide-vue-next'
import request from '@/api/request'

const router = useRouter()

const tasks = ref<any[]>([])
const loading = ref(false)
const showDialog = ref(false)
const editingId = ref<number | null>(null)
const formData = ref({ crontab: '', instruction: '' })

const loadData = async () => {
    loading.value = true
    try {
        const res = await request('/scheduler', { method: 'GET' })
        tasks.value = res.data || []
    } catch (e) {
        console.error(e)
    } finally {
        loading.value = false
    }
}

const openCreate = () => {
    editingId.value = null
    formData.value = { crontab: '', instruction: '' }
    showDialog.value = true
}

const openEdit = (task: any) => {
    editingId.value = task.id
    formData.value = { crontab: task.crontab, instruction: task.instruction }
    showDialog.value = true
}

const handleSave = async () => {
    if (!formData.value.crontab || !formData.value.instruction) return
    try {
        if (editingId.value) {
            await request(`/scheduler/${editingId.value}`, {
                method: 'PUT',
                data: formData.value
            })
        } else {
            await request('/scheduler', {
                method: 'POST',
                data: formData.value
            })
        }
        showDialog.value = false
        formData.value = { crontab: '', instruction: '' }
        editingId.value = null
        loadData()
    } catch (e: any) {
        alert(e?.response?.data?.detail || '操作失败')
    }
}

const handleDelete = async (id: number) => {
    if (!confirm('确定删除该定时任务吗？')) return
    try {
        await request(`/scheduler/${id}`, { method: 'DELETE' })
        loadData()
    } catch (e) {
        console.error(e)
    }
}

const toggleStatus = async (task: any) => {
    try {
        await request(`/scheduler/${task.id}/status`, {
            method: 'PUT',
            data: { is_active: !task.is_active }
        })
        loadData()
    } catch (e) {
        console.error(e)
    }
}

onMounted(() => {
    loadData()
})
</script>

<template>
  <div class="h-screen flex flex-col bg-slate-50 dark:bg-slate-900 absolute inset-0 z-50">
    <header class="bg-white dark:bg-slate-800 shadow-sm relative z-10 safe-top">
      <div class="flex items-center justify-between h-14 px-4">
        <button @click="router.back()" class="p-2 -ml-2 text-slate-600 dark:text-slate-300">
          <ChevronLeft class="w-6 h-6" />
        </button>
        <h1 class="text-lg font-bold text-slate-800 dark:text-white">定时任务</h1>
        <button @click="openCreate" class="p-2 -mr-2 text-blue-600 dark:text-blue-400">
          <Plus class="w-6 h-6" />
        </button>
      </div>
    </header>

    <main class="flex-1 overflow-y-auto p-4 safe-bottom">
      <div v-if="loading" class="flex justify-center py-8">
        <div class="w-8 h-8 rounded-full border-4 border-blue-500/30 border-t-blue-500 animate-spin"></div>
      </div>
      
      <div v-else-if="tasks.length === 0" class="flex flex-col items-center justify-center py-20 text-slate-400">
        <CalendarClock class="w-16 h-16 mb-4 text-slate-300" />
        <p>暂无定时任务</p>
      </div>

      <div v-else class="space-y-4">
        <div 
          v-for="task in tasks" 
          :key="task.id"
          class="bg-white dark:bg-slate-800 rounded-2xl p-4 shadow-sm border border-slate-100 dark:border-slate-700"
          :class="{'opacity-60': !task.is_active}"
        >
          <div class="flex items-start justify-between">
            <div class="flex-1 min-w-0 mr-3">
              <div class="flex items-center gap-2 mb-1">
                <Clock class="w-4 h-4 text-slate-400" />
                <span class="font-mono text-sm text-slate-600 dark:text-slate-300">{{ task.crontab }}</span>
              </div>
              <h3 class="font-bold text-slate-800 dark:text-white">{{ task.instruction }}</h3>
            </div>
            <div class="flex items-center gap-2 shrink-0">
              <label class="relative inline-flex items-center cursor-pointer">
                <input type="checkbox" :checked="task.is_active" @change="toggleStatus(task)" class="sr-only peer">
                <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-500"></div>
              </label>
              <button @click="openEdit(task)" class="text-slate-400 hover:text-blue-500 transition p-1 border border-transparent hover:border-blue-100 rounded-md">
                <Pencil class="w-4 h-4" />
              </button>
              <button @click="handleDelete(task.id)" class="text-slate-400 hover:text-rose-500 transition p-1 border border-transparent hover:border-rose-100 rounded-md">
                <Trash2 class="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </main>

    <div v-if="showDialog" class="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4">
      <div class="bg-white dark:bg-slate-800 rounded-2xl w-full max-w-sm overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        <div class="p-4 border-b border-slate-100 dark:border-slate-700">
          <h2 class="text-lg font-bold text-center">{{ editingId ? '编辑任务' : '添加任务' }}</h2>
        </div>
        <div class="p-4 space-y-4">
          <div>
            <label class="block text-sm text-slate-500 mb-1">指令内容</label>
            <textarea v-model="formData.instruction" rows="4" class="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-3 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 resize-none" placeholder="例如: 播报今天的天气"></textarea>
          </div>
          <div>
            <label class="block text-sm text-slate-500 mb-1">Crontab 表达式</label>
            <input v-model="formData.crontab" type="text" class="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-3 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500" placeholder="0 8 * * *">
          </div>
        </div>
        <div class="p-4 flex gap-3 border-t border-slate-100 dark:border-slate-700">
          <button @click="showDialog = false" class="flex-1 py-3 bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 rounded-xl font-medium">取消</button>
          <button @click="handleSave" class="flex-1 py-3 bg-blue-500 text-white rounded-xl font-medium shadow-lg shadow-blue-500/30">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>
