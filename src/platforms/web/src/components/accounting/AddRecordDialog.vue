<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { createRecord, getCategories, getAccounts, type CategoryItem, type AccountItem } from '@/api/accounting'
import { X, Delete, Loader2 } from 'lucide-vue-next'
import { appendOperationLog } from '@/utils/accountingLocal'

const props = defineProps<{
    bookId: number
}>()

const emit = defineEmits<{
    close: []
    saved: []
}>()

// Tab: 支出/收入/转账
const activeTab = ref<'支出' | '收入' | '转账'>('支出')
const tabs = ['支出', '收入', '转账'] as const

// Amount input
const amountStr = ref('0')

// Categories
const categories = ref<CategoryItem[]>([])
const selectedCategory = ref('')

// Accounts
const accounts = ref<AccountItem[]>([])
const selectedAccount = ref('')
const selectedTargetAccount = ref('')

// Other fields
const remark = ref('')
const saving = ref(false)

// Default categories per type
const defaultCategories: Record<string, string[]> = {
    '支出': ['餐饮', '购物', '日用', '交通', '买菜', '水果', '零食', '运动', '娱乐', '通讯', '服饰', '其他'],
    '收入': ['工资', '奖金', '红包', '理财', '报销', '兼职', '其他'],
    '转账': ['转账'],
}

const displayCategories = computed(() => {
    const userCats = categories.value
        .filter(c => c.type === activeTab.value)
        .map(c => c.name)
    const defaults = defaultCategories[activeTab.value] || []
    const all = [...new Set([...userCats, ...defaults])]
    return all
})

const handleKeyPress = (key: string) => {
    if (key === 'C') {
        amountStr.value = '0'
    } else if (key === '⌫') {
        if (amountStr.value.length > 1) {
            amountStr.value = amountStr.value.slice(0, -1)
        } else {
            amountStr.value = '0'
        }
    } else if (key === '.') {
        if (!amountStr.value.includes('.')) {
            amountStr.value += '.'
        }
    } else if (key === 'OK') {
        handleSave()
    } else {
        // Number
        if (amountStr.value === '0') {
            amountStr.value = key
        } else {
            // Limit decimal places
            const parts = amountStr.value.split('.')
            if (parts.length === 2 && (parts[1]?.length ?? 0) >= 2) return
            amountStr.value += key
        }
    }
}

const handleSave = async () => {
    const amount = parseFloat(amountStr.value)
    if (!amount || amount <= 0) return

    saving.value = true
    try {
        await createRecord(props.bookId, {
            type: activeTab.value,
            amount,
            category_name: selectedCategory.value || '未分类',
            account_name: selectedAccount.value,
            target_account_name: selectedTargetAccount.value,
            remark: remark.value,
            record_time: new Date().toISOString(),
        })
        appendOperationLog(
            props.bookId,
            '新增交易',
            `${activeTab.value} · ¥${amount.toFixed(2)} · ${selectedCategory.value || '未分类'}`,
        )
        emit('saved')
    } catch (e) {
        console.error('Failed to save', e)
        alert('保存失败')
    } finally {
        saving.value = false
    }
}

onMounted(async () => {
    try {
        const [catRes, accRes] = await Promise.all([
            getCategories(props.bookId),
            getAccounts(props.bookId),
        ])
        categories.value = catRes.data
        accounts.value = accRes.data
    } catch {
        // ignore
    }
})

const keyRows = [
    ['C', '÷', '×', '⌫'],
    ['1', '2', '3', '-'],
    ['4', '5', '6', '+'],
    ['7', '8', '9', 'OK_TOP'],
    ['()', '0', '.', 'OK_BOT'],
]
</script>

<template>
  <!-- Full screen overlay -->
  <div class="fixed inset-0 z-50 flex flex-col bg-gradient-to-b from-teal-50 to-white dark:from-slate-900 dark:to-slate-950">
    <!-- Header -->
    <div class="flex items-center justify-between px-4 py-3 bg-gradient-to-r from-teal-500 to-teal-400 text-white">
      <button @click="emit('close')" class="p-1">
        <X class="w-5 h-5" />
      </button>
      <span class="font-semibold">记一笔</span>
      <span class="text-sm opacity-80">设置</span>
    </div>

    <!-- Scrollable Content -->
    <div class="flex-1 overflow-auto">
      <!-- Type Tabs -->
      <div class="flex px-4 pt-4 gap-2">
        <button
          v-for="tab in tabs"
          :key="tab"
          @click="activeTab = tab; selectedCategory = ''"
          :class="[
            'px-6 py-2 rounded-full text-sm font-medium transition',
            activeTab === tab
              ? 'bg-teal-500 text-white shadow-sm'
              : 'bg-gray-100 dark:bg-slate-800 text-theme-secondary'
          ]"
        >
          {{ tab }}
        </button>
      </div>

      <!-- Amount -->
      <div class="px-4 py-4">
        <p :class="[
          'text-4xl font-bold',
          activeTab === '支出' ? 'text-rose-500' : 'text-teal-500'
        ]">
          {{ activeTab === '支出' ? '-' : activeTab === '收入' ? '+' : '' }}¥{{ amountStr }}
        </p>
      </div>

      <div class="border-t border-gray-100 dark:border-slate-800" />

      <!-- Categories Grid -->
      <div class="px-4 py-3">
        <div class="grid grid-cols-3 gap-2">
          <button
            v-for="cat in displayCategories"
            :key="cat"
            @click="selectedCategory = cat"
            :class="[
              'py-2.5 rounded-xl text-sm font-medium border transition',
              selectedCategory === cat
                ? 'border-rose-300 dark:border-rose-700 text-rose-500 bg-rose-50 dark:bg-rose-900/20'
                : 'border-gray-200 dark:border-slate-700 text-theme-secondary hover:bg-gray-50 dark:hover:bg-slate-800'
            ]"
          >
            {{ cat }}
          </button>
        </div>
      </div>

      <!-- Account Selector -->
      <div class="px-4 py-2 border-t border-gray-100 dark:border-slate-800">
        <label class="text-xs text-teal-500 font-medium">账户</label>
        <select
          v-model="selectedAccount"
          class="w-full mt-1 px-3 py-2 rounded-xl border border-gray-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-theme-primary text-sm focus:outline-none focus:ring-2 focus:ring-teal-500"
        >
          <option value="">未指定</option>
          <option v-for="acc in accounts" :key="acc.id" :value="acc.name">
            {{ acc.name }} ({{ acc.type }})
          </option>
        </select>
      </div>

      <!-- Target account (for transfer) -->
      <div v-if="activeTab === '转账'" class="px-4 py-2">
        <label class="text-xs text-teal-500 font-medium">转入账户</label>
        <select
          v-model="selectedTargetAccount"
          class="w-full mt-1 px-3 py-2 rounded-xl border border-gray-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-theme-primary text-sm focus:outline-none focus:ring-2 focus:ring-teal-500"
        >
          <option value="">未指定</option>
          <option v-for="acc in accounts" :key="acc.id" :value="acc.name">
            {{ acc.name }}
          </option>
        </select>
      </div>

      <!-- Remark -->
      <div class="px-4 py-2 border-t border-gray-100 dark:border-slate-800">
        <label class="text-xs text-teal-500 font-medium">备注</label>
        <input
          v-model="remark"
          type="text"
          placeholder="点击添加备注"
          class="w-full mt-1 px-3 py-2 rounded-xl border border-gray-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-theme-primary text-sm focus:outline-none focus:ring-2 focus:ring-teal-500"
        />
      </div>
    </div>

    <!-- Calculator Keyboard -->
    <div class="bg-white dark:bg-slate-900 border-t border-gray-200 dark:border-slate-700">
      <div class="grid grid-cols-4">
        <template v-for="(row, ri) in keyRows" :key="ri">
          <template v-for="key in row" :key="key">
            <!-- OK button spans 2 rows -->
            <button
              v-if="key === 'OK_TOP'"
              @click="handleKeyPress('OK')"
              :disabled="saving"
              class="row-span-2 bg-teal-500 hover:bg-teal-600 text-white text-lg font-bold py-4 transition active:bg-teal-700 disabled:opacity-50 col-start-4 row-start-4 row-end-6"
              style="grid-row: span 2"
            >
              <Loader2 v-if="saving" class="w-5 h-5 animate-spin mx-auto" />
              <span v-else>OK</span>
            </button>
            <button
              v-else-if="key === 'OK_BOT'"
              class="hidden"
            />
            <button
              v-else-if="key === '⌫'"
              @click="handleKeyPress('⌫')"
              class="py-4 text-lg font-medium text-theme-primary hover:bg-gray-100 dark:hover:bg-slate-800 transition active:bg-gray-200"
            >
              <Delete class="w-5 h-5 mx-auto" />
            </button>
            <button
              v-else-if="key === 'C'"
              @click="handleKeyPress('C')"
              class="py-4 text-lg font-medium text-theme-secondary hover:bg-gray-100 dark:hover:bg-slate-800 transition"
            >
              C
            </button>
            <button
              v-else-if="['÷', '×', '-', '+'].includes(key)"
              @click="handleKeyPress(key)"
              class="py-4 text-lg font-medium text-theme-secondary hover:bg-gray-100 dark:hover:bg-slate-800 transition"
            >
              {{ key }}
            </button>
            <button
              v-else-if="key === '()'"
              @click="handleKeyPress(key)"
              class="py-4 text-lg font-medium text-theme-secondary hover:bg-gray-100 dark:hover:bg-slate-800 transition"
            >
              ( )
            </button>
            <button
              v-else
              @click="handleKeyPress(key)"
              class="py-4 text-xl font-medium text-theme-primary hover:bg-gray-100 dark:hover:bg-slate-800 transition active:bg-gray-200"
            >
              {{ key }}
            </button>
          </template>
        </template>
      </div>
    </div>
  </div>
</template>
