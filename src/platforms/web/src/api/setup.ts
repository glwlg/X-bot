import request from './request'

export interface SetupModelRole {
    configured: boolean
    role: string
    model_key: string
    provider_name: string
    base_url: string
    api_key: string
    api_style: string
    model_id: string
    display_name: string
    reasoning: boolean
    input_types: string[]
}

export interface SetupSnapshot {
    admin_user: {
        id: number
        email: string
        username: string | null
        display_name: string | null
        role: string
        is_superuser: boolean
        current_admin_user_id: string
    }
    models: {
        primary: SetupModelRole
        routing: SetupModelRole
    }
    docs: {
        soul_path: string
        soul_content: string
        user_path: string
        user_content: string
    }
    channels: {
        platforms: Record<string, boolean>
        admin_user_ids: string[]
        telegram_bot_token: string
        discord_bot_token: string
        dingtalk_client_id: string
        dingtalk_client_secret: string
        weixin_enable: boolean
        weixin_base_url: string
        weixin_cdn_base_url: string
        web_channel_enable: boolean
    }
    status: {
        admin_bound: boolean
        primary_ready: boolean
        routing_ready: boolean
        soul_ready: boolean
        user_ready: boolean
    }
    paths: {
        env: string
        models: string
    }
    restart_notice: string
}

export interface SetupModelRoleInput {
    provider_name: string
    base_url: string
    api_key: string
    api_style: string
    model_id: string
    display_name?: string | null
    reasoning: boolean
    input_types: string[]
}

export interface SetupPatchPayload {
    admin_user?: {
        email?: string
        username?: string
        display_name?: string
        password?: string
    }
    models?: {
        primary?: SetupModelRoleInput
        routing?: SetupModelRoleInput
    }
    docs?: {
        soul_content?: string
        user_content?: string
    }
    channels?: {
        platforms?: Record<string, boolean>
        admin_user_ids?: string[]
        telegram_bot_token?: string
        discord_bot_token?: string
        dingtalk_client_id?: string
        dingtalk_client_secret?: string
        weixin_enable?: boolean
        weixin_base_url?: string
        weixin_cdn_base_url?: string
        web_channel_enable?: boolean
    }
}

export interface SetupPatchResponse {
    snapshot: SetupSnapshot
    changed_sections: string[]
    restart_required: boolean
}

export interface SetupGeneratePayload {
    kind: 'soul' | 'user'
    brief?: string
    current_content?: string
    model_key?: string
}

export interface SetupGenerateResponse {
    kind: 'soul' | 'user'
    model_key: string
    content: string
}

export const getSetupSnapshot = () =>
    request.get<SetupSnapshot>('/admin/setup')

export const patchSetup = (payload: SetupPatchPayload) =>
    request.patch<SetupPatchResponse>('/admin/setup', payload)

export const generateSetupDoc = (payload: SetupGeneratePayload) =>
    request.post<SetupGenerateResponse>('/admin/setup/generate', payload)

