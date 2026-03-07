import request from './request'
import type { ReportData, ReportParams } from '@/types/report'

export function getReportData(params: ReportParams) {
    let url = ''
    if (params.type === 'flapping') {
        url = '/reports/flapping-alerts/data'
    } else if (params.type === 'unprocessed') {
        url = '/reports/long-unprocessed-alerts/data'
    }

    return request.get<ReportData>(url, { params })
}
