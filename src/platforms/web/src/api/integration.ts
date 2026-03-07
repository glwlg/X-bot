import request from './request'

export interface Integration {
    id: number
    name: string
    url: string
    icon: string
    open_mode: 'embed' | 'new_tab'
    is_active: boolean
    sort_order: number
    created_at: string
}

export interface IntegrationCreate {
    name: string
    url: string
    icon?: string
    open_mode?: 'embed' | 'new_tab'
    is_active?: boolean
    sort_order?: number
}

export type IntegrationUpdate = Partial<IntegrationCreate>

// 获取集成列表
export const getIntegrations = (activeOnly = false) => {
    return request.get<Integration[]>('/integrations', {
        params: { active_only: activeOnly }
    })
}

// 创建集成
export const createIntegration = (data: IntegrationCreate) => {
    return request.post<Integration>('/integrations', data)
}

// 更新集成
export const updateIntegration = (id: number, data: IntegrationUpdate) => {
    return request.patch<Integration>(`/integrations/${id}`, data)
}

// 删除集成
export const deleteIntegration = (id: number) => {
    return request.delete(`/integrations/${id}`)
}
