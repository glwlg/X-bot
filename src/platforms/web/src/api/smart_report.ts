import request from './request'

export interface SmartReportResponse {
    title: string
    date_range: string
    generated_at: string
    metrics: any
    ai_analysis: string
}

export function generateSmartReport(endDate?: string) {
    const params = endDate ? { end_date: endDate } : {}
    return request({
        url: '/reports/smart-weekly',
        method: 'post',
        params
    })
}

// 获取最新报告
export function getLatestReport() {
    return request({
        url: '/reports/smart-weekly/latest',
        method: 'get'
    })
}
