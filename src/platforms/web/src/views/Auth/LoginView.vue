<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Bot, Loader2, ShieldUser } from 'lucide-vue-next'

import { bootstrapAdmin, getBootstrapStatus, getCurrentUser, login, type BootstrapStatus } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const loading = ref(false)
const checking = ref(true)
const error = ref('')
const bootstrapStatus = ref<BootstrapStatus | null>(null)

const email = ref('')
const password = ref('')
const displayName = ref('')

const bootstrapMode = computed(() => bootstrapStatus.value?.needs_bootstrap === true)

onMounted(async () => {
    const token = localStorage.getItem('access_token')
    if (token) {
        try {
            await getCurrentUser()
            await authStore.fetchUser()
            await router.push('/chat')
            return
        } catch {
            localStorage.removeItem('access_token')
        }
    }

    try {
        const response = await getBootstrapStatus()
        bootstrapStatus.value = response.data
    } catch (err: any) {
        error.value = err?.response?.data?.detail || '无法获取初始化状态'
    } finally {
        checking.value = false
    }
})

const handleSubmit = async () => {
    if (!email.value.trim() || !password.value.trim()) {
        error.value = '请输入邮箱和密码'
        return
    }

    loading.value = true
    error.value = ''
    try {
        if (bootstrapMode.value) {
            await bootstrapAdmin({
                email: email.value.trim(),
                password: password.value,
                display_name: displayName.value.trim() || undefined,
                username: email.value.split('@')[0],
            })
        }

        const response = await login(email.value.trim(), password.value)
        authStore.setToken(response.data.access_token)
        await authStore.fetchUser()
        await router.push(bootstrapMode.value && authStore.isAdmin ? '/admin/setup' : '/chat')
    } catch (err: any) {
        error.value = err?.response?.data?.detail || (bootstrapMode.value ? '初始化失败' : '登录失败')
    } finally {
        loading.value = false
    }
}
</script>

<template>
  <div class="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(8,145,178,0.24),_transparent_26%),linear-gradient(180deg,_#020617_0%,_#0f172a_50%,_#082f49_100%)] px-4 py-10 text-slate-100">
    <div class="mx-auto grid max-w-6xl gap-6 lg:grid-cols-[1.1fr_0.9fr]">
      <section class="overflow-hidden rounded-[36px] border border-white/10 bg-white/6 p-8 shadow-[0_36px_90px_rgba(2,6,23,0.42)] backdrop-blur">
        <div class="inline-flex items-center gap-2 rounded-full border border-cyan-300/20 bg-cyan-300/10 px-3 py-1 text-xs uppercase tracking-[0.24em] text-cyan-200">
          <Bot class="h-4 w-4" />
          Web Channel Console
        </div>
        <h1 class="mt-6 max-w-3xl text-4xl font-semibold leading-tight text-white md:text-5xl">
          Ikaros 现在在 Web 上也有正式对话入口了。
        </h1>
        <p class="mt-5 max-w-2xl text-base leading-8 text-slate-300">
          统一会话、命令、菜单回调、文件、语音、管理员控制台都在一个终端里完成。
          这次登录页也不再提供开放注册，而是明确区分首个管理员初始化与后续登录。
        </p>

        <div class="mt-8 grid gap-4 md:grid-cols-2">
          <div class="rounded-[28px] border border-white/10 bg-slate-950/40 p-5">
            <div class="text-sm font-medium text-white">安全基线</div>
            <p class="mt-2 text-sm leading-7 text-slate-400">
              公开注册提权链路已经关闭。首个管理员初始化完成后，后续账号只能由管理员在后台创建。
            </p>
          </div>
          <div class="rounded-[28px] border border-white/10 bg-white/5 p-5">
            <div class="text-sm font-medium text-white">工作台能力</div>
            <p class="mt-2 text-sm leading-7 text-slate-400">
              Chat 页面支持文字、语音、文件、命令、菜单动作；Admin 页面负责用户、模型和平台开关。
            </p>
          </div>
        </div>
      </section>

      <section class="rounded-[36px] border border-white/10 bg-white p-8 text-slate-900 shadow-[0_36px_90px_rgba(2,6,23,0.3)]">
        <div v-if="checking" class="flex min-h-[440px] items-center justify-center">
          <Loader2 class="h-8 w-8 animate-spin text-cyan-600" />
        </div>

        <template v-else>
          <div class="flex items-start justify-between gap-4">
            <div>
              <div class="text-xs uppercase tracking-[0.24em] text-slate-400">
                {{ bootstrapMode ? 'Bootstrap' : 'Sign In' }}
              </div>
              <h2 class="mt-2 text-3xl font-semibold">
                {{ bootstrapMode ? '初始化首个管理员' : '登录 Ikaros' }}
              </h2>
            </div>
            <div class="flex h-12 w-12 items-center justify-center rounded-2xl bg-cyan-100 text-cyan-700">
              <ShieldUser class="h-6 w-6" />
            </div>
          </div>

          <p class="mt-4 text-sm leading-7 text-slate-500">
            {{ bootstrapMode
              ? '当前系统还没有管理员。完成一次初始化后，将自动进入普通登录流。'
              : '请输入管理员创建的账号。Web 端不再提供开放注册。' }}
          </p>

          <div v-if="error" class="mt-5 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-600">
            {{ error }}
          </div>

          <form class="mt-8 space-y-5" @submit.prevent="handleSubmit">
            <div v-if="bootstrapMode">
              <label class="mb-2 block text-sm font-medium text-slate-700">显示名称</label>
              <input
                v-model="displayName"
                type="text"
                class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white"
                placeholder="例如：系统管理员"
              >
            </div>

            <div>
              <label class="mb-2 block text-sm font-medium text-slate-700">邮箱</label>
              <input
                v-model="email"
                type="email"
                class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white"
                placeholder="admin@example.com"
                required
              >
            </div>

            <div>
              <label class="mb-2 block text-sm font-medium text-slate-700">密码</label>
              <input
                v-model="password"
                type="password"
                minlength="8"
                class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white"
                placeholder="至少 8 位"
                required
              >
            </div>

            <button
              type="submit"
              :disabled="loading"
              class="inline-flex w-full items-center justify-center gap-3 rounded-2xl bg-slate-950 px-5 py-3.5 text-sm font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Loader2 v-if="loading" class="h-4 w-4 animate-spin" />
              <span>{{ bootstrapMode ? '初始化并登录' : '登录' }}</span>
            </button>
          </form>
        </template>
      </section>
    </div>
  </div>
</template>
