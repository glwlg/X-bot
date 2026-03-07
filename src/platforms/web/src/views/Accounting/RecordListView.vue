<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAccountingStore } from '@/stores/accounting'
import { getRecords, type RecordItem } from '@/api/accounting'
import { Loader2, Search, Calendar as CalendarIcon, X, ChevronRight } from 'lucide-vue-next'

const router = useRouter()
const store = useAccountingStore()
const loading = ref(false)
const records = ref<RecordItem[]>([])

const keyword = ref('')
const searchInput = ref('')

const startDate = ref('')
const endDate = ref('')
const selectedType = ref('')

const formatMoney = (n: number) =>
    new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(n)

const formatDate = (iso: string) => {
    const d = new Date(iso)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

const loadData = async () => {
    if (!store.currentBookId) return
    loading.value = true
    try {
        const res = await getRecords(
            store.currentBookId,
            200,
            keyword.value || undefined,
            startDate.value || undefined,
            endDate.value || undefined,
            selectedType.value || undefined
        )
        records.value = res.data
    } catch (e) {
        console.error('Failed to load records', e)
    } finally {
        loading.value = false
    }
}

const applySearch = () => {
    keyword.value = searchInput.value
    loadData()
}

const clearFilters = () => {
    startDate.value = ''
    endDate.value = ''
    selectedType.value = ''
    keyword.value = ''
    searchInput.value = ''
    loadData()
}

const openRecordDetail = (id: number) => {
    router.push({ name: 'RecordDetail', params: { id } })
}

watch([startDate, endDate, selectedType], () => loadData())

onMounted(async () => {
    if (!store.currentBookId) await store.fetchBooks()
    if (store.currentBookId) {
        await loadData()
    }
})
</script>

<template>
  <div class="pb-10 pt-4 px-4">
    <h2 class="text-xl font-bold text-theme-primary mb-4">交易明细</h2>

    <!-- Search & Filter Bar -->
    <div class="flex flex-col gap-3 mb-4">
      <div class="relative">
        <Search class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
        <input
          v-model="searchInput"
          @keyup.enter="applySearch"
          type="text"
          placeholder="搜索备注或付款对象(回车)"
          class="w-full pl-9 pr-4 py-2.5 rounded-xl border border-gray-200 dark:border-slate-700 bg-gray-50 dark:bg-slate-800 text-sm text-theme-primary focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />
      </div>
      
      <div class="flex gap-2 items-center overflow-x-auto no-scrollbar">
        <select
          v-model="selectedType"
          class="px-3 py-1.5 rounded-full text-xs font-medium bg-gray-100 dark:bg-slate-800 text-theme-secondary appearance-none outline-none border border-transparent focus:border-indigo-500"
        >
          <option value="">全部类型</option>
          <option value="支出">支出</option>
          <option value="收入">收入</option>
          <option value="转账">转账</option>
        </select>
        
        <div class="flex items-center gap-1 bg-gray-100 dark:bg-slate-800 rounded-full px-3 py-1 border border-transparent focus-within:border-indigo-500 text-xs text-theme-secondary">
          <CalendarIcon class="w-3.5 h-3.5" />
          <input v-model="startDate" type="date" class="bg-transparent outline-none w-[90px]" />
          <span>-</span>
          <input v-model="endDate" type="date" class="bg-transparent outline-none w-[90px]" />
        </div>
        
        <button v-if="keyword || startDate || endDate || selectedType" @click="clearFilters" class="p-1.5 rounded-full bg-rose-50 dark:bg-rose-900/30 text-rose-500 ml-auto">
          <X class="w-3.5 h-3.5" />
        </button>
      </div>
    </div>

    <!-- Record List -->
    <div class="rounded-2xl bg-white dark:bg-slate-800 shadow-sm border border-gray-100 dark:border-slate-700">
      <div v-if="loading" class="p-8 text-center text-theme-muted">
        <Loader2 class="w-5 h-5 animate-spin mx-auto mb-2 text-indigo-400" />
        加载中...
      </div>

      <div v-else-if="records.length === 0" class="p-8 text-center text-theme-muted text-sm">
        这里空空如也，找不到符合条件的记录
      </div>

      <ul v-else class="divide-y divide-gray-50 dark:divide-slate-700/50">
        <li
          v-for="rec in records"
          :key="rec.id"
          class="px-4 py-3 cursor-pointer hover:bg-gray-50 dark:hover:bg-slate-700/30 transition"
          @click="openRecordDetail(rec.id)"
        >
          <div class="flex items-start justify-between">
            <!-- Left -->
            <div class="flex items-start gap-3">
              <div :class="[
                'w-2 h-2 mt-2 rounded-full flex-shrink-0',
                rec.type === '收入' ? 'bg-indigo-400' : (rec.type === '转账' ? 'bg-amber-400' : 'bg-rose-400')
              ]" />
              <div>
                <p class="font-medium text-theme-primary text-sm">{{ rec.category || rec.payee || rec.remark || '未分类' }}</p>
                <p class="text-xs text-theme-muted mt-0.5">
                  {{ formatDate(rec.record_time) }}
                  <template v-if="rec.payee"> · {{ rec.payee }}</template>
                  <template v-if="rec.remark"> · {{ rec.remark }}</template>
                </p>
              </div>
            </div>
            <!-- Right -->
            <div class="text-right flex-shrink-0">
              <div class="flex items-center justify-end gap-1">
                <p :class="[
                  'font-semibold text-sm',
                  rec.type === '收入' ? 'text-indigo-500' : (rec.type === '转账' ? 'text-amber-500' : 'text-rose-500')
                ]">
                  {{ rec.type === '收入' ? '+' : '' }}¥{{ formatMoney(rec.amount) }}
                </p>
                <ChevronRight class="w-3.5 h-3.5 text-theme-muted" />
              </div>
              <p v-if="rec.account" class="text-[10px] text-theme-muted mt-0.5 px-1.5 py-0.5 rounded bg-gray-50 dark:bg-slate-700 inline-block">
                <template v-if="rec.type === '转账'">{{ rec.account }} -> {{ rec.target_account }}</template>
                <template v-else>{{ rec.account }}</template>
              </p>
            </div>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>
