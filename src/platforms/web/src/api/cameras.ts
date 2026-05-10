import request from './request'

export interface CameraItem {
    id: number
    name: string
    enabled: boolean
    mediamtx_path: string
    rtsp_configured: boolean
    rtsp_url: string
    onvif_enabled: boolean
    onvif_host: string
    onvif_port: number
    onvif_username: string
    onvif_password: string
    onvif_configured: boolean
    created_at: string | null
    updated_at: string | null
}

export interface CameraPayload {
    name: string
    rtsp_url?: string
    enabled: boolean
    mediamtx_path?: string
    onvif_enabled: boolean
    onvif_host?: string
    onvif_port?: number
    onvif_username?: string
    onvif_password?: string
}

export interface StreamToken {
    token: string
    expires_at: string
    ttl_seconds: number
    path: string
    webrtc_url: string
    webrtc_whep_url: string
    hls_page_url: string
    hls_url: string
}

export type PtzAction =
    | 'up'
    | 'down'
    | 'left'
    | 'right'
    | 'up_left'
    | 'up_right'
    | 'down_left'
    | 'down_right'
    | 'zoom_in'
    | 'zoom_out'
    | 'stop'

export const listCameras = () => request.get<CameraItem[]>('/cameras')

export const createCamera = (payload: CameraPayload) => request.post<CameraItem>('/cameras', payload)

export const updateCamera = (id: number, payload: Partial<CameraPayload>) =>
    request.put<CameraItem>(`/cameras/${id}`, payload)

export const deleteCamera = (id: number) => request.delete(`/cameras/${id}`)

export const createStreamToken = (id: number) =>
    request.post<StreamToken>(`/cameras/${id}/stream-token`)

export const testCamera = (id: number) => request.post(`/cameras/${id}/test`)

export const sendPtz = (id: number, action: PtzAction, speed: number) =>
    request.post(`/cameras/${id}/ptz`, { action, speed })
