
import request from './request'
import type { UserInfo } from './auth'

export interface UserListResponse {
    items: UserInfo[]
    total: number
    page: number
    page_size: number
}

// 获取用户列表
export const getUsers = (params: { page: number; page_size: number; keyword?: string }) => {
    return request.get<UserListResponse>('/users', { params })
}

// 更新用户角色
export const updateUserRole = (userId: number, role: 'admin' | 'operator' | 'viewer') => {
    return request.patch<UserInfo>(`/users/${userId}/role`, { role })
}

// 更新用户状态（启用/禁用）
export const updateUserStatus = (userId: number, isActive: boolean) => {
    return request.patch<UserInfo>(`/users/${userId}/status`, { is_active: isActive })
}

// 更新用户显示名称
export const updateUserDisplayName = (userId: number, displayName: string) => {
    return request.patch<UserInfo>(`/users/${userId}/display_name`, { display_name: displayName })
}
