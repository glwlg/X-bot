<script setup lang="ts">
import axios from 'axios'
import { computed, onMounted, ref } from 'vue'
import {
    CheckCircle2,
    Eye,
    EyeOff,
    KeyRound,
    Loader2,
    Plus,
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

type FieldMeta = {
    key: string
    label: string
    placeholder: string
    secret?: boolean
}

type ServiceMeta = {
    value: string
    label: string
    hint: string
    notice: string
    fields: FieldMeta[]
}

type ServiceOption = {
    value: string
    label: string
}

type CredentialFieldState = {
    id: string
    key: string
    value: string
    secret: boolean
}

type CredentialFieldView = {
    key: string
    label: string
    value: unknown
    secret: boolean
}

type CredentialFormState = {
    name: string
    fields: CredentialFieldState[]
    is_default: boolean
}

const servicePresets: ServiceMeta[] = [
    {
        value: 'wechat_official_account',
        label: '微信公众号',
        hint: '这是一个预设模板。你也可以输入任意其他服务类型；article_publisher 会直接读取这个服务下的公众号凭据。',
        notice: 'article_publisher 可直接使用这条公众号凭据。',
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
        hint: '这是一个预设模板。你也可以输入任意其他服务类型，按 key/value 自由维护需要的凭据字段。',
        notice: '这条发布通道配置已就绪。',
        fields: [
            { key: 'endpoint', label: 'Endpoint', placeholder: 'https://publisher.example.com/xhs' },
            { key: 'token', label: 'Token', placeholder: '可选 token', secret: true },
            { key: 'api_key', label: 'API Key', placeholder: '可选 api_key', secret: true },
            { key: 'author', label: '作者署名', placeholder: '可选，例如：Ikaros' },
            { key: 'note', label: '备注', placeholder: '可选，用于标注用途或环境' },
        ],
    },
]

const SENSITIVE_KEY_TOKENS = [
    'secret',
    'token',
    'password',
    'passwd',
    'api_key',
    'apikey',
    'private',
    'access_key',
    'refresh_key',
]

const nextFieldId = () => `field_${Math.random().toString(36).slice(2, 10)}`

const normalizeText = (value: unknown) => String(value ?? '').trim()

const createFieldState = (
    key = '',
    value = '',
    secret = false,
): CredentialFieldState => ({
    id: nextFieldId(),
    key,
    value,
    secret,
})

const getServicePreset = (service: string) =>
    servicePresets.find(item => item.value === normalizeText(service)) || null

const guessSecretField = (key: string) => {
    const normalized = normalizeText(key).toLowerCase()
    return SENSITIVE_KEY_TOKENS.some(token => normalized.includes(token))
}

const buildServiceMeta = (service: string): ServiceMeta => {
    const normalized = normalizeText(service)
    const preset = getServicePreset(normalized)
    if (preset) {
        return preset
    }
    return {
        value: normalized,
        label: normalized || '自定义服务',
        hint: normalized
            ? `当前服务类型为 ${normalized}。凭据字段不做限制，你可以按任意 key/value 录入。`
            : '输入任意服务类型，例如：github_app、telegram_bot、aliyun_oss、openai_api。',
        notice: normalized
            ? '这条凭据会按当前服务类型保存，可供对应技能或模块读取。'
            : '先输入服务类型，再保存对应凭据。',
        fields: [],
    }
}

const buildFieldStates = (
    service: string,
    data: Record<string, unknown> = {},
): CredentialFieldState[] => {
    const preset = getServicePreset(service)
    const rows: CredentialFieldState[] = []
    const usedKeys = new Set<string>()

    if (preset) {
        for (const field of preset.fields) {
            usedKeys.add(field.key)
            rows.push(
                createFieldState(
                    field.key,
                    String(data[field.key] ?? ''),
                    Boolean(field.secret),
                ),
            )
        }
    }

    for (const [key, rawValue] of Object.entries(data)) {
        if (usedKeys.has(key)) {
            continue
        }
        rows.push(createFieldState(key, String(rawValue ?? ''), guessSecretField(key)))
    }

    if (!rows.length) {
        rows.push(createFieldState('', '', false))
    }

    return rows
}

const describeFields = (
    service: string,
    data: Record<string, unknown>,
): CredentialFieldView[] => {
    const preset = getServicePreset(service)
    const rows: CredentialFieldView[] = []
    const usedKeys = new Set<string>()

    if (preset) {
        for (const field of preset.fields) {
            usedKeys.add(field.key)
            rows.push({
                key: field.key,
                label: field.label,
                value: data[field.key],
                secret: Boolean(field.secret),
            })
        }
    }

    for (const [key, rawValue] of Object.entries(data)) {
        if (usedKeys.has(key)) {
            continue
        }
        rows.push({
            key,
            label: key,
            value: rawValue,
            secret: guessSecretField(key),
        })
    }

    return rows
}

const emptyForm = (service: string, isDefault = false): CredentialFormState => ({
    name: '',
    fields: buildFieldStates(service),
    is_default: isDefault,
})

const services = ref<CredentialService[]>([])
const selectedService = ref('')
const selectedEntryId = ref('')
const form = ref<CredentialFormState>(emptyForm(''))
const loading = ref(false)
const saving = ref(false)
const defaultingKey = ref('')
const deletingKey = ref('')
const errorText = ref('')
const successText = ref('')

const getEntriesByService = (service: string) => {
    const normalized = normalizeText(service)
    const target = services.value.find(item => item.service === normalized)
    return Array.isArray(target?.entries) ? target.entries : []
}

const serviceMeta = computed(() => buildServiceMeta(selectedService.value))

const serviceSuggestions = computed<ServiceOption[]>(() => {
    const seen = new Set<string>()
    const options: ServiceOption[] = []

    for (const preset of servicePresets) {
        if (seen.has(preset.value)) continue
        seen.add(preset.value)
        options.push({ value: preset.value, label: preset.label })
    }

    for (const service of services.value) {
        if (!service.service || seen.has(service.service)) continue
        seen.add(service.service)
        options.push({
            value: service.service,
            label: getServicePreset(service.service)?.label || service.service,
        })
    }

    return options
})

const entries = computed<CredentialEntry[]>(() => getEntriesByService(selectedService.value))

const selectedEntry = computed(() =>
    entries.value.find(item => item.id === selectedEntryId.value) || null,
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

const maskValue = (field: CredentialFieldView) => {
    const text = String(field.value ?? '').trim()
    if (!text) return '未填写'
    if (!field.secret) return text
    if (text.length <= 8) return '••••••••'
    return `${text.slice(0, 4)}••••${text.slice(-4)}`
}

const resetForm = (service = selectedService.value) => {
    selectedEntryId.value = ''
    form.value = emptyForm(service, getEntriesByService(service).length === 0)
}

const applyEntryToForm = (entry: CredentialEntry | null) => {
    if (!entry) {
        resetForm()
        return
    }

    selectedService.value = entry.service
    form.value = {
        name: entry.name,
        fields: buildFieldStates(entry.service, entry.data || {}),
        is_default: Boolean(entry.is_default),
    }
    selectedEntryId.value = entry.id
}

const selectEntry = (entry: CredentialEntry) => {
    errorText.value = ''
    successText.value = ''
    applyEntryToForm(entry)
}

const chooseService = (service: string) => {
    selectedService.value = normalizeText(service)
    errorText.value = ''
    successText.value = ''
    resetForm(selectedService.value)
}

const handleServiceInput = () => {
    errorText.value = ''
    successText.value = ''
    selectedEntryId.value = ''
}

const startCreate = () => {
    errorText.value = ''
    successText.value = ''
    resetForm(selectedService.value)
}

const addField = () => {
    form.value.fields.push(createFieldState('', '', false))
}

const removeField = (fieldId: string) => {
    if (form.value.fields.length <= 1) {
        form.value.fields = [createFieldState('', '', false)]
        return
    }
    form.value.fields = form.value.fields.filter(field => field.id !== fieldId)
}

const toggleFieldSecret = (fieldId: string) => {
    const target = form.value.fields.find(field => field.id === fieldId)
    if (!target) return
    target.secret = !target.secret
}

const fieldPlaceholder = (field: CredentialFieldState) => {
    const preset = getServicePreset(selectedService.value)
    const meta = preset?.fields.find(item => item.key === normalizeText(field.key))
    if (meta) return meta.placeholder
    return '字段值'
}

const buildPayload = (): { data: Record<string, unknown> | null; error: string } => {
    const payload: Record<string, unknown> = {}
    const seenKeys = new Set<string>()

    for (const field of form.value.fields) {
        const key = normalizeText(field.key)
        const value = normalizeText(field.value)

        if (!key && !value) {
            continue
        }
        if (!key) {
            return { data: null, error: '存在未填写字段名的凭据项。' }
        }
        if (!value) {
            return { data: null, error: `字段 ${key} 还没有填写值。` }
        }
        if (seenKeys.has(key)) {
            return { data: null, error: `字段名重复：${key}` }
        }

        seenKeys.add(key)
        payload[key] = value
    }

    if (!Object.keys(payload).length) {
        return { data: null, error: '请至少填写一项凭据内容。' }
    }

    return { data: payload, error: '' }
}

const load = async () => {
    loading.value = true
    errorText.value = ''
    try {
        const response = await listMyCredentials()
        services.value = Array.isArray(response.data) ? response.data : []

        if (!normalizeText(selectedService.value) && services.value.length > 0) {
            selectedService.value = services.value[0]!.service
        }

        if (selectedEntryId.value) {
            const current = getEntriesByService(selectedService.value).find(
                item => item.id === selectedEntryId.value,
            ) || null
            if (current) {
                applyEntryToForm(current)
                return
            }
        }

        resetForm(selectedService.value)
    } catch (error) {
        errorText.value = parseErrorMessage(error, '凭据加载失败')
    } finally {
        loading.value = false
    }
}

const submit = async () => {
    errorText.value = ''
    successText.value = ''

    const service = normalizeText(selectedService.value)
    if (!service) {
        errorText.value = '请先填写服务类型。'
        return
    }

    const name = normalizeText(form.value.name)
    if (!name) {
        errorText.value = '请先填写凭据别名。'
        return
    }

    const payload = buildPayload()
    if (!payload.data) {
        errorText.value = payload.error
        return
    }

    saving.value = true
    try {
        const isDefault = form.value.is_default || getEntriesByService(service).length === 0
        const response = selectedEntry.value
            ? await updateMyCredential(service, selectedEntry.value.id, {
                name,
                data: payload.data,
                is_default: isDefault,
            })
            : await createMyCredential(service, {
                name,
                data: payload.data,
                is_default: isDefault,
            })

        selectedService.value = service
        successText.value = selectedEntry.value ? '凭据已更新。' : '凭据已保存。'
        await load()
        const next = getEntriesByService(service).find(item => item.id === response.data.id) || null
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
        await setMyDefaultCredential(entry.service, entry.id)
        successText.value = `默认凭据已切换为 ${entry.name}。`
        await load()
        const next = getEntriesByService(entry.service).find(item => item.id === entry.id) || null
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
        await deleteMyCredential(entry.service, entry.id)
        successText.value = `${entry.name} 已删除。`
        await load()
    } catch (error) {
        errorText.value = parseErrorMessage(error, '凭据删除失败')
    } finally {
        deletingKey.value = ''
    }
}

const entryNotice = (service: string) => buildServiceMeta(service).notice

onMounted(load)
</script>

<template>
  <div class="grid gap-6 p-6 md:grid-cols-[440px_minmax(0,1fr)] md:p-8">
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
        <div class="space-y-3">
          <div class="text-sm font-medium text-slate-700">服务类型</div>

          <input
            v-model="selectedService"
            list="credential-service-options"
            type="text"
            class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none focus:border-cyan-400 focus:bg-white"
            placeholder="输入服务类型，例如：github_app / telegram_bot / aliyun_oss"
            @input="handleServiceInput"
          >

          <datalist id="credential-service-options">
            <option v-for="service in serviceSuggestions" :key="service.value" :value="service.value">
              {{ service.label }}
            </option>
          </datalist>

          <div class="flex flex-wrap gap-2">
            <button
              v-for="service in serviceSuggestions"
              :key="service.value"
              type="button"
              class="inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs transition"
              :class="selectedService === service.value ? 'border-cyan-300 bg-cyan-50 text-cyan-700' : 'border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100'"
              @click="chooseService(service.value)"
            >
              {{ service.label }}
            </button>
          </div>

          <div class="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-7 text-slate-600">
            {{ serviceMeta.hint }}
          </div>
        </div>

        <input
          v-model="form.name"
          type="text"
          class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none focus:border-cyan-400 focus:bg-white"
          placeholder="凭据别名，例如：主号 / 生产环境 / 研发机器人"
        >

        <div class="space-y-3">
          <div class="flex items-center justify-between gap-3">
            <div class="text-sm font-medium text-slate-700">凭据字段</div>
            <button
              type="button"
              class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs text-slate-700 transition hover:bg-slate-100"
              @click="addField"
            >
              <Plus class="h-3.5 w-3.5" />
              添加字段
            </button>
          </div>

          <div
            v-for="field in form.fields"
            :key="field.id"
            class="rounded-2xl border border-slate-200 bg-slate-50 p-3"
          >
            <div class="grid gap-3 md:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)_auto_auto]">
              <input
                v-model="field.key"
                type="text"
                class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none focus:border-cyan-400"
                placeholder="字段名，例如 app_id / token / username"
              >

              <input
                v-model="field.value"
                :type="field.secret ? 'password' : 'text'"
                class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none focus:border-cyan-400"
                :placeholder="fieldPlaceholder(field)"
              >

              <button
                type="button"
                class="inline-flex items-center justify-center rounded-2xl border border-slate-200 bg-white px-3 py-3 text-slate-600 transition hover:bg-slate-100"
                @click="toggleFieldSecret(field.id)"
              >
                <EyeOff v-if="field.secret" class="h-4 w-4" />
                <Eye v-else class="h-4 w-4" />
              </button>

              <button
                type="button"
                class="inline-flex items-center justify-center rounded-2xl border border-rose-200 bg-rose-50 px-3 py-3 text-rose-700 transition hover:bg-rose-100"
                @click="removeField(field.id)"
              >
                <Trash2 class="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>

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
          <div v-if="selectedService" class="mt-1 text-xs text-slate-400">{{ selectedService }}</div>
        </div>
        <div class="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-600">
          {{ entries.length }} 条
        </div>
      </div>

      <div v-if="loading" class="mt-6 flex items-center gap-2 text-sm text-slate-500">
        <Loader2 class="h-4 w-4 animate-spin" />
        正在加载凭据列表
      </div>

      <div v-else-if="!selectedService" class="mt-6 flex min-h-[240px] flex-col items-center justify-center gap-3 rounded-[24px] border border-dashed border-slate-200 bg-slate-50 px-6 text-center text-slate-500">
        <TriangleAlert class="h-5 w-5" />
        <div>先输入一个服务类型，或点上方预设模板开始配置。</div>
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
              v-for="field in describeFields(entry.service, entry.data)"
              :key="field.key"
              class="flex items-start justify-between gap-4 text-sm"
            >
              <span class="text-slate-500">{{ field.label }}</span>
              <span class="max-w-[58%] truncate text-right text-slate-700">
                {{ maskValue(field) }}
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
            {{ entryNotice(entry.service) }}
          </div>
        </article>
      </div>
    </section>
  </div>
</template>
