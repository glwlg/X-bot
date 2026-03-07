
import request from './request'

// 类型定义
export interface DutyLogCreate {
    content: string
    status: 'normal' | 'abnormal'
    handover_notes?: string
}

export interface DutyLog extends DutyLogCreate {
    id: number
    user_id: number
    user_name: string
    created_at: string
    updated_at: string
}

export interface DutyLogList {
    items: DutyLog[]
    total: number
}

// API
export function createDutyLog(data: DutyLogCreate) {
    return request.post<DutyLog>('/duty-log/', data)
}

export function getDutyLogs(params?: { skip?: number; limit?: number; user_id?: number; start_date?: string; end_date?: string }) {
    return request.get<DutyLogList>('/duty-log/', { params })
}

export function deleteDutyLog(id: number) {
    return request.delete(`/duty-log/${id}`)
}

export function exportDutyLogs(params?: { user_id?: number; start_date?: string; end_date?: string }) {
    return request.get('/duty-log/export', {
        params,
        responseType: 'blob',
    })
}
