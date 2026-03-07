/**
 * 实时业务巡检 API
 */
import request from './request'

export interface ServiceHealth {
  name: string
  type: 'api' | 'database' | 'cache' | 'queue' | 'gateway'
  status: 'healthy' | 'warning' | 'critical' | 'unknown'
  health_score: number
  availability: number
  avg_response_time: number
  error_rate: number
  throughput: number
  last_check: string
  metrics: Record<string, any>
}

export interface Alert {
  id: string
  title: string
  severity: 'critical' | 'warning' | 'info'
  status: string
  description: string
  trigger_time: number
  targets: string[]
  labels: Record<string, any>
}

export interface RealtimeStatus {
  timestamp: string
  overall_status: 'healthy' | 'warning' | 'critical'
  health_score: number
  services: ServiceHealth[]
  active_alerts_count: number
  critical_alerts_count: number
  total_throughput: number
  avg_response_time: number
  total_error_rate: number
  recommendations: string[]
  alerts?: Alert[]
}

/**
 * 获取实时业务系统状态
 */
export function getRealtimeStatus() {
  return request.get<{
    success: boolean
    message?: string
    data: RealtimeStatus
  }>('/realtime-inspection/status')
}

/**
 * 获取服务状态列表
 */
export function getServicesStatus(params?: { service_type?: string }) {
  return request.get<{
    success: boolean
    data: {
      total: number
      healthy: number
      warning: number
      critical: number
      services: ServiceHealth[]
    }
  }>('/realtime-inspection/services', { params })
}

/**
 * 获取活跃告警列表
 */
export function getActiveAlerts(params?: { severity?: string; limit?: number }) {
  return request.get<{
    success: boolean
    data: {
      total: number
      critical: number
      warning: number
      info: number
      alerts: Alert[]
    }
  }>('/realtime-inspection/alerts', { params })
}

/**
 * 获取服务拓扑
 */
export function getServiceTopology() {
  return request.get<{
    success: boolean
    data: {
      nodes: { id: string; name: string; type: string; status: string }[]
      edges: { source: string; target: string; type: string }[]
    }
  }>('/realtime-inspection/topology')
}

/**
 * 手动刷新数据
 */
export function refreshRealtimeData() {
  return request.post<{
    success: boolean
    message: string
    data: {
      timestamp: string
      overall_status: string
      health_score: number
    }
  }>('/realtime-inspection/refresh')
}
