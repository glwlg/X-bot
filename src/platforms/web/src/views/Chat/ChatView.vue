<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
    AudioLines,
    CircleStop,
    FilePlus2,
    Loader2,
    MessageSquareText,
    Mic,
    PanelLeftClose,
    PanelLeftOpen,
    Plus,
    RefreshCw,
    Search,
    SendHorizonal,
    SquarePen,
    Volume2,
    X
} from 'lucide-vue-next'

import {
    createSession,
    fetchChatFileBlob,
    generateTts,
    getSessionMessages,
    listSessions,
    postSessionEvent,
    streamSessionEvents,
    type ChatAttachment,
    type ChatMessage,
    type ChatSession,
    uploadChatFile,
} from '@/api/web-chat'

const sessions = ref<ChatSession[]>([])
const messages = ref<ChatMessage[]>([])
const currentSessionId = ref('')
const composer = ref('')
const loadingSessions = ref(false)
const sending = ref(false)
const streamStatus = ref<'idle' | 'connecting' | 'connected' | 'reconnecting' | 'error'>('idle')
const statusNote = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const streamAbort = ref<AbortController | null>(null)
const lastEventId = ref(0)
const recording = ref(false)
const recorder = ref<MediaRecorder | null>(null)
const recorderStream = ref<MediaStream | null>(null)
const messagesEl = ref<HTMLElement | null>(null)
const statusTimer = ref<number | null>(null)
const waitingAssistant = ref(false)
const showSessions = ref(false)
const showCommandPicker = ref(false)

const filteredCommands = computed(() => {
    const query = composer.value.trim().toLowerCase()
    if (!query.startsWith('/')) return commandEntries
    const search = query.slice(1).toLowerCase()
    if (!search) return commandEntries
    return commandEntries.filter(cmd =>
        cmd.label.toLowerCase().includes(search) ||
        cmd.text.toLowerCase().includes(search)
    )
})

watch(composer, (val) => {
    showCommandPicker.value = val.trim().startsWith('/')
})

const selectCommand = (cmd: { label: string; text: string }) => {
    composer.value = cmd.text + ' '
    showCommandPicker.value = false
}

const commandEntries = [
    { label: '/start', text: '/start' },
    { label: '/help', text: '/help' },
    { label: '/model', text: '/model' },
    { label: '/usage', text: '/usage' },
    { label: '/task', text: '/task recent' },
    { label: '/heartbeat', text: '/heartbeat list' },
    { label: '/skills', text: '/skills' },
    { label: '/wxbind', text: '/wxbind' },
]

const attachmentKind = (attachment: ChatAttachment) =>
    String(attachment.kind || '').trim().toLowerCase()

const attachmentMimeType = (attachment: ChatAttachment) =>
    String(attachment.mime_type || '').trim().toLowerCase()

const attachmentName = (attachment: ChatAttachment) =>
    String(attachment.name || '').trim().toLowerCase()

const normalizeAttachment = (attachment: Record<string, unknown>): ChatAttachment => ({
    id: String(attachment.id || attachment.file_id || ''),
    file_id: String(attachment.file_id || attachment.id || ''),
    kind: String(attachment.kind || ''),
    name: String(attachment.name || ''),
    mime_type: String(attachment.mime_type || 'application/octet-stream'),
    size: Number(attachment.size || 0),
})

const isImageAttachment = (attachment: ChatAttachment) => {
    const kind = attachmentKind(attachment)
    const mimeType = attachmentMimeType(attachment)
    const name = attachmentName(attachment)
    return (
        kind === 'image' ||
        mimeType.startsWith('image/') ||
        /\.(avif|bmp|gif|jpe?g|png|svg|webp)$/i.test(name)
    )
}

const isAudioAttachment = (attachment: ChatAttachment) => {
    const kind = attachmentKind(attachment)
    const mimeType = attachmentMimeType(attachment)
    const name = attachmentName(attachment)
    return (
        kind === 'audio' ||
        kind === 'voice' ||
        mimeType.startsWith('audio/') ||
        /\.(aac|flac|m4a|mp3|ogg|opus|wav|webm)$/i.test(name)
    )
}

const attachmentBlobUrls = ref<Record<string, string>>({})
const loadingAttachmentIds = new Set<string>()

const attachmentFileId = (attachment: ChatAttachment) =>
    String(attachment.file_id || attachment.id || '').trim()

const attachmentObjectUrl = (attachment: ChatAttachment) =>
    attachmentBlobUrls.value[attachmentFileId(attachment)] || ''

const cacheAttachmentObjectUrl = (fileId: string, url: string) => {
    const safeFileId = String(fileId || '').trim()
    if (!safeFileId || !url) return
    const current = attachmentBlobUrls.value[safeFileId]
    if (current && current !== url) {
        URL.revokeObjectURL(current)
    }
    attachmentBlobUrls.value = {
        ...attachmentBlobUrls.value,
        [safeFileId]: url,
    }
}

const ensureAttachmentUrl = async (attachment: ChatAttachment) => {
    const fileId = attachmentFileId(attachment)
    if (!fileId) return ''
    const cached = attachmentBlobUrls.value[fileId]
    if (cached) return cached
    if (loadingAttachmentIds.has(fileId)) return ''

    loadingAttachmentIds.add(fileId)
    try {
        const blob = await fetchChatFileBlob(fileId)
        const url = URL.createObjectURL(blob)
        cacheAttachmentObjectUrl(fileId, url)
        return url
    } catch (error) {
        console.error('Failed to load chat attachment', error)
        return ''
    } finally {
        loadingAttachmentIds.delete(fileId)
    }
}

const primeMessageAttachments = (message?: ChatMessage | null) => {
    if (!message?.attachments?.length) return
    for (const attachment of message.attachments) {
        void ensureAttachmentUrl(attachment)
    }
}

const primeMessagesAttachments = (items: ChatMessage[]) => {
    for (const message of items) {
        primeMessageAttachments(message)
    }
}

const revokeAttachmentUrls = () => {
    for (const url of Object.values(attachmentBlobUrls.value)) {
        URL.revokeObjectURL(url)
    }
    attachmentBlobUrls.value = {}
}

const openAttachment = async (attachment: ChatAttachment) => {
    const url = attachmentObjectUrl(attachment) || await ensureAttachmentUrl(attachment)
    if (!url) return
    const opened = window.open(url, '_blank', 'noopener,noreferrer')
    if (opened) return

    const link = document.createElement('a')
    link.href = url
    link.target = '_blank'
    link.rel = 'noopener noreferrer'
    if (attachment.name) {
        link.download = attachment.name
    }
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
}

const visibleSessions = computed(() => {
    const ordered = [...sessions.value].sort((a, b) => {
        const left = String(a.updated_at || a.last_message_at || a.created_at || '')
        const right = String(b.updated_at || b.last_message_at || b.created_at || '')
        return right.localeCompare(left)
    })

    return ordered.filter(session => {
        const isEmpty = !session.message_count && !String(session.preview || '').trim()
        if (!isEmpty) return true
        return session.id === currentSessionId.value
    })
})

const currentSession = computed(() =>
    sessions.value.find(item => item.id === currentSessionId.value) || null
)

const statusBadge = computed(() => {
    if (streamStatus.value === 'reconnecting') {
        return '重连中'
    }
    if (streamStatus.value === 'error') {
        return '连接异常'
    }
    if (statusNote.value) {
        return statusNote.value
    }
    if (waitingAssistant.value) {
        return '等待 Ikaros 响应'
    }
    return ''
})

const setStatus = (text: string, timeoutMs = 0) => {
    if (statusTimer.value) {
        window.clearTimeout(statusTimer.value)
        statusTimer.value = null
    }
    statusNote.value = text
    if (timeoutMs > 0) {
        statusTimer.value = window.setTimeout(() => {
            statusNote.value = ''
            statusTimer.value = null
        }, timeoutMs)
    }
}

const scrollToBottom = async () => {
    await nextTick()
    await new Promise(resolve => requestAnimationFrame(resolve))
    if (messagesEl.value) {
        messagesEl.value.scrollTop = messagesEl.value.scrollHeight
    }
}

const mergeMessage = (message: ChatMessage) => {
    const index = messages.value.findIndex(item => item.id === message.id)
    if (index >= 0) {
        const currentMessage = messages.value[index]
        if (!currentMessage) return
        messages.value[index] = {
            ...currentMessage,
            ...message,
            attachments: message.attachments || currentMessage.attachments || [],
            actions: message.actions || currentMessage.actions || [],
            meta: message.meta || currentMessage.meta || {},
        }
        primeMessageAttachments(messages.value[index])
    } else {
        messages.value.push(message)
        primeMessageAttachments(message)
    }
}

const updateSessionSummary = (message: ChatMessage) => {
    const session = sessions.value.find(item => item.id === message.session_id)
    if (!session) return
    session.preview = message.content || session.preview
    session.updated_at = message.updated_at || new Date().toISOString()
    session.last_message_at = message.updated_at || session.last_message_at
    session.message_count = Math.max(session.message_count || 0, messages.value.length)
    if (message.role === 'user' && (!session.title || session.title === '新对话') && message.content) {
        session.title = message.content.slice(0, 48)
    }
}

const attachAudioToMessage = (messageId: string, attachment: Record<string, unknown>) => {
    const target = messages.value.find(item => item.id === messageId)
    if (!target) return
    const normalizedAttachment = normalizeAttachment(attachment)
    const nextAttachments = [...(target.attachments || [])]
    const exists = nextAttachments.some(item => item.file_id === normalizedAttachment.file_id)
    if (!exists) {
        nextAttachments.push(normalizedAttachment)
        target.attachments = nextAttachments
        void ensureAttachmentUrl(normalizedAttachment)
    }
}

const ensureActiveSession = async () => {
    if (currentSessionId.value) return currentSessionId.value

    const created = await createSession({})
    sessions.value = [created, ...sessions.value.filter(item => item.id !== created.id)]
    await openSession(created.id)
    return created.id
}

const handleStreamEvent = (event: { id: number; type: string; payload: Record<string, unknown> }) => {
    lastEventId.value = Math.max(lastEventId.value, Number(event.id || 0))
    streamStatus.value = 'connected'

    if (event.type === 'task_status') {
        setStatus(String(event.payload.action || '处理中'))
        return
    }

    if (event.type === 'error') {
        waitingAssistant.value = false
        streamStatus.value = 'error'
        setStatus(String(event.payload.message || '处理失败'))
        return
    }

    if (event.type === 'done') {
        waitingAssistant.value = false
        setStatus(String(event.payload.text || '已完成'), 2400)
        return
    }

    if (event.type === 'attachment_ready' || event.type === 'audio_ready') {
        const messageId = String(event.payload.message_id || '')
        const attachment = event.payload.attachment as Record<string, unknown> | undefined
        if (messageId && attachment) {
            attachAudioToMessage(messageId, attachment)
        }
        return
    }

    const message = event.payload.message as ChatMessage | undefined
    if (!message) return

    mergeMessage(message)
    updateSessionSummary(message)
    if (message.role === 'assistant') {
        waitingAssistant.value = false
        setStatus('', 0)
    }
    scrollToBottom()
}

const startStream = (sessionId: string) => {
    streamAbort.value?.abort()
    const controller = new AbortController()
    streamAbort.value = controller
    streamStatus.value = 'connecting'

    const run = async () => {
        while (!controller.signal.aborted && currentSessionId.value === sessionId) {
            try {
                await streamSessionEvents(sessionId, lastEventId.value, handleStreamEvent, controller.signal)
                streamStatus.value = 'connected'
            } catch (error) {
                if (controller.signal.aborted) break
                console.error(error)
                streamStatus.value = 'reconnecting'
                setStatus('连接中断，正在重连')
                await new Promise(resolve => window.setTimeout(resolve, 1000))
            }
        }
    }

    void run()
}

const openSession = async (sessionId: string) => {
    currentSessionId.value = sessionId
    lastEventId.value = 0
    waitingAssistant.value = false
    const response = await getSessionMessages(sessionId)
    messages.value = response.items || []
    primeMessagesAttachments(messages.value)
    await scrollToBottom()
    startStream(sessionId)
}

const ensureInitialSession = async () => {
    loadingSessions.value = true
    try {
        sessions.value = await listSessions()
        const firstSession = visibleSessions.value[0]
        if (!currentSessionId.value && firstSession) {
            await openSession(firstSession.id)
        } else if (!firstSession) {
            currentSessionId.value = ''
            messages.value = []
        }
    } finally {
        loadingSessions.value = false
    }
}

const createNewSession = async () => {
    const created = await createSession({})
    sessions.value = [created, ...sessions.value.filter(item => item.id !== created.id)]
    await openSession(created.id)
}

const sendEvent = async (payload: Record<string, unknown>) => {
    const sessionId = await ensureActiveSession()
    if (!sessionId) return
    const response = await postSessionEvent(sessionId, payload)
    if (response.message) {
        mergeMessage(response.message as ChatMessage)
        updateSessionSummary(response.message as ChatMessage)
        waitingAssistant.value = true
        setStatus('等待 Ikaros 响应')
        await scrollToBottom()
    }
}

const sendText = async () => {
    const text = composer.value.trim()
    if (!text) return
    sending.value = true
    try {
        await sendEvent({
            type: text.startsWith('/') ? 'command' : 'message_text',
            text,
        })
        composer.value = ''
    } finally {
        sending.value = false
    }
}

const openFilePicker = () => fileInput.value?.click()

const sendFile = async (file: File, isVoice = false) => {
    const sessionId = await ensureActiveSession()
    if (!sessionId) return
    const uploaded = await uploadChatFile(file, sessionId)
    await sendEvent({
        type: isVoice ? 'message_voice' : 'message_file',
        file_id: uploaded.id,
        file_name: uploaded.name,
        file_size: uploaded.size,
        mime_type: uploaded.mime_type,
        caption: '',
    })
}

const handleFileSelection = async (event: Event) => {
    const input = event.target as HTMLInputElement
    const files = Array.from(input.files || [])
    for (const file of files) {
        await sendFile(file, false)
    }
    input.value = ''
}

const toggleRecord = async () => {
    if (recording.value && recorder.value) {
        recorder.value.stop()
        return
    }

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    const chunks: BlobPart[] = []
    recorderStream.value = stream
    const mediaRecorder = new MediaRecorder(stream)
    recorder.value = mediaRecorder
    recording.value = true
    setStatus('录音中')

    mediaRecorder.ondataavailable = event => {
        if (event.data.size > 0) {
            chunks.push(event.data)
        }
    }

    mediaRecorder.onstop = async () => {
        recording.value = false
        setStatus('上传语音中')
        const blob = new Blob(chunks, { type: mediaRecorder.mimeType || 'audio/webm' })
        const voiceFile = new File([blob], `voice-${Date.now()}.webm`, {
            type: mediaRecorder.mimeType || 'audio/webm',
        })
        recorderStream.value?.getTracks().forEach(track => track.stop())
        recorderStream.value = null
        await sendFile(voiceFile, true)
    }

    mediaRecorder.start()
}

const runMenuAction = async (callbackData: string) => {
    await sendEvent({
        type: 'menu_action',
        callback_data: callbackData,
    })
}

const playMessage = async (message: ChatMessage) => {
    const existingAudio = (message.attachments || []).find(isAudioAttachment)
    if (existingAudio) {
        const audioUrl = attachmentObjectUrl(existingAudio) || await ensureAttachmentUrl(existingAudio)
        if (!audioUrl) return
        const audio = new Audio(audioUrl)
        await audio.play()
        return
    }
    const sessionId = await ensureActiveSession()
    if (!sessionId) return
    const result = await generateTts(sessionId, message.id)
    mergeMessage(result.message)
    const audioUrl = await ensureAttachmentUrl(result.attachment)
    if (!audioUrl) return
    const audio = new Audio(audioUrl)
    await audio.play()
}

const onDrop = async (event: DragEvent) => {
    event.preventDefault()
    const files = Array.from(event.dataTransfer?.files || [])
    for (const file of files) {
        await sendFile(file, false)
    }
}

onMounted(async () => {
    await ensureInitialSession()
})

onBeforeUnmount(() => {
    if (statusTimer.value) {
        window.clearTimeout(statusTimer.value)
    }
    streamAbort.value?.abort()
    recorderStream.value?.getTracks().forEach(track => track.stop())
    revokeAttachmentUrls()
})
</script>

<template>
  <div class="chat-page">
    <section class="chat-title-panel">
      <h1>对话工作台 / Chat</h1>
      <p>与 IKAROS AI 助手对话，获取平台能力支持</p>
    </section>

    <div class="chat-workbench grid h-full min-h-0 gap-0 md:grid-cols-[330px_minmax(0,1fr)] xl:grid-cols-[340px_minmax(0,1fr)_300px]">
    <!-- Mobile overlay -->
    <div
      v-if="showSessions"
      class="fixed inset-0 z-30 bg-black/50 md:hidden"
      @click="showSessions = false"
    />

    <!-- Sessions sidebar - fixed on mobile, relative on desktop -->
    <aside
      class="chat-panel chat-sessions-rail border-r border-slate-200 bg-slate-50/80 p-4 transition-transform duration-300 h-auto md:h-full"
      :class="[
        showSessions ? 'translate-x-0' : '-translate-x-full md:translate-x-0',
        'md:relative md:block md:w-[300px] md:flex-shrink-0',
        'fixed top-[56px] left-0 bottom-0 z-40 w-[280px] md:static md:z-auto'
      ]"
    >
      <div class="flex items-center justify-between">
        <div>
          <div class="text-lg font-semibold text-slate-900">会话列表</div>
        </div>
        <button class="chat-primary-small" @click="createNewSession">
          <Plus class="h-4 w-4" />
          新建会话
        </button>
      </div>

      <div class="mt-4 grid grid-cols-[minmax(0,1fr)_44px] gap-2">
        <label class="chat-session-search">
          <Search class="h-4 w-4" />
          <input type="search" placeholder="搜索会话标题或内容">
        </label>
        <button
          class="chat-icon-only"
          @click="ensureInitialSession"
        >
          <RefreshCw class="h-4 w-4" />
        </button>
      </div>

      <div class="mt-5 space-y-2">
        <div v-if="loadingSessions" class="flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-3 text-sm text-slate-500">
          <Loader2 class="h-4 w-4 animate-spin" />
          正在加载会话
        </div>

        <button
          v-for="session in visibleSessions"
          :key="session.id"
          class="w-full rounded-[24px] border px-4 py-4 text-left transition"
          :class="session.id === currentSessionId
            ? 'border-blue-400 bg-blue-50 shadow-sm'
            : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50'"
          @click="openSession(session.id)"
        >
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="truncate text-sm font-semibold text-slate-900">{{ session.title || '新对话' }}</div>
              <div class="mt-2 max-h-[3.2rem] overflow-hidden text-xs leading-6 text-slate-500">{{ session.preview || '等待第一条消息' }}</div>
            </div>
            <div class="text-[11px] uppercase tracking-[0.16em] text-slate-400">{{ session.message_count || 0 }}</div>
          </div>
        </button>

        <div v-if="!loadingSessions && !visibleSessions.length" class="rounded-[24px] border border-dashed border-slate-300 bg-white/70 px-4 py-5 text-sm leading-7 text-slate-500">
          还没有会话。点击右上角 `+` 开始一个新对话。
        </div>
      </div>
    </aside>

    <section class="chat-panel chat-canvas flex min-h-0 flex-col bg-[linear-gradient(180deg,_#ffffff_0%,_#f8fafc_100%)]" @drop="onDrop" @dragover.prevent>
      <header class="chat-canvas-header flex items-center justify-between border-b border-slate-200 px-5 py-4">
        <div class="flex items-center gap-3">
          <!-- Toggle sessions button (mobile) -->
          <button
            class="rounded-xl border border-slate-200 bg-white p-2 text-slate-700 transition hover:bg-slate-100 md:hidden"
            @click="showSessions = !showSessions"
          >
            <PanelLeftOpen v-if="!showSessions" class="h-5 w-5" />
            <PanelLeftClose v-else class="h-5 w-5" />
          </button>
          <div>
            <div class="text-sm font-semibold text-slate-900">{{ currentSession?.title || '准备开始新的对话' }}</div>
            <div class="mt-1 text-xs text-slate-500">
              {{ currentSession ? '当前会话已就绪' : '新建会话后即可开始对话' }}
            </div>
          </div>
        </div>
        <div class="flex items-center gap-3">
          <div
            v-if="statusBadge"
            class="inline-flex items-center gap-2 rounded-full border px-3 py-2 text-xs"
            :class="streamStatus === 'error'
              ? 'border-rose-200 bg-rose-50 text-rose-700'
              : 'border-slate-200 bg-white text-slate-600'"
          >
            <span class="h-2 w-2 rounded-full" :class="streamStatus === 'error' ? 'bg-rose-500' : 'bg-emerald-500'" />
            {{ statusBadge }}
          </div>
          <div class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-2 text-xs text-slate-500">
            <MessageSquareText class="h-4 w-4 text-blue-600" />
            输入 / 使用命令
          </div>
        </div>
      </header>

      <div ref="messagesEl" class="chat-messages-container min-h-0 flex-1 overflow-auto px-5 py-5">
        <div v-if="!messages.length" class="flex h-full min-h-[420px] items-center justify-center">
          <div class="max-w-xl rounded-[28px] border border-dashed border-slate-300 bg-white/80 px-8 py-10 text-center shadow-sm">
            <div class="mx-auto flex h-14 w-14 items-center justify-center rounded-3xl bg-cyan-50 text-cyan-700">
              <AudioLines class="h-6 w-6" />
            </div>
            <h3 class="mt-5 text-2xl font-semibold text-slate-950">开始一轮真正可用的对话</h3>
            <p class="mt-3 text-sm leading-7 text-slate-500">
              支持文本、命令、语音和文件。输入 / 开始使用命令，或点击下方按钮开始。
            </p>
          </div>
        </div>

        <div v-else class="space-y-5">
          <div
            v-for="message in messages"
            :key="message.id"
            class="flex"
            :class="message.role === 'user' ? 'justify-end' : 'justify-start'"
          >
            <div
              class="message-bubble max-w-[860px] rounded-[28px] border px-4 py-3 shadow-sm"
              :class="message.role === 'user'
                ? 'border-blue-200 bg-blue-50 text-slate-900'
                : 'border-slate-200 bg-white text-slate-900'"
            >
              <div class="mb-2 flex items-center gap-2 text-[11px] uppercase tracking-[0.2em] text-slate-400">
                <span>{{ message.role === 'user' ? 'You' : 'Ikaros' }}</span>
                <span>{{ message.message_type }}</span>
              </div>
              <div v-if="message.content" class="whitespace-pre-wrap text-sm leading-7 text-slate-700">{{ message.content }}</div>

              <div v-if="message.attachments?.length" class="mt-3 space-y-2">
                <div
                  v-for="attachment in message.attachments"
                  :key="`${message.id}-${attachment.file_id}`"
                  class="overflow-hidden rounded-2xl border border-slate-200 bg-slate-50"
                >
                  <button
                    v-if="isImageAttachment(attachment) && attachmentObjectUrl(attachment)"
                    type="button"
                    class="block w-full bg-slate-100 text-left"
                    @click="openAttachment(attachment)"
                  >
                    <img
                      :src="attachmentObjectUrl(attachment)"
                      :alt="attachment.name || '图片附件'"
                      class="block max-h-[420px] w-full object-contain"
                      loading="lazy"
                    >
                  </button>

                  <div
                    v-else-if="isImageAttachment(attachment)"
                    class="flex min-h-[180px] items-center justify-center bg-slate-100 px-4 py-8 text-sm text-slate-500"
                  >
                    正在加载图片…
                  </div>

                  <div class="space-y-3 p-3">
                    <audio
                      v-if="isAudioAttachment(attachment) && attachmentObjectUrl(attachment)"
                      :src="attachmentObjectUrl(attachment)"
                      controls
                      preload="metadata"
                      class="w-full"
                    />

                    <div
                      v-else-if="isAudioAttachment(attachment)"
                      class="rounded-xl border border-dashed border-slate-200 bg-white px-3 py-4 text-sm text-slate-500"
                    >
                      正在加载音频…
                    </div>

                    <div class="flex items-center justify-between gap-3">
                      <div class="min-w-0">
                        <div class="truncate text-sm font-medium text-slate-900">{{ attachment.name }}</div>
                        <div class="text-xs text-slate-500">{{ attachment.mime_type }}</div>
                      </div>
                      <button
                        type="button"
                        class="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 transition hover:bg-slate-100"
                        @click="openAttachment(attachment)"
                      >
                        打开
                      </button>
                    </div>
                  </div>
                </div>
              </div>

              <div v-if="message.actions?.length" class="mt-3 space-y-2">
                <div v-for="(row, rowIndex) in message.actions" :key="`${message.id}-row-${rowIndex}`" class="flex flex-wrap gap-2">
                  <button
                    v-for="action in row"
                    :key="action.callback_data"
                    class="rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-100"
                    @click="runMenuAction(action.callback_data)"
                  >
                    {{ action.text }}
                  </button>
                </div>
              </div>

              <div v-if="message.role === 'assistant' && message.content" class="mt-3 flex justify-end">
                <button
                  class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600 transition hover:bg-slate-100"
                  @click="playMessage(message)"
                >
                  <Volume2 class="h-4 w-4" />
                  朗读
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <footer class="chat-canvas-footer border-t border-slate-200 bg-white/90 p-4">
        <!-- Command picker dropdown -->
        <div v-if="showCommandPicker && filteredCommands.length" class="mb-3 rounded-[16px] border border-slate-200 bg-white p-2 shadow-lg">
          <div class="mb-2 flex items-center justify-between px-2 py-1">
            <span class="text-xs font-medium text-slate-500">可用命令</span>
            <button class="text-slate-400 hover:text-slate-600" @click="showCommandPicker = false">
              <X class="h-3 w-3" />
            </button>
          </div>
          <div class="flex flex-wrap gap-2">
            <button
              v-for="cmd in filteredCommands"
              :key="cmd.text"
              class="rounded-xl border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-100"
              @click="selectCommand(cmd)"
            >
              {{ cmd.label }}
            </button>
          </div>
        </div>

        <div class="composer-box rounded-[28px] border border-slate-200 bg-slate-50 p-3">
          <textarea
            v-model="composer"
            class="min-h-[110px] w-full resize-none bg-transparent px-2 py-2 text-sm leading-7 text-slate-800 outline-none"
            placeholder="输入消息，或输入 / 查看命令..."
            @keydown.enter.exact.prevent="sendText"
          />
          <div class="mt-3 flex flex-wrap items-center justify-between gap-3">
            <div class="flex flex-wrap items-center gap-2">
              <button class="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-100" @click="openFilePicker">
                <FilePlus2 class="h-4 w-4" />
                文件
              </button>
              <button
                class="inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm transition"
                :class="recording ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-100'"
                @click="toggleRecord"
              >
                <component :is="recording ? CircleStop : Mic" class="h-4 w-4" />
                {{ recording ? '停止录音' : '语音' }}
              </button>
              <input ref="fileInput" type="file" class="hidden" multiple @change="handleFileSelection">
            </div>

            <button
              class="inline-flex items-center gap-2 rounded-2xl bg-blue-500 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-blue-600 disabled:opacity-60"
              :disabled="sending || !composer.trim()"
              @click="sendText"
            >
              <Loader2 v-if="sending" class="h-4 w-4 animate-spin" />
              <SendHorizonal v-else class="h-4 w-4" />
              发送
            </button>
          </div>
        </div>
      </footer>
    </section>

    <aside class="chat-info-panel hidden xl:flex">
      <section class="info-section">
        <div class="info-heading">
          <h2>会话信息</h2>
          <button type="button">⌃</button>
        </div>
        <dl class="info-list">
          <div>
            <dt>会话标题</dt>
            <dd>{{ currentSession?.title || '未命名会话' }}</dd>
          </div>
          <div>
            <dt>创建时间</dt>
            <dd>{{ currentSession?.created_at || '-' }}</dd>
          </div>
          <div>
            <dt>最后更新</dt>
            <dd>{{ currentSession?.updated_at || currentSession?.last_message_at || '-' }}</dd>
          </div>
          <div>
            <dt>消息数</dt>
            <dd>{{ messages.length }}</dd>
          </div>
          <div>
            <dt>会话 ID</dt>
            <dd class="truncate">{{ currentSession?.id || '-' }}</dd>
          </div>
        </dl>
      </section>

      <section class="info-section">
        <div class="info-heading">
          <h2>快捷指令</h2>
          <button type="button">⌃</button>
        </div>
        <div class="quick-command-list">
          <button v-for="cmd in commandEntries.slice(0, 5)" :key="cmd.text" type="button" @click="selectCommand(cmd)">
            <SquarePen class="h-4 w-4" />
            <span>{{ cmd.label }}</span>
            <small>{{ cmd.text }}</small>
          </button>
        </div>
        <button type="button" class="manage-command-btn">
          <Plus class="h-4 w-4" />
          管理快捷指令
        </button>
      </section>
    </aside>
  </div>
  </div>
</template>

<style scoped>
.chat-page {
  display: grid;
  min-height: calc(100vh - 154px);
  grid-template-rows: auto minmax(0, 1fr);
  overflow: hidden;
  border: 1px solid var(--panel-border);
  border-radius: 14px;
  background: #fff;
  box-shadow: var(--shadow-card);
}

.chat-title-panel {
  padding: 22px 24px;
  border-bottom: 1px solid var(--panel-border);
  background: #fff;
}

.chat-title-panel h1 {
  margin: 0;
  color: var(--text-strong);
  font-size: 24px;
  font-weight: 800;
}

.chat-title-panel p {
  margin: 8px 0 0;
  color: var(--text-muted);
  font-size: 15px;
}

.chat-workbench {
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

.chat-panel {
  min-height: 0;
}

.chat-sessions-rail {
  background: #fbfdff;
  overflow-y: auto;
}

.chat-canvas {
  background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
  overflow: hidden;
}

.chat-primary-small {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 40px;
  padding: 0 14px;
  border: 0;
  border-radius: 8px;
  background: var(--brand-blue);
  color: #fff;
  font-size: 14px;
  font-weight: 700;
}

.chat-session-search {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  height: 40px;
  padding: 0 12px;
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  background: #fff;
  color: var(--text-subtle);
}

.chat-session-search input {
  min-width: 0;
  width: 100%;
  border: 0 !important;
  box-shadow: none !important;
  outline: 0;
  font-size: 14px;
}

.chat-icon-only {
  display: grid;
  place-items: center;
  width: 42px;
  height: 40px;
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  background: #fff;
  color: var(--text-body);
}

.chat-messages-container {
  scrollbar-gutter: stable;
  overscroll-behavior: contain;
}

.chat-messages-container::-webkit-scrollbar {
  width: 6px;
}

.chat-messages-container::-webkit-scrollbar-track {
  background: transparent;
}

.chat-messages-container::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 999px;
}

.chat-messages-container::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

.chat-canvas-header,
.chat-canvas-footer {
  background: rgba(255, 255, 255, 0.94);
  backdrop-filter: blur(18px);
}

.message-bubble {
  border-radius: 10px !important;
}

.composer-box {
  border-color: #6aa8ff !important;
  background: #fff !important;
  box-shadow: 0 0 0 1px rgba(47, 124, 246, 0.08);
}

.chat-info-panel {
  min-height: 0;
  flex-direction: column;
  border-left: 1px solid var(--panel-border);
  background: #fff;
  overflow-y: auto;
}

.info-section {
  padding: 22px 24px;
  border-bottom: 1px solid var(--panel-border);
}

.info-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.info-heading h2 {
  margin: 0;
  color: var(--text-strong);
  font-size: 16px;
  font-weight: 800;
}

.info-heading button {
  border: 0;
  background: transparent;
  color: var(--text-body);
}

.info-list {
  display: grid;
  gap: 20px;
  margin: 22px 0 0;
}

.info-list div {
  min-width: 0;
}

.info-list dt {
  color: var(--text-muted);
  font-size: 13px;
}

.info-list dd {
  margin: 8px 0 0;
  color: var(--text-strong);
  font-size: 14px;
}

.quick-command-list {
  display: grid;
  gap: 12px;
  margin-top: 20px;
}

.quick-command-list button {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr);
  align-items: center;
  column-gap: 10px;
  row-gap: 2px;
  width: 100%;
  border: 0;
  background: transparent;
  color: var(--text-strong);
  text-align: left;
}

.quick-command-list svg {
  grid-row: span 2;
  color: var(--brand-blue);
}

.quick-command-list span {
  font-weight: 800;
}

.quick-command-list small {
  color: var(--text-muted);
  font-size: 12px;
}

.manage-command-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-top: 22px;
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  background: #fff;
  color: var(--brand-blue);
  padding: 10px 14px;
  font-size: 14px;
  font-weight: 800;
}

@media (max-width: 1280px) {
  .chat-workbench {
    min-height: auto;
  }
}

@media (max-width: 768px) {
  .chat-page {
    min-height: calc(100vh - 110px);
  }
}
</style>
