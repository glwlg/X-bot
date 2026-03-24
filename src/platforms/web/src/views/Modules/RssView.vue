<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ChevronLeft, Pencil, Plus, Rss, Trash2 } from 'lucide-vue-next'
import request from '@/api/request'

const router = useRouter()

const subs = ref<any[]>([])
const loading = ref(false)
const showDialog = ref(false)
const editingId = ref<number | null>(null)
const formData = ref({
    title: '',
    feed_url: ''
})

const loadData = async () => {
    loading.value = true
    try {
        const res = await request('/rss', { method: 'GET' })
        subs.value = res.data || []
    } catch (e) {
        console.error(e)
    } finally {
        loading.value = false
    }
}

const resetForm = () => {
    formData.value = { title: '', feed_url: '' }
    editingId.value = null
}

const openCreate = () => {
    resetForm()
    showDialog.value = true
}

const openEdit = (sub: any) => {
    editingId.value = sub.id
    formData.value = {
        title: sub.title || '',
        feed_url: sub.feed_url || ''
    }
    showDialog.value = true
}

const handleSave = async () => {
    if (!formData.value.title || !formData.value.feed_url) return

    const payload = {
        title: formData.value.title,
        feed_url: formData.value.feed_url
    }

    try {
        if (editingId.value) {
            await request(`/rss/${editingId.value}`, {
                method: 'PUT',
                data: payload
            })
        } else {
            await request('/rss', {
                method: 'POST',
                data: payload
            })
        }
        showDialog.value = false
        resetForm()
        loadData()
    } catch (e: any) {
        alert(e?.response?.data?.detail || '操作失败')
    }
}

const handleDelete = async (id: number) => {
    if (!confirm('确定取消订阅吗？')) return
    try {
        await request(`/rss/${id}`, { method: 'DELETE' })
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
  <div class="absolute inset-0 z-50 flex h-screen flex-col bg-slate-50 dark:bg-slate-900">
    <header class="safe-top relative z-10 bg-white shadow-sm dark:bg-slate-800">
      <div class="flex h-14 items-center justify-between px-4">
        <button @click="router.back()" class="-ml-2 p-2 text-slate-600 dark:text-slate-300">
          <ChevronLeft class="h-6 w-6" />
        </button>
        <h1 class="text-lg font-bold text-slate-800 dark:text-white">RSS 订阅</h1>
        <button @click="openCreate" class="-mr-2 p-2 text-orange-600 dark:text-orange-400">
          <Plus class="h-6 w-6" />
        </button>
      </div>
    </header>

    <main class="safe-bottom flex-1 overflow-y-auto p-4">
      <div v-if="loading" class="flex justify-center py-8">
        <div class="h-8 w-8 animate-spin rounded-full border-4 border-orange-500/30 border-t-orange-500"></div>
      </div>

      <div v-else-if="subs.length === 0" class="flex flex-col items-center justify-center py-20 text-slate-400">
        <Rss class="mb-4 h-16 w-16 text-slate-300" />
        <p>暂无 RSS 订阅</p>
      </div>

      <div v-else class="space-y-4">
        <div
          v-for="sub in subs"
          :key="sub.id"
          class="rounded-2xl border border-slate-100 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800"
        >
          <div class="flex items-start justify-between">
            <div class="mr-3 min-w-0 flex-1">
              <h3 class="font-bold text-slate-800 dark:text-white">{{ sub.title }}</h3>
              <p class="mt-1 text-xs text-slate-500">
                <span class="inline-flex rounded-full bg-sky-100 px-2 py-0.5 text-sky-700">RSS</span>
              </p>
              <p class="mt-1 break-all text-xs text-slate-500">{{ sub.feed_url }}</p>
            </div>
            <div class="flex shrink-0 items-center gap-1">
              <button @click="openEdit(sub)" class="rounded-md border border-transparent p-2 text-slate-400 transition hover:border-blue-100 hover:text-blue-500">
                <Pencil class="h-4 w-4" />
              </button>
              <button @click="handleDelete(sub.id)" class="rounded-md border border-transparent p-2 text-slate-400 transition hover:border-rose-100 hover:text-rose-500">
                <Trash2 class="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </main>

    <div v-if="showDialog" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4">
      <div class="w-full max-w-sm overflow-hidden rounded-2xl bg-white duration-200 animate-in fade-in zoom-in-95 dark:bg-slate-800">
        <div class="border-b border-slate-100 p-4 dark:border-slate-700">
          <h2 class="text-center text-lg font-bold">{{ editingId ? '编辑 RSS 订阅' : '添加 RSS 订阅' }}</h2>
        </div>
        <div class="space-y-4 p-4">
          <div>
            <label class="mb-1 block text-sm text-slate-500">标题名称</label>
            <input
              v-model="formData.title"
              type="text"
              class="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-slate-800 focus:border-orange-500 focus:outline-none focus:ring-2 focus:ring-orange-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-white"
              placeholder="例如: 极客公园"
            >
          </div>
          <div>
            <label class="mb-1 block text-sm text-slate-500">RSS 链接</label>
            <textarea
              v-model="formData.feed_url"
              rows="3"
              class="w-full resize-none rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-slate-800 focus:border-orange-500 focus:outline-none focus:ring-2 focus:ring-orange-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-white"
              placeholder="https://..."
            ></textarea>
          </div>
        </div>
        <div class="flex gap-3 border-t border-slate-100 p-4 dark:border-slate-700">
          <button @click="showDialog = false" class="flex-1 rounded-xl bg-slate-100 py-3 font-medium text-slate-600 dark:bg-slate-700 dark:text-slate-300">取消</button>
          <button @click="handleSave" class="flex-1 rounded-xl bg-orange-500 py-3 font-medium text-white shadow-lg shadow-orange-500/30">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>
