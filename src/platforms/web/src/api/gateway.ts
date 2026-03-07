/**
 * 网关 API 统计
 * 
 * 从 ClickHouse 查询实时 QPS、流量、Top APIs 等数据
 */

import request from './request'

export interface GatewayQpsData {
  timestamp: string
  qps: number
  total_requests: number
  error_count: number
  success_rate: number
  avg_response_time: number
  active_apis: number
  time_range: string
}

export interface TopApiItem {
  api_path: string
  method: string
  request_count: number
  error_count: number
  avg_response_time: number
  success_rate: number
}

/**
 * 获取网关实时 QPS 数据
 * @param params 查询参数
 * @param params.time_range 时间范围 (1m/5m/15m/1h)
 */
export function getGatewayQps(params?: { time_range?: string }) {
  return request.get<{
    success: boolean
    message?: string
    data: GatewayQpsData
  }>('/gateway/qps', { params })
}

/**
 * 获取 Top APIs 列表
 * @param params 查询参数
 * @param params.limit 返回数量限制
 * @param params.time_range 时间范围
 */
export function getGatewayTopApis(params?: { limit?: number; time_range?: string }) {
  return request.get<{
    success: boolean
    message?: string
    data: {
      total: number
      apis: TopApiItem[]
    }
  }>('/gateway/top-apis', { params })
}
