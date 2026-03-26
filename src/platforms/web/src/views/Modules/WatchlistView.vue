<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { Loader2, Plus, Trash2, TrendingUp, Pencil, RefreshCw } from 'lucide-vue-next'
import request from '@/api/request'

interface Stock {
    stock_code: string
    stock_name: string
    platform: string
    price: number
    change: number
    percent: number
    high: number
    low: number
    open: number
    yesterday_close: number
}

const stocks = ref<Stock[]>([])
const loading = ref(false)
const refreshing = ref(false)
const showDialog = ref(false)
const editingCode = ref<string | null>(null)
const formData = ref({ stock_code: '', stock_name: '' })

const loadData = async (isRefresh = false) => {
    if (isRefresh) {
        refreshing.value = true
    } else {
        loading.value = true
    }
    try {
        const res = await request('/watchlist', { method: 'GET' })
        stocks.value = res.data || []
    } catch (e) {
        console.error(e)
    } finally {
        loading.value = false
        refreshing.value = false
    }
}

const openCreate = () => {
    editingCode.value = null
    formData.value = { stock_code: '', stock_name: '' }
    showDialog.value = true
}

const closeDialog = () => {
    showDialog.value = false
    editingCode.value = null
    formData.value = { stock_code: '', stock_name: '' }
}

const openEdit = (stock: Stock) => {
    editingCode.value = stock.stock_code
    formData.value = { stock_code: stock.stock_code, stock_name: stock.stock_name }
    showDialog.value = true
}

const handleSave = async () => {
    if (!formData.value.stock_code.trim() || !formData.value.stock_name.trim()) return
    try {
        if (editingCode.value) {
            await request(`/watchlist/${encodeURIComponent(editingCode.value)}`, {
                method: 'PUT',
                data: formData.value,
            })
        } else {
            await request('/watchlist', {
                method: 'POST',
                data: formData.value,
            })
        }
        closeDialog()
        loadData()
    } catch (e: any) {
        alert(e?.response?.data?.detail || '操作失败')
    }
}

const handleDelete = async (code: string) => {
    if (!confirm(`确定移除 ${code} 吗？`)) return
    try {
        await request(`/watchlist/${encodeURIComponent(code)}`, { method: 'DELETE' })
        loadData()
    } catch (e) {
        console.error(e)
    }
}

const priceColor = (change: number) => {
    if (change > 0) return 'text-red-500'
    if (change < 0) return 'text-green-600'
    return 'text-slate-500'
}

const formatPercent = (change: number, percent: number) => {
    const sign = change > 0 ? '+' : ''
    return `${sign}${percent.toFixed(2)}%`
}

const formatChange = (change: number) => {
    const sign = change > 0 ? '+' : ''
    return `${sign}${change.toFixed(2)}`
}

onMounted(() => {
    loadData()
})

const gainersCount = computed(() => stocks.value.filter((stock) => stock.change > 0).length)
const losersCount = computed(() => stocks.value.filter((stock) => stock.change < 0).length)
</script>

<template>
  <div class="space-y-6 p-6 md:p-8">
    <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex items-center justify-between">
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Module</div>
          <h2 class="mt-1 text-2xl font-semibold text-slate-900">自选股管理</h2>
        </div>
        <div class="flex items-center gap-2">
          <button @click="loadData(true)" class="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-100" :class="{ 'opacity-70': refreshing }">
            <RefreshCw class="h-4 w-4" :class="{ 'animate-spin': refreshing }" />
            刷新
          </button>
          <button @click="openCreate" class="inline-flex items-center gap-2 rounded-2xl bg-red-500 px-4 py-3 text-sm font-medium text-white shadow-lg shadow-red-500/20 transition hover:bg-red-600">
            <Plus class="h-4 w-4" />
            添加股票
          </button>
        </div>
      </div>

      <div class="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <div class="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Watchlist</div>
          <div class="mt-3 text-3xl font-semibold text-slate-950">{{ stocks.length }}</div>
        </div>
        <div class="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Up</div>
          <div class="mt-3 text-3xl font-semibold text-red-500">{{ gainersCount }}</div>
        </div>
        <div class="rounded-[24px] border border-slate-200 bg-slate-950 p-4 text-slate-100">
          <div class="text-xs uppercase tracking-[0.24em] text-slate-500">Down</div>
          <div class="mt-3 text-2xl font-semibold text-emerald-400">{{ losersCount }}</div>
        </div>
      </div>
    </section>

    <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex items-center justify-between gap-3">
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">List</div>
          <h3 class="mt-1 text-xl font-semibold text-slate-950">股票列表</h3>
        </div>
        <div class="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-sm text-slate-600">
          {{ stocks.length }} 项
        </div>
      </div>

      <div class="mt-6">
        <div v-if="loading" class="flex justify-center py-8">
          <Loader2 class="h-8 w-8 animate-spin text-red-500" />
        </div>

        <div v-else-if="stocks.length === 0" class="flex flex-col items-center justify-center py-20 text-slate-400">
          <TrendingUp class="mb-4 h-16 w-16 text-slate-300" />
          <p>暂无自选股</p>
        </div>

        <div v-else class="space-y-3">
          <div
            v-for="stock in stocks"
            :key="stock.stock_code"
            class="rounded-[24px] border border-slate-200 bg-slate-50 p-5"
          >
            <div class="flex items-center justify-between gap-3">
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-3">
                  <h3 class="truncate text-lg font-semibold text-slate-950">{{ stock.stock_name }}</h3>
                  <span class="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs uppercase tracking-[0.2em] text-slate-500">
                    {{ stock.platform }}
                  </span>
                </div>
                <p class="mt-2 font-mono text-sm text-slate-400">{{ stock.stock_code }}</p>
              </div>

              <div class="text-right">
                <p class="font-mono text-xl font-semibold" :class="priceColor(stock.change)">
                  {{ stock.price ? stock.price.toFixed(2) : '--' }}
                </p>
                <div class="mt-1 flex items-center justify-end gap-2 font-mono text-xs" :class="priceColor(stock.change)">
                  <span>{{ stock.price ? formatChange(stock.change) : '--' }}</span>
                  <span>{{ stock.price ? formatPercent(stock.change, stock.percent) : '--' }}</span>
                </div>
              </div>

              <div class="flex shrink-0 items-center gap-2">
                <button @click="openEdit(stock)" class="rounded-xl border border-slate-200 bg-white p-2 text-slate-500 transition hover:border-blue-200 hover:text-blue-600">
                  <Pencil class="h-4 w-4" />
                </button>
                <button @click="handleDelete(stock.stock_code)" class="rounded-xl border border-slate-200 bg-white p-2 text-slate-500 transition hover:border-rose-200 hover:text-rose-600">
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
          <h2 class="mt-1 text-xl font-semibold text-slate-950">{{ editingCode ? '编辑自选股' : '添加自选股' }}</h2>
        </div>
        <div class="space-y-4 p-6">
          <div>
            <label class="mb-1 block text-sm text-slate-500">股票代码</label>
            <input
              v-model="formData.stock_code"
              type="text"
              :disabled="!!editingCode"
              class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-slate-900 focus:border-red-500 focus:outline-none focus:ring-2 focus:ring-red-500/20 disabled:opacity-50"
              placeholder="例如: sh600519"
            >
          </div>
          <div>
            <label class="mb-1 block text-sm text-slate-500">股票名称</label>
            <input
              v-model="formData.stock_name"
              type="text"
              class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-slate-900 focus:border-red-500 focus:outline-none focus:ring-2 focus:ring-red-500/20"
              placeholder="例如: 贵州茅台"
            >
          </div>
        </div>
        <div class="flex gap-3 border-t border-slate-200 p-6">
          <button @click="closeDialog" class="flex-1 rounded-2xl border border-slate-200 bg-white py-3 font-medium text-slate-600">取消</button>
          <button @click="handleSave" class="flex-1 rounded-2xl bg-red-500 py-3 font-medium text-white shadow-lg shadow-red-500/25">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>
