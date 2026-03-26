<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { Loader2, Plus, Trash2, Activity, Pencil } from 'lucide-vue-next'
import request from '@/api/request'

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

const closeDialog = () => {
    showDialog.value = false
    editingIndex.value = null
    formData.value = { item: '' }
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
        closeDialog()
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

const itemCount = computed(() => items.value.length)
</script>

<template>
  <div class="space-y-6 p-6 md:p-8">
    <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex items-center justify-between">
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Module</div>
          <h2 class="mt-1 text-2xl font-semibold text-slate-900">心跳监控</h2>
        </div>
        <button @click="openCreate" class="inline-flex items-center gap-2 rounded-2xl bg-purple-500 px-4 py-3 text-sm font-medium text-white shadow-lg shadow-purple-500/20 transition hover:bg-purple-600">
          <Plus class="h-4 w-4" />
          添加监控项
        </button>
      </div>

      <div class="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <div class="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Items</div>
          <div class="mt-3 text-3xl font-semibold text-slate-950">{{ itemCount }}</div>
        </div>
        <div class="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Source</div>
          <div class="mt-3 text-2xl font-semibold text-slate-950">API</div>
        </div>
        <div class="rounded-[24px] border border-slate-200 bg-slate-950 p-4 text-slate-100">
          <div class="text-xs uppercase tracking-[0.24em] text-slate-500">Mode</div>
          <div class="mt-3 text-2xl font-semibold">Watch</div>
        </div>
      </div>
    </section>

    <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex items-center justify-between gap-3">
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">List</div>
          <h3 class="mt-1 text-xl font-semibold text-slate-950">监控列表</h3>
        </div>
        <div class="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-sm text-slate-600">
          {{ itemCount }} 项
        </div>
      </div>

      <div class="mt-6">
        <div v-if="loading" class="flex justify-center py-8">
          <Loader2 class="h-8 w-8 animate-spin text-purple-500" />
        </div>

        <div v-else-if="items.length === 0" class="flex flex-col items-center justify-center py-20 text-slate-400">
          <Activity class="mb-4 h-16 w-16 text-slate-300" />
          <p>暂无监控项</p>
        </div>

        <div v-else class="space-y-4">
          <div
            v-for="(item, index) in items"
            :key="index"
            class="rounded-[24px] border border-slate-200 bg-slate-50 p-5"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0 flex-1">
                <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Item {{ index + 1 }}</div>
                <h3 class="mt-3 break-all text-lg font-semibold text-slate-950">{{ item }}</h3>
              </div>
              <div class="flex shrink-0 items-center gap-2">
                <button @click="openEdit(index, item)" class="rounded-xl border border-slate-200 bg-white p-2 text-slate-500 transition hover:border-purple-200 hover:text-purple-600">
                  <Pencil class="h-4 w-4" />
                </button>
                <button @click="handleDelete(index)" class="rounded-xl border border-slate-200 bg-white p-2 text-slate-500 transition hover:border-rose-200 hover:text-rose-600">
                  <Trash2 class="h-4 w-4" />
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <div v-if="showDialog" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4">
      <div class="w-full max-w-md overflow-hidden rounded-[28px] border border-slate-200 bg-white shadow-[0_24px_60px_rgba(15,23,42,0.2)]">
        <div class="border-b border-slate-200 px-6 py-5">
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Form</div>
          <h2 class="mt-1 text-xl font-semibold text-slate-950">{{ editingIndex !== null ? '编辑监控项' : '添加监控项' }}</h2>
        </div>
        <div class="space-y-4 p-6">
          <div>
            <label class="mb-1 block text-sm text-slate-500">监控指令或设备信息</label>
            <textarea v-model="formData.item" rows="3" class="w-full resize-none rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-slate-900 focus:border-purple-500 focus:outline-none focus:ring-2 focus:ring-purple-500/20" placeholder="例如: 检查服务器状态"></textarea>
          </div>
        </div>
        <div class="flex gap-3 border-t border-slate-200 p-6">
          <button @click="closeDialog" class="flex-1 rounded-2xl border border-slate-200 bg-white py-3 font-medium text-slate-600">取消</button>
          <button @click="handleSave" class="flex-1 rounded-2xl bg-purple-500 py-3 font-medium text-white shadow-lg shadow-purple-500/25">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>
