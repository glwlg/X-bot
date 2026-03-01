<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ChevronLeft, Plus, Trash2, Activity, Pencil } from 'lucide-vue-next'
import request from '@/api/request'

const router = useRouter()

const items = ref<string[]>([])
const loading = ref(false)
const showDialog = ref(false)
const editingIndex = ref<number | null>(null)
const formData = ref({ item: '' })

const loadData = async () => {
    loading.value = true
    try {
        const res = await request('/monitor', { method: 'GET' })
        items.value = res.data || []
    } catch (e) {
        console.error(e)
    } finally {
        loading.value = false
    }
}

const openCreate = () => {
    editingIndex.value = null
    formData.value = { item: '' }
    showDialog.value = true
}

const openEdit = (index: number, text: string) => {
    editingIndex.value = index
    formData.value = { item: text }
    showDialog.value = true
}

const handleSave = async () => {
    if (!formData.value.item) return
    try {
        if (editingIndex.value !== null) {
            // Delete old, then add new (checklist is index-based, no PUT)
            await request(`/monitor/${editingIndex.value + 1}`, { method: 'DELETE' })
            await request('/monitor', { method: 'POST', data: formData.value })
        } else {
            await request('/monitor', { method: 'POST', data: formData.value })
        }
        showDialog.value = false
        formData.value = { item: '' }
        editingIndex.value = null
        loadData()
    } catch (e: any) {
        alert(e?.response?.data?.detail || '操作失败')
    }
}

const handleDelete = async (index: number) => {
    if (!confirm('确定删除该监控项吗？')) return
    try {
        await request(`/monitor/${index + 1}`, { method: 'DELETE' })
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
        <h1 class="text-lg font-bold text-slate-800 dark:text-white">心跳监控</h1>
        <button @click="openCreate" class="p-2 -mr-2 text-purple-600 dark:text-purple-400">
          <Plus class="w-6 h-6" />
        </button>
      </div>
    </header>

    <main class="flex-1 overflow-y-auto p-4 safe-bottom">
      <div v-if="loading" class="flex justify-center py-8">
        <div class="w-8 h-8 rounded-full border-4 border-purple-500/30 border-t-purple-500 animate-spin"></div>
      </div>
      
      <div v-else-if="items.length === 0" class="flex flex-col items-center justify-center py-20 text-slate-400">
        <Activity class="w-16 h-16 mb-4 text-slate-300" />
        <p>暂无监控项</p>
      </div>

      <div v-else class="space-y-4">
        <div 
          v-for="(item, index) in items" 
          :key="index"
          class="bg-white dark:bg-slate-800 rounded-2xl p-4 shadow-sm border border-slate-100 dark:border-slate-700"
        >
          <div class="flex items-start justify-between">
            <h3 class="font-bold text-slate-800 dark:text-white break-all pr-4 flex-1">{{ item }}</h3>
            <div class="flex items-center gap-1 shrink-0">
              <button @click="openEdit(index, item)" class="text-slate-400 hover:text-purple-500 transition p-2 border border-transparent hover:border-purple-100 rounded-md">
                <Pencil class="w-4 h-4" />
              </button>
              <button @click="handleDelete(index)" class="text-slate-400 hover:text-rose-500 transition p-2 border border-transparent hover:border-rose-100 rounded-md">
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
          <h2 class="text-lg font-bold text-center">{{ editingIndex !== null ? '编辑监控项' : '添加监控项' }}</h2>
        </div>
        <div class="p-4 space-y-4">
          <div>
            <label class="block text-sm text-slate-500 mb-1">监控指令或设备信息</label>
            <input v-model="formData.item" type="text" class="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-3 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-purple-500/20 focus:border-purple-500" placeholder="例如: 检查服务器状态">
          </div>
        </div>
        <div class="p-4 flex gap-3 border-t border-slate-100 dark:border-slate-700">
          <button @click="showDialog = false" class="flex-1 py-3 bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 rounded-xl font-medium">取消</button>
          <button @click="handleSave" class="flex-1 py-3 bg-purple-500 text-white rounded-xl font-medium shadow-lg shadow-purple-500/30">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>
