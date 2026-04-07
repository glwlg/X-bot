<script setup lang="ts">
import axios from 'axios'
import { computed, onMounted, ref, watch } from 'vue'
import { Bot, Loader2, Plus, Save, Trash2 } from 'lucide-vue-next'

import { getModelsSnapshot, patchModelsSnapshot, type ModelsQuickRoleSnapshot, type ModelsSnapshot } from '@/api/models'

const snapshot = ref<ModelsSnapshot | null>(null)
const modelConfigForm = ref<ModelConfigForm | null>(null)
const loading = ref(false)
const saving = ref(false)
const errorText = ref('')
const successText = ref('')
const modelsConfigError = ref('')

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
const quickRoles = ref<Record<QuickRoleKey, QuickRoleForm>>({
    primary: {
        providerName: '',
        baseUrl: '',
        apiKey: '',
        apiStyle: 'openai-completions',
        modelId: '',
        displayName: '',
        reasoning: false,
        inputTypes: ['text', 'image', 'voice'],
    },
    routing: {
        providerName: '',
        baseUrl: '',
        apiKey: '',
        apiStyle: 'openai-completions',
        modelId: '',
        displayName: '',
        reasoning: false,
        inputTypes: ['text'],
    },
})

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
    modelConfigForm.value.providers.push(createEmptyProvider())
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

    applyQuickRolesToModelConfigForm()
    modelsConfigError.value = ''
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

onMounted(load)
</script>

<template>
  <div class="space-y-6 p-6 md:p-8">
    <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Models</div>
          <h2 class="mt-1 text-2xl font-semibold text-slate-900">模型配置</h2>
          <p class="mt-2 max-w-3xl text-sm leading-7 text-slate-500">
            顶部优先完成 Primary / Routing 的首次配置，下方保留完整结构化编辑器维护 provider、模型目录、角色池和选择策略。
          </p>
        </div>
        <button
          class="inline-flex items-center gap-2 rounded-2xl bg-slate-950 px-4 py-3 text-sm font-medium text-white transition hover:bg-slate-800 disabled:opacity-60"
          :disabled="saving || loading || !snapshot || !modelConfigForm"
          @click="save"
        >
          <Loader2 v-if="saving" class="h-4 w-4 animate-spin" />
          <Save v-else class="h-4 w-4" />
          保存变更
        </button>
      </div>

      <div v-if="errorText" class="mt-5 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
        {{ errorText }}
      </div>
      <div v-if="successText" class="mt-4 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
        {{ successText }}
      </div>
      <div v-if="modelsConfigError" class="mt-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
        {{ modelsConfigError }}
      </div>
    </section>

    <div v-if="loading" class="flex items-center gap-2 rounded-[28px] border border-slate-200 bg-white px-5 py-4 text-sm text-slate-500 shadow-sm">
      <Loader2 class="h-4 w-4 animate-spin" />
      正在加载模型配置
    </div>

    <template v-else-if="snapshot && modelConfigForm">
      <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
        <div class="flex items-center justify-between gap-4">
          <div>
            <div class="text-sm font-semibold text-slate-900">快速开始</div>
            <div class="mt-1 text-sm text-slate-500">第一次安装时先补齐 Primary 和 Routing，保存后就可以回到运行配置页继续完成文档与渠道。</div>
          </div>
          <div class="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs leading-6 text-slate-600">
            <div>models.json：{{ snapshot.models_config.path }}</div>
            <div>文件状态：{{ snapshot.models_config.exists ? '已存在' : '将首次创建' }}</div>
          </div>
        </div>

        <div class="mt-5 flex flex-wrap gap-3">
          <div
            v-for="item in quickRoleSummary"
            :key="item.role"
            class="rounded-full px-3 py-1.5 text-xs font-medium"
            :class="item.ready ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'"
          >
            {{ item.ready ? '已就绪' : '待配置' }} · {{ item.label }} · {{ item.modelKey || '未设置' }}
          </div>
        </div>

        <div class="mt-6 grid gap-6 xl:grid-cols-2">
          <article
            v-for="role in quickRoleOrder"
            :key="role"
            class="rounded-[24px] border border-slate-200 bg-slate-50 p-5"
          >
            <div class="flex items-center justify-between gap-3">
              <div class="flex items-center gap-3">
                <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-white text-slate-700">
                  <Bot class="h-4 w-4" />
                </div>
                <div>
                  <div class="text-xs uppercase tracking-[0.2em] text-slate-400">{{ roleLabels[role] }}</div>
                  <div class="mt-1 text-sm text-slate-500">用于首装快速补齐核心模型连接。</div>
                </div>
              </div>
              <span class="rounded-full bg-white px-2.5 py-1 text-xs text-slate-500">{{ role }}</span>
            </div>

            <div class="mt-4 grid gap-4 md:grid-cols-2">
              <label class="space-y-2">
                <div class="text-sm font-medium text-slate-700">Provider 名称</div>
                <input v-model="quickRoles[role].providerName" type="text" placeholder="例如 openai" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-cyan-400">
              </label>
              <label class="space-y-2">
                <div class="text-sm font-medium text-slate-700">模型 ID</div>
                <input v-model="quickRoles[role].modelId" type="text" placeholder="例如 gpt-5.4" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-cyan-400">
              </label>
              <label class="space-y-2 md:col-span-2">
                <div class="text-sm font-medium text-slate-700">Base URL</div>
                <input v-model="quickRoles[role].baseUrl" type="text" placeholder="https://api.example.com/v1" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-cyan-400">
              </label>
              <label class="space-y-2 md:col-span-2">
                <div class="text-sm font-medium text-slate-700">API Key</div>
                <input v-model="quickRoles[role].apiKey" type="text" placeholder="sk-..." class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-cyan-400">
              </label>
              <label class="space-y-2">
                <div class="text-sm font-medium text-slate-700">展示名称</div>
                <input v-model="quickRoles[role].displayName" type="text" placeholder="可留空" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-cyan-400">
              </label>
              <label class="space-y-2">
                <div class="text-sm font-medium text-slate-700">API 形式</div>
                <input v-model="quickRoles[role].apiStyle" type="text" placeholder="openai-completions" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-cyan-400">
              </label>
            </div>

            <div class="mt-4 flex flex-wrap gap-3">
              <label class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700">
                <input v-model="quickRoles[role].reasoning" type="checkbox" class="h-4 w-4">
                开启 reasoning
              </label>
              <label
                v-for="inputType in inputTypeOptions"
                :key="`${role}-${inputType}`"
                class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
              >
                <input v-model="quickRoles[role].inputTypes" type="checkbox" :value="inputType" class="h-4 w-4">
                {{ inputType }}
              </label>
            </div>
          </article>
        </div>
      </section>

      <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
          <div class="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div class="text-sm font-semibold text-slate-900">高级编辑器</div>
              <div class="mt-1 text-sm text-slate-500">
                在同一块里维护角色绑定、角色池、provider 和具体模型参数。删除 provider 或模型时，相关角色引用会一起清理。
              </div>
              <div class="mt-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs leading-6 text-slate-600">
                <div>路径：{{ snapshot.models_config.path }}</div>
                <div>文件状态：{{ snapshot.models_config.exists ? '已存在' : '将首次创建' }}</div>
                <div>Provider：{{ modelConfigForm.providers.length }} 个</div>
                <div>模型：{{ availableModelOptions.length }} 个</div>
                <div>说明：文本/多模态模型按今日 tokens 限制，生图模型按今日产图张数限制</div>
              </div>
            </div>
            <div class="flex flex-wrap gap-3">
              <button
                type="button"
                class="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700 transition hover:bg-slate-100"
                @click="resetModelsConfigForm"
              >
                还原当前加载值
              </button>
              <button
                type="button"
                class="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700 transition hover:bg-slate-100"
                @click="addProvider"
              >
                <Plus class="h-4 w-4" />
                新增 Provider
              </button>
            </div>
          </div>

          <div class="mt-6 grid gap-4 md:grid-cols-[220px_minmax(0,1fr)]">
            <label class="space-y-2">
              <div class="text-sm font-medium text-slate-700">Mode</div>
              <input
                v-model="modelConfigForm.mode"
                type="text"
                placeholder="merge"
                class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white"
              >
            </label>
            <div class="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-7 text-slate-600">
              默认模型通过下拉指定，角色池通过标签按钮维护。选中某个默认模型时，它会自动加入对应角色池；从角色池移除时，也会同时清掉该角色的默认模型绑定。
            </div>
          </div>

          <div class="mt-6 grid gap-4 2xl:grid-cols-2">
            <article
              v-for="card in roleCards"
              :key="card.role"
              class="rounded-[24px] border border-slate-200 bg-slate-50 p-5"
            >
              <div class="flex items-center justify-between gap-3">
                <div class="flex items-center gap-3">
                  <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-white text-slate-700">
                    <Bot class="h-4 w-4" />
                  </div>
                  <div>
                    <div class="text-xs uppercase tracking-[0.2em] text-slate-400">{{ card.label }}</div>
                    <div class="mt-1 text-sm text-slate-500">当前池 {{ card.poolCount }} 个模型，{{ card.capabilityText }}</div>
                  </div>
                </div>
                <span class="rounded-full bg-white px-2.5 py-1 text-xs text-slate-500">{{ card.role }}</span>
              </div>

              <label class="mt-4 block space-y-2">
                <div class="text-sm font-medium text-slate-700">默认模型</div>
                <select
                  :value="modelConfigForm.roles[card.role].bindingUid"
                  class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none focus:border-cyan-400"
                  @change="setRoleBinding(card.role, ($event.target as HTMLSelectElement).value)"
                >
                  <option value="">未绑定</option>
                  <option v-for="option in card.bindingOptions" :key="option.uid" :value="option.uid">
                    {{ option.key }}
                  </option>
                </select>
              </label>

              <div class="mt-3 text-xs leading-6 text-slate-500">
                当前绑定：{{ card.currentKey || '未设置' }}
              </div>

              <label class="mt-4 block space-y-2">
                <div class="text-sm font-medium text-slate-700">池内选择策略</div>
                <select
                  v-model="modelConfigForm.roles[card.role].selectionStrategy"
                  class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none focus:border-cyan-400"
                >
                  <option v-for="strategy in selectionStrategyOptions" :key="`${card.role}-${strategy}`" :value="strategy">
                    {{ selectionStrategyLabels[strategy] }}
                  </option>
                </select>
              </label>

              <div class="mt-3 text-xs leading-6 text-slate-500">
                当前策略：{{ selectionStrategyLabels[card.selectionStrategy] }}
              </div>

              <div class="mt-4">
                <div class="text-sm font-medium text-slate-700">角色池</div>
                <div class="mt-2 flex flex-wrap gap-2" v-if="card.candidateOptions.length">
                  <button
                    v-for="option in card.candidateOptions"
                    :key="`${card.role}-${option.uid}`"
                    type="button"
                    class="rounded-full border px-3 py-2 text-left text-xs transition"
                    :class="isModelInRolePool(card.role, option.uid)
                        ? 'border-cyan-300 bg-cyan-50 text-cyan-800'
                        : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-100'"
                    @click="toggleRolePoolModel(card.role, option.uid)"
                  >
                    <div class="font-medium">{{ option.key }}</div>
                    <div class="mt-1 text-[11px] uppercase tracking-[0.12em] opacity-75">
                      IN {{ option.input.join(' / ') || '-' }} · OUT {{ option.output.join(' / ') || '-' }}{{ option.reasoning ? ' · reasoning' : '' }}
                    </div>
                  </button>
                </div>
                <div v-else class="mt-2 rounded-2xl border border-dashed border-slate-300 bg-white px-4 py-3 text-sm text-slate-500">
                  当前没有满足该角色能力要求的模型。先在下方补充 {{ card.capabilityText }} 的模型。
                </div>
              </div>
            </article>
          </div>

          <div class="mt-8 flex items-center justify-between gap-4">
            <div>
              <div class="text-sm font-semibold text-slate-900">Provider 与模型明细</div>
              <div class="mt-1 text-sm text-slate-500">完整维护 `baseUrl`、`apiKey`、`api`、输入类型、reasoning、cost`、`contextWindow`、`maxTokens`。</div>
            </div>
            <button
              type="button"
              class="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700 transition hover:bg-slate-100"
              @click="addProvider"
            >
              <Plus class="h-4 w-4" />
              新增 Provider
            </button>
          </div>

          <div v-if="!modelConfigForm.providers.length" class="mt-4 rounded-[24px] border border-dashed border-slate-300 bg-slate-50 px-5 py-6 text-sm text-slate-500">
            还没有 Provider。点击“新增 Provider”后，可以继续在每个 Provider 下添加模型。
          </div>

          <div v-else class="mt-4 space-y-5">
            <article
              v-for="provider in modelConfigForm.providers"
              :key="provider.uid"
              class="rounded-[24px] border border-slate-200 bg-slate-50 p-5"
            >
              <div class="flex items-center justify-between gap-3">
                <div>
                  <div class="text-xs uppercase tracking-[0.2em] text-slate-400">Provider</div>
                  <div class="mt-1 text-lg font-semibold text-slate-900">{{ provider.name || '未命名 Provider' }}</div>
                </div>
                <button
                  type="button"
                  class="inline-flex items-center gap-2 rounded-2xl border border-rose-200 bg-white px-3 py-2 text-sm text-rose-600 transition hover:bg-rose-50"
                  @click="removeProvider(provider.uid)"
                >
                  <Trash2 class="h-4 w-4" />
                  删除 Provider
                </button>
              </div>

              <div class="mt-5 grid gap-4 md:grid-cols-2">
                <label class="space-y-2">
                  <div class="text-sm font-medium text-slate-700">Provider 名称</div>
                  <input v-model="provider.name" type="text" placeholder="例如 proxy" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-cyan-400">
                </label>
                <label class="space-y-2">
                  <div class="text-sm font-medium text-slate-700">API 形式</div>
                  <input v-model="provider.api" type="text" placeholder="openai-completions" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-cyan-400">
                </label>
                <label class="space-y-2 md:col-span-2">
                  <div class="text-sm font-medium text-slate-700">Base URL</div>
                  <input v-model="provider.baseUrl" type="text" placeholder="https://api.example.com/v1" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-cyan-400">
                </label>
                <label class="space-y-2 md:col-span-2">
                  <div class="text-sm font-medium text-slate-700">API Key</div>
                  <input v-model="provider.apiKey" type="text" placeholder="sk-..." class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-cyan-400">
                </label>
              </div>

              <div class="mt-6 flex items-center justify-between gap-4">
                <div>
                  <div class="text-sm font-semibold text-slate-900">模型列表</div>
                  <div class="mt-1 text-sm text-slate-500">模型键会实时使用 `provider/model_id` 生成。</div>
                </div>
                <button
                  type="button"
                  class="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 transition hover:bg-slate-100"
                  @click="addProviderModel(provider.uid)"
                >
                  <Plus class="h-4 w-4" />
                  新增模型
                </button>
              </div>

              <div v-if="!provider.models.length" class="mt-4 rounded-2xl border border-dashed border-slate-300 bg-white px-4 py-4 text-sm text-slate-500">
                该 Provider 还没有模型。
              </div>

              <div v-else class="mt-4 space-y-4">
                <div
                  v-for="model in provider.models"
                  :key="model.uid"
                  class="rounded-2xl border border-slate-200 bg-white p-4"
                >
                  <div class="flex items-center justify-between gap-3">
                    <div>
                      <div class="text-xs uppercase tracking-[0.18em] text-slate-400">Model</div>
                      <div class="mt-1 text-sm font-medium text-slate-900">
                        {{ provider.name && model.id ? `${provider.name}/${model.id}` : '未完成的模型配置' }}
                      </div>
                    </div>
                    <button
                      type="button"
                      class="inline-flex items-center gap-2 rounded-2xl border border-rose-200 bg-white px-3 py-2 text-sm text-rose-600 transition hover:bg-rose-50"
                      @click="removeProviderModel(provider.uid, model.uid)"
                    >
                      <Trash2 class="h-4 w-4" />
                      删除
                    </button>
                  </div>

                  <div class="mt-4 grid gap-4 md:grid-cols-2">
                    <label class="space-y-2">
                      <div class="text-sm font-medium text-slate-700">模型 ID</div>
                      <input v-model="model.id" type="text" placeholder="例如 gpt-5.4" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
                    </label>
                    <label class="space-y-2">
                      <div class="text-sm font-medium text-slate-700">展示名称</div>
                      <input v-model="model.name" type="text" placeholder="可留空，默认回退到模型 ID" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
                    </label>
                    <label class="space-y-2">
                      <div class="text-sm font-medium text-slate-700">Context Window</div>
                      <input v-model.number="model.contextWindow" type="number" min="1" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
                    </label>
                    <label class="space-y-2">
                      <div class="text-sm font-medium text-slate-700">Max Tokens</div>
                      <input v-model.number="model.maxTokens" type="number" min="1" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
                    </label>
                  </div>

                  <div class="mt-4 flex flex-wrap gap-3">
                    <label class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
                      <input v-model="model.reasoning" type="checkbox" class="h-4 w-4">
                      开启 reasoning
                    </label>
                    <label
                      v-for="inputType in inputTypeOptions"
                      :key="`${model.uid}-${inputType}`"
                      class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700"
                    >
                      <input v-model="model.input" type="checkbox" :value="inputType" class="h-4 w-4">
                      {{ inputType }}
                    </label>
                  </div>

                  <div class="mt-4">
                    <div class="text-sm font-medium text-slate-700">输出能力</div>
                    <div class="mt-2 flex flex-wrap gap-3">
                      <label
                        v-for="outputType in outputTypeOptions"
                        :key="`${model.uid}-output-${outputType}`"
                        class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700"
                      >
                        <input v-model="model.output" type="checkbox" :value="outputType" class="h-4 w-4">
                        {{ outputType }}
                      </label>
                    </div>
                  </div>

                  <div class="mt-5">
                    <div class="text-sm font-medium text-slate-700">Daily Limits</div>
                    <div class="mt-3 grid gap-4 md:grid-cols-2">
                      <label class="space-y-2">
                        <div class="text-xs uppercase tracking-[0.16em] text-slate-400">Daily Tokens</div>
                        <input v-model.number="model.limits.dailyTokens" type="number" min="0" step="1" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
                        <div class="text-xs leading-6 text-slate-500">0 表示不限。用于文本、多模态理解、路由等按 token 计费的模型。</div>
                      </label>
                      <label class="space-y-2">
                        <div class="text-xs uppercase tracking-[0.16em] text-slate-400">Daily Images</div>
                        <input v-model.number="model.limits.dailyImages" type="number" min="0" step="1" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
                        <div class="text-xs leading-6 text-slate-500">0 表示不限。用于生图模型的每日产图张数上限。</div>
                      </label>
                    </div>
                  </div>

                  <div class="mt-5">
                    <div class="text-sm font-medium text-slate-700">Cost</div>
                    <div class="mt-3 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                      <label class="space-y-2">
                        <div class="text-xs uppercase tracking-[0.16em] text-slate-400">Input</div>
                        <input v-model.number="model.cost.input" type="number" min="0" step="0.0001" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
                      </label>
                      <label class="space-y-2">
                        <div class="text-xs uppercase tracking-[0.16em] text-slate-400">Output</div>
                        <input v-model.number="model.cost.output" type="number" min="0" step="0.0001" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
                      </label>
                      <label class="space-y-2">
                        <div class="text-xs uppercase tracking-[0.16em] text-slate-400">Cache Read</div>
                        <input v-model.number="model.cost.cacheRead" type="number" min="0" step="0.0001" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
                      </label>
                      <label class="space-y-2">
                        <div class="text-xs uppercase tracking-[0.16em] text-slate-400">Cache Write</div>
                        <input v-model.number="model.cost.cacheWrite" type="number" min="0" step="0.0001" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none transition focus:border-cyan-400 focus:bg-white">
                      </label>
                    </div>
                  </div>
                </div>
              </div>
            </article>
          </div>
      </section>
    </template>
  </div>
</template>
