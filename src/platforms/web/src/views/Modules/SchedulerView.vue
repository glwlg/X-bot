<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { Loader2, Plus, Trash2, Clock, CalendarClock, Pencil } from 'lucide-vue-next'
import request from '@/api/request'

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

const closeDialog = () => {
    showDialog.value = false
    formData.value = { crontab: '', instruction: '' }
    editingId.value = null
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
        closeDialog()
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

const activeCount = computed(() => tasks.value.filter((task) => task.is_active).length)
const pausedCount = computed(() => tasks.value.length - activeCount.value)
</script>

<template>
  <div class="space-y-6 p-6 md:p-8">
    <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex items-center justify-between">
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Module</div>
          <h2 class="mt-1 text-2xl font-semibold text-slate-900">定时任务</h2>
        </div>
        <button @click="openCreate" class="inline-flex items-center gap-2 rounded-2xl bg-blue-500 px-4 py-3 text-sm font-medium text-white shadow-lg shadow-blue-500/20 transition hover:bg-blue-600">
          <Plus class="h-4 w-4" />
          添加任务
        </button>
      </div>

      <div class="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <div class="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Tasks</div>
          <div class="mt-3 text-3xl font-semibold text-slate-950">{{ tasks.length }}</div>
        </div>
        <div class="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Active</div>
          <div class="mt-3 text-3xl font-semibold text-slate-950">{{ activeCount }}</div>
        </div>
        <div class="rounded-[24px] border border-slate-200 bg-slate-950 p-4 text-slate-100">
          <div class="text-xs uppercase tracking-[0.24em] text-slate-500">Paused</div>
          <div class="mt-3 text-2xl font-semibold">{{ pausedCount }}</div>
        </div>
      </div>
    </section>

    <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex items-center justify-between gap-3">
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">List</div>
          <h3 class="mt-1 text-xl font-semibold text-slate-950">任务列表</h3>
        </div>
        <div class="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-sm text-slate-600">
          {{ tasks.length }} 项
        </div>
      </div>

      <div class="mt-6">
        <div v-if="loading" class="flex justify-center py-8">
          <Loader2 class="h-8 w-8 animate-spin text-blue-500" />
        </div>

        <div v-else-if="tasks.length === 0" class="flex flex-col items-center justify-center py-20 text-slate-400">
          <CalendarClock class="mb-4 h-16 w-16 text-slate-300" />
          <p>暂无定时任务</p>
        </div>

        <div v-else class="space-y-4">
          <div
            v-for="task in tasks"
            :key="task.id"
            class="rounded-[24px] border border-slate-200 bg-slate-50 p-5"
            :class="{ 'opacity-60': !task.is_active }"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-3">
                  <span class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600">
                    <Clock class="h-3.5 w-3.5" />
                    <span class="font-mono">{{ task.crontab }}</span>
                  </span>
                  <span
                    class="rounded-full px-3 py-1 text-xs font-medium uppercase tracking-[0.2em]"
                    :class="task.is_active ? 'border border-emerald-200 bg-emerald-50 text-emerald-700' : 'border border-slate-200 bg-white text-slate-500'"
                  >
                    {{ task.is_active ? 'Active' : 'Paused' }}
                  </span>
                </div>
                <h3 class="mt-4 text-lg font-semibold text-slate-950">{{ task.instruction }}</h3>
              </div>
              <div class="flex shrink-0 items-center gap-2">
                <label class="relative inline-flex cursor-pointer items-center">
                  <input type="checkbox" :checked="task.is_active" @change="toggleStatus(task)" class="peer sr-only">
                  <div class="h-6 w-11 rounded-full bg-slate-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:border after:border-slate-200 after:bg-white after:transition-all after:content-[''] peer-checked:bg-blue-500 peer-checked:after:translate-x-full"></div>
                </label>
                <button @click="openEdit(task)" class="rounded-xl border border-slate-200 bg-white p-2 text-slate-500 transition hover:border-blue-200 hover:text-blue-600">
                  <Pencil class="h-4 w-4" />
                </button>
                <button @click="handleDelete(task.id)" class="rounded-xl border border-slate-200 bg-white p-2 text-slate-500 transition hover:border-rose-200 hover:text-rose-600">
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
          <h2 class="mt-1 text-xl font-semibold text-slate-950">{{ editingId ? '编辑任务' : '添加任务' }}</h2>
        </div>
        <div class="space-y-4 p-6">
          <div>
            <label class="mb-1 block text-sm text-slate-500">指令内容</label>
            <textarea v-model="formData.instruction" rows="4" class="w-full resize-none rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20" placeholder="例如: 播报今天的天气"></textarea>
          </div>
          <div>
            <label class="mb-1 block text-sm text-slate-500">Crontab 表达式</label>
            <input v-model="formData.crontab" type="text" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20" placeholder="0 8 * * *">
          </div>
        </div>
        <div class="flex gap-3 border-t border-slate-200 p-6">
          <button @click="closeDialog" class="flex-1 rounded-2xl border border-slate-200 bg-white py-3 font-medium text-slate-600">取消</button>
          <button @click="handleSave" class="flex-1 rounded-2xl bg-blue-500 py-3 font-medium text-white shadow-lg shadow-blue-500/25">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>
