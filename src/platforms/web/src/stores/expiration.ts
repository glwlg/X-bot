import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
    getExpirations,
    getExpirationStats,
    getCategories,
    createExpiration,
    updateExpiration,
    deleteExpiration
} from '@/api/expiration'
import type { ExpirationRecord, ExpirationListRequest, ExpirationStats } from '@/types/expiration'

export const useExpirationStore = defineStore('expiration', () => {
    // State
    const records = ref<ExpirationRecord[]>([])
    const total = ref(0)
    const loading = ref(false)
    const stats = ref<ExpirationStats | null>(null)
    const categories = ref<string[]>([])

    // Query Parameters
    const page = ref(1)
    const pageSize = ref(10) // 默认10条，日历视图可能需要更多
    const searchKeyword = ref('')
    const categoryFilter = ref('')
    const statusFilter = ref('')
    const sortBy = ref('expire_at')
    const sortOrder = ref<'asc' | 'desc'>('asc')

    // Actions
    async function fetchRecords() {
        loading.value = true
        try {
            const params: ExpirationListRequest = {
                page: page.value,
                page_size: pageSize.value,
                search: searchKeyword.value || undefined,
                category: categoryFilter.value || undefined,
                status: statusFilter.value || undefined,
                sort_by: sortBy.value,
                sort_order: sortOrder.value
            }
            const response = await getExpirations(params)
            records.value = response.data.items
            total.value = response.data.total
        } finally {
            loading.value = false
        }
    }

    async function fetchStats() {
        const response = await getExpirationStats()
        stats.value = response.data
    }

    async function fetchCategories() {
        const response = await getCategories()
        categories.value = response.data.categories
    }

    async function create(data: any) {
        await createExpiration(data)
        await fetchRecords()
        await fetchStats()
        await fetchCategories()
    }

    async function update(id: number, data: any) {
        await updateExpiration(id, data)
        await fetchRecords()
        await fetchStats()
    }

    async function remove(id: number) {
        await deleteExpiration(id)
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
        remove
    }
})
