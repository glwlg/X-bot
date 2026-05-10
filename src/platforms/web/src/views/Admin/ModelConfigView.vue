<script setup lang="ts">
import axios from 'axios'
import { computed, onMounted, ref, watch } from 'vue'
import { Activity, Bot, Box, Copy, Globe2, Loader2, MoreVertical, Play, Plus, Save, ShieldCheck, Trash2 } from 'lucide-vue-next'

import {
    getModelsSnapshot,
    patchModelsSnapshot,
    postModelsLatencyCheck,
    type ModelsLatencyCheckResponse,
    type ModelsQuickRoleSnapshot,
    type ModelsSnapshot,
} from '@/api/models'

const snapshot = ref<ModelsSnapshot | null>(null)
const modelConfigForm = ref<ModelConfigForm | null>(null)
const loading = ref(false)
const saving = ref(false)
const errorText = ref('')
const successText = ref('')
const modelsConfigError = ref('')
const activeModelTab = ref<'defaults' | 'providers' | 'roles' | 'matrix'>('defaults')
const selectedProviderUid = ref('')
const testingActions = ref<Record<string, boolean>>({})
const openRouteMenuRole = ref<RoleKey | ''>('')
const expandedModelUid = ref('')
const providerConnectionStatus = ref<Record<string, ProviderConnectionStatus>>({})

const roleOrder = ['primary', 'routing', 'vision', 'image_generation', 'voice'] as const
const quickRoleOrder = ['primary', 'routing'] as const
const inputTypeOptions = ['text', 'image', 'voice'] as const
const outputTypeOptions = ['text', 'image', 'voice', 'video'] as const
const selectionStrategyOptions = ['priority', 'round_robin', 'least_usage'] as const

type RoleKey = (typeof roleOrder)[number]
type QuickRoleKey = (typeof quickRoleOrder)[number]
type InputType = (typeof inputTypeOptions)[number]
type OutputType = (typeof outputTypeOptions)[number]
type SelectionStrategy = (typeof selectionStrategyOptions)[number]
type NumericValue = number | ''

interface CostForm {
    input: NumericValue
    output: NumericValue
    cacheRead: NumericValue
    cacheWrite: NumericValue
    extras: Record<string, unknown>
}

interface LimitsForm {
    dailyTokens: NumericValue
    dailyImages: NumericValue
    extras: Record<string, unknown>
}

interface ModelForm {
    uid: string
    id: string
    name: string
    reasoning: boolean
    input: InputType[]
    output: OutputType[]
    cost: CostForm
    limits: LimitsForm
    contextWindow: NumericValue
    maxTokens: NumericValue
    extras: Record<string, unknown>
}

interface ProviderForm {
    uid: string
    name: string
    baseUrl: string
    apiKey: string
    api: string
    models: ModelForm[]
    extras: Record<string, unknown>
}

interface RoleConfigForm {
    bindingUid: string
    bindingKey: string
    poolKey: string
    poolUids: string[]
    poolMetaByUid: Record<string, Record<string, unknown>>
    selectionStrategy: SelectionStrategy
    selectionExtras: Record<string, unknown>
}

interface ModelConfigForm {
    mode: string
    topLevelExtras: Record<string, unknown>
    modelExtras: Record<string, unknown>
    poolExtras: Record<string, unknown>
    selectionExtras: Record<string, unknown>
    providers: ProviderForm[]
    roles: Record<RoleKey, RoleConfigForm>
}

interface ModelOption {
    uid: string
    key: string
    providerName: string
    modelId: string
    name: string
    input: InputType[]
    output: OutputType[]
    reasoning: boolean
}

interface QuickRoleForm {
    providerName: string
    baseUrl: string
    apiKey: string
    apiStyle: string
    modelId: string
    displayName: string
    reasoning: boolean
    inputTypes: InputType[]
}

interface ProviderConnectionStatus {
    state: 'success' | 'error'
    message: string
    checkedAt: string
    elapsedMs?: number
}

const roleLabels: Record<RoleKey, string> = {
    primary: 'Primary',
    routing: 'Routing',
    vision: 'Vision',
    image_generation: 'Image Generation',
    voice: 'Voice',
}

const primaryRoleStorageKey = (role: RoleKey) => role
const roleRequiredInputs: Record<RoleKey, InputType[]> = {
    primary: ['text'],
    routing: ['text'],
    vision: ['image'],
    image_generation: [],
    voice: ['voice'],
}
const roleRequiredOutputs: Record<RoleKey, OutputType[]> = {
    primary: [],
    routing: [],
    vision: [],
    image_generation: ['image'],
    voice: [],
}
const roleCapabilityText: Record<RoleKey, string> = {
    primary: '至少支持 text 输入',
    routing: '至少支持 text 输入',
    vision: '至少支持 image 输入',
    image_generation: '至少支持 image 输出',
    voice: '至少支持 voice 输入',
}
const selectionStrategyLabels: Record<SelectionStrategy, string> = {
    priority: '优先级顺序',
    round_robin: '轮询均衡',
    least_usage: '按今日最低用量',
}

let uidCounter = 0
const createDefaultQuickRole = (role: QuickRoleKey): QuickRoleForm => ({
    providerName: '',
    baseUrl: '',
    apiKey: '',
    apiStyle: 'openai-completions',
    modelId: '',
    displayName: '',
    reasoning: false,
    inputTypes: role === 'primary' ? ['text', 'image', 'voice'] : ['text'],
})

const quickRoles = ref<Record<QuickRoleKey, QuickRoleForm>>({
    primary: createDefaultQuickRole('primary'),
    routing: createDefaultQuickRole('routing'),
})
const quickRoleDirty = ref<Record<QuickRoleKey, boolean>>({
    primary: false,
    routing: false,
})
const routingLatencyChecking = ref(false)
const routingLatencyError = ref('')
const routingLatencyResult = ref<ModelsLatencyCheckResponse | null>(null)

const nextUid = (prefix: string) => `${prefix}-${uidCounter++}`

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
    if (error instanceof Error && error.message.trim()) {
        return error.message
    }
    return fallback
}

const asObject = (value: unknown): Record<string, unknown> | null => {
    if (!value || Array.isArray(value) || typeof value !== 'object') {
        return null
    }
    return { ...(value as Record<string, unknown>) }
}

const omitKeys = (source: Record<string, unknown>, keys: string[]) =>
    Object.fromEntries(Object.entries(source).filter(([key]) => !keys.includes(key)))

const normalizeInputTypes = (value: unknown): InputType[] => {
    const normalized: InputType[] = []
    if (!Array.isArray(value)) {
        return normalized
    }
    for (const item of value) {
        const token = String(item || '').trim().toLowerCase() as InputType
        if (inputTypeOptions.includes(token) && !normalized.includes(token)) {
            normalized.push(token)
        }
    }
    return normalized
}

const normalizeOutputTypes = (value: unknown): OutputType[] => {
    const normalized: OutputType[] = []
    if (!Array.isArray(value)) {
        return normalized
    }
    for (const item of value) {
        const token = String(item || '').trim().toLowerCase() as OutputType
        if (outputTypeOptions.includes(token) && !normalized.includes(token)) {
            normalized.push(token)
        }
    }
    return normalized
}

const normalizeSelectionStrategy = (value: unknown): SelectionStrategy => {
    const normalized = String(value || '').trim().toLowerCase() as SelectionStrategy
    if (selectionStrategyOptions.includes(normalized)) {
        return normalized
    }
    return 'priority'
}

const coerceNumber = (value: unknown, fallback: number, minimum = 0) => {
    const parsed = Number(value)
    if (!Number.isFinite(parsed)) {
        return fallback
    }
    return Math.max(minimum, parsed)
}

const coerceInteger = (value: unknown, fallback: number, minimum = 1) =>
    Math.max(minimum, Math.round(coerceNumber(value, fallback, minimum)))

const buildModelKey = (providerName: string, modelId: string) =>
    `${providerName.trim()}/${modelId.trim()}`

const createEmptyModel = (): ModelForm => ({
    uid: nextUid('model'),
    id: '',
    name: '',
    reasoning: false,
    input: ['text'],
    output: ['text'],
    cost: {
        input: 0,
        output: 0,
        cacheRead: 0,
        cacheWrite: 0,
        extras: {},
    },
    limits: {
        dailyTokens: 0,
        dailyImages: 0,
        extras: {},
    },
    contextWindow: 1000000,
    maxTokens: 65536,
    extras: {},
})

const createEmptyProvider = (): ProviderForm => ({
    uid: nextUid('provider'),
    name: '',
    baseUrl: '',
    apiKey: '',
    api: 'openai-completions',
    models: [],
    extras: {},
})

const availableModelOptions = computed<ModelOption[]>(() => {
    const form = modelConfigForm.value
    if (!form) {
        return []
    }
    return form.providers.flatMap(provider =>
        provider.models
            .map(model => {
                const providerName = provider.name.trim()
                const modelId = model.id.trim()
                if (!providerName || !modelId) {
                    return null
                }
                return {
                    uid: model.uid,
                    key: buildModelKey(providerName, modelId),
                    providerName,
                    modelId,
                    name: model.name.trim() || modelId,
                    input: [...model.input],
                    output: [...model.output],
                    reasoning: Boolean(model.reasoning),
                }
            })
            .filter((item): item is ModelOption => Boolean(item))
    )
})

const availableModelMap = computed<Record<string, ModelOption>>(() =>
    Object.fromEntries(availableModelOptions.value.map(option => [option.uid, option]))
)

const sameQuickRole = (left: QuickRoleForm, right: QuickRoleForm) =>
    left.providerName === right.providerName
    && left.baseUrl === right.baseUrl
    && left.apiKey === right.apiKey
    && left.apiStyle === right.apiStyle
    && left.modelId === right.modelId
    && left.displayName === right.displayName
    && left.reasoning === right.reasoning
    && left.inputTypes.length === right.inputTypes.length
    && left.inputTypes.every((item, index) => item === right.inputTypes[index])

const hydrateQuickRole = (role: QuickRoleKey, payload: ModelsQuickRoleSnapshot) => {
    quickRoles.value[role] = {
        providerName: payload.provider_name || '',
        baseUrl: payload.base_url || '',
        apiKey: payload.api_key || '',
        apiStyle: payload.api_style || 'openai-completions',
        modelId: payload.model_id || '',
        displayName: payload.display_name || '',
        reasoning: Boolean(payload.reasoning),
        inputTypes: ((payload.input_types || []) as InputType[]).filter(item => inputTypeOptions.includes(item)),
    }
    quickRoleDirty.value[role] = false
    if (role === 'routing') {
        routingLatencyError.value = ''
        routingLatencyResult.value = null
    }
}

const quickRoleSummary = computed(() =>
    quickRoleOrder.map(role => {
        const payload = snapshot.value?.quick_roles[role]
        return {
            role,
            label: roleLabels[role],
            ready: Boolean(payload?.ready),
            modelKey: payload?.model_key || '',
        }
    })
)

const roleCards = computed(() =>
    roleOrder.map(role => {
        const roleConfig = modelConfigForm.value?.roles[role]
        const selectedOption = roleConfig ? availableModelMap.value[roleConfig.bindingUid] : null
        const poolOptions = rolePoolOptions(role)
        return {
            role,
            label: roleLabels[role],
            currentKey: selectedOption?.key || '',
            poolCount: poolOptions.length,
            bindingOptions: poolOptions,
            candidateOptions: roleCandidateOptions(role),
            capabilityText: roleCapabilityText[role],
            selectionStrategy: roleConfig?.selectionStrategy || 'priority',
        }
    })
)

const modelOverviewStats = computed(() => {
    const providers = modelConfigForm.value?.providers || []
    const models = providers.flatMap(provider => provider.models)
    const readyRoles = quickRoleSummary.value.filter(item => item.ready).length
    return [
        { label: 'Providers 数量', value: providers.length, detail: `已启用 ${providers.length} 个`, icon: Globe2, tone: 'blue' },
        { label: '模型数量', value: models.length, detail: `可用 ${availableModelOptions.value.length} 个`, icon: Box, tone: 'violet' },
        { label: '默认模型状态', value: readyRoles >= quickRoleOrder.length ? '正常' : '待配置', detail: '所有分类已配置', icon: ShieldCheck, tone: 'green' },
        { label: '最近校验结果', value: routingLatencyResult.value ? '通过' : '待测试', detail: routingLatencyResult.value ? `${routingLatencyResult.value.elapsed_ms} ms` : '可进行测试', icon: Activity, tone: 'blue' },
    ]
})

const providerQuickList = computed(() =>
    (modelConfigForm.value?.providers || []).map(provider => ({
        uid: provider.uid,
        name: provider.name || '未命名 provider',
        models: provider.models.map(model => model.id || model.name).filter(Boolean).slice(0, 2),
        count: provider.models.length,
        healthy: Boolean(provider.name && provider.baseUrl),
    }))
)

const selectedProvider = computed(() => {
    const providers = modelConfigForm.value?.providers || []
    return providers.find(provider => provider.uid === selectedProviderUid.value) || providers[0] || null
})

const selectedProviderModels = computed(() => selectedProvider.value?.models || [])

const selectProvider = (providerUid: string) => {
    selectedProviderUid.value = providerUid
    activeModelTab.value = 'providers'
}

const actionKeyForRole = (role: RoleKey) => `role:${role}`
const actionKeyForProvider = (providerUid: string) => `provider:${providerUid}`

const isTestingAction = (key: string) => Boolean(testingActions.value[key])

const setTestingAction = (key: string, busy: boolean) => {
    const next = { ...testingActions.value }
    if (busy) {
        next[key] = true
    } else {
        delete next[key]
    }
    testingActions.value = next
}

const toggleRouteMenu = (role: RoleKey) => {
    openRouteMenuRole.value = openRouteMenuRole.value === role ? '' : role
}

const nowTimeLabel = () =>
    new Date().toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
    })

const roleCompatibilityStatus = (role: RoleKey, option: ModelOption | null | undefined) => {
    if (!option) {
        return 'ineligible' as const
    }
    const requiredInputs = roleRequiredInputs[role]
    const requiredOutputs = roleRequiredOutputs[role]
    for (const inputType of requiredInputs) {
        if (!option.input.includes(inputType)) {
            return 'ineligible' as const
        }
    }
    for (const outputType of requiredOutputs) {
        if (!option.output.length) {
            return 'legacy' as const
        }
        if (!option.output.includes(outputType)) {
            return 'ineligible' as const
        }
    }
    return 'eligible' as const
}

const roleCandidateOptions = (role: RoleKey) =>
    availableModelOptions.value.filter(option => roleCompatibilityStatus(role, option) === 'eligible')

const rolePoolOptions = (role: RoleKey) => {
    const roleConfig = modelConfigForm.value?.roles[role]
    if (!roleConfig) {
        return []
    }
    return roleConfig.poolUids
        .map(uid => availableModelMap.value[uid])
        .filter((option): option is ModelOption => roleCompatibilityStatus(role, option) !== 'ineligible')
}

const findModelEntryByUid = (modelUid: string) => {
    const form = modelConfigForm.value
    if (!form) {
        return null
    }
    for (const provider of form.providers) {
        const model = provider.models.find(item => item.uid === modelUid)
        if (model) {
            return { provider, model }
        }
    }
    return null
}

const findRoleBindingEntry = (role: RoleKey) => {
    const bindingUid = modelConfigForm.value?.roles[role].bindingUid
    return bindingUid ? findModelEntryByUid(bindingUid) : null
}

const findProviderTestModel = (provider: ProviderForm) =>
    provider.models.find(model => model.input.includes('text')) || provider.models[0] || null

const runLatencyForEntry = async (role: RoleKey, provider: ProviderForm, model: ModelForm) => {
    const providerName = provider.name.trim()
    const modelId = model.id.trim()
    if (!providerName) {
        throw new Error('Provider 名称不能为空')
    }
    if (!modelId) {
        throw new Error(`${providerName} 下存在空的模型 ID`)
    }
    if (!provider.apiKey.trim()) {
        throw new Error(`${providerName}/${modelId} 缺少 API Key`)
    }
    const response = await postModelsLatencyCheck({
        role,
        provider_name: providerName,
        base_url: provider.baseUrl.trim(),
        api_key: provider.apiKey,
        api_style: provider.api.trim() || 'openai-completions',
        model_id: modelId,
    })
    return response.data
}

const copyTextToClipboard = async (text: string, label: string) => {
    const payload = text.trim()
    if (!payload) {
        errorText.value = `${label}为空，无法复制`
        successText.value = ''
        return false
    }

    try {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(payload)
        } else {
            throw new Error('clipboard api unavailable')
        }
    } catch {
        try {
            const textarea = document.createElement('textarea')
            textarea.value = payload
            textarea.setAttribute('readonly', 'true')
            textarea.style.position = 'fixed'
            textarea.style.left = '-9999px'
            document.body.appendChild(textarea)
            textarea.select()
            document.execCommand('copy')
            document.body.removeChild(textarea)
        } catch {
            errorText.value = `${label}复制失败，请检查浏览器剪贴板权限`
            successText.value = ''
            return false
        }
    }

    errorText.value = ''
    successText.value = `${label}已复制`
    return true
}

const serializeProviderConfig = (provider: ProviderForm) => {
    const providerName = provider.name.trim() || 'provider'
    return JSON.stringify(
        {
            [providerName]: {
                ...provider.extras,
                baseUrl: provider.baseUrl.trim(),
                apiKey: provider.apiKey,
                api: provider.api.trim() || 'openai-completions',
                models: provider.models.map(model => ({
                    ...model.extras,
                    id: model.id.trim(),
                    name: model.name.trim() || model.id.trim(),
                    reasoning: Boolean(model.reasoning),
                    input: [...model.input],
                    output: [...model.output],
                    cost: {
                        ...model.cost.extras,
                        input: coerceNumber(model.cost.input, 0, 0),
                        output: coerceNumber(model.cost.output, 0, 0),
                        cacheRead: coerceNumber(model.cost.cacheRead, 0, 0),
                        cacheWrite: coerceNumber(model.cost.cacheWrite, 0, 0),
                    },
                    limits: {
                        ...model.limits.extras,
                        dailyTokens: coerceInteger(model.limits.dailyTokens, 0, 0),
                        dailyImages: coerceInteger(model.limits.dailyImages, 0, 0),
                    },
                    contextWindow: coerceInteger(model.contextWindow, 1000000, 1),
                    maxTokens: coerceInteger(model.maxTokens, 65536, 1),
                })),
            },
        },
        null,
        2
    )
}

const setProviderConnectionStatus = (providerUid: string, status: ProviderConnectionStatus) => {
    providerConnectionStatus.value = {
        ...providerConnectionStatus.value,
        [providerUid]: status,
    }
}

const providerConnectionClass = (provider: ProviderForm) =>
    providerConnectionStatus.value[provider.uid]?.state || 'idle'

const providerConnectionText = (provider: ProviderForm) => {
    const status = providerConnectionStatus.value[provider.uid]
    if (status?.state === 'success') {
        return '连接正常'
    }
    if (status?.state === 'error') {
        return '连接失败'
    }
    return provider.name && provider.baseUrl ? '待测试' : '待配置'
}

const providerConnectionDetail = (provider: ProviderForm) => {
    const status = providerConnectionStatus.value[provider.uid]
    if (status) {
        return `最后检测：${status.checkedAt}`
    }
    const testModel = findProviderTestModel(provider)
    return testModel ? `测试模型：${testModel.id || testModel.name}` : '请先新增模型'
}

const providerConnectionSummary = (provider: ProviderForm) => {
    const status = providerConnectionStatus.value[provider.uid]
    if (!status) {
        return '尚未测试连接'
    }
    if (status.state === 'success') {
        return '连接测试成功'
    }
    return '连接测试失败'
}

const providerConnectionMessage = (provider: ProviderForm) => {
    const status = providerConnectionStatus.value[provider.uid]
    if (!status) {
        return ''
    }
    if (status.state === 'success') {
        return `响应时间：${status.elapsedMs || '-'}ms`
    }
    return status.message
}

const testRoleModel = async (role: RoleKey) => {
    const entry = findRoleBindingEntry(role)
    if (!entry) {
        errorText.value = `请先为 ${roleLabels[role]} 选择默认模型`
        successText.value = ''
        openRouteMenuRole.value = ''
        return
    }

    const actionKey = actionKeyForRole(role)
    setTestingAction(actionKey, true)
    errorText.value = ''
    successText.value = ''
    modelsConfigError.value = ''
    try {
        const result = await runLatencyForEntry(role, entry.provider, entry.model)
        if (role === 'routing') {
            routingLatencyResult.value = result
            routingLatencyError.value = ''
        }
        successText.value = `${roleLabels[role]} 测试通过：${result.model_key} · ${result.elapsed_ms} ms`
    } catch (error) {
        errorText.value = parseErrorMessage(error, `${roleLabels[role]} 测试失败`)
    } finally {
        setTestingAction(actionKey, false)
        openRouteMenuRole.value = ''
    }
}

const testAllDefaultRoutes = async () => {
    const entries = roleOrder
        .map(role => ({ role, entry: findRoleBindingEntry(role) }))
        .filter((item): item is { role: RoleKey; entry: { provider: ProviderForm; model: ModelForm } } => Boolean(item.entry))

    if (!entries.length) {
        errorText.value = '请先为默认模型路由绑定至少一个模型'
        successText.value = ''
        return
    }

    setTestingAction('routes:all', true)
    errorText.value = ''
    successText.value = ''
    routingLatencyError.value = ''
    const passed: string[] = []
    const failed: string[] = []

    for (const { role, entry } of entries) {
        const roleActionKey = actionKeyForRole(role)
        setTestingAction(roleActionKey, true)
        try {
            const result = await runLatencyForEntry(role, entry.provider, entry.model)
            if (role === 'routing') {
                routingLatencyResult.value = result
            }
            passed.push(`${roleLabels[role]} ${result.elapsed_ms}ms`)
        } catch (error) {
            failed.push(`${roleLabels[role]}：${parseErrorMessage(error, '测试失败')}`)
        } finally {
            setTestingAction(roleActionKey, false)
        }
    }

    setTestingAction('routes:all', false)
    if (failed.length) {
        errorText.value = `批量测试完成：通过 ${passed.length}，失败 ${failed.length}。${failed.join('；')}`
        return
    }
    successText.value = `批量测试通过：${passed.join('，')}`
}

const copyRoleBindingKey = async (role: RoleKey) => {
    const entry = findRoleBindingEntry(role)
    if (!entry) {
        errorText.value = `请先为 ${roleLabels[role]} 选择默认模型`
        successText.value = ''
        openRouteMenuRole.value = ''
        return
    }
    await copyTextToClipboard(
        buildModelKey(entry.provider.name.trim(), entry.model.id.trim()),
        `${roleLabels[role]} 模型 Key`
    )
    openRouteMenuRole.value = ''
}

const clearRoleBinding = (role: RoleKey) => {
    setRoleBinding(role, '')
    openRouteMenuRole.value = ''
    errorText.value = ''
    successText.value = `${roleLabels[role]} 默认模型已清空`
}

const openRolePool = () => {
    activeModelTab.value = 'roles'
    openRouteMenuRole.value = ''
}

const testProviderConnection = async (provider: ProviderForm) => {
    const model = findProviderTestModel(provider)
    if (!model) {
        const message = `${provider.name || '当前 Provider'} 没有可用于测试的模型`
        setProviderConnectionStatus(provider.uid, {
            state: 'error',
            message,
            checkedAt: nowTimeLabel(),
        })
        errorText.value = message
        successText.value = ''
        return
    }

    const actionKey = actionKeyForProvider(provider.uid)
    setTestingAction(actionKey, true)
    errorText.value = ''
    successText.value = ''
    try {
        const result = await runLatencyForEntry('routing', provider, model)
        setProviderConnectionStatus(provider.uid, {
            state: 'success',
            message: `响应时间：${result.elapsed_ms}ms`,
            checkedAt: nowTimeLabel(),
            elapsedMs: result.elapsed_ms,
        })
        successText.value = `${provider.name || 'Provider'} 连接测试通过：${result.model_key} · ${result.elapsed_ms} ms`
    } catch (error) {
        const message = parseErrorMessage(error, `${provider.name || 'Provider'} 连接测试失败`)
        setProviderConnectionStatus(provider.uid, {
            state: 'error',
            message,
            checkedAt: nowTimeLabel(),
        })
        errorText.value = message
    } finally {
        setTestingAction(actionKey, false)
    }
}

const copyProviderConfig = async (provider: ProviderForm) => {
    await copyTextToClipboard(serializeProviderConfig(provider), `${provider.name || 'Provider'} 配置`)
}

const toggleModelEditor = (modelUid: string) => {
    expandedModelUid.value = expandedModelUid.value === modelUid ? '' : modelUid
}

const toggleModelInput = (model: ModelForm, inputType: InputType) => {
    model.input = model.input.includes(inputType)
        ? model.input.filter(item => item !== inputType)
        : [...model.input, inputType]
}

const toggleModelOutput = (model: ModelForm, outputType: OutputType) => {
    model.output = model.output.includes(outputType)
        ? model.output.filter(item => item !== outputType)
        : [...model.output, outputType]
}

const syncQuickRoleFromModelConfigForm = (role: QuickRoleKey) => {
    if (quickRoleDirty.value[role]) {
        return
    }
    const bindingUid = modelConfigForm.value?.roles[role].bindingUid || ''
    const entry = bindingUid ? findModelEntryByUid(bindingUid) : null
    const nextValue: QuickRoleForm = entry
        ? {
            providerName: entry.provider.name,
            baseUrl: entry.provider.baseUrl,
            apiKey: entry.provider.apiKey,
            apiStyle: entry.provider.api || 'openai-completions',
            modelId: entry.model.id,
            displayName: entry.model.name,
            reasoning: Boolean(entry.model.reasoning),
            inputTypes: entry.model.input.length
                ? [...entry.model.input]
                : [...createDefaultQuickRole(role).inputTypes],
        }
        : createDefaultQuickRole(role)
    if (!sameQuickRole(quickRoles.value[role], nextValue)) {
        quickRoles.value[role] = nextValue
    }
}

const syncQuickRolesFromModelConfigForm = () => {
    for (const role of quickRoleOrder) {
        syncQuickRoleFromModelConfigForm(role)
    }
}

const testRoutingLatency = async () => {
    const routing = quickRoles.value.routing
    routingLatencyChecking.value = true
    routingLatencyError.value = ''
    routingLatencyResult.value = null
    errorText.value = ''
    successText.value = ''
    try {
        const response = await postModelsLatencyCheck({
            role: 'routing',
            provider_name: routing.providerName.trim(),
            base_url: routing.baseUrl.trim(),
            api_key: routing.apiKey,
            api_style: routing.apiStyle.trim() || 'openai-completions',
            model_id: routing.modelId.trim(),
        })
        routingLatencyResult.value = response.data
    } catch (error) {
        routingLatencyError.value = parseErrorMessage(error, 'Routing 模型延迟测试失败')
    } finally {
        routingLatencyChecking.value = false
    }
}

const normalizeRoleSelections = () => {
    const form = modelConfigForm.value
    if (!form) {
        return
    }
    for (const role of roleOrder) {
        const roleConfig = form.roles[role]
        const compatibleUids = new Set(
            availableModelOptions.value
                .filter(option => roleCompatibilityStatus(role, option) !== 'ineligible')
                .map(option => option.uid)
        )
        const filteredPoolUids = roleConfig.poolUids.filter(uid => compatibleUids.has(uid))
        if (filteredPoolUids.length !== roleConfig.poolUids.length) {
            roleConfig.poolUids = filteredPoolUids
        }
        for (const uid of Object.keys(roleConfig.poolMetaByUid)) {
            if (!compatibleUids.has(uid)) {
                delete roleConfig.poolMetaByUid[uid]
            }
        }
        if (roleConfig.bindingUid && !compatibleUids.has(roleConfig.bindingUid)) {
            roleConfig.bindingUid = ''
        }
        if (roleConfig.bindingUid && !roleConfig.poolUids.includes(roleConfig.bindingUid)) {
            roleConfig.poolUids = [...roleConfig.poolUids, roleConfig.bindingUid]
            roleConfig.poolMetaByUid[roleConfig.bindingUid] = roleConfig.poolMetaByUid[roleConfig.bindingUid] || {}
        }
    }
}

const hydrateModelsConfigForm = (payload: Record<string, unknown>) => {
    uidCounter = 0
    const rawProviders = asObject(payload.providers) || {}
    const providers: ProviderForm[] = []
    const modelUidByKey: Record<string, string> = {}

    for (const [providerName, rawProviderValue] of Object.entries(rawProviders)) {
        const rawProvider = asObject(rawProviderValue)
        if (!rawProvider) {
            continue
        }
        const provider: ProviderForm = {
            uid: nextUid('provider'),
            name: providerName,
            baseUrl: String(rawProvider.baseUrl || '').trim(),
            apiKey: String(rawProvider.apiKey || ''),
            api: String(rawProvider.api || '').trim() || 'openai-completions',
            models: [],
            extras: omitKeys(rawProvider, ['baseUrl', 'apiKey', 'api', 'models']),
        }

        const rawModels = Array.isArray(rawProvider.models) ? rawProvider.models : []
        for (const item of rawModels) {
            const rawModel = asObject(item)
            if (!rawModel) {
                continue
            }
            const cost = asObject(rawModel.cost) || {}
            const limits = asObject(rawModel.limits) || {}
            const model: ModelForm = {
                uid: nextUid('model'),
                id: String(rawModel.id || '').trim(),
                name: String(rawModel.name || rawModel.id || '').trim(),
                reasoning: Boolean(rawModel.reasoning),
                input: normalizeInputTypes(rawModel.input),
                output: normalizeOutputTypes(rawModel.output),
                cost: {
                    input: coerceNumber(cost.input, 0, 0),
                    output: coerceNumber(cost.output, 0, 0),
                    cacheRead: coerceNumber(cost.cacheRead, 0, 0),
                    cacheWrite: coerceNumber(cost.cacheWrite, 0, 0),
                    extras: omitKeys(cost, ['input', 'output', 'cacheRead', 'cacheWrite']),
                },
                limits: {
                    dailyTokens: coerceInteger(limits.dailyTokens, 0, 0),
                    dailyImages: coerceInteger(limits.dailyImages, 0, 0),
                    extras: omitKeys(limits, ['dailyTokens', 'dailyImages']),
                },
                contextWindow: coerceInteger(rawModel.contextWindow, 1000000, 1),
                maxTokens: coerceInteger(rawModel.maxTokens, 65536, 1),
                extras: omitKeys(rawModel, ['id', 'name', 'reasoning', 'input', 'output', 'cost', 'limits', 'contextWindow', 'maxTokens']),
            }
            provider.models.push(model)
            if (provider.name && model.id) {
                modelUidByKey[buildModelKey(provider.name, model.id)] = model.uid
            }
        }

        providers.push(provider)
    }

    const rawModelBindings = asObject(payload.model) || {}
    const rawPools = asObject(payload.models) || {}
    const rawSelection = asObject(payload.selection) || {}
    const roles = {} as Record<RoleKey, RoleConfigForm>

    for (const role of roleOrder) {
        const bindingKey = primaryRoleStorageKey(role)
        const selectedModelKey = String(rawModelBindings[bindingKey] || '').trim()
        const poolKey = primaryRoleStorageKey(role)
        const rawPool = rawPools[poolKey]
        const rawSelectionValue = rawSelection[role]
        const selectionPayload =
            typeof rawSelectionValue === 'string'
                ? { strategy: rawSelectionValue }
                : asObject(rawSelectionValue) || {}
        const poolUids: string[] = []
        const poolMetaByUid: Record<string, Record<string, unknown>> = {}

        if (Array.isArray(rawPool)) {
            for (const item of rawPool) {
                const modelKey = String(item || '').trim()
                const uid = modelUidByKey[modelKey]
                if (uid && !poolUids.includes(uid)) {
                    poolUids.push(uid)
                }
            }
        } else {
            const poolObject = asObject(rawPool)
            if (poolObject) {
                for (const [modelKey, rawMeta] of Object.entries(poolObject)) {
                    const uid = modelUidByKey[String(modelKey || '').trim()]
                    if (!uid || poolUids.includes(uid)) {
                        continue
                    }
                    poolUids.push(uid)
                    poolMetaByUid[uid] = asObject(rawMeta) || {}
                }
            }
        }

        roles[role] = {
            bindingUid: modelUidByKey[selectedModelKey] || '',
            bindingKey,
            poolKey,
            poolUids,
            poolMetaByUid,
            selectionStrategy: normalizeSelectionStrategy(selectionPayload.strategy),
            selectionExtras: omitKeys(selectionPayload, ['strategy']),
        }
    }

    modelConfigForm.value = {
        mode: String(payload.mode || '').trim() || 'merge',
        topLevelExtras: omitKeys(payload, ['mode', 'model', 'models', 'providers', 'selection']),
        modelExtras: {},
        poolExtras: {},
        selectionExtras: {},
        providers,
        roles,
    }
    normalizeRoleSelections()
    modelsConfigError.value = ''
}

const hydrate = (payload: ModelsSnapshot) => {
    snapshot.value = payload
    hydrateModelsConfigForm(payload.models_config.payload || {})
    hydrateQuickRole('primary', payload.quick_roles.primary)
    hydrateQuickRole('routing', payload.quick_roles.routing)
    selectedProviderUid.value = modelConfigForm.value?.providers[0]?.uid || ''
    errorText.value = ''
    successText.value = ''
}

const load = async () => {
    loading.value = true
    errorText.value = ''
    successText.value = ''
    try {
        const response = await getModelsSnapshot()
        hydrate(response.data)
    } catch (error) {
        errorText.value = parseErrorMessage(error, '模型配置加载失败')
    } finally {
        loading.value = false
    }
}

const resetModelsConfigForm = () => {
    if (!snapshot.value) {
        return
    }
    hydrateModelsConfigForm(snapshot.value.models_config.payload || {})
    hydrateQuickRole('primary', snapshot.value.quick_roles.primary)
    hydrateQuickRole('routing', snapshot.value.quick_roles.routing)
    modelsConfigError.value = ''
    successText.value = ''
}

const addProvider = () => {
    if (!modelConfigForm.value) {
        return
    }
    const provider = createEmptyProvider()
    modelConfigForm.value.providers.push(provider)
    selectedProviderUid.value = provider.uid
    activeModelTab.value = 'providers'
}

const addProviderModel = (providerUid: string) => {
    const provider = modelConfigForm.value?.providers.find(item => item.uid === providerUid)
    if (!provider) {
        return
    }
    provider.models.push(createEmptyModel())
}

const detachModelFromRoles = (modelUid: string) => {
    const form = modelConfigForm.value
    if (!form) {
        return
    }
    for (const role of roleOrder) {
        const roleConfig = form.roles[role]
        if (roleConfig.bindingUid === modelUid) {
            roleConfig.bindingUid = ''
        }
        roleConfig.poolUids = roleConfig.poolUids.filter(uid => uid !== modelUid)
        delete roleConfig.poolMetaByUid[modelUid]
    }
}

const removeProviderModel = (providerUid: string, modelUid: string) => {
    const provider = modelConfigForm.value?.providers.find(item => item.uid === providerUid)
    if (!provider) {
        return
    }
    detachModelFromRoles(modelUid)
    provider.models = provider.models.filter(model => model.uid !== modelUid)
}

const removeProvider = (providerUid: string) => {
    if (!modelConfigForm.value) {
        return
    }
    const provider = modelConfigForm.value.providers.find(item => item.uid === providerUid)
    if (!provider) {
        return
    }
    for (const model of provider.models) {
        detachModelFromRoles(model.uid)
    }
    modelConfigForm.value.providers = modelConfigForm.value.providers.filter(provider => provider.uid !== providerUid)
    if (selectedProviderUid.value === providerUid) {
        selectedProviderUid.value = modelConfigForm.value.providers[0]?.uid || ''
    }
}

const isModelInRolePool = (role: RoleKey, modelUid: string) =>
    Boolean(modelConfigForm.value?.roles[role].poolUids.includes(modelUid))

const toggleRolePoolModel = (role: RoleKey, modelUid: string) => {
    const roleConfig = modelConfigForm.value?.roles[role]
    if (!roleConfig) {
        return
    }
    const option = availableModelMap.value[modelUid]
    if (roleCompatibilityStatus(role, option) !== 'eligible') {
        return
    }
    if (roleConfig.poolUids.includes(modelUid)) {
        roleConfig.poolUids = roleConfig.poolUids.filter(uid => uid !== modelUid)
        delete roleConfig.poolMetaByUid[modelUid]
        if (roleConfig.bindingUid === modelUid) {
            roleConfig.bindingUid = ''
        }
        return
    }
    roleConfig.poolUids = [...roleConfig.poolUids, modelUid]
    roleConfig.poolMetaByUid[modelUid] = roleConfig.poolMetaByUid[modelUid] || {}
}

const setRoleBinding = (role: RoleKey, modelUid: string) => {
    const roleConfig = modelConfigForm.value?.roles[role]
    if (!roleConfig) {
        return
    }
    const option = availableModelMap.value[modelUid]
    if (modelUid && roleCompatibilityStatus(role, option) !== 'eligible') {
        return
    }
    roleConfig.bindingUid = modelUid
    if (modelUid && !roleConfig.poolUids.includes(modelUid)) {
        roleConfig.poolUids = [...roleConfig.poolUids, modelUid]
        roleConfig.poolMetaByUid[modelUid] = roleConfig.poolMetaByUid[modelUid] || {}
    }
}

const applyQuickRolesToModelConfigForm = () => {
    const form = modelConfigForm.value
    if (!form) {
        return
    }

    for (const role of quickRoleOrder) {
        if (!quickRoleDirty.value[role]) {
            continue
        }
        const quick = quickRoles.value[role]
        const providerName = quick.providerName.trim()
        const modelId = quick.modelId.trim()
        if (!providerName || !modelId) {
            continue
        }

        let provider = form.providers.find(item => item.name.trim() === providerName)
        if (!provider) {
            provider = createEmptyProvider()
            provider.name = providerName
            form.providers.push(provider)
        }
        provider.baseUrl = quick.baseUrl.trim()
        provider.apiKey = quick.apiKey
        provider.api = quick.apiStyle.trim() || 'openai-completions'

        let model = provider.models.find(item => item.id.trim() === modelId)
        if (!model) {
            model = createEmptyModel()
            provider.models.push(model)
        }
        model.id = modelId
        model.name = quick.displayName.trim() || modelId
        model.reasoning = Boolean(quick.reasoning)
        model.input = quick.inputTypes.length ? [...quick.inputTypes] : (role === 'primary' ? ['text', 'image', 'voice'] : ['text'])
        if (!model.output.length) {
            model.output = ['text']
        }

        const modelKey = buildModelKey(providerName, modelId)
        const option = availableModelOptions.value.find(item => item.key === modelKey)
        if (option) {
            setRoleBinding(role, option.uid)
        }
    }
}

const buildModelsConfigSubmission = () => {
    const form = modelConfigForm.value
    if (!form) {
        return null
    }

    modelsConfigError.value = ''
    applyQuickRolesToModelConfigForm()
    const providersPayload: Record<string, unknown> = {}
    const modelKeyByUid: Record<string, string> = {}
    const seenProviderNames = new Set<string>()

    for (const provider of form.providers) {
        const providerName = provider.name.trim()
        if (!providerName) {
            modelsConfigError.value = 'Provider 名称不能为空'
            return null
        }
        if (seenProviderNames.has(providerName)) {
            modelsConfigError.value = `Provider 名称重复：${providerName}`
            return null
        }
        seenProviderNames.add(providerName)

        const seenModelIds = new Set<string>()
        const modelsPayload = []
        for (const model of provider.models) {
            const modelId = model.id.trim()
            if (!modelId) {
                modelsConfigError.value = `${providerName} 下存在空的模型 ID`
                return null
            }
            if (seenModelIds.has(modelId)) {
                modelsConfigError.value = `${providerName} 下模型 ID 重复：${modelId}`
                return null
            }
            seenModelIds.add(modelId)
            modelKeyByUid[model.uid] = buildModelKey(providerName, modelId)
            modelsPayload.push({
                ...model.extras,
                id: modelId,
                name: model.name.trim() || modelId,
                reasoning: Boolean(model.reasoning),
                input: [...model.input],
                output: [...model.output],
                cost: {
                    ...model.cost.extras,
                    input: coerceNumber(model.cost.input, 0, 0),
                    output: coerceNumber(model.cost.output, 0, 0),
                    cacheRead: coerceNumber(model.cost.cacheRead, 0, 0),
                    cacheWrite: coerceNumber(model.cost.cacheWrite, 0, 0),
                },
                limits: {
                    ...model.limits.extras,
                    dailyTokens: coerceInteger(model.limits.dailyTokens, 0, 0),
                    dailyImages: coerceInteger(model.limits.dailyImages, 0, 0),
                },
                contextWindow: coerceInteger(model.contextWindow, 1000000, 1),
                maxTokens: coerceInteger(model.maxTokens, 65536, 1),
            })
        }

        providersPayload[providerName] = {
            ...provider.extras,
            baseUrl: provider.baseUrl.trim(),
            apiKey: provider.apiKey,
            api: provider.api.trim() || 'openai-completions',
            models: modelsPayload,
        }
    }

    const modelPayload: Record<string, unknown> = {}
    const poolsPayload: Record<string, unknown> = {}
    const selectionPayload: Record<string, unknown> = {}

    for (const role of roleOrder) {
        const roleConfig = form.roles[role]
        const bindingKey = primaryRoleStorageKey(role)
        const poolKey = primaryRoleStorageKey(role)
        const selectedModelKey = roleConfig.bindingUid ? modelKeyByUid[roleConfig.bindingUid] : ''

        if (roleConfig.bindingUid && !selectedModelKey) {
            modelsConfigError.value = `${roleLabels[role]} 绑定了一个未完整配置的模型`
            return null
        }
        if (selectedModelKey) {
            modelPayload[bindingKey] = selectedModelKey
        }

        const poolPayload: Record<string, Record<string, unknown>> = {}
        for (const modelUid of roleConfig.poolUids) {
            const modelKey = modelKeyByUid[modelUid]
            if (!modelKey || poolPayload[modelKey]) {
                continue
            }
            poolPayload[modelKey] = { ...(roleConfig.poolMetaByUid[modelUid] || {}) }
        }
        if (Object.keys(poolPayload).length > 0) {
            poolsPayload[poolKey] = poolPayload
        }

        selectionPayload[role] = {
            strategy: normalizeSelectionStrategy(roleConfig.selectionStrategy),
        }
    }

    return {
        modelsConfig: {
            ...form.topLevelExtras,
            mode: form.mode.trim() || 'merge',
            model: modelPayload,
            models: poolsPayload,
            selection: selectionPayload,
            providers: providersPayload,
        },
    }
}

const save = async () => {
    if (!snapshot.value) {
        return
    }
    errorText.value = ''
    successText.value = ''
    const submission = buildModelsConfigSubmission()
    if (!submission) {
        return
    }
    saving.value = true
    try {
        const response = await patchModelsSnapshot({
            models_config: submission.modelsConfig,
        })
        hydrate(response.data.snapshot)
        successText.value = '模型配置已保存'
    } catch (error) {
        errorText.value = parseErrorMessage(error, '模型配置保存失败')
    } finally {
        saving.value = false
    }
}

watch(
    () =>
        roleOrder.map(role => ({
            role,
            compatibility: availableModelOptions.value.map(option => ({
                uid: option.uid,
                status: roleCompatibilityStatus(role, option),
            })),
        })),
    () => {
        normalizeRoleSelections()
    },
    { deep: true }
)

watch(
    modelConfigForm,
    () => {
        syncQuickRolesFromModelConfigForm()
    },
    { deep: true }
)

onMounted(load)
</script>

<template>
  <div class="models-page">
    <section class="models-hero">
      <div>
        <h1>模型配置 / Models</h1>
        <p>配置默认模型路由、Providers 与角色池，优化模型分发与调用策略，提升系统稳定性与响应效率。</p>
      </div>
      <div class="models-actions">
        <button type="button" class="secondary-btn" :disabled="loading || saving" @click="resetModelsConfigForm">
          还原当前加载值
        </button>
        <button type="button" class="secondary-btn" :disabled="routingLatencyChecking || loading || saving" @click="testRoutingLatency">
          <Loader2 v-if="routingLatencyChecking" class="h-4 w-4 animate-spin" />
          <Activity v-else class="h-4 w-4" />
          测试配置
        </button>
        <button type="button" class="primary-btn" :disabled="saving || loading || !snapshot || !modelConfigForm" @click="save">
          <Loader2 v-if="saving" class="h-4 w-4 animate-spin" />
          <Save v-else class="h-4 w-4" />
          保存更改
        </button>
      </div>

      <div v-if="errorText" class="notice danger">{{ errorText }}</div>
      <div v-if="successText" class="notice success">{{ successText }}</div>
      <div v-if="modelsConfigError" class="notice warning">{{ modelsConfigError }}</div>
      <div v-if="routingLatencyError" class="notice danger">{{ routingLatencyError }}</div>
      <div v-else-if="routingLatencyResult" class="notice success">
        Routing 测试通过：{{ routingLatencyResult.model_key }} · {{ routingLatencyResult.elapsed_ms }} ms
      </div>
    </section>

    <div v-if="loading" class="loading-card">
      <Loader2 class="h-4 w-4 animate-spin" />
      正在加载模型配置
    </div>

    <template v-else-if="snapshot && modelConfigForm">
      <section class="models-surface">
        <div class="model-stat-grid">
          <article v-for="item in modelOverviewStats" :key="item.label" class="model-stat-card" :class="`tone-${item.tone}`">
            <div class="model-stat-icon">
              <component :is="item.icon" class="h-7 w-7" />
            </div>
            <div>
              <div class="model-stat-label">{{ item.label }}</div>
              <div class="model-stat-value">{{ item.value }}</div>
              <p>{{ item.detail }}</p>
            </div>
          </article>
        </div>

        <div class="model-tabs">
          <button type="button" :class="{ active: activeModelTab === 'defaults' }" @click="activeModelTab = 'defaults'">默认模型</button>
          <button type="button" :class="{ active: activeModelTab === 'providers' }" @click="activeModelTab = 'providers'">Providers</button>
          <button type="button" :class="{ active: activeModelTab === 'roles' }" @click="activeModelTab = 'roles'">角色池</button>
          <button type="button" :class="{ active: activeModelTab === 'matrix' }" @click="activeModelTab = 'matrix'">能力矩阵</button>
        </div>

        <section v-if="activeModelTab === 'defaults'" class="defaults-layout">
          <div class="route-table-card">
            <div class="panel-head">
              <div>
                <h2>默认模型路由</h2>
                <p>为不同能力分类配置默认模型与降级策略</p>
              </div>
              <button
                type="button"
                class="secondary-btn small"
                :disabled="isTestingAction('routes:all')"
                @click="testAllDefaultRoutes"
              >
                <Loader2 v-if="isTestingAction('routes:all')" class="h-4 w-4 animate-spin" />
                <Play v-else class="h-4 w-4" />
                {{ isTestingAction('routes:all') ? '测试中' : '批量测试' }}
              </button>
            </div>

            <div class="route-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>能力分类</th>
                    <th>默认模型</th>
                    <th>降级策略</th>
                    <th>启用能力</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="card in roleCards" :key="`route-${card.role}`">
                    <td>
                      <div class="route-role">
                        <span><Bot class="h-4 w-4" /></span>
                        <div>
                          <strong>{{ card.label }}</strong>
                          <small>{{ card.capabilityText }}</small>
                        </div>
                      </div>
                    </td>
                    <td>
                      <select :value="modelConfigForm.roles[card.role].bindingUid" @change="setRoleBinding(card.role, ($event.target as HTMLSelectElement).value)">
                        <option value="">未绑定</option>
                        <option v-for="option in card.bindingOptions" :key="option.uid" :value="option.uid">{{ option.key }}</option>
                      </select>
                    </td>
                    <td>
                      <select v-model="modelConfigForm.roles[card.role].selectionStrategy">
                        <option v-for="strategy in selectionStrategyOptions" :key="`table-${card.role}-${strategy}`" :value="strategy">
                          {{ selectionStrategyLabels[strategy] }}
                        </option>
                      </select>
                    </td>
                    <td>
                      <div class="capability-list">
                        <span v-for="option in card.candidateOptions.slice(0, 3)" :key="`${card.role}-chip-${option.uid}`">
                          {{ option.input[0] || option.output[0] || 'text' }}
                        </span>
                        <span v-if="card.candidateOptions.length > 3">+{{ card.candidateOptions.length - 3 }}</span>
                      </div>
                    </td>
                    <td>
                      <div class="route-actions">
                        <button
                          type="button"
                          class="table-action"
                          :disabled="isTestingAction(actionKeyForRole(card.role))"
                          @click="testRoleModel(card.role)"
                        >
                          <Loader2 v-if="isTestingAction(actionKeyForRole(card.role))" class="h-4 w-4 animate-spin" />
                          <Play v-else class="h-4 w-4" />
                          {{ isTestingAction(actionKeyForRole(card.role)) ? '测试中' : '测试' }}
                        </button>
                        <div class="route-menu-wrap">
                          <button type="button" class="table-menu" @click="toggleRouteMenu(card.role)">
                            <MoreVertical class="h-4 w-4" />
                          </button>
                          <div v-if="openRouteMenuRole === card.role" class="action-menu">
                            <button type="button" @click="copyRoleBindingKey(card.role)">复制模型 Key</button>
                            <button type="button" @click="openRolePool">管理角色池</button>
                            <button type="button" class="danger" @click="clearRoleBinding(card.role)">清空默认模型</button>
                          </div>
                        </div>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <aside class="provider-quick-panel">
            <div class="provider-head">
              <h2>Providers 快速视图</h2>
              <button type="button" @click="load">刷新</button>
            </div>
            <label class="provider-search">
              <span>⌕</span>
              <input type="search" placeholder="搜索 Provider 或模型...">
            </label>
            <div class="provider-list">
              <button v-for="provider in providerQuickList" :key="provider.uid" type="button" class="provider-item" @click="selectProvider(provider.uid)">
                <div class="provider-mark"><Box class="h-4 w-4" /></div>
                <div>
                  <strong>{{ provider.name }}</strong>
                  <p>{{ provider.models.join(', ') || '暂无模型' }}</p>
                </div>
                <span :class="{ healthy: provider.healthy }">{{ provider.healthy ? '健康' : '待配置' }}</span>
              </button>
              <div v-if="!providerQuickList.length" class="provider-empty">还没有 Provider</div>
            </div>
            <button type="button" class="all-provider-link" @click="activeModelTab = 'providers'">
              查看全部 Providers ({{ providerQuickList.length }}) <span>→</span>
            </button>
          </aside>
        </section>

        <section v-else-if="activeModelTab === 'providers'" class="providers-layout">
          <aside class="provider-list-panel">
            <div class="provider-list-head">
              <h2>提供商列表 <span>{{ modelConfigForm.providers.length }}</span></h2>
              <button type="button" @click="addProvider"><Plus class="h-4 w-4" />新增提供商</button>
            </div>
            <label class="provider-search">
              <span>⌕</span>
              <input type="search" placeholder="搜索提供商...">
            </label>
            <div class="provider-card-list">
              <button
                v-for="provider in modelConfigForm.providers"
                :key="provider.uid"
                type="button"
                class="provider-card"
                :class="{ active: selectedProvider?.uid === provider.uid }"
                @click="selectProvider(provider.uid)"
              >
                <span class="provider-logo"><Box class="h-5 w-5" /></span>
                <strong>{{ provider.name || '未命名 provider' }}</strong>
                <em>{{ provider.baseUrl ? '启用' : '待配置' }}</em>
                <small>{{ provider.models.length }} 个模型</small>
              </button>
            </div>
          </aside>

          <section v-if="selectedProvider" class="provider-detail-panel">
            <div class="provider-detail-actions">
              <h2>提供商详情</h2>
              <div>
                <button
                  type="button"
                  class="secondary-btn small"
                  :disabled="isTestingAction(actionKeyForProvider(selectedProvider.uid))"
                  @click="testProviderConnection(selectedProvider)"
                >
                  <Loader2 v-if="isTestingAction(actionKeyForProvider(selectedProvider.uid))" class="h-4 w-4 animate-spin" />
                  <Activity v-else class="h-4 w-4" />
                  {{ isTestingAction(actionKeyForProvider(selectedProvider.uid)) ? '测试中' : '测试连接' }}
                </button>
                <button type="button" class="secondary-btn small" @click="copyProviderConfig(selectedProvider)">
                  <Copy class="h-4 w-4" />
                  复制配置
                </button>
                <button type="button" class="danger-btn small" @click="removeProvider(selectedProvider.uid)"><Trash2 class="h-4 w-4" />删除提供商</button>
              </div>
            </div>

            <div class="provider-section">
              <h3>基本信息</h3>
              <div class="provider-form-grid">
                <label><span>提供商名称</span><input v-model="selectedProvider.name" type="text" placeholder="proxy"></label>
                <label><span>显示名称</span><input v-model="selectedProvider.name" type="text" placeholder="代理服务（内网）"></label>
                <label><span>API 形式</span><input v-model="selectedProvider.api" type="text" placeholder="openai-completions"></label>
                <div class="provider-status-box" :class="providerConnectionClass(selectedProvider)">
                  <span>状态</span>
                  <strong><i />{{ providerConnectionText(selectedProvider) }}</strong>
                  <small>{{ providerConnectionDetail(selectedProvider) }}</small>
                </div>
              </div>
            </div>

            <div class="provider-section">
              <h3>接口配置</h3>
              <div class="provider-form-grid interface-grid">
                <label><span>Base URL</span><input v-model="selectedProvider.baseUrl" type="text" placeholder="https://api.example.com/v1"></label>
                <label><span>API Key</span><input v-model="selectedProvider.apiKey" type="password" placeholder="sk-..."></label>
                <label><span>超时（秒）</span><input type="number" value="30"></label>
                <label><span>最大重试</span><input type="number" value="3"></label>
              </div>
              <div class="connection-line" :class="providerConnectionClass(selectedProvider)">
                <i />{{ providerConnectionSummary(selectedProvider) }}
                <span>{{ providerConnectionMessage(selectedProvider) || providerConnectionDetail(selectedProvider) }}</span>
              </div>
            </div>

            <div class="provider-section">
              <div class="model-list-head">
                <h3>模型列表（{{ selectedProviderModels.length }}）</h3>
                <button type="button" class="secondary-btn small" @click="addProviderModel(selectedProvider.uid)"><Plus class="h-4 w-4" />新增模型</button>
              </div>
              <div class="model-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>模型 ID</th>
                      <th>显示名称</th>
                      <th>Context Window</th>
                      <th>Max Tokens</th>
                      <th>输入能力</th>
                      <th>输出能力</th>
                      <th>限额</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    <template v-for="model in selectedProviderModels" :key="model.uid">
                      <tr>
                        <td><input v-model="model.id" type="text" placeholder="gpt-4o"></td>
                        <td><input v-model="model.name" type="text" placeholder="GPT-4o"></td>
                        <td><input v-model.number="model.contextWindow" type="number" min="1"></td>
                        <td><input v-model.number="model.maxTokens" type="number" min="1"></td>
                        <td><div class="mini-tags"><span v-for="item in model.input" :key="item">{{ item }}</span></div></td>
                        <td><div class="mini-tags"><span v-for="item in model.output" :key="item">{{ item }}</span></div></td>
                        <td>
                          <div class="limit-inline">
                            <input v-model.number="model.limits.dailyTokens" type="number" min="0" title="Daily Tokens">
                            <input v-model.number="model.limits.dailyImages" type="number" min="0" title="Daily Images">
                          </div>
                        </td>
                        <td>
                          <button type="button" class="text-action" @click="toggleModelEditor(model.uid)">
                            {{ expandedModelUid === model.uid ? '收起' : '编辑' }}
                          </button>
                          <button type="button" class="text-action danger" @click="removeProviderModel(selectedProvider.uid, model.uid)">删除</button>
                        </td>
                      </tr>
                      <tr v-if="expandedModelUid === model.uid" class="model-edit-row">
                        <td colspan="8">
                          <div class="model-edit-panel">
                            <label class="switch-inline">
                              <input v-model="model.reasoning" type="checkbox">
                              <span>支持 Reasoning</span>
                            </label>
                            <div class="model-toggle-block">
                              <strong>输入能力</strong>
                              <div class="toggle-chip-row">
                                <button
                                  v-for="item in inputTypeOptions"
                                  :key="`${model.uid}-input-${item}`"
                                  type="button"
                                  :class="{ active: model.input.includes(item) }"
                                  @click="toggleModelInput(model, item)"
                                >
                                  {{ item }}
                                </button>
                              </div>
                            </div>
                            <div class="model-toggle-block">
                              <strong>输出能力</strong>
                              <div class="toggle-chip-row">
                                <button
                                  v-for="item in outputTypeOptions"
                                  :key="`${model.uid}-output-${item}`"
                                  type="button"
                                  :class="{ active: model.output.includes(item) }"
                                  @click="toggleModelOutput(model, item)"
                                >
                                  {{ item }}
                                </button>
                              </div>
                            </div>
                            <label>
                              <span>输入成本</span>
                              <input v-model.number="model.cost.input" type="number" min="0" step="0.000001">
                            </label>
                            <label>
                              <span>输出成本</span>
                              <input v-model.number="model.cost.output" type="number" min="0" step="0.000001">
                            </label>
                          </div>
                        </td>
                      </tr>
                    </template>
                  </tbody>
                </table>
                <div v-if="!selectedProviderModels.length" class="provider-empty">该 Provider 还没有模型。</div>
              </div>
            </div>
          </section>

          <section v-else class="provider-detail-panel empty-detail">
            还没有 Provider。点击左侧“新增提供商”开始配置。
          </section>
        </section>

        <section v-else-if="activeModelTab === 'roles'" class="role-pool-grid">
          <article v-for="card in roleCards" :key="card.role" class="role-card">
            <div class="route-role">
              <span><Bot class="h-4 w-4" /></span>
              <div>
                <strong>{{ card.label }}</strong>
                <small>当前池 {{ card.poolCount }} 个模型，{{ card.capabilityText }}</small>
              </div>
            </div>
            <label>
              <span>默认模型</span>
              <select :value="modelConfigForm.roles[card.role].bindingUid" @change="setRoleBinding(card.role, ($event.target as HTMLSelectElement).value)">
                <option value="">未绑定</option>
                <option v-for="option in card.bindingOptions" :key="option.uid" :value="option.uid">{{ option.key }}</option>
              </select>
            </label>
            <label>
              <span>池内选择策略</span>
              <select v-model="modelConfigForm.roles[card.role].selectionStrategy">
                <option v-for="strategy in selectionStrategyOptions" :key="`${card.role}-${strategy}`" :value="strategy">{{ selectionStrategyLabels[strategy] }}</option>
              </select>
            </label>
            <div class="pool-chip-list">
              <button
                v-for="option in card.candidateOptions"
                :key="`${card.role}-${option.uid}`"
                type="button"
                :class="{ selected: isModelInRolePool(card.role, option.uid) }"
                @click="toggleRolePoolModel(card.role, option.uid)"
              >
                {{ option.key }}
                <small>IN {{ option.input.join(' / ') || '-' }} · OUT {{ option.output.join(' / ') || '-' }}</small>
              </button>
            </div>
          </article>
        </section>

        <section v-else class="matrix-panel">
          <div class="panel-head">
            <div>
              <h2>能力矩阵</h2>
              <p>集中查看模型输入输出能力、reasoning 与角色兼容性。</p>
            </div>
            <label class="mode-field">
              <span>Mode</span>
              <input v-model="modelConfigForm.mode" type="text" placeholder="merge">
            </label>
          </div>
          <div class="matrix-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>模型</th>
                  <th>输入</th>
                  <th>输出</th>
                  <th>Reasoning</th>
                  <th>Primary</th>
                  <th>Routing</th>
                  <th>Vision</th>
                  <th>Image</th>
                  <th>Voice</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="option in availableModelOptions" :key="option.uid">
                  <td>{{ option.key }}</td>
                  <td><div class="mini-tags"><span v-for="item in option.input" :key="item">{{ item }}</span></div></td>
                  <td><div class="mini-tags"><span v-for="item in option.output" :key="item">{{ item }}</span></div></td>
                  <td>{{ option.reasoning ? '是' : '否' }}</td>
                  <td v-for="role in roleOrder" :key="`${option.uid}-${role}`">
                    <span class="compat" :class="roleCompatibilityStatus(role, option)">{{ roleCompatibilityStatus(role, option) }}</span>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-if="!availableModelOptions.length" class="provider-empty">暂无可用模型。</div>
          </div>
        </section>
      </section>
    </template>
  </div>
</template>

<style scoped>
.model-overview-card {
  display: grid;
  gap: 22px;
  padding: 24px;
  border: 1px solid var(--panel-border);
  border-radius: 14px;
  background: #fff;
  box-shadow: var(--shadow-card);
}

.model-stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 28px;
}

.model-stat-card {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  align-items: center;
  gap: 18px;
  min-height: 110px;
  padding: 20px 22px;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: #fff;
}

.model-stat-icon {
  display: grid;
  place-items: center;
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: var(--brand-blue-soft);
  color: var(--brand-blue);
}

.model-stat-card.tone-violet .model-stat-icon {
  background: #f1e8ff;
  color: #7c3aed;
}

.model-stat-card.tone-green .model-stat-icon {
  background: #ecfdf3;
  color: #16a34a;
}

.model-stat-label {
  color: var(--text-body);
  font-size: 14px;
  font-weight: 700;
}

.model-stat-value {
  margin-top: 5px;
  color: var(--text-strong);
  font-size: 25px;
  font-weight: 800;
}

.model-stat-card p {
  margin: 4px 0 0;
  color: var(--text-muted);
  font-size: 13px;
}

.model-workspace-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 24px;
  align-items: start;
}

.model-tabs {
  display: flex;
  align-items: center;
  gap: 34px;
  border-bottom: 1px solid var(--panel-border);
}

.model-tabs button {
  position: relative;
  height: 46px;
  border: 0;
  background: transparent;
  color: var(--text-body);
  font-size: 16px;
  font-weight: 800;
}

.model-tabs button.active {
  color: var(--brand-blue);
}

.model-tabs button.active::after {
  content: '';
  position: absolute;
  right: 0;
  bottom: -1px;
  left: 0;
  height: 3px;
  border-radius: 999px;
  background: var(--brand-blue);
}

.route-table-card,
.provider-quick-panel {
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: #fff;
}

.route-table-card {
  margin-top: 24px;
  overflow: hidden;
}

.route-table-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 20px 22px;
  border-bottom: 1px solid var(--panel-border);
}

.route-table-head h3,
.provider-head h3 {
  margin: 0;
  color: var(--text-strong);
  font-size: 17px;
  font-weight: 800;
}

.route-table-head p {
  margin: 6px 0 0;
  color: var(--text-muted);
  font-size: 14px;
}

.route-table-head button,
.table-action,
.table-menu,
.provider-head button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  background: #fff;
  color: var(--text-body);
  font-size: 13px;
  font-weight: 700;
}

.route-table-head button {
  height: 36px;
  padding: 0 14px;
}

.route-table-wrap {
  overflow-x: auto;
}

.route-table-wrap table {
  width: 100%;
  min-width: 880px;
  border-collapse: collapse;
  font-size: 14px;
}

.route-table-wrap th {
  padding: 14px 18px;
  border-bottom: 1px solid var(--panel-border);
  background: #f8fafc;
  color: var(--text-muted);
  text-align: left;
}

.route-table-wrap td {
  padding: 14px 18px;
  border-bottom: 1px solid #eef2f7;
  color: var(--text-body);
}

.route-table-wrap tbody tr:last-child td {
  border-bottom: 0;
}

.route-role {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  align-items: center;
  gap: 12px;
}

.route-role span {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: var(--brand-blue-soft);
  color: var(--brand-blue);
}

.route-role strong {
  display: block;
  color: var(--text-strong);
}

.route-role small {
  display: block;
  margin-top: 3px;
  color: var(--text-muted);
}

.route-table-wrap select {
  min-width: 180px;
  height: 36px;
  border-radius: 8px !important;
  padding: 0 12px;
}

.capability-list {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.capability-list span {
  border: 1px solid #e5ebf3;
  border-radius: 7px;
  background: #f8fafc;
  color: var(--text-body);
  padding: 4px 8px;
  font-size: 12px;
}

.table-action {
  height: 34px;
  padding: 0 11px;
}

.route-actions {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.route-menu-wrap {
  position: relative;
}

.table-menu {
  width: 34px;
  height: 34px;
}

.action-menu {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 20;
  display: grid;
  min-width: 150px;
  padding: 6px;
  border: 1px solid var(--panel-border);
  border-radius: 9px;
  background: #fff;
  box-shadow: 0 18px 40px rgb(15 23 42 / 12%);
}

.action-menu button {
  justify-content: flex-start;
  height: 34px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--text-body);
  padding: 0 10px;
  text-align: left;
  font-size: 13px;
  font-weight: 700;
}

.action-menu button:hover {
  background: #f8fafc;
}

.action-menu button.danger {
  color: #e11d48;
}

.provider-quick-panel {
  padding: 20px;
}

.provider-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.provider-head button {
  height: 30px;
  padding: 0 10px;
}

.provider-search {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 40px;
  margin-top: 16px;
  padding: 0 12px;
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  color: var(--text-subtle);
}

.provider-search input {
  width: 100%;
  border: 0 !important;
  outline: 0;
  box-shadow: none !important;
}

.provider-list {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}

.provider-item {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid #eef2f7;
}

.provider-mark {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: #f2f4f7;
  color: var(--text-muted);
}

.provider-item strong {
  color: var(--text-strong);
  font-size: 14px;
}

.provider-item p {
  margin: 4px 0 0;
  overflow: hidden;
  color: var(--text-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.provider-item > span {
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 800;
}

.provider-item > span.healthy {
  color: #16a34a;
}

.provider-empty {
  padding: 28px 0;
  color: var(--text-muted);
  text-align: center;
}

.all-provider-link {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
  border: 0;
  background: transparent;
  color: var(--brand-blue);
  font-weight: 800;
}

@media (max-width: 1400px) {
  .model-stat-grid,
  .model-workspace-grid {
    grid-template-columns: 1fr 1fr;
  }

  .provider-quick-panel {
    grid-column: span 2;
  }
}

@media (max-width: 900px) {
  .model-stat-grid,
  .model-workspace-grid {
    grid-template-columns: 1fr;
  }

  .provider-quick-panel {
    grid-column: auto;
  }
}

.models-page {
  display: grid;
  gap: 20px;
}

.models-hero,
.models-surface,
.loading-card {
  border: 1px solid var(--panel-border);
  border-radius: 14px;
  background: #fff;
  box-shadow: var(--shadow-card);
}

.models-hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 20px;
  padding: 28px 30px;
}

.models-hero h1 {
  margin: 0;
  color: var(--text-strong);
  font-size: 26px;
  font-weight: 800;
}

.models-hero p {
  margin: 10px 0 0;
  color: var(--text-body);
  font-size: 15px;
}

.models-actions {
  display: flex;
  gap: 14px;
}

.primary-btn,
.secondary-btn,
.danger-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 42px;
  padding: 0 18px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 800;
}

.primary-btn {
  border: 0;
  background: var(--brand-blue);
  color: #fff;
}

.secondary-btn {
  border: 1px solid var(--panel-border);
  background: #fff;
  color: var(--text-body);
}

.danger-btn {
  border: 1px solid #fecdd3;
  background: #fff;
  color: #e11d48;
}

.small {
  height: 34px;
  padding: 0 12px;
  font-size: 13px;
}

.notice,
.loading-card {
  grid-column: 1 / -1;
  border-radius: 8px;
  padding: 12px 14px;
  font-size: 14px;
}

.notice.success { background: #ecfdf3; color: #15803d; }
.notice.warning { background: #fffbeb; color: #b45309; }
.notice.danger { background: #fff1f2; color: #be123c; }

.loading-card {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
}

.models-surface {
  display: grid;
  gap: 24px;
  padding: 24px;
}

.defaults-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 24px;
  align-items: start;
}

.route-table-card,
.provider-quick-panel,
.provider-list-panel,
.provider-detail-panel,
.role-card,
.matrix-panel {
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: #fff;
}

.panel-head,
.provider-detail-actions,
.model-list-head,
.provider-list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

.panel-head,
.provider-detail-actions,
.provider-list-head {
  padding: 20px 22px;
  border-bottom: 1px solid var(--panel-border);
}

.panel-head h2,
.provider-head h2,
.provider-list-head h2,
.provider-detail-actions h2,
.model-list-head h3,
.provider-section h3,
.matrix-panel h2 {
  margin: 0;
  color: var(--text-strong);
  font-size: 17px;
  font-weight: 800;
}

.panel-head p,
.matrix-panel p {
  margin: 6px 0 0;
  color: var(--text-muted);
  font-size: 14px;
}

.route-table-card {
  overflow: hidden;
}

.route-table-wrap table,
.model-table-wrap table,
.matrix-table-wrap table {
  width: 100%;
  min-width: 900px;
  border-collapse: collapse;
  font-size: 14px;
}

.route-table-wrap th,
.model-table-wrap th,
.matrix-table-wrap th {
  padding: 14px 18px;
  border-bottom: 1px solid var(--panel-border);
  background: #f8fafc;
  color: var(--text-muted);
  text-align: left;
  white-space: nowrap;
}

.route-table-wrap td,
.model-table-wrap td,
.matrix-table-wrap td {
  padding: 14px 18px;
  border-bottom: 1px solid #eef2f7;
  color: var(--text-body);
  vertical-align: middle;
}

.route-table-wrap,
.model-table-wrap,
.matrix-table-wrap {
  overflow-x: auto;
}

.providers-layout {
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr);
  gap: 24px;
  align-items: start;
}

.provider-list-panel {
  padding-bottom: 18px;
  overflow: hidden;
}

.provider-list-head h2 span {
  display: inline-grid;
  place-items: center;
  min-width: 24px;
  height: 24px;
  margin-left: 8px;
  border-radius: 999px;
  background: #eef2f7;
  color: var(--text-muted);
  font-size: 12px;
}

.provider-list-head button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 36px;
  border: 1px solid #9ec5ff;
  border-radius: 8px;
  background: #fff;
  color: var(--brand-blue);
  padding: 0 12px;
  font-weight: 800;
}

.provider-card-list {
  display: grid;
  gap: 10px;
  margin-top: 14px;
  padding: 0 14px;
}

.provider-card {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  min-height: 76px;
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  background: #fff;
  padding: 12px;
  text-align: left;
}

.provider-card.active {
  border-color: #7fb2ff;
  background: #f0f7ff;
}

.provider-logo {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: var(--brand-blue-soft);
  color: var(--brand-blue);
}

.provider-card strong {
  color: var(--text-strong);
}

.provider-card em {
  color: #16a34a;
  font-style: normal;
  font-size: 12px;
  font-weight: 800;
}

.provider-card small {
  grid-column: 2 / 4;
  color: var(--text-muted);
}

.provider-detail-panel {
  overflow: hidden;
}

.provider-detail-actions > div {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.provider-section {
  padding: 20px 24px;
  border-bottom: 1px solid var(--panel-border);
}

.provider-section:last-child {
  border-bottom: 0;
}

.provider-form-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr)) 240px;
  gap: 18px 24px;
  margin-top: 16px;
}

.interface-grid {
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1.2fr) 140px 140px;
}

.provider-form-grid label,
.role-card label,
.mode-field {
  display: grid;
  gap: 8px;
}

.provider-form-grid label span,
.role-card label span,
.mode-field span {
  color: var(--text-body);
  font-size: 13px;
  font-weight: 800;
}

.provider-form-grid input,
.role-card select,
.matrix-panel input,
.route-table-wrap select {
  width: 100%;
  height: 40px;
  border: 1px solid var(--panel-border);
  border-radius: 8px !important;
  padding: 0 12px;
}

.provider-status-box {
  display: grid;
  gap: 7px;
  align-content: center;
}

.provider-status-box span {
  color: var(--text-body);
  font-size: 13px;
  font-weight: 800;
}

.provider-status-box strong,
.connection-line {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #b45309;
}

.provider-status-box i,
.connection-line i {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #f59e0b;
}

.provider-status-box small,
.connection-line span {
  color: var(--text-muted);
}

.provider-status-box.success strong,
.connection-line.success {
  color: #16a34a;
}

.provider-status-box.success i,
.connection-line.success i {
  background: #22c55e;
}

.provider-status-box.error strong,
.connection-line.error {
  color: #e11d48;
}

.provider-status-box.error i,
.connection-line.error i {
  background: #e11d48;
}

.connection-line {
  margin-top: 14px;
  font-size: 13px;
}

.model-list-head {
  margin-bottom: 16px;
}

.model-table-wrap input {
  width: 100%;
  min-width: 94px;
  height: 34px;
  border: 1px solid var(--panel-border);
  border-radius: 7px !important;
  padding: 0 9px;
}

.model-edit-row td {
  background: #fbfdff;
}

.model-edit-panel {
  display: grid;
  grid-template-columns: 170px minmax(190px, 1fr) minmax(190px, 1fr) 130px 130px;
  gap: 14px;
  align-items: end;
}

.model-edit-panel label,
.model-toggle-block {
  display: grid;
  gap: 8px;
}

.model-edit-panel label span,
.model-toggle-block strong {
  color: var(--text-body);
  font-size: 12px;
  font-weight: 800;
}

.switch-inline {
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  height: 34px;
  align-self: end;
}

.switch-inline input {
  width: 16px;
  min-width: 16px;
  height: 16px;
}

.toggle-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.toggle-chip-row button {
  height: 30px;
  border: 1px solid var(--panel-border);
  border-radius: 7px;
  background: #fff;
  color: var(--text-body);
  padding: 0 10px;
  font-size: 12px;
  font-weight: 800;
}

.toggle-chip-row button.active {
  border-color: #93c5fd;
  background: #eff6ff;
  color: var(--brand-blue);
}

.limit-inline {
  display: grid;
  grid-template-columns: 78px 78px;
  gap: 8px;
}

.mini-tags,
.capability-list {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.mini-tags span,
.capability-list span {
  border: 1px solid #dbeafe;
  border-radius: 7px;
  background: #eff6ff;
  color: var(--brand-blue);
  padding: 4px 8px;
  font-size: 12px;
}

.text-action {
  border: 0;
  background: transparent;
  color: var(--brand-blue);
  font-weight: 800;
}

.text-action.danger {
  color: #ef4444;
}

.role-pool-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.role-card {
  display: grid;
  gap: 16px;
  padding: 20px;
}

.pool-chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 9px;
}

.pool-chip-list button {
  display: grid;
  gap: 4px;
  border: 1px solid var(--panel-border);
  border-radius: 9px;
  background: #fff;
  color: var(--text-body);
  padding: 10px 12px;
  text-align: left;
}

.pool-chip-list button.selected {
  border-color: #7fb2ff;
  background: #eff6ff;
  color: var(--brand-blue);
}

.pool-chip-list small {
  color: var(--text-muted);
}

.matrix-panel {
  overflow: hidden;
}

.mode-field {
  min-width: 180px;
}

.compat {
  display: inline-flex;
  border-radius: 999px;
  background: #f2f4f7;
  color: var(--text-muted);
  padding: 4px 8px;
  font-size: 12px;
  font-weight: 800;
}

.compat.eligible {
  background: #dcfce7;
  color: #15803d;
}

.compat.legacy {
  background: #fff7ed;
  color: #c2410c;
}

.empty-detail {
  display: grid;
  place-items: center;
  min-height: 360px;
  color: var(--text-muted);
}

@media (max-width: 1400px) {
  .defaults-layout,
  .providers-layout {
    grid-template-columns: 1fr;
  }

  .provider-form-grid,
  .interface-grid,
  .role-pool-grid {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 900px) {
  .models-hero,
  .model-stat-grid,
  .provider-form-grid,
  .interface-grid,
  .role-pool-grid {
    grid-template-columns: 1fr;
  }

  .models-actions,
  .panel-head,
  .provider-detail-actions,
  .model-list-head {
    flex-wrap: wrap;
  }
}
</style>
