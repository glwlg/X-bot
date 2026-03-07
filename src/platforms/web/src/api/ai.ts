import request from '@/api/request'

interface AiChatRequest {
    message: string
    context: {
        page: string
        path: string
        description: string
        pageData?: any
    }
    conversationId: string
}

interface AiChatResponse {
    reply: string
    conversationId: string
}

export async function aiChatApi(data: AiChatRequest): Promise<AiChatResponse> {
    const response = await request({
        url: '/ai/chat',
        method: 'post',
        data
    })
    return response.data
}

export async function streamAiChatApi(
    data: AiChatRequest, 
    onChunk: (chunk: string) => void,
    onError: (error: any) => void,
    onFinish: () => void
) {
    try {
        // Replicate Auth logic from request.ts
        let token = localStorage.getItem('access_token')
        try {
            if (window.parent && window.parent !== window) {
                const parentWindow = window.parent as Window & { localStorage?: Storage }
                if (parentWindow.localStorage) {
                    const parentToken = parentWindow.localStorage.getItem('access_token')
                    if (parentToken) token = parentToken
                }
            }
        } catch (e) { /* ignore */ }

        const response = await fetch('/ops/api/v1/ai/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(token ? { 'Authorization': `Bearer ${token}` } : {})
            },
            body: JSON.stringify(data)
        })

        if (!response.ok) {
            const errText = await response.text()
            throw new Error(`API Error: ${response.status} ${errText}`)
        }

        if (!response.body) {
            throw new Error('Response body is empty')
        }

        const reader = response.body.getReader()
        const decoder = new TextDecoder()

        while (true) {
            const { done, value } = await reader.read()
            if (done) break
            
            const chunk = decoder.decode(value, { stream: true })
            onChunk(chunk)
        }
        
        onFinish()

    } catch (e) {
        onError(e)
    }
}
