import { getAuthToken } from './request'
import request from './request'

export interface ChatAttachment {
    id: string
    file_id: string
    kind: string
    name: string
    mime_type: string
    size: number
}

export interface ChatMessage {
    id: string
    session_id: string
    role: 'user' | 'assistant' | 'system'
    content: string
    status: string
    message_type: string
    attachments: ChatAttachment[]
    actions: Array<Array<{ text: string; callback_data: string }>>
    meta: Record<string, unknown>
    created_at: string
    updated_at: string
}

export interface ChatSession {
    id: string
    title: string
    preview: string
    message_count: number
    created_at: string
    updated_at: string
    last_message_at: string
    preferences?: Record<string, unknown>
}

export interface StreamEvent {
    id: number
    type: string
    payload: Record<string, unknown>
}

export const listSessions = async () => {
    const response = await request.get<{ items: ChatSession[] }>('/web-chat/sessions')
    return response.data.items
}

export const createSession = async (payload: { title?: string; preferences?: Record<string, unknown> }) => {
    const response = await request.post<ChatSession>('/web-chat/sessions', payload)
    return response.data
}

export const getSessionMessages = async (sessionId: string) => {
    const response = await request.get<{ session: ChatSession; items: ChatMessage[] }>(
        `/web-chat/sessions/${sessionId}/messages`
    )
    return response.data
}

export const postSessionEvent = async (sessionId: string, payload: Record<string, unknown>) => {
    const response = await request.post(`/web-chat/sessions/${sessionId}/events`, payload)
    return response.data
}

export const uploadChatFile = async (file: File, sessionId: string) => {
    const formData = new FormData()
    formData.append('file', file)
    const response = await request.post(
        `/web-chat/uploads?session_id=${encodeURIComponent(sessionId)}`,
        formData,
        {
            headers: {
                'Content-Type': 'multipart/form-data',
            },
        }
    )
    return response.data as ChatAttachment & {
        owner_user_id: string
        session_id: string
        path: string
        created_at: string
    }
}

export const generateTts = async (sessionId: string, messageId: string, voice = 'alloy') => {
    const response = await request.post(`/web-chat/sessions/${sessionId}/tts`, {
        message_id: messageId,
        voice,
    })
    return response.data as {
        message: ChatMessage
        attachment: ChatAttachment
    }
}

export const chatFileUrl = (fileId: string) => `/api/v1/web-chat/files/${fileId}`

export async function streamSessionEvents(
    sessionId: string,
    after: number,
    onEvent: (event: StreamEvent) => void,
    signal?: AbortSignal
) {
    const token = getAuthToken()
    const response = await fetch(
        `/api/v1/web-chat/sessions/${encodeURIComponent(sessionId)}/stream?after=${encodeURIComponent(String(after))}`,
        {
            method: 'GET',
            headers: {
                Authorization: token ? `Bearer ${token}` : '',
            },
            signal,
        }
    )
    if (!response.ok || !response.body) {
        throw new Error(`stream failed: ${response.status}`)
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let eventId = after
    let eventType = ''
    let eventData = ''

    const flushEvent = () => {
        if (!eventType || !eventData) return
        try {
            const payload = JSON.parse(eventData)
            onEvent({
                id: eventId,
                type: eventType,
                payload,
            })
        } catch (error) {
            console.error('Failed to parse SSE payload', error)
        }
        eventType = ''
        eventData = ''
    }

    while (true) {
        const { done, value } = await reader.read()
        if (done) {
            flushEvent()
            break
        }
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''
        for (const rawLine of lines) {
            const line = rawLine.trimEnd()
            if (!line) {
                flushEvent()
                continue
            }
            if (line.startsWith(':')) {
                continue
            }
            if (line.startsWith('id:')) {
                eventId = Number(line.slice(3).trim() || eventId)
                continue
            }
            if (line.startsWith('event:')) {
                eventType = line.slice(6).trim()
                continue
            }
            if (line.startsWith('data:')) {
                const chunk = line.slice(5).trim()
                eventData = eventData ? `${eventData}\n${chunk}` : chunk
            }
        }
    }
}
