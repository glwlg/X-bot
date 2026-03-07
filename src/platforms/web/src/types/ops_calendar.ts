
export interface CalendarEvent {
    id: number
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
    
    // Recurrence fields
    is_recurring: boolean
    recurrence_cycle?: 'month' | 'quarter' | 'year'
    recurrence_interval?: number
    recurrence_deadline_day?: number
    last_completed_at?: string

    status?: string
    remarks?: string
    created_at?: string
}

export interface CalendarListRequest {
    page?: number
    page_size?: number
    search?: string
    category?: string
    status?: string
    ops_team?: string
    sort_by?: string
    sort_order?: 'asc' | 'desc'
}

export interface CalendarListResponse {
    total: number
    page: number
    page_size: number
    items: CalendarEvent[]
}

export interface CalendarStats {
    total: number
    expiring_30: number
    expiring_60: number
    expired: number
    by_category: Record<string, number>
}

export interface CalendarEventCreate {
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
    
    is_recurring?: boolean
    recurrence_cycle?: string
    recurrence_interval?: number
    recurrence_deadline_day?: number

    status?: string
    remarks?: string
}

export interface CalendarEventUpdate extends Partial<CalendarEventCreate> { }
