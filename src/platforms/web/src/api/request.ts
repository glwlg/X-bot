/**
 * Axios 请求封装
 * 集成 JWT 认证
 */
import axios, { type AxiosInstance, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'

// 创建 axios 实例
const request: AxiosInstance = axios.create({
    baseURL: '/api/v1',
    timeout: 300000,
    headers: {
        'Content-Type': 'application/json',
    },
})

/**
 * 获取夜莺认证 Token
 * 从父级 iframe 获取
 */
export function getAuthToken(): string | null {
    try {
        // 尝试从父窗口获取 token (iframe 嵌入场景)
        if (window.parent && window.parent !== window) {
            const parentWindow = window.parent as Window & {
                localStorage?: Storage
            }
            if (parentWindow.localStorage) {
                return parentWindow.localStorage.getItem('access_token')
            }
        }
        // 本地开发场景
        return localStorage.getItem('access_token')
    } catch {
        return localStorage.getItem('access_token')
    }
}

// 请求拦截器
request.interceptors.request.use(
    (config: InternalAxiosRequestConfig) => {
        const token = getAuthToken()
        if (token && config.headers) {
            config.headers['Authorization'] = `Bearer ${token}`
        }
        return config
    },
    (error) => {
        return Promise.reject(error)
    }
)

// 响应拦截器
request.interceptors.response.use(
    (response: AxiosResponse) => {
        return response
    },
    (error) => {
        if (error.response) {
            const status = error.response.status
            const message = error.response.data?.detail || '请求失败'

            switch (status) {
                case 401:
                    console.error('登录已过期，请重新登录')
                    // 清除 token 并跳转到登录页
                    localStorage.removeItem('access_token')
                    if (window.location.pathname !== '/login') {
                        window.location.href = '/login'
                    }
                    break
                case 403:
                    console.error('没有访问权限')
                    break
                case 404:
                    console.error('请求的资源不存在')
                    break
                case 500:
                    console.error('服务器错误')
                    break
                default:
                    console.error(message)
            }
        } else {
            console.error('网络连接失败')
        }
        return Promise.reject(error)
    }
)

export default request
