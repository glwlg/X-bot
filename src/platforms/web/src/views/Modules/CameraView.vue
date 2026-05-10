<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
    ArrowDown,
    ArrowDownLeft,
    ArrowDownRight,
    ArrowLeft,
    ArrowRight,
    ArrowUp,
    ArrowUpLeft,
    ArrowUpRight,
    Cctv,
    CircleStop,
    Loader2,
    Pencil,
    Play,
    Plus,
    RefreshCw,
    Trash2,
    Video,
    ZoomIn,
    ZoomOut,
} from 'lucide-vue-next'

import {
    createCamera,
    createStreamToken,
    deleteCamera,
    listCameras,
    sendPtz,
    testCamera,
    updateCamera,
    type CameraItem,
    type CameraPayload,
    type PtzAction,
    type StreamToken,
} from '@/api/cameras'

type PlayerMode = 'webrtc' | 'hls'

interface CameraForm {
    name: string
    rtsp_url: string
    enabled: boolean
    mediamtx_path: string
    onvif_enabled: boolean
    onvif_host: string
    onvif_port: number
    onvif_username: string
    onvif_password: string
}

const cameras = ref<CameraItem[]>([])
const loading = ref(false)
const refreshing = ref(false)
const streamLoading = ref(false)
const selectedId = ref<number | null>(null)
const stream = ref<StreamToken | null>(null)
const streamError = ref('')
const playerMode = ref<PlayerMode>('webrtc')
const showDialog = ref(false)
const editingId = ref<number | null>(null)
const saving = ref(false)
const testingId = ref<number | null>(null)
const ptzAction = ref<PtzAction | ''>('')
const ptzSpeed = ref(0.4)

const emptyForm = (): CameraForm => ({
    name: '',
    rtsp_url: '',
    enabled: true,
    mediamtx_path: '',
    onvif_enabled: true,
    onvif_host: '',
    onvif_port: 80,
    onvif_username: '',
    onvif_password: '',
})

const formData = ref<CameraForm>(emptyForm())

const selectedCamera = computed(() =>
    cameras.value.find((camera) => camera.id === selectedId.value) || null
)

const enabledCount = computed(() => cameras.value.filter((camera) => camera.enabled).length)
const ptzCount = computed(() => cameras.value.filter((camera) => camera.onvif_enabled).length)
const canControlPtz = computed(() =>
    !!selectedCamera.value?.onvif_enabled && !!selectedCamera.value?.onvif_host
)

const loadData = async (isRefresh = false) => {
    if (isRefresh) {
        refreshing.value = true
    } else {
        loading.value = true
    }
    try {
        const res = await listCameras()
        cameras.value = res.data || []
        if (!selectedId.value && cameras.value.length > 0) {
            selectedId.value = cameras.value[0]?.id || null
        }
        if (selectedId.value && !cameras.value.some((camera) => camera.id === selectedId.value)) {
            selectedId.value = cameras.value[0]?.id || null
        }
    } catch (error) {
        console.error(error)
    } finally {
        loading.value = false
        refreshing.value = false
    }
}

const loadStream = async () => {
    stream.value = null
    streamError.value = ''
    if (!selectedId.value) return
    streamLoading.value = true
    try {
        const res = await createStreamToken(selectedId.value)
        stream.value = res.data
    } catch (error: any) {
        streamError.value = error?.response?.data?.detail || '拉流失败'
    } finally {
        streamLoading.value = false
    }
}

const selectCamera = (camera: CameraItem) => {
    selectedId.value = camera.id
}

const openCreate = () => {
    editingId.value = null
    formData.value = emptyForm()
    showDialog.value = true
}

const openEdit = (camera: CameraItem) => {
    editingId.value = camera.id
    formData.value = {
        name: camera.name,
        rtsp_url: camera.rtsp_url || '',
        enabled: camera.enabled,
        mediamtx_path: camera.mediamtx_path,
        onvif_enabled: camera.onvif_enabled,
        onvif_host: camera.onvif_host || '',
        onvif_port: camera.onvif_port || 80,
        onvif_username: camera.onvif_username || '',
        onvif_password: camera.onvif_password || '',
    }
    showDialog.value = true
}

const closeDialog = () => {
    showDialog.value = false
    editingId.value = null
    formData.value = emptyForm()
}

const buildPayload = () => {
    const base: CameraPayload = {
        name: formData.value.name.trim(),
        enabled: formData.value.enabled,
        mediamtx_path: formData.value.mediamtx_path.trim() || undefined,
        onvif_enabled: formData.value.onvif_enabled,
        onvif_host: formData.value.onvif_host.trim() || undefined,
        onvif_port: Number(formData.value.onvif_port || 80),
        onvif_username: formData.value.onvif_username.trim() || undefined,
    }
    if (formData.value.rtsp_url.trim()) {
        base.rtsp_url = formData.value.rtsp_url.trim()
    }
    if (formData.value.onvif_password.trim()) {
        base.onvif_password = formData.value.onvif_password
    }
    return base
}

const handleSave = async () => {
    if (!formData.value.name.trim()) return
    if (!editingId.value && !formData.value.rtsp_url.trim()) return
    saving.value = true
    try {
        if (editingId.value) {
            await updateCamera(editingId.value, buildPayload())
        } else {
            await createCamera(buildPayload())
        }
        closeDialog()
        await loadData(true)
    } catch (error: any) {
        alert(error?.response?.data?.detail || '保存失败')
    } finally {
        saving.value = false
    }
}

const handleDelete = async (camera: CameraItem) => {
    if (!confirm(`确定删除 ${camera.name} 吗？`)) return
    try {
        await deleteCamera(camera.id)
        await loadData(true)
    } catch (error: any) {
        alert(error?.response?.data?.detail || '删除失败')
    }
}

const handleTest = async (camera: CameraItem) => {
    testingId.value = camera.id
    try {
        const res = await testCamera(camera.id)
        const mediamtx = res.data?.mediamtx
        const onvif = res.data?.onvif
        alert(`MediaMTX: ${mediamtx?.ok ? 'OK' : mediamtx?.detail || '失败'}\nONVIF: ${onvif?.ok ? 'OK' : onvif?.detail || '失败'}`)
    } catch (error: any) {
        alert(error?.response?.data?.detail || '测试失败')
    } finally {
        testingId.value = null
    }
}

const startPtz = async (action: PtzAction) => {
    if (!selectedCamera.value || !canControlPtz.value) return
    ptzAction.value = action
    try {
        await sendPtz(selectedCamera.value.id, action, ptzSpeed.value)
    } catch (error: any) {
        alert(error?.response?.data?.detail || '云台控制失败')
    }
}

const stopPtz = async () => {
    if (!selectedCamera.value || !ptzAction.value) return
    const cameraId = selectedCamera.value.id
    ptzAction.value = ''
    try {
        await sendPtz(cameraId, 'stop', ptzSpeed.value)
    } catch (error) {
        console.error(error)
    }
}

watch(selectedId, () => {
    loadStream()
})

onMounted(() => {
    loadData()
})
</script>

<template>
  <div class="camera-page flex min-h-screen flex-col gap-4 bg-slate-50 p-3 md:gap-6 md:p-8">
    <section class="camera-summary order-2 rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm md:order-1">
      <div class="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Module</div>
          <h2 class="mt-1 text-2xl font-semibold text-slate-900">实时监控</h2>
        </div>
        <div class="flex items-center gap-2">
          <button @click="loadData(true)" class="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-100">
            <RefreshCw class="h-4 w-4" :class="{ 'animate-spin': refreshing }" />
            刷新
          </button>
          <button @click="openCreate" class="inline-flex items-center gap-2 rounded-2xl bg-blue-500 px-4 py-3 text-sm font-medium text-white shadow-lg shadow-blue-500/20 transition hover:bg-blue-600">
            <Plus class="h-4 w-4" />
            添加摄像头
          </button>
        </div>
      </div>

      <div class="mt-6 grid gap-4 md:grid-cols-3">
        <div class="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Cameras</div>
          <div class="mt-3 text-3xl font-semibold text-slate-950">{{ cameras.length }}</div>
        </div>
        <div class="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Online</div>
          <div class="mt-3 text-3xl font-semibold text-slate-950">{{ enabledCount }}</div>
        </div>
        <div class="rounded-[24px] border border-slate-200 bg-slate-950 p-4 text-slate-100">
          <div class="text-xs uppercase tracking-[0.24em] text-slate-500">PTZ</div>
          <div class="mt-3 text-2xl font-semibold">{{ ptzCount }}</div>
        </div>
      </div>
    </section>

    <div class="order-1 grid gap-4 md:order-2 md:gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
      <section class="camera-live-card overflow-hidden rounded-[28px] border border-slate-200 bg-white shadow-sm">
        <div class="camera-live-header flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 p-4">
          <div class="min-w-0">
            <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Live</div>
            <h3 class="mt-1 truncate text-xl font-semibold text-slate-950">{{ selectedCamera?.name || '未选择摄像头' }}</h3>
          </div>
          <div class="flex items-center gap-2">
            <button class="rounded-xl border px-3 py-2 text-sm" :class="playerMode === 'webrtc' ? 'border-blue-200 bg-blue-50 text-blue-600' : 'border-slate-200 bg-white text-slate-600'" @click="playerMode = 'webrtc'">WebRTC</button>
            <button class="rounded-xl border px-3 py-2 text-sm" :class="playerMode === 'hls' ? 'border-blue-200 bg-blue-50 text-blue-600' : 'border-slate-200 bg-white text-slate-600'" @click="playerMode = 'hls'">HLS</button>
            <button class="rounded-xl border border-slate-200 bg-white p-2 text-slate-500 transition hover:border-blue-200 hover:text-blue-600" @click="loadStream" :disabled="streamLoading || !selectedCamera">
              <RefreshCw class="h-4 w-4" :class="{ 'animate-spin': streamLoading }" />
            </button>
          </div>
        </div>

        <div class="camera-player relative bg-slate-950">
          <div v-if="streamLoading" class="absolute inset-0 flex items-center justify-center text-slate-100">
            <Loader2 class="h-8 w-8 animate-spin" />
          </div>
          <div v-else-if="streamError" class="absolute inset-0 flex flex-col items-center justify-center gap-3 p-6 text-center text-slate-200">
            <Video class="h-12 w-12 text-slate-500" />
            <p>{{ streamError }}</p>
          </div>
          <div v-else-if="!selectedCamera" class="absolute inset-0 flex flex-col items-center justify-center gap-3 text-slate-400">
            <Cctv class="h-16 w-16 text-slate-600" />
            <p>暂无摄像头</p>
          </div>
          <iframe
            v-else-if="playerMode === 'webrtc' && stream?.webrtc_url"
            :key="stream.webrtc_url"
            :src="stream.webrtc_url"
            class="h-full w-full"
            allow="autoplay; fullscreen; picture-in-picture"
          />
          <iframe
            v-else-if="playerMode === 'hls' && stream?.hls_page_url"
            :key="stream.hls_page_url"
            :src="stream.hls_page_url"
            class="h-full w-full"
            allow="autoplay; fullscreen; picture-in-picture"
          />
        </div>
      </section>

      <aside class="camera-side space-y-4 md:space-y-6">
        <section class="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
          <div class="flex items-center justify-between gap-3">
            <div>
              <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Devices</div>
              <h3 class="mt-1 text-lg font-semibold text-slate-950">摄像头列表</h3>
            </div>
            <span class="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-sm text-slate-600">{{ cameras.length }} 路</span>
          </div>

          <div class="mt-4">
            <div v-if="loading" class="flex justify-center py-10">
              <Loader2 class="h-7 w-7 animate-spin text-blue-500" />
            </div>
            <div v-else-if="cameras.length === 0" class="flex flex-col items-center justify-center py-12 text-slate-400">
              <Cctv class="mb-3 h-12 w-12 text-slate-300" />
              <p>暂无摄像头</p>
            </div>
            <div v-else class="space-y-3">
              <button
                v-for="camera in cameras"
                :key="camera.id"
                type="button"
                class="w-full rounded-[24px] border p-4 text-left transition"
                :class="selectedId === camera.id ? 'border-blue-200 bg-blue-50' : 'border-slate-200 bg-slate-50 hover:bg-slate-100'"
                @click="selectCamera(camera)"
              >
                <div class="flex items-start justify-between gap-3">
                  <div class="min-w-0">
                    <div class="flex items-center gap-2">
                      <span class="h-2 w-2 rounded-full" :class="camera.enabled ? 'bg-emerald-500' : 'bg-slate-300'" />
                      <h4 class="truncate font-semibold text-slate-950">{{ camera.name }}</h4>
                    </div>
                    <p class="mt-2 truncate font-mono text-xs text-slate-500">{{ camera.mediamtx_path }}</p>
                    <p class="mt-1 text-xs text-slate-400">{{ camera.onvif_enabled ? 'ONVIF' : 'RTSP' }}</p>
                  </div>
                  <div class="flex shrink-0 items-center gap-1">
                    <button type="button" class="rounded-xl border border-slate-200 bg-white p-2 text-slate-500 hover:text-blue-600" @click.stop="handleTest(camera)">
                      <Loader2 v-if="testingId === camera.id" class="h-4 w-4 animate-spin" />
                      <Play v-else class="h-4 w-4" />
                    </button>
                    <button type="button" class="rounded-xl border border-slate-200 bg-white p-2 text-slate-500 hover:text-blue-600" @click.stop="openEdit(camera)">
                      <Pencil class="h-4 w-4" />
                    </button>
                    <button type="button" class="rounded-xl border border-slate-200 bg-white p-2 text-slate-500 hover:text-rose-600" @click.stop="handleDelete(camera)">
                      <Trash2 class="h-4 w-4" />
                    </button>
                  </div>
                </div>
              </button>
            </div>
          </div>
        </section>

        <section class="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm" :class="{ 'opacity-60': !canControlPtz }">
          <div class="flex items-center justify-between gap-3">
            <div>
              <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Control</div>
              <h3 class="mt-1 text-lg font-semibold text-slate-950">云台控制</h3>
            </div>
            <input v-model.number="ptzSpeed" class="w-24" type="range" min="0.05" max="1" step="0.05">
          </div>

          <div class="mt-5 grid grid-cols-3 gap-2">
            <button class="ptz-btn" :disabled="!canControlPtz" @pointerdown.prevent="startPtz('up_left')" @pointerup.prevent="stopPtz" @pointerleave.prevent="stopPtz"><ArrowUpLeft class="h-5 w-5" /></button>
            <button class="ptz-btn" :disabled="!canControlPtz" @pointerdown.prevent="startPtz('up')" @pointerup.prevent="stopPtz" @pointerleave.prevent="stopPtz"><ArrowUp class="h-5 w-5" /></button>
            <button class="ptz-btn" :disabled="!canControlPtz" @pointerdown.prevent="startPtz('up_right')" @pointerup.prevent="stopPtz" @pointerleave.prevent="stopPtz"><ArrowUpRight class="h-5 w-5" /></button>
            <button class="ptz-btn" :disabled="!canControlPtz" @pointerdown.prevent="startPtz('left')" @pointerup.prevent="stopPtz" @pointerleave.prevent="stopPtz"><ArrowLeft class="h-5 w-5" /></button>
            <button class="ptz-btn bg-slate-950 text-white" :disabled="!canControlPtz" @click="stopPtz"><CircleStop class="h-5 w-5" /></button>
            <button class="ptz-btn" :disabled="!canControlPtz" @pointerdown.prevent="startPtz('right')" @pointerup.prevent="stopPtz" @pointerleave.prevent="stopPtz"><ArrowRight class="h-5 w-5" /></button>
            <button class="ptz-btn" :disabled="!canControlPtz" @pointerdown.prevent="startPtz('down_left')" @pointerup.prevent="stopPtz" @pointerleave.prevent="stopPtz"><ArrowDownLeft class="h-5 w-5" /></button>
            <button class="ptz-btn" :disabled="!canControlPtz" @pointerdown.prevent="startPtz('down')" @pointerup.prevent="stopPtz" @pointerleave.prevent="stopPtz"><ArrowDown class="h-5 w-5" /></button>
            <button class="ptz-btn" :disabled="!canControlPtz" @pointerdown.prevent="startPtz('down_right')" @pointerup.prevent="stopPtz" @pointerleave.prevent="stopPtz"><ArrowDownRight class="h-5 w-5" /></button>
          </div>

          <div class="mt-3 grid grid-cols-2 gap-2">
            <button class="ptz-btn" :disabled="!canControlPtz" @pointerdown.prevent="startPtz('zoom_in')" @pointerup.prevent="stopPtz" @pointerleave.prevent="stopPtz">
              <ZoomIn class="h-5 w-5" />
            </button>
            <button class="ptz-btn" :disabled="!canControlPtz" @pointerdown.prevent="startPtz('zoom_out')" @pointerup.prevent="stopPtz" @pointerleave.prevent="stopPtz">
              <ZoomOut class="h-5 w-5" />
            </button>
          </div>
        </section>
      </aside>
    </div>

    <div v-if="showDialog" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4">
      <div class="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-[28px] border border-slate-200 bg-white shadow-[0_24px_60px_rgba(15,23,42,0.2)]">
        <div class="border-b border-slate-200 px-6 py-5">
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Form</div>
          <h2 class="mt-1 text-xl font-semibold text-slate-950">{{ editingId ? '编辑摄像头' : '添加摄像头' }}</h2>
        </div>
        <div class="grid gap-4 p-6 md:grid-cols-2">
          <label class="block">
            <span class="mb-1 block text-sm text-slate-500">名称</span>
            <input v-model="formData.name" class="w-full rounded-2xl border border-slate-200 px-4 py-3" type="text" placeholder="客厅摄像头">
          </label>
          <label class="block">
            <span class="mb-1 block text-sm text-slate-500">MediaMTX 路径</span>
            <input v-model="formData.mediamtx_path" class="w-full rounded-2xl border border-slate-200 px-4 py-3" type="text" placeholder="自动生成">
          </label>
          <label class="block md:col-span-2">
            <span class="mb-1 block text-sm text-slate-500">RTSP 地址</span>
            <input v-model="formData.rtsp_url" class="w-full rounded-2xl border border-slate-200 px-4 py-3" type="text" placeholder="rtsp://user:pass@host:554/stream1">
          </label>
          <label class="inline-flex items-center gap-3 rounded-2xl border border-slate-200 px-4 py-3">
            <input v-model="formData.enabled" type="checkbox" class="h-4 w-4">
            <span class="text-sm text-slate-600">启用摄像头</span>
          </label>
          <label class="inline-flex items-center gap-3 rounded-2xl border border-slate-200 px-4 py-3">
            <input v-model="formData.onvif_enabled" type="checkbox" class="h-4 w-4">
            <span class="text-sm text-slate-600">启用 ONVIF PTZ</span>
          </label>
          <label class="block">
            <span class="mb-1 block text-sm text-slate-500">ONVIF Host</span>
            <input v-model="formData.onvif_host" class="w-full rounded-2xl border border-slate-200 px-4 py-3" type="text" placeholder="192.168.1.179">
          </label>
          <label class="block">
            <span class="mb-1 block text-sm text-slate-500">ONVIF Port</span>
            <input v-model.number="formData.onvif_port" class="w-full rounded-2xl border border-slate-200 px-4 py-3" type="number" min="1" max="65535">
          </label>
          <label class="block">
            <span class="mb-1 block text-sm text-slate-500">ONVIF 用户名</span>
            <input v-model="formData.onvif_username" class="w-full rounded-2xl border border-slate-200 px-4 py-3" type="text" placeholder="admin">
          </label>
          <label class="block">
            <span class="mb-1 block text-sm text-slate-500">ONVIF 密码</span>
            <input v-model="formData.onvif_password" class="w-full rounded-2xl border border-slate-200 px-4 py-3" type="text">
          </label>
        </div>
        <div class="flex gap-3 border-t border-slate-200 p-6">
          <button @click="closeDialog" class="flex-1 rounded-2xl border border-slate-200 bg-white py-3 font-medium text-slate-600">取消</button>
          <button @click="handleSave" class="flex-1 rounded-2xl bg-blue-500 py-3 font-medium text-white shadow-lg shadow-blue-500/25" :disabled="saving">
            {{ saving ? '保存中' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.camera-player {
  aspect-ratio: 16 / 9;
}

.camera-player iframe {
  border: 0;
}

.ptz-btn {
  display: inline-flex;
  min-height: 44px;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  border: 1px solid #e5ebf3;
  background: #ffffff;
  color: #475569;
  transition: all 0.16s ease;
}

.ptz-btn:disabled {
  opacity: 0.45;
}

.ptz-btn:not(:disabled):hover {
  border-color: #9ec5ff;
  color: #2f7cf6;
}

@media (max-width: 768px) {
  .camera-page {
    gap: 12px;
    padding: 0 !important;
    background: #f7f9fc;
  }

  .camera-live-card {
    border-radius: 0 !important;
    border-left: 0;
    border-right: 0;
    border-top: 0;
  }

  .camera-live-header {
    padding: 10px 12px;
  }

  .camera-live-header h3 {
    max-width: 52vw;
    font-size: 1rem;
  }

  .camera-player {
    height: 62vh;
    height: min(62svh, 520px);
    min-height: 320px;
    aspect-ratio: auto;
  }

  .camera-summary,
  .camera-side {
    margin-left: 12px;
    margin-right: 12px;
  }
}
</style>
