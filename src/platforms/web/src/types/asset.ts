/**
 * 资产相关类型定义
 */

export interface AssetInfo {
    id: number
    // 核心标识
    instance_name: string
    ip_address: string
    uuid?: string
    fixed_asset_id?: string
    // 业务归属
    business_unit?: string
    product?: string
    service?: string
    dept_name?: string
    // 环境与优先级
    env?: string
    priority?: string
    // 责任人
    owner?: string
    primary_owner_id?: string
    // 凭证
    mgmt_password_id?: string
    // 位置与架构
    region?: string
    cluster?: string
    role?: string
    cabinet_loc?: string
    mgmt_ip?: string
    // 硬件
    provider?: string
    warranty_end?: string
    asset_type?: string
    model?: string
    status?: string
    // 操作系统
    os?: string
    os_kernel?: string
    xc_category?: string
    // 其他
    ver?: string
    component?: string
    // 系统字段
    sync_source?: string
    last_sync_at?: string
    created_at?: string
    updated_at?: string
    tags?: Tag[]
}

export interface Tag {
    id: number
    name: string
    color: string
}

export interface AssetListResponse {
    total: number
    page: number
    page_size: number
    items: AssetInfo[]
}

export interface AssetStatsResponse {
    total: number
    by_env: Record<string, number>
    by_dept: Record<string, number>
    by_business_unit?: Record<string, number>
}

export interface AssetCreateRequest {
    instance_name: string
    ip_address: string
    uuid?: string
    fixed_asset_id?: string
    business_unit?: string
    product?: string
    service?: string
    dept_name?: string
    env?: string
    priority?: string
    owner?: string
    primary_owner_id?: string
    mgmt_password_id?: string
    region?: string
    cluster?: string
    role?: string
    cabinet_loc?: string
    mgmt_ip?: string
    provider?: string
    warranty_end?: string
    asset_type?: string
    model?: string
    status?: string
    os?: string
    os_kernel?: string
    xc_category?: string
    ver?: string
    component?: string
}

export type AssetUpdateRequest = Partial<AssetCreateRequest>

export interface BatchDeleteRequest {
    ids: number[]
}

export interface BatchUpdateRequest {
    ids: number[]
    data: AssetUpdateRequest
}
