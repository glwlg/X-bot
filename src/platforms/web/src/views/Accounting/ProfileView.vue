<script setup lang="ts">
import { ref, onMounted, type Component } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useAccountingStore } from '@/stores/accounting'
import { exportRecordsCsv, getStatsOverview, importCsv, type StatsOverview } from '@/api/accounting'
import { appendOperationLog } from '@/utils/accountingLocal'
import {
    Grid2x2, ListOrdered, Store, Tag,
    Download, Upload, Share2, BookOpen,
    Bot, Puzzle, ScrollText,
    ChevronRight, User, Settings
} from 'lucide-vue-next'

const authStore = useAuthStore()
const store = useAccountingStore()
const router = useRouter()


const overview = ref<StatsOverview>({ days: 0, transactions: 0, net_assets: 0 })
const loading = ref(false)
const uploading = ref(false)
const exporting = ref(false)
const sharing = ref(false)
const actionMessage = ref('')
const fileInput = ref<HTMLInputElement | null>(null)

type ManagementAction = 'category' | 'project' | 'merchant' | 'tag' | 'import' | 'export' | 'share' | 'book'
type SettingsAction = 'global' | 'auto' | 'extensions' | 'logs'

interface ManagementItem {
    icon: Component
    label: string
    color: string
    action: ManagementAction
}

interface SettingsItem {
    icon: Component
    label: string
    desc: string
    action: SettingsAction
}

const formatMoney = (n: number) =>
    new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(n)

const managementItems: ManagementItem[] = [
    { icon: Grid2x2, label: '分类', color: 'bg-indigo-500', action: 'category' },
    { icon: ListOrdered, label: '项目', color: 'bg-indigo-500', action: 'project' },
    { icon: Store, label: '商家', color: 'bg-indigo-500', action: 'merchant' },
    { icon: Tag, label: '标签', color: 'bg-indigo-500', action: 'tag' },
    { icon: Download, label: '导入', color: 'bg-indigo-500', action: 'import' },
    { icon: Upload, label: '导出', color: 'bg-indigo-500', action: 'export' },
    { icon: Share2, label: '共享', color: 'bg-indigo-500', action: 'share' },
    { icon: BookOpen, label: '账本', color: 'bg-indigo-500', action: 'book' },
]

const settingsItems: SettingsItem[] = [
    { icon: Settings, label: '全局设置', desc: '显示/通用设置', action: 'global' },
    { icon: Bot, label: '自动记账', desc: '自动化规则', action: 'auto' },
    { icon: Puzzle, label: '扩展组件', desc: '', action: 'extensions' },
    { icon: ScrollText, label: '操作日志', desc: '查看与撤回操作', action: 'logs' },
]

const setActionMessage = (text: string) => {
    actionMessage.value = text
    window.setTimeout(() => {
        if (actionMessage.value === text) {
            actionMessage.value = ''
        }
    }, 1800)
}

const triggerImport = () => {
    fileInput.value?.click()
}

const handleFileUpload = async (event: Event) => {
    const target = event.target as HTMLInputElement
    if (!store.currentBookId || !target.files?.length) return
    const file = target.files[0]
    if (!file) return
    uploading.value = true
    try {
        await importCsv(store.currentBookId, file)
        appendOperationLog(store.currentBookId, '导入CSV', file.name)
        setActionMessage('导入成功')
    } catch (e: any) {
        setActionMessage(e.response?.data?.detail || '导入失败')
    } finally {
        uploading.value = false
        target.value = ''
    }
}

const parseFilename = (contentDisposition: string | undefined) => {
    if (!contentDisposition) return ''
    const match = contentDisposition.match(/filename="?([^";]+)"?/)
    return match?.[1] ?? ''
}

const handleExport = async () => {
    if (!store.currentBookId) return
    exporting.value = true
    try {
        const res = await exportRecordsCsv(store.currentBookId)
        const filename = parseFilename(res.headers['content-disposition']) || `records_${Date.now()}.csv`
        const blob = new Blob([res.data], { type: 'text/csv;charset=utf-8;' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = filename
        document.body.appendChild(a)
        a.click()
        a.remove()
        URL.revokeObjectURL(url)

        appendOperationLog(store.currentBookId, '导出CSV', filename)
        setActionMessage('导出成功')
    } catch {
        setActionMessage('导出失败')
    } finally {
        exporting.value = false
    }
}

const handleShare = async () => {
    if (!store.currentBookId) return

    const userName = authStore.user?.display_name || authStore.user?.email || '用户'
    const text = [
        `Ikaros 智能记账`,
        `用户：${userName}`,
        `记账天数：${overview.value.days}`,
        `交易笔数：${overview.value.transactions}`,
        `净资产：${formatMoney(overview.value.net_assets)}`,
    ].join('\n')

    sharing.value = true
    try {
        if (navigator.share) {
            await navigator.share({
                title: '智能记账',
                text,
            })
        } else if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(text)
        }
        appendOperationLog(store.currentBookId, '共享账本概览', userName)
        setActionMessage('共享内容已准备好')
    } catch {
        setActionMessage('共享已取消')
    } finally {
        sharing.value = false
    }
}

const handleItemClick = async (item: ManagementItem) => {
    if (item.action === 'import') {
        triggerImport()
        return
    }

    if (item.action === 'export') {
        await handleExport()
        return
    }

    if (item.action === 'share') {
        await handleShare()
        return
    }

    router.push({
        name: 'ProfileManage',
        params: { kind: item.action },
    })
}

const handleSettingsClick = (item: SettingsItem) => {
    if (item.action === 'auto') {
        router.push({ name: 'ScheduledTaskList' })
        return
    }

    router.push({
        name: 'ProfileSettings',
        params: { kind: item.action },
    })
}

onMounted(async () => {
    if (!store.currentBookId) await store.fetchBooks()
    if (store.currentBookId) {
        loading.value = true
        try {
            const res = await getStatsOverview(store.currentBookId)
            overview.value = res.data
        } finally {
            loading.value = false
        }
    }
})
</script>

<template>
  <div class="pb-4">
    <!-- User Card -->
    <div class="mx-4 mt-4 rounded-2xl bg-gradient-to-r from-indigo-500 to-indigo-400 dark:from-indigo-700 dark:to-indigo-600 p-5 text-white shadow-lg">
      <div class="flex items-center gap-4 mb-4">
        <div class="w-16 h-16 rounded-full bg-white/20 flex items-center justify-center">
          <User class="w-8 h-8 text-white" />
        </div>
        <div>
          <h2 class="text-xl font-bold">{{ authStore.user?.display_name || authStore.user?.email || '用户' }}</h2>
          <p class="text-sm opacity-80">ID: {{ authStore.user?.id }}</p>
        </div>
      </div>
      <div class="grid grid-cols-3 text-center">
        <div>
          <p class="text-2xl font-bold">{{ overview.days }}</p>
          <p class="text-xs opacity-80">记账天数</p>
        </div>
        <div>
          <p class="text-2xl font-bold">{{ overview.transactions }}</p>
          <p class="text-xs opacity-80">交易笔数</p>
        </div>
        <div>
          <p class="text-2xl font-bold">{{ formatMoney(overview.net_assets) }}</p>
          <p class="text-xs opacity-80">净资产</p>
        </div>
      </div>
    </div>

    <!-- Management Grid -->
    <div class="mx-4 mt-4 rounded-2xl bg-white dark:bg-slate-800 shadow-sm border border-gray-100 dark:border-slate-700 p-4">
      <p v-if="actionMessage" class="text-xs text-indigo-600 mb-2 text-center">{{ actionMessage }}</p>
      <div class="grid grid-cols-4 gap-4">
        <button
          v-for="item in managementItems"
          :key="item.label"
          type="button"
          @click="handleItemClick(item)"
          :disabled="uploading || exporting || sharing"
          class="flex flex-col items-center gap-1.5 py-2 hover:bg-gray-50 dark:hover:bg-slate-700 rounded-xl transition"
        >
          <div :class="['w-10 h-10 rounded-xl flex items-center justify-center', item.color]">
            <component :is="item.icon" class="w-5 h-5 text-white" />
          </div>
          <span class="text-xs font-medium text-theme-primary">{{ item.label }}</span>
        </button>
      </div>
    </div>

    <!-- Settings -->
    <div class="mx-4 mt-4 rounded-2xl bg-white dark:bg-slate-800 shadow-sm border border-gray-100 dark:border-slate-700 overflow-hidden">
      <button
        v-for="item in settingsItems"
        :key="item.label"
        type="button"
        @click="handleSettingsClick(item)"
        class="w-full text-left flex items-center gap-3 px-4 py-3 hover:bg-gray-50 dark:hover:bg-slate-700 transition cursor-pointer border-b border-gray-50 dark:border-slate-700/50 last:border-b-0"
      >
        <component :is="item.icon" class="w-5 h-5 text-theme-muted" />
        <span class="flex-1 font-medium text-theme-primary text-sm">{{ item.label }}</span>
        <span v-if="item.desc" class="text-xs text-theme-muted">{{ item.desc }}</span>
        <ChevronRight class="w-4 h-4 text-theme-muted" />
      </button>
    </div>

    <!-- Hidden file input for CSV import -->
    <input
      type="file"
      ref="fileInput"
      accept=".csv"
      class="hidden"
      @change="handleFileUpload"
    />
  </div>
</template>
