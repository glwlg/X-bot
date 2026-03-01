<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAccountingStore } from '@/stores/accounting'
import {
    getRecordDetail,
    updateRecord,
    deleteRecord,
    getCategories,
    getAccounts,
    type CategoryItem,
    type AccountItem,
    type RecordItem,
} from '@/api/accounting'
import { ChevronLeft, Loader2, Trash2 } from 'lucide-vue-next'
import { appendOperationLog } from '@/utils/accountingLocal'

const router = useRouter()
const route = useRoute()
const store = useAccountingStore()

const loading = ref(false)
const saving = ref(false)
const deleting = ref(false)
const categories = ref<CategoryItem[]>([])
const accounts = ref<AccountItem[]>([])
const originalRecord = ref<RecordItem | null>(null)
const loadFailed = ref(false)

const getRecordId = () => {
    const raw = route.params.id
    const value = Number(Array.isArray(raw) ? raw[0] : raw)
    return Number.isFinite(value) ? value : 0
}

const recordId = getRecordId()

const form = ref({
    type: '支出',
    amount: '',
    category_name: '未分类',
    account_name: '',
    target_account_name: '',
    payee: '',
    remark: '',
    record_time: '',
})

const tabs = ['支出', '收入', '转账'] as const

const displayCategories = computed(() => {
    const names = categories.value
        .filter(c => c.type === form.value.type)
        .map(c => c.name)

    if (form.value.category_name && !names.includes(form.value.category_name)) {
        names.unshift(form.value.category_name)
    }
    if (!names.includes('未分类')) {
        names.unshift('未分类')
    }

    return names
})

const formatToDatetimeLocal = (iso: string) => {
    if (!iso) return ''
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return ''

    const pad = (n: number) => String(n).padStart(2, '0')
    const yyyy = d.getFullYear()
    const mm = pad(d.getMonth() + 1)
    const dd = pad(d.getDate())
    const hh = pad(d.getHours())
    const min = pad(d.getMinutes())
    return `${yyyy}-${mm}-${dd}T${hh}:${min}`
}

const toIsoString = (localValue: string) => {
    if (!localValue) return ''
    const d = new Date(localValue)
    return Number.isNaN(d.getTime()) ? '' : d.toISOString()
}

const loadData = async () => {
    if (!recordId) {
        loadFailed.value = true
        return
    }

    if (!store.currentBookId) {
        await store.fetchBooks()
    }

    if (!store.currentBookId) {
        loadFailed.value = true
        return
    }

    loading.value = true
    loadFailed.value = false

    try {
        const [recordRes, categoryRes, accountRes] = await Promise.all([
            getRecordDetail(store.currentBookId, recordId),
            getCategories(store.currentBookId),
            getAccounts(store.currentBookId),
        ])

        const record = recordRes.data
        originalRecord.value = record
        categories.value = categoryRes.data
        accounts.value = accountRes.data

        form.value = {
            type: record.type || '支出',
            amount: String(record.amount || ''),
            category_name: record.category || '未分类',
            account_name: record.account || '',
            target_account_name: record.target_account || '',
            payee: record.payee || '',
            remark: record.remark || '',
            record_time: formatToDatetimeLocal(record.record_time),
        }
    } catch (e) {
        console.error('Failed to load record detail', e)
        loadFailed.value = true
    } finally {
        loading.value = false
    }
}

const handleSave = async () => {
    if (!store.currentBookId || !recordId) return

    const amount = Number(form.value.amount)
    if (!amount || amount <= 0) {
        alert('请输入正确的金额')
        return
    }

    saving.value = true
    try {
        const recordTime = toIsoString(form.value.record_time)
        await updateRecord(store.currentBookId, recordId, {
            type: form.value.type,
            amount,
            category_name: form.value.category_name?.trim() || '',
            account_name: form.value.account_name?.trim() || '',
            target_account_name: form.value.type === '转账'
                ? (form.value.target_account_name?.trim() || '')
                : '',
            payee: form.value.payee?.trim() || '',
            remark: form.value.remark?.trim() || '',
            record_time: recordTime || undefined,
        })
        appendOperationLog(
            store.currentBookId,
            '更新交易',
            `ID ${recordId} · ${form.value.type} · ¥${amount.toFixed(2)}`,
        )
        alert('保存成功')
        await loadData()
    } catch (e) {
        console.error('Failed to update record', e)
        alert('保存失败，请稍后重试')
    } finally {
        saving.value = false
    }
}

const handleDelete = async () => {
    if (!store.currentBookId || !recordId) return
    if (!confirm('确定删除这条记录吗？删除后可在操作日志里回滚。')) return

    const snapshot = originalRecord.value
    deleting.value = true
    try {
        await deleteRecord(store.currentBookId, recordId)
        if (snapshot) {
            const rollbackType = snapshot.type === '收入' || snapshot.type === '转账' ? snapshot.type : '支出'
            appendOperationLog(
                store.currentBookId,
                '删除交易',
                `ID ${recordId} · ${snapshot.type} · ¥${snapshot.amount.toFixed(2)}`,
                {
                    rollback: {
                        kind: 'record',
                        data: {
                            type: rollbackType,
                            amount: snapshot.amount,
                            category_name: snapshot.category || '未分类',
                            account_name: snapshot.account || '',
                            target_account_name: snapshot.target_account || '',
                            payee: snapshot.payee || '',
                            remark: snapshot.remark || '',
                            record_time: snapshot.record_time,
                        },
                    },
                },
            )
        } else {
            appendOperationLog(store.currentBookId, '删除交易', `ID ${recordId}`)
        }
        alert('删除成功')
        router.replace('/accounting/records')
    } catch (e) {
        console.error('Failed to delete record', e)
        alert('删除失败，请稍后重试')
    } finally {
        deleting.value = false
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
        <h1 class="text-lg font-bold text-slate-800 dark:text-white">交易详情</h1>
        <button
          @click="handleSave"
          :disabled="saving || deleting || loading || loadFailed"
          class="px-3 py-1.5 rounded-lg bg-teal-500 text-white text-sm font-medium disabled:opacity-50"
        >
          <Loader2 v-if="saving" class="w-4 h-4 animate-spin" />
          <span v-else>保存</span>
        </button>
      </div>
    </header>

    <main class="flex-1 overflow-y-auto p-4 safe-bottom">
      <div v-if="loading" class="flex justify-center py-10">
        <Loader2 class="w-6 h-6 animate-spin text-teal-500" />
      </div>

      <div v-else-if="loadFailed" class="bg-white dark:bg-slate-800 rounded-2xl p-6 text-center text-sm text-slate-500">
        记录不存在或加载失败
      </div>

      <div v-else class="space-y-4">
        <div class="bg-white dark:bg-slate-800 rounded-2xl p-4 shadow-sm border border-slate-100 dark:border-slate-700">
          <p class="text-xs text-slate-500 mb-2">交易类型</p>
          <div class="grid grid-cols-3 gap-2">
            <button
              v-for="tab in tabs"
              :key="tab"
              @click="form.type = tab"
              :class="[
                'py-2 rounded-lg text-sm font-medium transition',
                form.type === tab
                  ? 'bg-teal-500 text-white'
                  : 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300'
              ]"
            >
              {{ tab }}
            </button>
          </div>
        </div>

        <div class="bg-white dark:bg-slate-800 rounded-2xl p-4 shadow-sm border border-slate-100 dark:border-slate-700 space-y-4">
          <div>
            <label class="block text-xs text-slate-500 mb-1">金额</label>
            <input
              v-model="form.amount"
              type="number"
              step="0.01"
              min="0"
              class="w-full px-3 py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500"
            />
          </div>

          <div>
            <label class="block text-xs text-slate-500 mb-1">分类</label>
            <select
              v-model="form.category_name"
              class="w-full px-3 py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 text-slate-800 dark:text-white focus:outline-none"
            >
              <option v-for="name in displayCategories" :key="name" :value="name">{{ name }}</option>
            </select>
          </div>

          <div>
            <label class="block text-xs text-slate-500 mb-1">账户</label>
            <select
              v-model="form.account_name"
              class="w-full px-3 py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 text-slate-800 dark:text-white focus:outline-none"
            >
              <option value="">未指定</option>
              <option v-for="acc in accounts" :key="acc.id" :value="acc.name">{{ acc.name }} ({{ acc.type }})</option>
            </select>
          </div>

          <div v-if="form.type === '转账'">
            <label class="block text-xs text-slate-500 mb-1">转入账户</label>
            <select
              v-model="form.target_account_name"
              class="w-full px-3 py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 text-slate-800 dark:text-white focus:outline-none"
            >
              <option value="">未指定</option>
              <option v-for="acc in accounts" :key="acc.id" :value="acc.name">{{ acc.name }}</option>
            </select>
          </div>

          <div>
            <label class="block text-xs text-slate-500 mb-1">交易对象</label>
            <input
              v-model="form.payee"
              type="text"
              placeholder="例如：超市、公司、朋友"
              class="w-full px-3 py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500"
            />
          </div>

          <div>
            <label class="block text-xs text-slate-500 mb-1">备注</label>
            <textarea
              v-model="form.remark"
              rows="3"
              placeholder="可选备注"
              class="w-full px-3 py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500"
            />
          </div>

          <div>
            <label class="block text-xs text-slate-500 mb-1">交易时间</label>
            <input
              v-model="form.record_time"
              type="datetime-local"
              class="w-full px-3 py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500"
            />
          </div>
        </div>

        <button
          @click="handleSave"
          :disabled="saving || deleting || loadFailed"
          class="w-full py-3 bg-teal-500 hover:bg-teal-600 text-white rounded-xl font-medium shadow-lg shadow-teal-500/30 disabled:opacity-50"
        >
          <Loader2 v-if="saving" class="w-4 h-4 animate-spin mx-auto" />
          <span v-else>保存修改</span>
        </button>

        <button
          @click="handleDelete"
          :disabled="saving || deleting || loadFailed"
          class="w-full py-3 rounded-xl font-medium border border-rose-200 text-rose-600 bg-rose-50 hover:bg-rose-100 disabled:opacity-50 flex items-center justify-center gap-1"
        >
          <Loader2 v-if="deleting" class="w-4 h-4 animate-spin" />
          <template v-else>
            <Trash2 class="w-4 h-4" />
            删除记录
          </template>
        </button>
      </div>
    </main>
  </div>
</template>
