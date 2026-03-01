/**
 * 系统自检 API
 */
import request from './request'

export interface ComponentCheck {
  name: string
  status: 'healthy' | 'warning' | 'critical' | 'unknown'
  message: string
  metrics: Record<string, any>
  last_check: string
}

export interface HealthReport {
  report_id: string
  generated_at: string
  overall_status: 'healthy' | 'warning' | 'critical'
  summary: {
    total_components: number
    status_distribution: {
      healthy: number
      warning: number
      critical: number
      unknown: number
    }
  }
  components: ComponentCheck[]
  recommendations: string[]
}

export interface QuickStatus {
  overall_status: 'healthy' | 'warning' | 'critical'
  timestamp: string
  components: {
    name: string
    status: 'healthy' | 'warning' | 'critical' | 'unknown'
    message: string
  }[]
}

/**
 * 执行系统自检
 */
export function runHealthCheck() {
  return request.post<{
    success: boolean
    data: HealthReport
  }>('/health-check/run')
}

/**
 * 获取快速状态概览
 */
export function getQuickStatus() {
  return request.get<{
    success: boolean
    data: QuickStatus
  }>('/health-check/status')
}

/**
 * 获取最新巡检报告
 */
export function getLatestReport() {
  return request.get<{
    success: boolean
    data: HealthReport | null
    message?: string
  }>('/health-check/reports/latest')
}

/**
 * 获取巡检报告历史
 */
export function getReportHistory(limit: number = 10) {
  return request.get<{
    success: boolean
    data: {
      items: HealthReport[]
      total: number
      limit: number
    }
    message?: string
  }>('/health-check/reports/history', {
    params: { limit }
  })
}
