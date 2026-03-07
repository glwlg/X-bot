<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ChevronLeft, Plus, Trash2, TrendingUp, Pencil, RefreshCw } from 'lucide-vue-next'
import request from '@/api/request'

const router = useRouter()

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
        showDialog.value = false
        formData.value = { stock_code: '', stock_name: '' }
        editingCode.value = null
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
</script>

<template>
  <div class="h-screen flex flex-col bg-slate-50 dark:bg-slate-900 absolute inset-0 z-50">
    <header class="bg-white dark:bg-slate-800 shadow-sm relative z-10 safe-top">
      <div class="flex items-center justify-between h-14 px-4">
        <button @click="router.back()" class="p-2 -ml-2 text-slate-600 dark:text-slate-300">
          <ChevronLeft class="w-6 h-6" />
        </button>
        <h1 class="text-lg font-bold text-slate-800 dark:text-white">自选股管理</h1>
        <div class="flex items-center gap-1">
          <button @click="loadData(true)" class="p-2 text-slate-500 dark:text-slate-400" :class="{ 'animate-spin': refreshing }">
            <RefreshCw class="w-5 h-5" />
          </button>
          <button @click="openCreate" class="p-2 -mr-2 text-red-500 dark:text-red-400">
            <Plus class="w-6 h-6" />
          </button>
        </div>
      </div>
    </header>

    <main class="flex-1 overflow-y-auto p-4 safe-bottom">
      <div v-if="loading" class="flex justify-center py-8">
        <div class="w-8 h-8 rounded-full border-4 border-red-500/30 border-t-red-500 animate-spin"></div>
      </div>

      <div v-else-if="stocks.length === 0" class="flex flex-col items-center justify-center py-20 text-slate-400">
        <TrendingUp class="w-16 h-16 mb-4 text-slate-300" />
        <p>暂无自选股</p>
      </div>

      <div v-else class="space-y-3">
        <div
          v-for="stock in stocks"
          :key="stock.stock_code"
          class="bg-white dark:bg-slate-800 rounded-2xl p-4 shadow-sm border border-slate-100 dark:border-slate-700"
        >
          <div class="flex items-center justify-between">
            <!-- Left: name + code -->
            <div class="min-w-0 flex-shrink-0" style="width: 35%;">
              <h3 class="font-bold text-slate-800 dark:text-white text-base truncate">{{ stock.stock_name }}</h3>
              <p class="text-xs text-slate-400 font-mono mt-0.5">{{ stock.stock_code }}</p>
            </div>

            <!-- Center: price -->
            <div class="text-right flex-1 mx-2">
              <p class="text-lg font-bold font-mono" :class="priceColor(stock.change)">
                {{ stock.price ? stock.price.toFixed(2) : '--' }}
              </p>
              <div class="flex items-center justify-end gap-2 text-xs font-mono" :class="priceColor(stock.change)">
                <span>{{ stock.price ? formatChange(stock.change) : '--' }}</span>
                <span>{{ stock.price ? formatPercent(stock.change, stock.percent) : '--' }}</span>
              </div>
            </div>

            <!-- Right: actions -->
            <div class="flex items-center gap-0.5 shrink-0 ml-1">
              <button @click="openEdit(stock)" class="text-slate-400 hover:text-blue-500 transition p-1.5 rounded-md">
                <Pencil class="w-3.5 h-3.5" />
              </button>
              <button @click="handleDelete(stock.stock_code)" class="text-slate-400 hover:text-rose-500 transition p-1.5 rounded-md">
                <Trash2 class="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </main>

    <!-- Dialog -->
    <div v-if="showDialog" class="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4">
      <div class="bg-white dark:bg-slate-800 rounded-2xl w-full max-w-sm overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        <div class="p-4 border-b border-slate-100 dark:border-slate-700">
          <h2 class="text-lg font-bold text-center">{{ editingCode ? '编辑自选股' : '添加自选股' }}</h2>
        </div>
        <div class="p-4 space-y-4">
          <div>
            <label class="block text-sm text-slate-500 mb-1">股票代码</label>
            <input
              v-model="formData.stock_code"
              type="text"
              :disabled="!!editingCode"
              class="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-3 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-red-500/20 focus:border-red-500 disabled:opacity-50"
              placeholder="例如: sh600519"
            >
          </div>
          <div>
            <label class="block text-sm text-slate-500 mb-1">股票名称</label>
            <input
              v-model="formData.stock_name"
              type="text"
              class="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-3 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-red-500/20 focus:border-red-500"
              placeholder="例如: 贵州茅台"
            >
          </div>
        </div>
        <div class="p-4 flex gap-3 border-t border-slate-100 dark:border-slate-700">
          <button @click="showDialog = false" class="flex-1 py-3 bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 rounded-xl font-medium">取消</button>
          <button @click="handleSave" class="flex-1 py-3 bg-red-500 text-white rounded-xl font-medium shadow-lg shadow-red-500/30">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>
