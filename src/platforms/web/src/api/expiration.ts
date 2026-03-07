import request from './request'
import type {
    ExpirationListRequest,
    ExpirationListResponse,
    ExpirationRecord,
    ExpirationRecordCreate,
    ExpirationRecordUpdate,
    ExpirationStats
} from '@/types/expiration'

const BASE_URL = '/expiration'

export function getExpirations(params: ExpirationListRequest) {
    return request.get<ExpirationListResponse>(`${BASE_URL}/list`, { params })
}

export function getExpirationStats() {
    return request.get<ExpirationStats>(`${BASE_URL}/stats`)
}

export function getCategories() {
    return request.get<{ categories: string[] }>(`${BASE_URL}/categories`)
}

export function createExpiration(data: ExpirationRecordCreate) {
    return request.post(`${BASE_URL}/create`, data)
}

export function updateExpiration(id: number, data: ExpirationRecordUpdate) {
    return request.put(`${BASE_URL}/${id}`, data)
}

export function deleteExpiration(id: number) {
    return request.delete(`${BASE_URL}/${id}`)
}

export function getExpiration(id: number) {
    return request.get<ExpirationRecord>(`${BASE_URL}/${id}`)
}
