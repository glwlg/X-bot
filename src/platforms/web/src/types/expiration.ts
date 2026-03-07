export interface ExpirationRecord {
    id: number
    name: string
    category: string
    expire_at: string | null
    ops_team?: string
    owner?: string
    operator?: string
    purchase_date?: string
    purchase_amount?: number
    provider?: string
    renewal_method?: string
    status?: string
    remarks?: string
    created_at?: string
}

export interface ExpirationListRequest {
    page?: number
    page_size?: number
    search?: string
    category?: string
    status?: string
    ops_team?: string
    sort_by?: string
    sort_order?: 'asc' | 'desc'
}

export interface ExpirationListResponse {
    total: number
    page: number
    page_size: number
    items: ExpirationRecord[]
}

export interface ExpirationStats {
    total: number
    expiring_30: number
    expiring_60: number
    expired: number
    by_category: Record<string, number>
}

export interface ExpirationRecordCreate {
    name: string
    category: string
    expire_at: string
    ops_team?: string
    owner?: string
    operator?: string
    purchase_date?: string
    purchase_amount?: number
    provider?: string
    renewal_method?: string
    status?: string
    remarks?: string
}

export interface ExpirationRecordUpdate extends Partial<ExpirationRecordCreate> { }
