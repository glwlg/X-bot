/**
 * 系统配置 API 服务
 */
import request from './request'
import type {
    SystemConfig,
    SystemConfigCreate,
    SystemConfigUpdate,
    SystemConfigListResponse,
} from '@/types/system_config'

/**
 * 获取所有配置
 */
export function getConfigs(group?: string) {
    return request.get<SystemConfigListResponse>('/system-config', {
        params: { group },
    })
}

/**
 * 获取单个配置
 */
export function getConfig(key: string) {
    return request.get<SystemConfig>(`/system-config/${key}`)
}

/**
 * 创建配置
 */
export function createConfig(data: SystemConfigCreate) {
    return request.post<SystemConfig>('/system-config', data)
}

/**
 * 更新配置
 */
export function updateConfig(key: string, data: SystemConfigUpdate) {
    return request.put<SystemConfig>(`/system-config/${key}`, data)
}

/**
 * 删除配置
 */
export function deleteConfig(key: string) {
    return request.delete(`/system-config/${key}`)
}
