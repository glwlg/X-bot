<script setup lang="ts">
import axios from 'axios'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
    Bot,
    FileText,
    Loader2,
    RadioTower,
    Save,
    Settings2,
    ShieldUser,
    Sparkles,
    TriangleAlert,
} from 'lucide-vue-next'

import {
    generateRuntimeDoc,
    getRuntimeSnapshot,
    patchRuntimeSnapshot,
    type RuntimeGeneratePayload,
    type RuntimePatchPayload,
    type RuntimeSnapshot,
} from '@/api/runtime'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const loading = ref(false)
const saving = ref(false)
const generatingSoul = ref(false)
const generatingUser = ref(false)
const errorText = ref('')
const successText = ref('')
const restartRequired = ref(false)
const corsInput = ref('')

const form = ref<RuntimeSnapshot | null>(null)
const adminPassword = ref('')
const adminIdsInput = ref('')
const soulBrief = ref('')
const userBrief = ref('')

const parseErrorMessage = (error: unknown, fallback: string) => {
    if (axios.isAxiosError(error)) {
        const detail = error.response?.data?.detail
        if (Array.isArray(detail) && detail.length > 0) {
            return String(detail[0]?.msg || fallback)
        }
        if (typeof detail === 'string' && detail.trim()) {
            return detail
        }
    }
    return fallback
}

const cloneSnapshot = (payload: RuntimeSnapshot) =>
    JSON.parse(JSON.stringify(payload)) as RuntimeSnapshot

const hydrate = (payload: RuntimeSnapshot) => {
    form.value = cloneSnapshot(payload)
    adminIdsInput.value = (payload.channels.admin_user_ids || []).join('\n')
    corsInput.value = (payload.cors_allowed_origins || []).join('\n')
    restartRequired.value = false
}

const load = async () => {
    loading.value = true
    errorText.value = ''
    try {
        const response = await getRuntimeSnapshot()
        hydrate(response.data)
    } catch (error) {
        errorText.value = parseErrorMessage(error, '运行配置加载失败')
    } finally {
        loading.value = false
    }
}

const checklist = computed(() => {
    if (!form.value) return []
    const status = form.value.status
    return [
        { label: '管理员绑定', ok: status.admin_bound },
        { label: 'Primary 模型', ok: status.primary_ready },
        { label: 'Routing 模型', ok: status.routing_ready },
        { label: 'SOUL.MD', ok: status.soul_ready },
        { label: 'USER.md', ok: status.user_ready },
        { label: '渠道配置', ok: status.channels_ready },
    ]
})

const parseAdminUserIds = () =>
    adminIdsInput.value
        .split(/[\n,]/)
        .map(item => item.trim())
        .filter(Boolean)

const primaryModelKey = computed(() => form.value?.model_status.primary.model_key || '')
const canGenerateDocs = computed(() => Boolean(form.value?.model_status.primary.ready && primaryModelKey.value))

const save = async () => {
    if (!form.value) return
    saving.value = true
    errorText.value = ''
    successText.value = ''
    try {
        const payload: RuntimePatchPayload = {
            admin_user: {
                email: form.value.admin_user.email.trim(),
                username: form.value.admin_user.username?.trim() || '',
                display_name: form.value.admin_user.display_name?.trim() || '',
                ...(adminPassword.value.trim() ? { password: adminPassword.value } : {}),
            },
            docs: {
                soul_content: form.value.docs.soul_content,
                user_content: form.value.docs.user_content,
            },
            channels: {
                admin_user_ids: parseAdminUserIds(),
                telegram: {
                    enabled: form.value.channels.telegram.enabled,
                    bot_token: form.value.channels.telegram.bot_token,
                },
                discord: {
                    enabled: form.value.channels.discord.enabled,
                    bot_token: form.value.channels.discord.bot_token,
                },
                dingtalk: {
                    enabled: form.value.channels.dingtalk.enabled,
                    client_id: form.value.channels.dingtalk.client_id,
                    client_secret: form.value.channels.dingtalk.client_secret,
                },
                weixin: {
                    enabled: form.value.channels.weixin.enabled,
                    base_url: form.value.channels.weixin.base_url,
                    cdn_base_url: form.value.channels.weixin.cdn_base_url,
                },
                web: {
                    enabled: form.value.channels.web.enabled,
                },
            },
            features: form.value.features,
            cors_allowed_origins: corsInput.value
                .split('\n')
                .map(item => item.trim())
                .filter(Boolean),
            memory_provider: form.value.memory.provider,
        }
        const response = await patchRuntimeSnapshot(payload)
        hydrate(response.data.snapshot)
        adminPassword.value = ''
        restartRequired.value = response.data.restart_required
        successText.value = response.data.restart_required
            ? '运行配置已保存，凭证相关改动需要重启 ikaros core。'
            : '运行配置已保存'
        await authStore.fetchUser()
    } catch (error) {
        errorText.value = parseErrorMessage(error, '保存运行配置失败')
    } finally {
        saving.value = false
    }
}

const generateDoc = async (payload: RuntimeGeneratePayload) => {
    if (!form.value) return
    errorText.value = ''
    successText.value = ''
    const isSoul = payload.kind === 'soul'
    if (isSoul) {
        generatingSoul.value = true
    } else {
        generatingUser.value = true
    }
    try {
        const response = await generateRuntimeDoc(payload)
        if (response.data.kind === 'soul') {
            form.value.docs.soul_content = response.data.content
        } else {
            form.value.docs.user_content = response.data.content
        }
        successText.value = `${response.data.kind.toUpperCase()} 文档已生成，确认后记得保存。`
    } catch (error) {
        errorText.value = parseErrorMessage(error, 'AI 生成文档失败')
    } finally {
        if (isSoul) {
            generatingSoul.value = false
        } else {
            generatingUser.value = false
        }
    }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6 p-6 md:p-8">
    <section class="rounded-[30px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Runtime</div>
          <h2 class="mt-2 text-3xl font-semibold text-slate-950">运行配置</h2>
          <p class="mt-3 max-w-3xl text-sm leading-7 text-slate-500">
            首次安装先在这里完成管理员、文档、渠道和运行项，再进入模型配置页补齐或调整模型目录。
          </p>
        </div>
        <div class="flex flex-wrap gap-3">
          <button
            type="button"
            class="inline-flex items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:opacity-60"
            :disabled="loading"
            @click="router.push('/admin/models')"
          >
            <Settings2 class="h-4 w-4" />
            去模型配置
          </button>
          <button
            type="button"
            class="inline-flex items-center justify-center gap-2 rounded-2xl bg-slate-950 px-4 py-3 text-sm font-medium text-white transition hover:bg-slate-800 disabled:opacity-60"
            :disabled="saving || loading || !form"
            @click="save"
          >
            <Loader2 v-if="saving" class="h-4 w-4 animate-spin" />
            <Save v-else class="h-4 w-4" />
            保存运行配置
          </button>
        </div>
      </div>

      <div v-if="checklist.length" class="mt-6 flex flex-wrap gap-3">
        <div
          v-for="item in checklist"
          :key="item.label"
          class="rounded-full px-3 py-1.5 text-xs font-medium"
          :class="item.ok ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'"
        >
          {{ item.ok ? '已就绪' : '待完成' }} · {{ item.label }}
        </div>
      </div>

      <div v-if="errorText" class="mt-6 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
        {{ errorText }}
      </div>
      <div v-if="successText" class="mt-4 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
        {{ successText }}
      </div>
      <div v-if="restartRequired && form" class="mt-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
        {{ form.restart_notice }}
      </div>
    </section>

    <div v-if="loading" class="flex items-center gap-2 rounded-[28px] border border-slate-200 bg-white px-5 py-4 text-sm text-slate-500 shadow-sm">
      <Loader2 class="h-4 w-4 animate-spin" />
      正在加载运行配置
    </div>

    <template v-else-if="form">
      <section class="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
          <div class="flex items-center gap-3">
            <div class="flex h-12 w-12 items-center justify-center rounded-2xl bg-cyan-100 text-cyan-700">
              <ShieldUser class="h-5 w-5" />
            </div>
            <div>
              <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Admin</div>
              <h3 class="text-xl font-semibold text-slate-900">管理员与访问</h3>
            </div>
          </div>

          <div class="mt-6 grid gap-4 md:grid-cols-2">
            <label class="space-y-2">
              <div class="text-sm font-medium text-slate-700">邮箱</div>
              <input v-model="form.admin_user.email" type="email" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
            </label>
            <label class="space-y-2">
              <div class="text-sm font-medium text-slate-700">用户名</div>
              <input v-model="form.admin_user.username" type="text" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
            </label>
            <label class="space-y-2">
              <div class="text-sm font-medium text-slate-700">显示名称</div>
              <input v-model="form.admin_user.display_name" type="text" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
            </label>
            <label class="space-y-2">
              <div class="text-sm font-medium text-slate-700">重设密码</div>
              <input v-model="adminPassword" type="password" minlength="8" placeholder="留空表示不修改" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
            </label>
          </div>

          <label class="mt-5 block space-y-2">
            <div class="text-sm font-medium text-slate-700">ADMIN_USER_IDS</div>
            <textarea v-model="adminIdsInput" class="min-h-[120px] w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-7 outline-none transition focus:border-cyan-400 focus:bg-white" placeholder="每行一个 ID，也支持逗号分隔" />
          </label>

          <div class="mt-5 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
            当前 Web 管理员用户 ID：<span class="font-semibold text-slate-900">{{ form.admin_user.current_admin_user_id }}</span>
          </div>
        </div>

        <div class="space-y-6">
          <div class="rounded-[28px] border border-slate-200 bg-slate-950 p-6 text-slate-100 shadow-sm">
            <div class="flex items-center gap-2 text-xs uppercase tracking-[0.24em] text-slate-500">
              <TriangleAlert class="h-4 w-4 text-amber-300" />
              First Install
            </div>
            <h3 class="mt-3 text-2xl font-semibold">推荐顺序</h3>
            <div class="mt-4 space-y-2 text-sm leading-7 text-slate-300">
              <div>1. 先去模型配置补齐 Primary / Routing</div>
              <div>2. 生成或编辑 SOUL / USER 文档</div>
              <div>3. 开启你需要的渠道并填写凭证</div>
            </div>
          </div>

          <div class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
            <div class="flex items-center gap-3">
              <div class="flex h-12 w-12 items-center justify-center rounded-2xl bg-indigo-100 text-indigo-700">
                <Bot class="h-5 w-5" />
              </div>
              <div>
                <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Models</div>
                <h3 class="text-xl font-semibold text-slate-900">模型状态</h3>
              </div>
            </div>
            <div class="mt-5 space-y-3">
              <div class="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
                <div class="font-medium text-slate-900">Primary</div>
                <div class="mt-1">{{ form.model_status.primary.model_key || '未配置' }}</div>
              </div>
              <div class="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
                <div class="font-medium text-slate-900">Routing</div>
                <div class="mt-1">{{ form.model_status.routing.model_key || '未配置' }}</div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="grid gap-6 xl:grid-cols-2">
        <article class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
          <div class="flex items-center gap-3">
            <div class="flex h-12 w-12 items-center justify-center rounded-2xl bg-emerald-100 text-emerald-700">
              <Sparkles class="h-5 w-5" />
            </div>
            <div>
              <div class="text-xs uppercase tracking-[0.24em] text-slate-400">SOUL</div>
              <h3 class="text-xl font-semibold text-slate-900">Ikaros SOUL.MD</h3>
            </div>
          </div>

          <div class="mt-5 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
            文件路径：{{ form.docs.soul_path }}
          </div>

          <label class="mt-5 block space-y-2">
            <div class="text-sm font-medium text-slate-700">AI 生成补充要求</div>
            <textarea v-model="soulBrief" class="min-h-[96px] w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-7 outline-none transition focus:border-cyan-400 focus:bg-white" placeholder="例如：偏温柔、有生活感，但保持执行力；避免太多 emoji。" />
          </label>

          <div class="mt-4 flex justify-end">
            <button
              type="button"
              class="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700 transition hover:bg-slate-100 disabled:opacity-60"
              :disabled="generatingSoul || !canGenerateDocs"
              @click="generateDoc({ kind: 'soul', brief: soulBrief, current_content: form.docs.soul_content, model_key: primaryModelKey })"
            >
              <Loader2 v-if="generatingSoul" class="h-4 w-4 animate-spin" />
              <Sparkles v-else class="h-4 w-4" />
              AI 生成 SOUL
            </button>
          </div>

          <textarea v-model="form.docs.soul_content" class="mt-4 min-h-[420px] w-full rounded-[24px] border border-slate-200 bg-slate-50 px-4 py-4 text-sm leading-7 outline-none transition focus:border-cyan-400 focus:bg-white" />
        </article>

        <article class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
          <div class="flex items-center gap-3">
            <div class="flex h-12 w-12 items-center justify-center rounded-2xl bg-violet-100 text-violet-700">
              <FileText class="h-5 w-5" />
            </div>
            <div>
              <div class="text-xs uppercase tracking-[0.24em] text-slate-400">USER</div>
              <h3 class="text-xl font-semibold text-slate-900">管理员 USER.md</h3>
            </div>
          </div>

          <div class="mt-5 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
            文件路径：{{ form.docs.user_path }}
          </div>

          <label class="mt-5 block space-y-2">
            <div class="text-sm font-medium text-slate-700">AI 生成补充要求</div>
            <textarea v-model="userBrief" class="min-h-[96px] w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-7 outline-none transition focus:border-cyan-400 focus:bg-white" placeholder="例如：我希望称呼我阿伟，偏好直接清晰，不要太多套话。" />
          </label>

          <div class="mt-4 flex justify-end">
            <button
              type="button"
              class="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700 transition hover:bg-slate-100 disabled:opacity-60"
              :disabled="generatingUser || !canGenerateDocs"
              @click="generateDoc({ kind: 'user', brief: userBrief, current_content: form.docs.user_content, model_key: primaryModelKey })"
            >
              <Loader2 v-if="generatingUser" class="h-4 w-4 animate-spin" />
              <Sparkles v-else class="h-4 w-4" />
              AI 生成 USER
            </button>
          </div>

          <textarea v-model="form.docs.user_content" class="mt-4 min-h-[420px] w-full rounded-[24px] border border-slate-200 bg-slate-50 px-4 py-4 text-sm leading-7 outline-none transition focus:border-cyan-400 focus:bg-white" />
        </article>
      </section>

      <section class="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
        <article class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
          <div class="flex items-center gap-3">
            <div class="flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-100 text-amber-700">
              <RadioTower class="h-5 w-5" />
            </div>
            <div>
              <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Channels</div>
              <h3 class="text-xl font-semibold text-slate-900">渠道接入与启停</h3>
            </div>
          </div>

          <div class="mt-6 space-y-4">
            <article class="rounded-[24px] border border-slate-200 bg-slate-50 p-5">
              <label class="flex items-center justify-between gap-4">
                <div>
                  <div class="text-sm font-semibold text-slate-900">Telegram</div>
                  <div class="mt-1 text-xs text-slate-500">{{ form.channels.telegram.configured ? '凭证已配置' : '缺少凭证' }}</div>
                </div>
                <input v-model="form.channels.telegram.enabled" type="checkbox" class="h-4 w-4">
              </label>
              <label class="mt-4 block space-y-2">
                <div class="text-sm font-medium text-slate-700">Bot Token</div>
                <input v-model="form.channels.telegram.bot_token" type="text" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-cyan-400">
              </label>
            </article>

            <article class="rounded-[24px] border border-slate-200 bg-slate-50 p-5">
              <label class="flex items-center justify-between gap-4">
                <div>
                  <div class="text-sm font-semibold text-slate-900">Discord</div>
                  <div class="mt-1 text-xs text-slate-500">{{ form.channels.discord.configured ? '凭证已配置' : '缺少凭证' }}</div>
                </div>
                <input v-model="form.channels.discord.enabled" type="checkbox" class="h-4 w-4">
              </label>
              <label class="mt-4 block space-y-2">
                <div class="text-sm font-medium text-slate-700">Bot Token</div>
                <input v-model="form.channels.discord.bot_token" type="text" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-cyan-400">
              </label>
            </article>

            <article class="rounded-[24px] border border-slate-200 bg-slate-50 p-5">
              <label class="flex items-center justify-between gap-4">
                <div>
                  <div class="text-sm font-semibold text-slate-900">DingTalk</div>
                  <div class="mt-1 text-xs text-slate-500">{{ form.channels.dingtalk.configured ? '凭证已配置' : '缺少凭证' }}</div>
                </div>
                <input v-model="form.channels.dingtalk.enabled" type="checkbox" class="h-4 w-4">
              </label>
              <div class="mt-4 grid gap-4 md:grid-cols-2">
                <label class="space-y-2">
                  <div class="text-sm font-medium text-slate-700">Client ID</div>
                  <input v-model="form.channels.dingtalk.client_id" type="text" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-cyan-400">
                </label>
                <label class="space-y-2">
                  <div class="text-sm font-medium text-slate-700">Client Secret</div>
                  <input v-model="form.channels.dingtalk.client_secret" type="text" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-cyan-400">
                </label>
              </div>
            </article>

            <article class="rounded-[24px] border border-slate-200 bg-slate-50 p-5">
              <label class="flex items-center justify-between gap-4">
                <div>
                  <div class="text-sm font-semibold text-slate-900">Weixin</div>
                  <div class="mt-1 text-xs text-slate-500">{{ form.channels.weixin.configured ? '连接参数已就绪' : '缺少连接参数' }}</div>
                </div>
                <input v-model="form.channels.weixin.enabled" type="checkbox" class="h-4 w-4">
              </label>
              <div class="mt-4 grid gap-4">
                <label class="space-y-2">
                  <div class="text-sm font-medium text-slate-700">Base URL</div>
                  <input v-model="form.channels.weixin.base_url" type="text" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-cyan-400">
                </label>
                <label class="space-y-2">
                  <div class="text-sm font-medium text-slate-700">CDN Base URL</div>
                  <input v-model="form.channels.weixin.cdn_base_url" type="text" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-cyan-400">
                </label>
              </div>
            </article>

            <article class="rounded-[24px] border border-slate-200 bg-slate-50 p-5">
              <label class="flex items-center justify-between gap-4">
                <div>
                  <div class="text-sm font-semibold text-slate-900">Web</div>
                  <div class="mt-1 text-xs text-slate-500">无需额外凭证</div>
                </div>
                <input v-model="form.channels.web.enabled" type="checkbox" class="h-4 w-4">
              </label>
            </article>
          </div>
        </article>

        <div class="space-y-6">
          <article class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
            <div class="text-sm font-semibold text-slate-900">功能开关</div>
            <div class="mt-4 space-y-3">
              <label v-for="name in Object.keys(form.features)" :key="name" class="flex items-center justify-between rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
                <div>
                  <div class="text-sm text-slate-700">{{ name }}</div>
                  <div class="text-xs text-slate-500">控制 Web console 与后台功能入口</div>
                </div>
                <input v-model="form.features[name]" type="checkbox" class="h-4 w-4">
              </label>
            </div>
          </article>

          <article class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
            <div class="text-sm font-semibold text-slate-900">CORS Allowlist</div>
            <div class="mt-1 text-sm text-slate-500">每行一个 Origin，生产环境不要使用宽泛通配。</div>
            <textarea v-model="corsInput" class="mt-4 min-h-[180px] w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-7 outline-none focus:border-cyan-400 focus:bg-white" placeholder="https://app.example.com&#10;http://127.0.0.1:8764" />
          </article>

          <article class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
            <div class="text-sm font-semibold text-slate-900">Memory Provider</div>
            <div class="mt-1 text-sm text-slate-500">这里只切换 provider，不在 Web 里直接改密钥。</div>
            <select v-model="form.memory.provider" class="mt-4 w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none focus:border-cyan-400 focus:bg-white">
              <option v-for="provider in form.memory.providers" :key="provider" :value="provider">{{ provider }}</option>
            </select>
            <div class="mt-4 rounded-2xl bg-slate-950 p-4 text-xs leading-6 text-slate-200">
              {{ JSON.stringify(form.memory.active_settings, null, 2) }}
            </div>
          </article>

          <article class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
            <div class="text-sm font-semibold text-slate-900">配置路径</div>
            <div class="mt-4 rounded-[24px] border border-slate-200 bg-slate-50 p-4 text-sm leading-7 text-slate-600">
              <div><span class="font-medium text-slate-900">.env</span>：{{ form.paths.env }}</div>
              <div class="mt-2"><span class="font-medium text-slate-900">models.json</span>：{{ form.paths.models }}</div>
              <div class="mt-2"><span class="font-medium text-slate-900">memory.json</span>：{{ form.paths.memory }}</div>
            </div>
          </article>
        </div>
      </section>
    </template>
  </div>
</template>
