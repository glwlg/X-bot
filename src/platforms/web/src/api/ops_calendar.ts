
import request from './request'
import type {
    CalendarListRequest,
    CalendarListResponse,
    CalendarEvent,
    CalendarEventCreate,
    CalendarEventUpdate,
    CalendarStats
} from '@/types/ops_calendar'

const BASE_URL = '/ops-calendar'

export function getEvents(params: CalendarListRequest) {
    return request.get<CalendarListResponse>(`${BASE_URL}/list`, { params })
}

export function getStats() {
    return request.get<CalendarStats>(`${BASE_URL}/stats`)
}

export function getCategories() {
    return request.get<{ categories: string[] }>(`${BASE_URL}/categories`)
}

export function createEvent(data: CalendarEventCreate) {
    return request.post(`${BASE_URL}/create`, data)
}

export function updateEvent(id: number, data: CalendarEventUpdate) {
    return request.put(`${BASE_URL}/${id}`, data)
}

export function deleteEvent(id: number) {
    return request.delete(`${BASE_URL}/${id}`)
}

export function getEvent(id: number) {
    return request.get<CalendarEvent>(`${BASE_URL}/${id}`)
}

export function renewEvent(id: number, remarks?: string) {
    const query = remarks ? `?remarks=${encodeURIComponent(remarks)}` : '';
    return request.post(`${BASE_URL}/${id}/renew${query}`)
}
