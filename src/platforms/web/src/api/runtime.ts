import request from './request'

export interface RuntimeModelStatus {
    configured: boolean
    ready: boolean
    role: string
    model_key: string
    provider_name: string
    model_id: string
    display_name: string
}

export interface RuntimeChannelSnapshot {
    enabled: boolean
    configured: boolean
}

export interface RuntimeSnapshot {
    admin_user: {
        id: number
        email: string
        username: string | null
        display_name: string | null
        role: string
        is_superuser: boolean
        current_admin_user_id: string
    }
    model_status: {
        primary: RuntimeModelStatus
        routing: RuntimeModelStatus
    }
    docs: {
        soul_path: string
        soul_content: string
        user_path: string
        user_content: string
    }
    channels: {
        admin_user_ids: string[]
        telegram: RuntimeChannelSnapshot & {
            bot_token: string
        }
        discord: RuntimeChannelSnapshot & {
            bot_token: string
        }
        dingtalk: RuntimeChannelSnapshot & {
            client_id: string
            client_secret: string
        }
        weixin: RuntimeChannelSnapshot & {
            base_url: string
            cdn_base_url: string
        }
        web: RuntimeChannelSnapshot
    }
    features: Record<string, boolean>
    cors_allowed_origins: string[]
    memory: {
        provider: string
        providers: string[]
        active_settings: Record<string, unknown>
    }
    status: {
        admin_bound: boolean
        primary_ready: boolean
        routing_ready: boolean
        soul_ready: boolean
        user_ready: boolean
        channels_ready: boolean
    }
    paths: {
        env: string
        models: string
        memory: string
    }
    restart_notice: string
}

export interface RuntimePatchPayload {
    admin_user?: {
        email?: string
        username?: string
        display_name?: string
        password?: string
    }
    docs?: {
        soul_content?: string
        user_content?: string
    }
    channels?: {
        admin_user_ids?: string[]
        telegram?: {
            enabled?: boolean
            bot_token?: string
        }
        discord?: {
            enabled?: boolean
            bot_token?: string
        }
        dingtalk?: {
            enabled?: boolean
            client_id?: string
            client_secret?: string
        }
        weixin?: {
            enabled?: boolean
            base_url?: string
            cdn_base_url?: string
        }
        web?: {
            enabled?: boolean
        }
    }
    features?: Record<string, boolean>
    cors_allowed_origins?: string[]
    memory_provider?: string
}

export interface RuntimePatchResponse {
    snapshot: RuntimeSnapshot
    changed_sections: string[]
    restart_required: boolean
}

export interface RuntimeGeneratePayload {
    kind: 'soul' | 'user'
    brief?: string
    current_content?: string
    model_key: string
}

export interface RuntimeGenerateResponse {
    kind: 'soul' | 'user'
    model_key: string
    content: string
}

export const getRuntimeSnapshot = () =>
    request.get<RuntimeSnapshot>('/admin/runtime')

export const patchRuntimeSnapshot = (payload: RuntimePatchPayload) =>
    request.patch<RuntimePatchResponse>('/admin/runtime', payload)

export const generateRuntimeDoc = (payload: RuntimeGeneratePayload) =>
    request.post<RuntimeGenerateResponse>('/admin/runtime/generate-doc', payload)
