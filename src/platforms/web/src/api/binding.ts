import request from './request'

export interface ChannelBinding {
    id: number
    platform: string
    platform_user_id: string
}

export const listMyBindings = () =>
    request.get<ChannelBinding[]>('/binding/me')

export const saveMyBinding = (payload: { platform: string; platform_user_id: string }) =>
    request.post<{ success: boolean; message: string }>('/binding/me', payload)

export const deleteMyBinding = (bindingId: number) =>
    request.delete<{ success: boolean }>(`/binding/me/${bindingId}`)
