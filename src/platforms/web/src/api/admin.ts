import request from './request'
import type { UserInfo } from './auth'

export interface RuntimeSnapshot {
    runtime_config: {
        auth: {
            public_registration_enabled: boolean
        }
        cors: {
            allowed_origins: string[]
        }
        platforms: Record<string, boolean>
        features: Record<string, boolean>
    }
    model_roles: Record<string, string>
    model_catalog: {
        all: string[]
        pools: Record<string, string[]>
    }
    memory: {
        provider: string
        providers: string[]
        active_settings: Record<string, unknown>
    }
    platform_env: Record<string, { configured: boolean }>
    config_files: Record<string, unknown>
    version: {
        git_head: string
    }
}

export interface RuntimePatchPayload {
    platforms?: Record<string, boolean>
    features?: Record<string, boolean>
    cors_allowed_origins?: string[]
    model_roles?: Record<string, string>
    memory_provider?: string
}

export const listUsers = () => request.get<UserInfo[]>('/auth/users')

export const createUser = (payload: Record<string, unknown>) =>
    request.post<UserInfo>('/auth/users', payload)

export const updateUser = (userId: number, payload: Record<string, unknown>) =>
    request.patch<UserInfo>(`/auth/users/${userId}`, payload)

export const getRuntimeSnapshot = () =>
    request.get<RuntimeSnapshot>('/admin/runtime')

export const patchRuntimeSnapshot = (payload: RuntimePatchPayload) =>
    request.patch<RuntimeSnapshot>('/admin/runtime', payload)

export const getDiagnostics = () =>
    request.get('/admin/diagnostics')

export const getAdminAudit = () =>
    request.get<{ items: Array<Record<string, unknown>> }>('/admin/audit')
