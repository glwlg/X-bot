<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useAccountingStore } from '@/stores/accounting'
import { getRecordsSummary, type MonthlySummary } from '@/api/accounting'
import { ThemeToggle } from '@/components/ThemeToggle'
import request from '@/api/request'
import {
  BookOpen, Rss, Clock, Activity, ArrowRight, LogOut, Link2, Unlink, Plus
} from 'lucide-vue-next'

const router = useRouter()
const authStore = useAuthStore()
const accountingStore = useAccountingStore()

const monthlySummary = ref<MonthlySummary | null>(null)
const loggingOut = ref(false)
const now = new Date()

// Platform binding state
const bindings = ref<any[]>([])
const showBindDialog = ref(false)
const bindForm = ref({ platform: 'telegram', platform_user_id: '' })
const bindLoading = ref(false)

const loadBindings = async () => {
    try {
        const res = await request('/binding/me', { method: 'GET' })
        bindings.value = res.data || []
    } catch {
        // ignore
    }
}

const handleBind = async () => {
    if (!bindForm.value.platform_user_id.trim()) return
    bindLoading.value = true
    try {
        await request('/binding/me', {
            method: 'POST',
            data: bindForm.value,
        })
        showBindDialog.value = false
        bindForm.value = { platform: 'telegram', platform_user_id: '' }
        await loadBindings()
    } catch (e: any) {
        alert(e?.response?.data?.detail || '绑定失败')
    } finally {
        bindLoading.value = false
    }
}

const handleUnbind = async (id: number) => {
    if (!confirm('确定解除绑定吗？')) return
    try {
        await request(`/binding/me/${id}`, { method: 'DELETE' })
        await loadBindings()
    } catch {
        alert('解绑失败')
    }
}

onMounted(async () => {
    await loadBindings()
    await accountingStore.fetchBooks()
    if (accountingStore.currentBookId) {
        try {
            const res = await getRecordsSummary(
                accountingStore.currentBookId,
                now.getFullYear(),
                now.getMonth() + 1
            )
            monthlySummary.value = res.data
        } catch {
            // ignore
        }
    }
})

const goAccounting = () => router.push('/accounting')

const handleLogout = async () => {
    loggingOut.value = true
    try {
        await authStore.logout()
        router.push('/login')
    } finally {
        loggingOut.value = false
    }
}


const modules = [
    {
        id: 'accounting',
        name: '智能记账',
        desc: '通过截图、文字或语音快速记录每一笔收支',
        icon: BookOpen,
        color: 'teal',
        enabled: true,
        action: goAccounting,
    },
    {
        id: 'rss',
        name: 'RSS 订阅',
        desc: '聚合新闻源，定时推送感兴趣的内容',
        icon: Rss,
        color: 'orange',
        enabled: true,
        action: () => router.push('/modules/rss'),
    },
    {
        id: 'scheduler',
        name: '定时任务',
        desc: '设置定时提醒、数据采集等自动化任务',
        icon: Clock,
        color: 'blue',
        enabled: true,
        action: () => router.push('/modules/scheduler'),
    },
    {
        id: 'monitor',
        name: '心跳监控',
        desc: '监控服务可用性，异常时自动告警',
        icon: Activity,
        color: 'purple',
        enabled: true,
        action: () => router.push('/modules/monitor'),
    },
]

const platformLabels: Record<string, string> = {
    telegram: 'Telegram',
    discord: 'Discord',
    wechat: '微信',
}

const colorMap: Record<string, { bg: string; icon: string; border: string; hover: string }> = {
    teal: {
        bg: 'bg-teal-50 dark:bg-teal-900/20',
        icon: 'bg-teal-500',
        border: 'border-teal-200 dark:border-teal-800',
        hover: 'hover:shadow-teal-100 dark:hover:shadow-teal-900/30',
    },
    orange: {
        bg: 'bg-orange-50 dark:bg-orange-900/20',
        icon: 'bg-orange-500',
        border: 'border-orange-200 dark:border-orange-800',
        hover: 'hover:shadow-orange-100 dark:hover:shadow-orange-900/30',
    },
    blue: {
        bg: 'bg-blue-50 dark:bg-blue-900/20',
        icon: 'bg-blue-500',
        border: 'border-blue-200 dark:border-blue-800',
        hover: 'hover:shadow-blue-100 dark:hover:shadow-blue-900/30',
    },
    purple: {
        bg: 'bg-purple-50 dark:bg-purple-900/20',
        icon: 'bg-purple-500',
        border: 'border-purple-200 dark:border-purple-800',
        hover: 'hover:shadow-purple-100 dark:hover:shadow-purple-900/30',
    },
}
</script>

<template>
  <div class="p-6 max-w-5xl mx-auto">
    <!-- Welcome Section -->
    <div class="mb-8 flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
      <div>
        <h1 class="text-2xl font-bold text-theme-primary">
          欢迎回来，{{ authStore.user?.display_name || authStore.user?.email || '用户' }} 👋
        </h1>
        <p class="text-theme-muted mt-1">这里是你的 X-Bot 控制面板，选择一个模块开始吧</p>
      </div>

      <div class="rounded-2xl border border-theme-primary bg-theme-elevated p-3 shadow-sm min-w-[220px]">
        <p class="text-xs text-theme-muted mb-2">偏好与账号</p>
        <div class="flex items-center justify-between gap-2 mb-2">
          <ThemeToggle variant="dropdown" show-label size="sm" class-name="justify-start" />
          <button
            type="button"
            :disabled="loggingOut"
            @click="handleLogout"
            class="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 disabled:opacity-60"
          >
            <LogOut class="w-3.5 h-3.5" />
            退出登录
          </button>
        </div>

        <!-- Platform Bindings -->
        <div class="border-t border-theme-primary pt-2 mt-1">
          <div class="flex items-center justify-between mb-1.5">
            <span class="text-xs text-theme-muted font-medium">平台绑定</span>
            <button
              @click="showBindDialog = true"
              class="inline-flex items-center gap-1 text-xs text-teal-600 hover:text-teal-700 transition"
            >
              <Plus class="w-3 h-3" />
              绑定
            </button>
          </div>
          <div v-if="bindings.length === 0" class="text-xs text-amber-600 bg-amber-50 dark:bg-amber-900/20 rounded-lg px-2.5 py-1.5">
            <Link2 class="w-3 h-3 inline mr-1" />
            未绑定平台账号，部分功能不可用
          </div>
          <div v-else class="space-y-1">
            <div
              v-for="b in bindings"
              :key="b.id"
              class="flex items-center justify-between text-xs bg-gray-50 dark:bg-slate-800 rounded-lg px-2.5 py-1.5"
            >
              <span class="text-theme-primary font-medium">
                {{ platformLabels[b.platform] || b.platform }}:
                <span class="text-theme-muted font-normal">{{ b.platform_user_id }}</span>
              </span>
              <button
                @click="handleUnbind(b.id)"
                class="text-gray-400 hover:text-rose-500 transition"
              >
                <Unlink class="w-3 h-3" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Module Cards -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-5">
      <div
        v-for="mod in modules"
        :key="mod.id"
        @click="mod.enabled && mod.action?.()"
        :class="[
          'group rounded-2xl border p-5 transition-all duration-200 shadow-sm',
          mod.enabled
            ? `cursor-pointer ${colorMap[mod.color]?.border} ${colorMap[mod.color]?.hover} hover:shadow-md`
            : 'opacity-50 cursor-not-allowed border-gray-200 dark:border-slate-700',
          colorMap[mod.color]?.bg || 'bg-theme-elevated',
        ]"
      >
        <div class="flex items-start justify-between mb-3">
          <div :class="[
            'w-12 h-12 rounded-xl flex items-center justify-center shadow-sm',
            colorMap[mod.color]?.icon || 'bg-gray-500'
          ]">
            <component :is="mod.icon" class="w-6 h-6 text-white" />
          </div>
          <ArrowRight
            v-if="mod.enabled"
            class="w-5 h-5 text-gray-300 group-hover:text-gray-500 dark:text-slate-600 dark:group-hover:text-slate-400 transition-colors"
          />
          <span
            v-else
            class="text-[10px] font-medium text-gray-400 bg-gray-100 dark:bg-slate-800 dark:text-slate-500 px-2 py-0.5 rounded-full"
          >
            即将推出
          </span>
        </div>
        <h2 class="text-lg font-semibold text-theme-primary mb-1">{{ mod.name }}</h2>
        <p class="text-sm text-theme-muted leading-relaxed">{{ mod.desc }}</p>
      </div>
    </div>

    <!-- Bind Dialog -->
    <div v-if="showBindDialog" class="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4">
      <div class="bg-white dark:bg-slate-800 rounded-2xl w-full max-w-sm overflow-hidden">
        <div class="p-4 border-b border-slate-100 dark:border-slate-700">
          <h2 class="text-lg font-bold text-center text-theme-primary">绑定平台账号</h2>
        </div>
        <div class="p-4 space-y-4">
          <div>
            <label class="block text-sm text-slate-500 mb-1">平台</label>
            <select
              v-model="bindForm.platform"
              class="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-3 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500"
            >
              <option value="telegram">Telegram</option>
              <option value="discord">Discord</option>
            </select>
          </div>
          <div>
            <label class="block text-sm text-slate-500 mb-1">平台用户 ID</label>
            <input
              v-model="bindForm.platform_user_id"
              type="text"
              class="w-full bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-3 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500"
              placeholder="例如: 257675041"
            >
            <p class="text-xs text-slate-400 mt-1">在 Telegram 中发送 /start 给 @userinfobot 获取你的 User ID</p>
          </div>
        </div>
        <div class="p-4 flex gap-3 border-t border-slate-100 dark:border-slate-700">
          <button @click="showBindDialog = false" class="flex-1 py-3 bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 rounded-xl font-medium">取消</button>
          <button @click="handleBind" :disabled="bindLoading" class="flex-1 py-3 bg-teal-500 text-white rounded-xl font-medium shadow-lg shadow-teal-500/30 disabled:opacity-60">
            {{ bindLoading ? '绑定中...' : '确认绑定' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
