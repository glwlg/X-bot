<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Loader2, Pencil, Plus, Rss, Trash2 } from 'lucide-vue-next'
import request from '@/api/request'

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

const closeDialog = () => {
    showDialog.value = false
    resetForm()
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
        closeDialog()
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

const totalFeeds = computed(() => subs.value.length)
const domainCount = computed(() => {
    const hosts = new Set<string>()
    subs.value.forEach((sub) => {
        try {
            hosts.add(new URL(sub.feed_url).host)
        } catch {
            // ignore invalid url
        }
    })
    return hosts.size
})
</script>

<template>
  <div class="space-y-6 p-6 md:p-8">
    <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex items-center justify-between">
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Module</div>
          <h2 class="mt-1 text-2xl font-semibold text-slate-900">RSS 订阅</h2>
        </div>
        <button @click="openCreate" class="inline-flex items-center gap-2 rounded-2xl bg-orange-500 px-4 py-3 text-sm font-medium text-white shadow-lg shadow-orange-500/20 transition hover:bg-orange-600">
          <Plus class="h-4 w-4" />
          添加订阅
        </button>
      </div>

      <div class="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <div class="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Feeds</div>
          <div class="mt-3 text-3xl font-semibold text-slate-950">{{ totalFeeds }}</div>
        </div>
        <div class="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Domains</div>
          <div class="mt-3 text-3xl font-semibold text-slate-950">{{ domainCount }}</div>
        </div>
        <div class="rounded-[24px] border border-slate-200 bg-slate-950 p-4 text-slate-100">
          <div class="text-xs uppercase tracking-[0.24em] text-slate-500">Mode</div>
          <div class="mt-3 text-2xl font-semibold">订阅池</div>
        </div>
      </div>
    </section>

    <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex items-center justify-between gap-3">
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">List</div>
          <h3 class="mt-1 text-xl font-semibold text-slate-950">订阅列表</h3>
        </div>
        <div class="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-sm text-slate-600">
          {{ totalFeeds }} 项
        </div>
      </div>

      <div class="mt-6">
        <div v-if="loading" class="flex justify-center py-8">
          <Loader2 class="h-8 w-8 animate-spin text-orange-500" />
        </div>

        <div v-else-if="subs.length === 0" class="flex flex-col items-center justify-center py-20 text-slate-400">
          <Rss class="mb-4 h-16 w-16 text-slate-300" />
          <p>暂无 RSS 订阅</p>
        </div>

        <div v-else class="space-y-4">
          <div
            v-for="sub in subs"
            :key="sub.id"
            class="rounded-[24px] border border-slate-200 bg-slate-50 p-5"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-3">
                  <h3 class="text-lg font-semibold text-slate-950">{{ sub.title }}</h3>
                  <span class="rounded-full border border-orange-200 bg-orange-50 px-3 py-1 text-xs font-medium uppercase tracking-[0.2em] text-orange-700">
                    RSS
                  </span>
                </div>
                <p class="mt-3 break-all text-sm text-slate-500">{{ sub.feed_url }}</p>
              </div>
              <div class="flex shrink-0 items-center gap-2">
                <button @click="openEdit(sub)" class="rounded-xl border border-slate-200 bg-white p-2 text-slate-500 transition hover:border-blue-200 hover:text-blue-600">
                  <Pencil class="h-4 w-4" />
                </button>
                <button @click="handleDelete(sub.id)" class="rounded-xl border border-slate-200 bg-white p-2 text-slate-500 transition hover:border-rose-200 hover:text-rose-600">
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
          <h2 class="mt-1 text-xl font-semibold text-slate-950">{{ editingId ? '编辑 RSS 订阅' : '添加 RSS 订阅' }}</h2>
        </div>
        <div class="space-y-4 p-6">
          <div>
            <label class="mb-1 block text-sm text-slate-500">标题名称</label>
            <input
              v-model="formData.title"
              type="text"
              class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-slate-900 focus:border-orange-500 focus:outline-none focus:ring-2 focus:ring-orange-500/20"
              placeholder="例如: 极客公园"
            >
          </div>
          <div>
            <label class="mb-1 block text-sm text-slate-500">RSS 链接</label>
            <textarea
              v-model="formData.feed_url"
              rows="3"
              class="w-full resize-none rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-slate-900 focus:border-orange-500 focus:outline-none focus:ring-2 focus:ring-orange-500/20"
              placeholder="https://..."
            ></textarea>
          </div>
        </div>
        <div class="flex gap-3 border-t border-slate-200 p-6">
          <button @click="closeDialog" class="flex-1 rounded-2xl border border-slate-200 bg-white py-3 font-medium text-slate-600">取消</button>
          <button @click="handleSave" class="flex-1 rounded-2xl bg-orange-500 py-3 font-medium text-white shadow-lg shadow-orange-500/25">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>
