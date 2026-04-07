<script setup lang="ts">
import axios from 'axios'
import { computed, onMounted, ref, watch } from 'vue'
import {
    CheckCircle2,
    KeyRound,
    Loader2,
    RefreshCw,
    SquarePen,
    Star,
    Trash2,
    TriangleAlert,
} from 'lucide-vue-next'

import {
    createMyCredential,
    deleteMyCredential,
    listMyCredentials,
    setMyDefaultCredential,
    updateMyCredential,
    type CredentialEntry,
    type CredentialService,
} from '@/api/credentials'

type ServiceKey = 'wechat_official_account' | 'xiaohongshu_publisher'

type FieldMeta = {
    key: string
    label: string
    placeholder: string
    secret?: boolean
}

type ServiceMeta = {
    value: ServiceKey
    label: string
    hint: string
    fields: FieldMeta[]
}

type CredentialFormState = {
    name: string
    app_id: string
    app_secret: string
    author: string
    note: string
    endpoint: string
    token: string
    api_key: string
    is_default: boolean
}

const serviceOptions: ServiceMeta[] = [
    {
        value: 'wechat_official_account',
        label: '微信公众号',
        hint: '可保存多个公众号凭据。别名会作为 article_publisher 的公众号选择名；未指定时默认使用默认项，没有默认项则回退第一条。',
        fields: [
            { key: 'app_id', label: 'App ID', placeholder: 'wx1234567890abcdef' },
            { key: 'app_secret', label: 'App Secret', placeholder: '填写公众号 app_secret', secret: true },
            { key: 'author', label: '作者署名', placeholder: '可选，例如：Ikaros 编辑部' },
            { key: 'note', label: '备注', placeholder: '可选，用于标注用途或团队' },
        ],
    },
    {
        value: 'xiaohongshu_publisher',
        label: '小红书发布',
        hint: '保存小红书发布通道配置。若后续需要多个发布端点，也可以在这里按别名管理多条配置。',
        fields: [
            { key: 'endpoint', label: 'Endpoint', placeholder: 'https://publisher.example.com/xhs' },
            { key: 'token', label: 'Token', placeholder: '可选 token', secret: true },
            { key: 'api_key', label: 'API Key', placeholder: '可选 api_key', secret: true },
            { key: 'author', label: '作者署名', placeholder: '可选，例如：Ikaros' },
            { key: 'note', label: '备注', placeholder: '可选，用于标注用途或环境' },
        ],
    },
]

const emptyForm = (): CredentialFormState => ({
    name: '',
    app_id: '',
    app_secret: '',
    author: '',
    note: '',
    endpoint: '',
    token: '',
    api_key: '',
    is_default: false,
})

const services = ref<CredentialService[]>([])
const selectedService = ref<ServiceKey>('wechat_official_account')
const selectedEntryId = ref('')
const form = ref<CredentialFormState>(emptyForm())
const loading = ref(false)
const saving = ref(false)
const defaultingKey = ref('')
const deletingKey = ref('')
const errorText = ref('')
const successText = ref('')

const serviceMeta = computed(() =>
    serviceOptions.find(option => option.value === selectedService.value) || serviceOptions[0]!
)

const entries = computed<CredentialEntry[]>(() => {
    const service = services.value.find(item => item.service === selectedService.value)
    return Array.isArray(service?.entries) ? service!.entries : []
})

const selectedEntry = computed(() =>
    entries.value.find(item => item.id === selectedEntryId.value) || null
)

const submitLabel = computed(() => (selectedEntry.value ? '更新凭据' : '新增凭据'))

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

const maskValue = (field: FieldMeta, value: unknown) => {
    const text = String(value ?? '').trim()
    if (!text) return '未填写'
    if (!field.secret) return text
    if (text.length <= 8) return '••••••••'
    return `${text.slice(0, 4)}••••${text.slice(-4)}`
}

const resetForm = () => {
    form.value = emptyForm()
    selectedEntryId.value = ''
}

const applyEntryToForm = (entry: CredentialEntry | null) => {
    if (!entry) {
        resetForm()
        return
    }

    const data = entry.data || {}
    form.value = {
        name: entry.name,
        app_id: String(data.app_id ?? ''),
        app_secret: String(data.app_secret ?? ''),
        author: String(data.author ?? ''),
        note: String(data.note ?? ''),
        endpoint: String(data.endpoint ?? ''),
        token: String(data.token ?? ''),
        api_key: String(data.api_key ?? ''),
        is_default: Boolean(entry.is_default),
    }
    selectedEntryId.value = entry.id
}

const selectEntry = (entry: CredentialEntry) => {
    errorText.value = ''
    successText.value = ''
    applyEntryToForm(entry)
}

const startCreate = () => {
    errorText.value = ''
    successText.value = ''
    resetForm()
    form.value.is_default = entries.value.length === 0
}

const load = async () => {
    loading.value = true
    errorText.value = ''
    try {
        const response = await listMyCredentials()
        services.value = Array.isArray(response.data) ? response.data : []
        if (selectedEntryId.value) {
            const current = entries.value.find(item => item.id === selectedEntryId.value) || null
            if (current) {
                applyEntryToForm(current)
                return
            }
        }
        startCreate()
    } catch (error) {
        errorText.value = parseErrorMessage(error, '凭据加载失败')
    } finally {
        loading.value = false
    }
}

const buildPayload = () => {
    const payload: Record<string, unknown> = {}
    for (const field of serviceMeta.value.fields) {
        const value = String(form.value[field.key as keyof CredentialFormState] ?? '').trim()
        if (value) {
            payload[field.key] = value
        }
    }
    return payload
}

const submit = async () => {
    errorText.value = ''
    successText.value = ''

    const name = form.value.name.trim()
    if (!name) {
        errorText.value = '请先填写凭据别名。'
        return
    }

    const payload = buildPayload()
    if (!Object.keys(payload).length) {
        errorText.value = '请至少填写一项凭据内容。'
        return
    }

    saving.value = true
    try {
        const isDefault = form.value.is_default || entries.value.length === 0
        const response = selectedEntry.value
            ? await updateMyCredential(selectedService.value, selectedEntry.value.id, {
                name,
                data: payload,
                is_default: isDefault,
            })
            : await createMyCredential(selectedService.value, {
                name,
                data: payload,
                is_default: isDefault,
            })
        successText.value = selectedEntry.value ? '凭据已更新。' : '凭据已保存。'
        await load()
        const next = entries.value.find(item => item.id === response.data.id) || null
        applyEntryToForm(next)
    } catch (error) {
        errorText.value = parseErrorMessage(error, '凭据保存失败')
    } finally {
        saving.value = false
    }
}

const markDefault = async (entry: CredentialEntry) => {
    errorText.value = ''
    successText.value = ''
    defaultingKey.value = entry.id
    try {
        await setMyDefaultCredential(selectedService.value, entry.id)
        successText.value = `默认凭据已切换为 ${entry.name}。`
        await load()
        const next = entries.value.find(item => item.id === entry.id) || null
        applyEntryToForm(next)
    } catch (error) {
        errorText.value = parseErrorMessage(error, '默认凭据设置失败')
    } finally {
        defaultingKey.value = ''
    }
}

const removeEntry = async (entry: CredentialEntry) => {
    errorText.value = ''
    successText.value = ''
    deletingKey.value = entry.id
    try {
        await deleteMyCredential(selectedService.value, entry.id)
        successText.value = `${entry.name} 已删除。`
        await load()
    } catch (error) {
        errorText.value = parseErrorMessage(error, '凭据删除失败')
    } finally {
        deletingKey.value = ''
    }
}

watch(selectedService, () => {
    errorText.value = ''
    successText.value = ''
    startCreate()
})

onMounted(load)
</script>

<template>
  <div class="grid gap-6 p-6 md:grid-cols-[400px_minmax(0,1fr)] md:p-8">
    <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex items-center gap-3">
        <div class="flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-100 text-amber-700">
          <KeyRound class="h-5 w-5" />
        </div>
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Credentials</div>
          <h2 class="text-xl font-semibold text-slate-900">凭据管理</h2>
        </div>
      </div>

      <form class="mt-6 space-y-4" @submit.prevent="submit">
        <select v-model="selectedService" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none focus:border-cyan-400 focus:bg-white">
          <option v-for="service in serviceOptions" :key="service.value" :value="service.value">
            {{ service.label }}
          </option>
        </select>

        <div class="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-7 text-slate-600">
          {{ serviceMeta.hint }}
        </div>

        <input
          v-model="form.name"
          type="text"
          class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none focus:border-cyan-400 focus:bg-white"
          placeholder="凭据别名，例如：主号 / 研发号 / 市场号"
        >

        <template v-for="field in serviceMeta.fields" :key="field.key">
          <input
            v-model="form[field.key as keyof CredentialFormState]"
            :type="field.secret ? 'password' : 'text'"
            class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none focus:border-cyan-400 focus:bg-white"
            :placeholder="field.label + ' · ' + field.placeholder"
          >
        </template>

        <label class="flex items-center gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
          <input v-model="form.is_default" type="checkbox" class="h-4 w-4 rounded border-slate-300">
          保存后设为默认凭据
        </label>

        <div v-if="errorText" class="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {{ errorText }}
        </div>

        <div v-if="successText" class="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          {{ successText }}
        </div>

        <div class="flex gap-3">
          <button class="inline-flex flex-1 items-center justify-center gap-2 rounded-2xl bg-slate-950 px-4 py-3 text-sm font-medium text-white transition hover:bg-slate-800 disabled:opacity-60" :disabled="saving">
            <Loader2 v-if="saving" class="h-4 w-4 animate-spin" />
            {{ submitLabel }}
          </button>

          <button type="button" class="inline-flex items-center justify-center rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700 transition hover:bg-slate-100" :disabled="loading" @click="startCreate">
            新建
          </button>

          <button type="button" class="inline-flex items-center justify-center rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700 transition hover:bg-slate-100" :disabled="loading" @click="load">
            <RefreshCw class="h-4 w-4" />
          </button>
        </div>
      </form>
    </section>

    <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex items-center justify-between gap-3">
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">{{ serviceMeta.label }}</div>
          <h2 class="text-xl font-semibold text-slate-900">当前凭据</h2>
        </div>
        <div class="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-600">
          {{ entries.length }} 条
        </div>
      </div>

      <div v-if="loading" class="mt-6 flex items-center gap-2 text-sm text-slate-500">
        <Loader2 class="h-4 w-4 animate-spin" />
        正在加载凭据列表
      </div>

      <div v-else-if="!entries.length" class="mt-6 flex min-h-[240px] flex-col items-center justify-center gap-3 rounded-[24px] border border-dashed border-slate-200 bg-slate-50 px-6 text-center text-slate-500">
        <TriangleAlert class="h-5 w-5" />
        <div>当前服务还没有保存凭据。</div>
      </div>

      <div v-else class="mt-6 grid gap-4 md:grid-cols-2">
        <article
          v-for="entry in entries"
          :key="entry.id"
          class="rounded-[24px] border p-5 transition"
          :class="selectedEntryId === entry.id ? 'border-cyan-300 bg-cyan-50/60' : 'border-slate-200 bg-slate-50'"
        >
          <div class="flex items-start justify-between gap-4">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <div class="text-lg font-semibold text-slate-900">{{ entry.name }}</div>
                <span v-if="entry.is_default" class="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-1 text-[11px] font-medium text-amber-700">
                  <Star class="h-3.5 w-3.5" />
                  默认
                </span>
              </div>
              <div class="mt-1 text-xs text-slate-400">ID: {{ entry.id }}</div>
            </div>

            <button
              type="button"
              class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-700 transition hover:bg-slate-100"
              @click="selectEntry(entry)"
            >
              <SquarePen class="h-3.5 w-3.5" />
              编辑
            </button>
          </div>

          <div class="mt-4 space-y-2 rounded-2xl border border-white/70 bg-white/80 px-4 py-3">
            <div
              v-for="field in serviceMeta.fields"
              :key="field.key"
              class="flex items-start justify-between gap-4 text-sm"
            >
              <span class="text-slate-500">{{ field.label }}</span>
              <span class="max-w-[58%] truncate text-right text-slate-700">
                {{ maskValue(field, entry.data[field.key]) }}
              </span>
            </div>
          </div>

          <div class="mt-4 flex flex-wrap items-center gap-2">
            <button
              v-if="!entry.is_default"
              type="button"
              class="inline-flex items-center gap-2 rounded-full border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-700 transition hover:bg-amber-100 disabled:opacity-60"
              :disabled="defaultingKey === entry.id"
              @click="markDefault(entry)"
            >
              <Loader2 v-if="defaultingKey === entry.id" class="h-3.5 w-3.5 animate-spin" />
              <Star v-else class="h-3.5 w-3.5" />
              设为默认
            </button>

            <button
              type="button"
              class="inline-flex items-center gap-2 rounded-full border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs text-rose-700 transition hover:bg-rose-100 disabled:opacity-60"
              :disabled="deletingKey === entry.id"
              @click="removeEntry(entry)"
            >
              <Loader2 v-if="deletingKey === entry.id" class="h-3.5 w-3.5 animate-spin" />
              <Trash2 v-else class="h-3.5 w-3.5" />
              删除
            </button>
          </div>

          <div class="mt-4 flex items-center gap-2 text-sm text-emerald-700">
            <CheckCircle2 class="h-4 w-4" />
            {{ serviceMeta.value === 'wechat_official_account' ? 'article_publisher 可直接使用这条公众号凭据。' : '发布通道配置已就绪。' }}
          </div>
        </article>
      </div>
    </section>
  </div>
</template>
