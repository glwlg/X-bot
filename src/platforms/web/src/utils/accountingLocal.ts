import request from '@/api/request'

export interface NamedItem {
    id: string
    name: string
    created_at: string
}

export interface OperationLogEntry {
    id: string
    created_at: string
    action: string
    detail: string
    rollback: OperationLogRollback | null
    rolled_back: boolean
    rolled_back_at: string | null
}

export interface OperationLogRollbackRecordPayload {
    type: '支出' | '收入' | '转账'
    amount: number
    category_name?: string
    account_name?: string
    target_account_name?: string
    payee?: string
    remark?: string
    record_time?: string
}

export interface OperationLogRollbackAccountPayload {
    name: string
    type: string
    balance: number
    include_in_assets?: boolean
}

export type OperationLogRollback =
    | {
        kind: 'record'
        data: OperationLogRollbackRecordPayload
    }
    | {
        kind: 'account'
        data: OperationLogRollbackAccountPayload
    }

interface AppendOperationLogOptions {
    rollback?: OperationLogRollback
}

export interface GlobalSettingsState {
    currency_symbol: string
    decimal_places: number
    week_start: '周一' | '周日'
    quick_create_enabled: boolean
}

export interface ExtensionSettingsState {
    smart_category_enabled: boolean
    recurring_reminder_enabled: boolean
    debt_reminder_enabled: boolean
    quick_import_enabled: boolean
}

export type StatsRangePreset =
    | 'all_time'
    | 'this_year'
    | 'this_quarter'
    | 'this_month'
    | 'this_week'
    | 'last_12_months'
    | 'last_30_days'
    | 'last_6_weeks'
    | 'year_range'
    | 'quarter_range'
    | 'month_range'
    | 'week_range'
    | 'day_range'

export type StatsPanelMetric = 'sum' | 'avg' | 'max' | 'min' | 'count'

export type StatsPanelSubject =
    | 'dynamic'
    | 'year'
    | 'quarter'
    | 'month'
    | 'week'
    | 'day'
    | 'amount'
    | 'category'
    | 'account'
    | 'project'

export type StatsPanelFilter =
    | 'type'
    | 'date_range'
    | 'category'
    | 'account'
    | 'project'

export type StatsPanelKind = 'category' | 'trend' | 'team' | 'generic'

export interface StatsPanelConfig {
    id: string
    name: string
    description: string
    kind: StatsPanelKind
    enabled: boolean
    is_custom: boolean
    metric: StatsPanelMetric
    subject: StatsPanelSubject
    filters: StatsPanelFilter[]
    default_type: '支出' | '收入'
    default_range: StatsRangePreset
    default_category: string
    sort_order: number
}

const STORAGE_PREFIX = 'x-bot:accounting'

const nowIso = () => new Date().toISOString()

const randomId = () => {
    return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

const storageKey = (bookId: number | null, section: string) => {
    const scope = bookId ?? 'global'
    return `${STORAGE_PREFIX}:${scope}:${section}`
}

const readJson = <T>(key: string, fallback: T): T => {
    if (typeof window === 'undefined') return fallback
    const raw = localStorage.getItem(key)
    if (!raw) return fallback

    try {
        return JSON.parse(raw) as T
    } catch {
        return fallback
    }
}

const writeJson = (key: string, value: unknown) => {
    if (typeof window === 'undefined') return
    localStorage.setItem(key, JSON.stringify(value))
}

export const loadNamedItems = (bookId: number | null, section: string) => {
    const key = storageKey(bookId, section)
    return readJson<NamedItem[]>(key, [])
}

export const saveNamedItems = (bookId: number | null, section: string, items: NamedItem[]) => {
    const key = storageKey(bookId, section)
    writeJson(key, items)
}

export const addNamedItem = (bookId: number | null, section: string, name: string) => {
    const cleanName = name.trim()
    if (!cleanName) return loadNamedItems(bookId, section)

    const items = loadNamedItems(bookId, section)
    if (items.some(item => item.name === cleanName)) {
        return items
    }

    const next: NamedItem[] = [
        {
            id: randomId(),
            name: cleanName,
            created_at: nowIso(),
        },
        ...items,
    ]
    saveNamedItems(bookId, section, next)
    return next
}

export const removeNamedItem = (bookId: number | null, section: string, id: string) => {
    const items = loadNamedItems(bookId, section)
    const next = items.filter(item => item.id !== id)
    saveNamedItems(bookId, section, next)
    return next
}

export const appendOperationLog = (
    bookId: number | null,
    action: string,
    detail: string,
    options: AppendOperationLogOptions = {},
) => {
    const key = storageKey(bookId, 'operation-logs')
    const logs = normalizeAndSortOperationLogs(readJson<OperationLogEntry[]>(key, []))
    const entry: OperationLogEntry = {
        id: randomId(),
        created_at: nowIso(),
        action,
        detail,
        rollback: cloneRollback(options.rollback),
        rolled_back: false,
        rolled_back_at: null,
    }

    const next = normalizeAndSortOperationLogs([entry, ...logs]).slice(0, 300)
    writeJson(key, next)

    if (bookId) {
        void upsertRemoteOperationLog(bookId, entry).catch((error) => {
            console.error('operation log sync failed', error)
        })
    }
}

export const loadOperationLogs = async (bookId: number | null) => {
    const key = storageKey(bookId, 'operation-logs')
    const localLogs = normalizeAndSortOperationLogs(readJson<OperationLogEntry[]>(key, []))

    if (!bookId) {
        return localLogs
    }

    try {
        const remoteLogs = await fetchRemoteOperationLogs(bookId)
        const remoteIds = new Set(remoteLogs.map(log => log.id))
        const pending = localLogs.filter(log => !remoteIds.has(log.id))

        if (pending.length > 0) {
            await Promise.all(pending.map(log => upsertRemoteOperationLog(bookId, log)))
            const merged = normalizeAndSortOperationLogs([...remoteLogs, ...pending]).slice(0, 300)
            writeJson(key, merged)
            return merged
        }

        writeJson(key, remoteLogs)
        return remoteLogs
    } catch (error) {
        console.error('load remote operation logs failed', error)
        return localLogs
    }
}

export const clearOperationLogs = async (bookId: number | null) => {
    const key = storageKey(bookId, 'operation-logs')
    writeJson(key, [])

    if (!bookId) return
    try {
        await request.delete('/accounting/operation-logs', {
            params: { book_id: bookId },
        })
    } catch (error) {
        console.error('clear remote operation logs failed', error)
    }
}

export const markOperationLogRolledBack = async (bookId: number | null, logId: string) => {
    const key = storageKey(bookId, 'operation-logs')
    const logs = await loadOperationLogs(bookId)
    const rolledBackAt = nowIso()
    const next = logs.map(log => {
        if (log.id !== logId) return log
        return {
            ...log,
            rolled_back: true,
            rolled_back_at: rolledBackAt,
        }
    })

    writeJson(key, next)

    if (!bookId) {
        return next
    }

    const target = next.find(log => log.id === logId)
    if (!target) {
        return next
    }

    try {
        await upsertRemoteOperationLog(bookId, target)
        await request.post(
            `/accounting/operation-logs/${encodeURIComponent(logId)}/rollback`,
            { rolled_back_at: rolledBackAt },
            { params: { book_id: bookId } },
        )
        const remoteLogs = await fetchRemoteOperationLogs(bookId)
        writeJson(key, remoteLogs)
        return remoteLogs
    } catch (error) {
        console.error('mark remote operation log rolled back failed', error)
        return next
    }
}

const cloneRollback = (rollback?: OperationLogRollback): OperationLogRollback | null => {
    if (!rollback) return null
    return JSON.parse(JSON.stringify(rollback)) as OperationLogRollback
}

const normalizeOperationLog = (entry: OperationLogEntry): OperationLogEntry => {
    return {
        id: entry.id,
        created_at: entry.created_at,
        action: entry.action,
        detail: entry.detail,
        rollback: entry.rollback ? cloneRollback(entry.rollback) : null,
        rolled_back: Boolean(entry.rolled_back),
        rolled_back_at: entry.rolled_back_at ?? null,
    }
}

const normalizeAndSortOperationLogs = (logs: OperationLogEntry[]) => {
    return logs
        .map(normalizeOperationLog)
        .sort((a, b) => {
            const bt = new Date(b.created_at).getTime() || 0
            const at = new Date(a.created_at).getTime() || 0
            if (bt !== at) return bt - at
            return b.id.localeCompare(a.id)
        })
}

const serializeOperationLogPayload = (log: OperationLogEntry) => {
    return {
        id: log.id,
        created_at: log.created_at,
        action: log.action,
        detail: log.detail,
        rollback: log.rollback ? cloneRollback(log.rollback) : null,
        rolled_back: Boolean(log.rolled_back),
        rolled_back_at: log.rolled_back_at,
    }
}

const fetchRemoteOperationLogs = async (bookId: number) => {
    const res = await request.get<OperationLogEntry[]>('/accounting/operation-logs', {
        params: { book_id: bookId },
    })
    return normalizeAndSortOperationLogs(res.data || []).slice(0, 300)
}

const upsertRemoteOperationLog = async (bookId: number, log: OperationLogEntry) => {
    await request.post('/accounting/operation-logs', serializeOperationLogPayload(log), {
        params: { book_id: bookId },
    })
}

export const loadGlobalSettings = (): GlobalSettingsState => {
    const key = storageKey(null, 'global-settings')
    return readJson<GlobalSettingsState>(key, {
        currency_symbol: '¥',
        decimal_places: 2,
        week_start: '周一',
        quick_create_enabled: true,
    })
}

export const saveGlobalSettings = (state: GlobalSettingsState) => {
    const key = storageKey(null, 'global-settings')
    writeJson(key, state)
}

export const loadExtensionSettings = (): ExtensionSettingsState => {
    const key = storageKey(null, 'extension-settings')
    return readJson<ExtensionSettingsState>(key, {
        smart_category_enabled: true,
        recurring_reminder_enabled: true,
        debt_reminder_enabled: true,
        quick_import_enabled: true,
    })
}

export const saveExtensionSettings = (state: ExtensionSettingsState) => {
    const key = storageKey(null, 'extension-settings')
    writeJson(key, state)
}

const DEFAULT_STATS_PANELS: StatsPanelConfig[] = [
    {
        id: 'preset-category',
        name: '分类统计',
        description: '查看各个分类收支占比',
        kind: 'category',
        enabled: true,
        is_custom: false,
        metric: 'sum',
        subject: 'category',
        filters: ['type', 'date_range'],
        default_type: '支出',
        default_range: 'last_12_months',
        default_category: '全部分类',
        sort_order: 10,
    },
    {
        id: 'preset-food-spend',
        name: '吃了多少钱',
        description: '查看某个分类每月收支多少，如看每月话费支出多少',
        kind: 'generic',
        enabled: false,
        is_custom: false,
        metric: 'sum',
        subject: 'month',
        filters: ['type', 'date_range', 'category'],
        default_type: '支出',
        default_range: 'last_12_months',
        default_category: '餐饮',
        sort_order: 20,
    },
    {
        id: 'preset-daily-trend',
        name: '日趋势',
        description: '查看每天收支变化趋势',
        kind: 'trend',
        enabled: false,
        is_custom: false,
        metric: 'sum',
        subject: 'day',
        filters: ['type', 'date_range'],
        default_type: '支出',
        default_range: 'last_30_days',
        default_category: '全部分类',
        sort_order: 30,
    },
    {
        id: 'preset-weekend',
        name: '周末统计',
        description: '查看每个周末收支多少',
        kind: 'generic',
        enabled: false,
        is_custom: false,
        metric: 'sum',
        subject: 'week',
        filters: ['type', 'date_range'],
        default_type: '支出',
        default_range: 'last_6_weeks',
        default_category: '全部分类',
        sort_order: 40,
    },
    {
        id: 'preset-amount-distribution',
        name: '金额分布',
        description: '查看交易金额范围分布',
        kind: 'generic',
        enabled: false,
        is_custom: false,
        metric: 'count',
        subject: 'amount',
        filters: ['type', 'date_range'],
        default_type: '支出',
        default_range: 'last_30_days',
        default_category: '全部分类',
        sort_order: 50,
    },
    {
        id: 'preset-month-summary',
        name: '月度收支',
        description: '查看每月收支多少',
        kind: 'trend',
        enabled: false,
        is_custom: false,
        metric: 'sum',
        subject: 'month',
        filters: ['type', 'date_range'],
        default_type: '支出',
        default_range: 'this_year',
        default_category: '全部分类',
        sort_order: 60,
    },
    {
        id: 'preset-yearly',
        name: '年度统计',
        description: '查看每年收支多少',
        kind: 'trend',
        enabled: true,
        is_custom: false,
        metric: 'sum',
        subject: 'year',
        filters: ['type', 'date_range', 'category'],
        default_type: '支出',
        default_range: 'all_time',
        default_category: '全部分类',
        sort_order: 70,
    },
    {
        id: 'preset-weekly',
        name: '一周统计',
        description: '查看一周中的每天收支多少',
        kind: 'generic',
        enabled: false,
        is_custom: false,
        metric: 'sum',
        subject: 'day',
        filters: ['type', 'date_range', 'category'],
        default_type: '支出',
        default_range: 'this_week',
        default_category: '全部分类',
        sort_order: 80,
    },
    {
        id: 'preset-account',
        name: '账户统计',
        description: '查看各个账户收支占比',
        kind: 'generic',
        enabled: false,
        is_custom: false,
        metric: 'sum',
        subject: 'account',
        filters: ['type', 'date_range', 'account'],
        default_type: '支出',
        default_range: 'last_12_months',
        default_category: '全部分类',
        sort_order: 90,
    },
    {
        id: 'preset-max-single',
        name: '单笔最高',
        description: '查看最大单笔交易',
        kind: 'generic',
        enabled: false,
        is_custom: false,
        metric: 'max',
        subject: 'day',
        filters: ['type', 'date_range', 'category'],
        default_type: '支出',
        default_range: 'last_30_days',
        default_category: '全部分类',
        sort_order: 100,
    },
]

const cloneStatsPanel = (panel: StatsPanelConfig): StatsPanelConfig => ({
    ...panel,
    filters: [...panel.filters],
})

const normalizeStatsPanel = (
    panel: Partial<StatsPanelConfig>,
    index: number,
): StatsPanelConfig => {
    return {
        id: panel.id || randomId(),
        name: panel.name?.trim() || '自定义统计',
        description: panel.description?.trim() || '',
        kind: panel.kind || 'generic',
        enabled: panel.enabled ?? true,
        is_custom: panel.is_custom ?? true,
        metric: panel.metric || 'sum',
        subject: panel.subject || 'dynamic',
        filters: panel.filters ? [...panel.filters] : ['type', 'date_range'],
        default_type: panel.default_type || '支出',
        default_range: panel.default_range || 'last_12_months',
        default_category: panel.default_category || '全部分类',
        sort_order: panel.sort_order ?? (200 + index),
    }
}

const mergeStatsPanels = (stored: StatsPanelConfig[]) => {
    const byId = new Map<string, StatsPanelConfig>()

    for (const panel of stored) {
        byId.set(panel.id, normalizeStatsPanel(panel, byId.size))
    }

    for (const preset of DEFAULT_STATS_PANELS) {
        if (!byId.has(preset.id)) {
            byId.set(preset.id, cloneStatsPanel(preset))
        }
    }

    return [...byId.values()].sort((a, b) => a.sort_order - b.sort_order)
}

export const listStatsPanelTemplates = () => {
    return DEFAULT_STATS_PANELS.map(cloneStatsPanel)
}

const normalizeAndSortPanels = (panels: StatsPanelConfig[]) => {
    return panels
        .map((panel, index) => normalizeStatsPanel(panel, index))
        .sort((a, b) => a.sort_order - b.sort_order)
}

const panelsFingerprint = (panels: StatsPanelConfig[]) => {
    return JSON.stringify(
        normalizeAndSortPanels(panels).map(panel => ({
            ...panel,
            filters: [...panel.filters].sort(),
        }))
    )
}

const fetchRemoteStatsPanels = async (bookId: number) => {
    const res = await request.get<StatsPanelConfig[]>('/accounting/stats-panels', {
        params: { book_id: bookId },
    })
    return normalizeAndSortPanels(res.data || [])
}

const pushRemoteStatsPanels = async (bookId: number, panels: StatsPanelConfig[]) => {
    const payload = normalizeAndSortPanels(panels)
    const res = await request.put<StatsPanelConfig[]>('/accounting/stats-panels', {
        panels: payload,
    }, {
        params: { book_id: bookId },
    })
    return normalizeAndSortPanels(res.data || payload)
}

export const loadStatsPanels = async (bookId: number | null) => {
    if (!bookId) {
        return DEFAULT_STATS_PANELS.map(cloneStatsPanel)
    }

    const remote = await fetchRemoteStatsPanels(bookId)
    const merged = mergeStatsPanels(remote)

    if (panelsFingerprint(remote) !== panelsFingerprint(merged)) {
        return pushRemoteStatsPanels(bookId, merged)
    }

    return merged
}

export const saveStatsPanels = async (bookId: number | null, panels: StatsPanelConfig[]) => {
    if (!bookId) {
        return normalizeAndSortPanels(panels)
    }

    return pushRemoteStatsPanels(bookId, panels)
}

export const getStatsPanel = async (bookId: number | null, id: string) => {
    const panels = await loadStatsPanels(bookId)
    return panels.find(panel => panel.id === id)
}

export const setStatsPanelEnabled = async (
    bookId: number | null,
    id: string,
    enabled: boolean,
) => {
    const next = (await loadStatsPanels(bookId)).map(panel => {
        if (panel.id !== id) return panel
        return { ...panel, enabled }
    })
    return saveStatsPanels(bookId, next)
}

export const upsertStatsPanel = async (
    bookId: number | null,
    panel: StatsPanelConfig,
) => {
    const current = await loadStatsPanels(bookId)
    const idx = current.findIndex(item => item.id === panel.id)
    let next: StatsPanelConfig[]

    if (idx === -1) {
        next = [...current, normalizeStatsPanel({ ...panel, is_custom: true }, current.length)]
    } else {
        next = current.map((item, index) => {
            if (index !== idx) return item
            return normalizeStatsPanel(panel, index)
        })
    }

    return saveStatsPanels(bookId, next)
}

export const removeStatsPanel = async (bookId: number | null, id: string) => {
    const current = await loadStatsPanels(bookId)
    const target = current.find(panel => panel.id === id)
    if (!target || !target.is_custom) return current

    const next = current.filter(panel => panel.id !== id)
    return saveStatsPanels(bookId, next)
}

export const createStatsPanelDraft = (): StatsPanelConfig => {
    return {
        id: randomId(),
        name: '自定义统计',
        description: '描述一下统计的功能',
        kind: 'generic',
        enabled: true,
        is_custom: true,
        metric: 'sum',
        subject: 'dynamic',
        filters: ['type', 'date_range'],
        default_type: '支出',
        default_range: 'last_12_months',
        default_category: '全部分类',
        sort_order: Date.now(),
    }
}
