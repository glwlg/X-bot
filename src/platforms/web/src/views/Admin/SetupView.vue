<script setup lang="ts">
import axios from 'axios'
import { computed, onMounted, ref } from 'vue'
import { Loader2, Save, Sparkles, ShieldUser, KeyRound, Bot, FileText, RadioTower, TriangleAlert } from 'lucide-vue-next'

import {
    generateSetupDoc,
    getSetupSnapshot,
    patchSetup,
    type SetupGeneratePayload,
    type SetupModelRole,
    type SetupPatchPayload,
    type SetupSnapshot,
} from '@/api/setup'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()

const loading = ref(false)
const saving = ref(false)
const generatingSoul = ref(false)
const generatingUser = ref(false)
const errorText = ref('')
const successText = ref('')
const restartRequired = ref(false)

const form = ref<SetupSnapshot | null>(null)
const adminPassword = ref('')
const adminIdsInput = ref('')
const soulBrief = ref('')
const userBrief = ref('')

const roleOrder: Array<'primary' | 'routing'> = ['primary', 'routing']
const roleLabels: Record<'primary' | 'routing', string> = {
    primary: 'Primary',
    routing: 'Routing',
}
const inputTypeOptions = ['text', 'image', 'voice']

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

const cloneSnapshot = (payload: SetupSnapshot) => JSON.parse(JSON.stringify(payload)) as SetupSnapshot

const hydrate = (payload: SetupSnapshot) => {
    form.value = cloneSnapshot(payload)
    adminIdsInput.value = (payload.channels.admin_user_ids || []).join('\n')
    restartRequired.value = false
}

const load = async () => {
    loading.value = true
    errorText.value = ''
    try {
        const response = await getSetupSnapshot()
        hydrate(response.data)
    } catch (error) {
        errorText.value = parseErrorMessage(error, '初始化配置加载失败')
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
    ]
})

const platformEntries = computed(() =>
    Object.entries(form.value?.channels.platforms || {})
)

const buildRolePayload = (role: SetupModelRole) => ({
    provider_name: role.provider_name.trim(),
    base_url: role.base_url.trim(),
    api_key: role.api_key,
    api_style: role.api_style.trim() || 'openai-completions',
    model_id: role.model_id.trim(),
    display_name: role.display_name?.trim() || undefined,
    reasoning: Boolean(role.reasoning),
    input_types: (role.input_types || []).map(item => item.trim()).filter(Boolean),
})

const parseAdminUserIds = () =>
    adminIdsInput.value
        .split(/[\n,]/)
        .map(item => item.trim())
        .filter(Boolean)

const save = async () => {
    if (!form.value) return
    saving.value = true
    errorText.value = ''
    successText.value = ''
    try {
        const payload: SetupPatchPayload = {
            admin_user: {
                email: form.value.admin_user.email.trim(),
                username: form.value.admin_user.username?.trim() || '',
                display_name: form.value.admin_user.display_name?.trim() || '',
                ...(adminPassword.value.trim() ? { password: adminPassword.value } : {}),
            },
            models: {
                primary: buildRolePayload(form.value.models.primary),
                routing: buildRolePayload(form.value.models.routing),
            },
            docs: {
                soul_content: form.value.docs.soul_content,
                user_content: form.value.docs.user_content,
            },
            channels: {
                platforms: form.value.channels.platforms,
                admin_user_ids: parseAdminUserIds(),
                telegram_bot_token: form.value.channels.telegram_bot_token,
                discord_bot_token: form.value.channels.discord_bot_token,
                dingtalk_client_id: form.value.channels.dingtalk_client_id,
                dingtalk_client_secret: form.value.channels.dingtalk_client_secret,
                weixin_enable: form.value.channels.weixin_enable,
                weixin_base_url: form.value.channels.weixin_base_url,
                weixin_cdn_base_url: form.value.channels.weixin_cdn_base_url,
                web_channel_enable: form.value.channels.web_channel_enable,
            },
        }
        const response = await patchSetup(payload)
        hydrate(response.data.snapshot)
        adminPassword.value = ''
        restartRequired.value = response.data.restart_required
        successText.value = response.data.restart_required
            ? '初始化配置已保存，渠道和 .env 相关改动需要重启 ikaros core。'
            : '初始化配置已保存'
        await authStore.fetchUser()
    } catch (error) {
        errorText.value = parseErrorMessage(error, '保存初始化配置失败')
    } finally {
        saving.value = false
    }
}

const generateDoc = async (payload: SetupGeneratePayload) => {
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
        const response = await generateSetupDoc(payload)
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
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Setup</div>
          <h2 class="mt-2 text-3xl font-semibold text-slate-950">Ikaros 初始化</h2>
          <p class="mt-3 max-w-3xl text-sm leading-7 text-slate-500">
            在一个页面里完成管理员、模型、SOUL / USER 文档和渠道接入配置。模型保存后，就可以直接调用 Primary 模型辅助生成符合规范的文档。
          </p>
        </div>
        <button
          type="button"
          class="inline-flex items-center justify-center gap-2 rounded-2xl bg-slate-950 px-4 py-3 text-sm font-medium text-white transition hover:bg-slate-800 disabled:opacity-60"
          :disabled="saving || loading || !form"
          @click="save"
        >
          <Loader2 v-if="saving" class="h-4 w-4 animate-spin" />
          <Save v-else class="h-4 w-4" />
          保存初始化配置
        </button>
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
      正在加载初始化配置
    </div>

    <template v-else-if="form">
      <section class="grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_360px]">
        <div class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
          <div class="flex items-center gap-3">
            <div class="flex h-12 w-12 items-center justify-center rounded-2xl bg-cyan-100 text-cyan-700">
              <ShieldUser class="h-5 w-5" />
            </div>
            <div>
              <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Admin</div>
              <h3 class="text-xl font-semibold text-slate-900">管理员用户</h3>
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

          <div class="mt-5 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
            当前 Web 管理员用户 ID：<span class="font-semibold text-slate-900">{{ form.admin_user.current_admin_user_id }}</span>
          </div>
        </div>

        <div class="rounded-[28px] border border-slate-200 bg-slate-950 p-6 text-slate-100 shadow-sm">
          <div class="flex items-center gap-2 text-xs uppercase tracking-[0.24em] text-slate-500">
            <TriangleAlert class="h-4 w-4 text-amber-300" />
            Restart
          </div>
          <h3 class="mt-3 text-2xl font-semibold">渠道与凭证</h3>
          <p class="mt-3 text-sm leading-7 text-slate-300">
            模型、SOUL 和 USER 文档保存后可以直接生效；但渠道开关、ADMIN_USER_IDS 和 `.env` 凭证改动，仍然需要重启 ikaros core。
          </p>
          <div class="mt-6 rounded-2xl border border-white/10 bg-white/5 p-4 text-sm leading-7 text-slate-300">
            {{ form.restart_notice }}
          </div>
        </div>
      </section>

      <section class="grid gap-6 xl:grid-cols-2">
        <article
          v-for="roleKey in roleOrder"
          :key="roleKey"
          class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm"
        >
          <div class="flex items-center gap-3">
            <div class="flex h-12 w-12 items-center justify-center rounded-2xl bg-indigo-100 text-indigo-700">
              <Bot class="h-5 w-5" />
            </div>
            <div>
              <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Model</div>
              <h3 class="text-xl font-semibold text-slate-900">{{ roleLabels[roleKey] }}</h3>
            </div>
          </div>

          <div class="mt-6 grid gap-4 md:grid-cols-2">
            <label class="space-y-2">
              <div class="text-sm font-medium text-slate-700">Provider 名称</div>
              <input v-model="form.models[roleKey].provider_name" type="text" placeholder="例如 proxy" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
            </label>
            <label class="space-y-2">
              <div class="text-sm font-medium text-slate-700">模型 ID</div>
              <input v-model="form.models[roleKey].model_id" type="text" placeholder="例如 gpt-5.4" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
            </label>
            <label class="space-y-2 md:col-span-2">
              <div class="text-sm font-medium text-slate-700">Base URL</div>
              <input v-model="form.models[roleKey].base_url" type="text" placeholder="https://api.example.com/v1" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
            </label>
            <label class="space-y-2 md:col-span-2">
              <div class="text-sm font-medium text-slate-700">API Key</div>
              <input v-model="form.models[roleKey].api_key" type="text" placeholder="sk-..." class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
            </label>
            <label class="space-y-2">
              <div class="text-sm font-medium text-slate-700">展示名称</div>
              <input v-model="form.models[roleKey].display_name" type="text" placeholder="可留空" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
            </label>
            <label class="space-y-2">
              <div class="text-sm font-medium text-slate-700">API 形式</div>
              <select v-model="form.models[roleKey].api_style" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
                <option value="openai-completions">openai-completions</option>
              </select>
            </label>
          </div>

          <div class="mt-5 flex flex-wrap gap-3">
            <label class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
              <input v-model="form.models[roleKey].reasoning" type="checkbox" class="h-4 w-4">
              开启 reasoning
            </label>
            <label
              v-for="inputType in inputTypeOptions"
              :key="inputType"
              class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700"
            >
              <input v-model="form.models[roleKey].input_types" type="checkbox" :value="inputType" class="h-4 w-4">
              {{ inputType }}
            </label>
          </div>

          <div class="mt-5 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
            当前模型键：<span class="font-medium text-slate-900">{{ form.models[roleKey].model_key || '未配置' }}</span>
          </div>
        </article>
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
              :disabled="generatingSoul"
              @click="generateDoc({ kind: 'soul', brief: soulBrief, current_content: form.docs.soul_content })"
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
              :disabled="generatingUser"
              @click="generateDoc({ kind: 'user', brief: userBrief, current_content: form.docs.user_content })"
            >
              <Loader2 v-if="generatingUser" class="h-4 w-4 animate-spin" />
              <Sparkles v-else class="h-4 w-4" />
              AI 生成 USER
            </button>
          </div>

          <textarea v-model="form.docs.user_content" class="mt-4 min-h-[420px] w-full rounded-[24px] border border-slate-200 bg-slate-50 px-4 py-4 text-sm leading-7 outline-none transition focus:border-cyan-400 focus:bg-white" />
        </article>
      </section>

      <section class="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
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

          <div class="mt-6 grid gap-3 md:grid-cols-2">
            <label
              v-for="[platform] in platformEntries"
              :key="platform"
              class="flex items-center justify-between rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3"
            >
              <div>
                <div class="text-sm font-medium text-slate-800">{{ platform }}</div>
                <div class="text-xs text-slate-500">runtime-config 平台开关</div>
              </div>
              <input v-model="form.channels.platforms[platform]" type="checkbox" class="h-4 w-4">
            </label>
          </div>

          <div class="mt-6 grid gap-4 md:grid-cols-2">
            <label class="space-y-2 md:col-span-2">
              <div class="text-sm font-medium text-slate-700">ADMIN_USER_IDS</div>
              <textarea v-model="adminIdsInput" class="min-h-[120px] w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-7 outline-none transition focus:border-cyan-400 focus:bg-white" placeholder="每行一个 ID，也支持逗号分隔" />
            </label>

            <label class="space-y-2 md:col-span-2">
              <div class="text-sm font-medium text-slate-700">Telegram Bot Token</div>
              <input v-model="form.channels.telegram_bot_token" type="text" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
            </label>

            <label class="space-y-2 md:col-span-2">
              <div class="text-sm font-medium text-slate-700">Discord Bot Token</div>
              <input v-model="form.channels.discord_bot_token" type="text" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
            </label>

            <label class="space-y-2">
              <div class="text-sm font-medium text-slate-700">DingTalk Client ID</div>
              <input v-model="form.channels.dingtalk_client_id" type="text" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
            </label>

            <label class="space-y-2">
              <div class="text-sm font-medium text-slate-700">DingTalk Client Secret</div>
              <input v-model="form.channels.dingtalk_client_secret" type="text" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
            </label>
          </div>
        </article>

        <article class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
          <div class="flex items-center gap-3">
            <div class="flex h-12 w-12 items-center justify-center rounded-2xl bg-sky-100 text-sky-700">
              <KeyRound class="h-5 w-5" />
            </div>
            <div>
              <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Env</div>
              <h3 class="text-xl font-semibold text-slate-900">`.env` 补充项</h3>
            </div>
          </div>

          <label class="mt-6 flex items-center justify-between rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
            <div>
              <div class="text-sm font-medium text-slate-800">Web Channel Enable</div>
              <div class="text-xs text-slate-500">对应 `.env` 中的 `WEB_CHANNEL_ENABLE`</div>
            </div>
            <input v-model="form.channels.web_channel_enable" type="checkbox" class="h-4 w-4">
          </label>

          <label class="mt-4 flex items-center justify-between rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
            <div>
              <div class="text-sm font-medium text-slate-800">Weixin Enable</div>
              <div class="text-xs text-slate-500">对应 `.env` 中的 `WEIXIN_ENABLE`</div>
            </div>
            <input v-model="form.channels.weixin_enable" type="checkbox" class="h-4 w-4">
          </label>

          <label class="mt-4 block space-y-2">
            <div class="text-sm font-medium text-slate-700">Weixin Base URL</div>
            <input v-model="form.channels.weixin_base_url" type="text" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
          </label>

          <label class="mt-4 block space-y-2">
            <div class="text-sm font-medium text-slate-700">Weixin CDN Base URL</div>
            <input v-model="form.channels.weixin_cdn_base_url" type="text" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
          </label>

          <div class="mt-6 rounded-[24px] border border-slate-200 bg-slate-50 p-4 text-sm leading-7 text-slate-600">
            <div><span class="font-medium text-slate-900">.env</span>：{{ form.paths.env }}</div>
            <div class="mt-2"><span class="font-medium text-slate-900">models.json</span>：{{ form.paths.models }}</div>
          </div>
        </article>
      </section>
    </template>
  </div>
</template>
