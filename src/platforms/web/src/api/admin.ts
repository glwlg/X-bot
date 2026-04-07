import request from './request'
import type { UserInfo } from './auth'

export const listUsers = () => request.get<UserInfo[]>('/auth/users')

export const createUser = (payload: Record<string, unknown>) =>
    request.post<UserInfo>('/auth/users', payload)

export const updateUser = (userId: number, payload: Record<string, unknown>) =>
    request.patch<UserInfo>(`/auth/users/${userId}`, payload)

export const deleteUser = (userId: number) =>
    request.delete(`/auth/users/${userId}`)

export const getDiagnostics = () =>
    request.get('/admin/diagnostics')

export const getAdminAudit = () =>
    request.get<{ items: Array<Record<string, unknown>> }>('/admin/audit')
