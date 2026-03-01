/**
 * 资产管理 API
 */
import request from './request'
import type {
    AssetInfo,
    AssetListResponse,
    AssetStatsResponse,
    AssetCreateRequest,
    AssetUpdateRequest,
    BatchDeleteRequest,
    BatchUpdateRequest,
} from '@/types/asset'

export interface AssetListParams {
    page?: number
    page_size?: number
    search?: string
    env?: string
    dept_name?: string
    business_unit?: string
    asset_type?: string
    sort_by?: string
    sort_order?: 'asc' | 'desc'
}

// 获取资产列表
export function getAssetList(params: AssetListParams) {
    return request.get<AssetListResponse>('/asset/list', { params })
}

// 获取资产统计
export function getAssetStats() {
    return request.get<AssetStatsResponse>('/asset/stats')
}

// 获取单个资产
export function getAsset(id: number) {
    return request.get<AssetInfo>(`/asset/${id}`)
}

// 创建资产
export function createAsset(data: AssetCreateRequest) {
    return request.post<{ id: number; message: string }>('/asset/create', data)
}

// 更新资产
export function updateAsset(id: number, data: AssetUpdateRequest) {
    return request.put<{ message: string }>(`/asset/${id}`, data)
}

// 删除资产
export function deleteAsset(id: number) {
    return request.delete<{ message: string }>(`/asset/${id}`)
}

// 批量删除
export function batchDeleteAssets(data: BatchDeleteRequest) {
    return request.post<{ message: string; deleted_count: number }>('/asset/batch-delete', data)
}

// 批量更新
export function batchUpdateAssets(data: BatchUpdateRequest) {
    return request.post<{ message: string; updated_count: number }>('/asset/batch-update', data)
}

// 导出 CSV
export async function exportAssets(): Promise<Blob> {
    const response = await request.get('/asset/export', { responseType: 'blob' })
    return response.data
}

// 导入资产
export function uploadAssets(data: FormData) {
    return request.post<{ message: string; ignored_errors: any[] }>('/asset/import', data, {
        headers: { 'Content-Type': 'multipart/form-data' }
    })
}

// 下载导入模板
export async function getImportTemplate(): Promise<Blob> {
    const response = await request.get('/asset/template', { responseType: 'blob' })
    return response.data
}

// 获取资产配置选项
export function getAssetOptions() {
    return request.get<{
        asset_type_options: { label: string; value: string }[]
        status_options: { label: string; value: string }[]
    }>('/asset/options')
}
