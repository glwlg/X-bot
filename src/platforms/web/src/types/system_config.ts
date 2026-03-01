/**
 * 系统配置类型定义
 */

export type ValueType = 'string' | 'int' | 'float' | 'bool' | 'json'
export type ConfigGroup = 'general' | 'etl' | 'notification' | 'asset'

export interface SystemConfig {
    id: number
    key: string
    value: string
    value_type: ValueType
    group: ConfigGroup
    description: string | null
    is_secret: boolean
    created_at: string
    updated_at: string
    typed_value: any
}

export interface SystemConfigCreate {
    key: string
    value: string
    value_type?: ValueType
    group?: ConfigGroup
    description?: string
    is_secret?: boolean
}

export interface SystemConfigUpdate {
    value?: string
    value_type?: ValueType
    group?: ConfigGroup
    description?: string
    is_secret?: boolean
}

export interface SystemConfigListResponse {
    items: SystemConfig[]
    total: number
    groups: string[]
}

export const VALUE_TYPE_OPTIONS = [
    { value: 'string', label: '文本' },
    { value: 'int', label: '整数' },
    { value: 'float', label: '小数' },
    { value: 'bool', label: '布尔' },
    { value: 'json', label: 'JSON' },
]

export const GROUP_OPTIONS = [
    { value: 'general', label: '通用' },
    { value: 'etl', label: 'ETL' },
    { value: 'notification', label: '通知' },
    { value: 'asset', label: '资产' },
]
