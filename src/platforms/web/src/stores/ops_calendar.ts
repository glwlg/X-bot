
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
    getEvents,
    getStats,
    getCategories,
    createEvent,
    updateEvent,
    deleteEvent,
    renewEvent
} from '@/api/ops_calendar'
import type { CalendarEvent, CalendarListRequest, CalendarStats } from '@/types/ops_calendar'

export const useOpsCalendarStore = defineStore('ops-calendar', () => {
    // State
    const records = ref<CalendarEvent[]>([])
    const total = ref(0)
    const loading = ref(false)
    const stats = ref<CalendarStats | null>(null)
    const categories = ref<string[]>([])

    // Query Parameters
    const page = ref(1)
    const pageSize = ref(10)
    const searchKeyword = ref('')
    const categoryFilter = ref('')
    const statusFilter = ref('')
    const sortBy = ref('expire_at')
    const sortOrder = ref<'asc' | 'desc'>('asc')

    // Actions
    async function fetchRecords() {
        loading.value = true
        try {
            const params: CalendarListRequest = {
                page: page.value,
                page_size: pageSize.value,
                search: searchKeyword.value || undefined,
                category: categoryFilter.value || undefined,
                status: statusFilter.value || undefined,
                sort_by: sortBy.value,
                sort_order: sortOrder.value
            }
            const response = await getEvents(params)
            records.value = response.data.items
            total.value = response.data.total
        } finally {
            loading.value = false
        }
    }

    async function fetchStats() {
        const response = await getStats()
        stats.value = response.data
    }

    async function fetchCategories() {
        const response = await getCategories()
        categories.value = response.data.categories
    }

    async function create(data: any) {
        await createEvent(data)
        await fetchRecords()
        await fetchStats()
        await fetchCategories()
    }

    async function update(id: number, data: any) {
        await updateEvent(id, data)
        await fetchRecords()
        await fetchStats()
    }

    async function remove(id: number) {
        await deleteEvent(id)
        await fetchRecords()
        await fetchStats()
    }

    async function renew(id: number, remarks?: string) {
        await renewEvent(id, remarks)
        await fetchRecords()
        await fetchStats()
    }

    return {
        records,
        total,
        loading,
        stats,
        categories,
        page,
        pageSize,
        searchKeyword,
        categoryFilter,
        statusFilter,
        sortBy,
        sortOrder,
        fetchRecords,
        fetchStats,
        fetchCategories,
        create,
        update,
        remove,
        renew
    }
})
