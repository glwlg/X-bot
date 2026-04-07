import request from './request'

export interface CredentialEntry {
    service: string
    id: string
    name: string
    data: Record<string, unknown>
    created_at: string
    updated_at: string
    is_default: boolean
}

export interface CredentialService {
    service: string
    default_entry_id: string
    entries: CredentialEntry[]
}

export const listMyCredentials = () =>
    request.get<CredentialService[]>('/auth/me/credentials')

export const listMyCredentialsByService = (service: string) =>
    request.get<CredentialEntry[]>(`/auth/me/credentials/${service}`)

export const createMyCredential = (
    service: string,
    payload: { name: string; data: Record<string, unknown>; is_default?: boolean },
) => request.post<CredentialEntry>(`/auth/me/credentials/${service}`, payload)

export const updateMyCredential = (
    service: string,
    credentialId: string,
    payload: { name?: string; data?: Record<string, unknown>; is_default?: boolean },
) => request.patch<CredentialEntry>(`/auth/me/credentials/${service}/${credentialId}`, payload)

export const setMyDefaultCredential = (service: string, credentialId: string) =>
    request.post<CredentialEntry>(`/auth/me/credentials/${service}/${credentialId}/default`)

export const deleteMyCredential = (service: string, credentialId: string) =>
    request.delete<{ success: boolean }>(`/auth/me/credentials/${service}/${credentialId}`)
