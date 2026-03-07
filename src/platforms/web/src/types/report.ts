export interface ReportData {
    title: string
    headers: string[]
    data: Record<string, any>[]
    report_type: 'flapping' | 'unprocessed'
    current_date?: string
    current_days?: number
}

export interface ReportParams {
    type: 'flapping' | 'unprocessed'
    date?: string
    days?: number
}
