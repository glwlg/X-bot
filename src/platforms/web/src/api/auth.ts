import request from './request'

export interface UserInfo {
    id: number
    email: string
    username: string | null
    display_name: string | null
    avatar_url: string | null
    role: 'admin' | 'operator' | 'viewer'
    is_active: boolean
    is_superuser: boolean
    is_verified: boolean
    created_at: string | null
    last_login_at: string | null
}

export interface UserPermissions {
    role: string
    is_admin: boolean
    is_operator: boolean
    is_superuser: boolean
}

// 获取当前用户信息
export const getCurrentUser = () => {
    return request.get<UserInfo>('/auth/me')
}

// 获取当前用户权限
export const getCurrentUserPermissions = () => {
    return request.get<UserPermissions>('/auth/me/permissions')
}

// JWT 登录
export const login = (email: string, password: string) => {
    const formData = new URLSearchParams()
    formData.append('username', email)
    formData.append('password', password)
    return request.post<{ access_token: string; token_type: string }>('/auth/jwt/login', formData, {
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
    })
}

// 注册
export const register = (email: string, password: string) => {
    return request.post('/auth/register', {
        email,
        password,
        is_active: true,
        is_superuser: true,
        is_verified: true,
        role: "admin"
    })
}

// 登出
export const logout = () => {
    return request.post('/auth/jwt/logout')
}

// 获取 GitLab OAuth 授权 URL 并重定向
export const redirectToGitLabAuth = async () => {
    const response = await request.get<{ authorization_url: string }>('/auth/gitlab/authorize')
    window.location.href = response.data.authorization_url
}

// 获取 GitLab OAuth 授权 URL (仅返回 URL)
export const getGitLabAuthUrl = async (): Promise<string> => {
    const response = await request.get<{ authorization_url: string }>('/auth/gitlab/authorize')
    return response.data.authorization_url
}
