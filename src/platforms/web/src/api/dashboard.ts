
import request from './request'

export interface TopologyNode {
    id: string
    label: string
    comboId?: string
    description?: string
    role?: string
    ip?: string
}

export interface TopologyEdge {
    source: string
    target: string
    data?: {
        latency: number
        loss: number
    }
}

export interface TopologyCombo {
    id: string
    label: string
}

export interface TopologyData {
    nodes: TopologyNode[]
    edges: TopologyEdge[]
    combos: TopologyCombo[]
}

export interface SlaStat {
    service_name: string
    url: string
    env: string
    availability: number
    avg_response_time: number
    total_downtime_counts: number
    target_sla: number
}

export const getTopologyData = (businessUnit?: string) => {
    const params = businessUnit ? { business_unit: businessUnit } : {}
    return request.get<TopologyData>('/dashboard/topology', { params })
}

export const getSlaStats = (days: number = 7) => {
    return request.get<SlaStat[]>('/dashboard/sla', { params: { days } })
}

export const getAlertStats = () => {
    return request.get<{
        activeCount: number
        criticalCount: number
        warningCount: number
    }>('/nightingale/stats')
}

export interface DatasourceDimensions {
    business_units: string[]
    clusters: string[]
    envs: string[]
    services: string[]
    datasource_types: string[]
    suggestions: string[]
}

export const getDatasourceDimensions = () => {
    return request.get<DatasourceDimensions>('/dashboard/datasources/dimensions')
}
