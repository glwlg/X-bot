/**
 * 资产管理 Store
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { AssetInfo, AssetStatsResponse } from '@/types/asset'
import { getAssetList, getAssetStats, type AssetListParams } from '@/api/asset'

export const useAssetStore = defineStore('asset', () => {
    // 状态
    const assets = ref<AssetInfo[]>([])
    const total = ref(0)
    const page = ref(1)
    const pageSize = ref(10)
    const loading = ref(false)
    const stats = ref<AssetStatsResponse | null>(null)
    const selectedIds = ref<Set<number>>(new Set())

    // 计算属性
    const hasSelection = computed(() => selectedIds.value.size > 0)
    const selectionCount = computed(() => selectedIds.value.size)

    // 方法
    async function fetchAssets(params: AssetListParams = {}) {
        loading.value = true
        try {
            const response = await getAssetList({
                page: params.page || page.value,
                page_size: params.page_size || pageSize.value,
                ...params,
            })
            assets.value = response.data.items
            total.value = response.data.total
            page.value = response.data.page
            pageSize.value = response.data.page_size
        } finally {
            loading.value = false
        }
    }

    async function fetchStats() {
        const response = await getAssetStats()
        stats.value = response.data
    }

    function toggleSelection(id: number) {
        if (selectedIds.value.has(id)) {
            selectedIds.value.delete(id)
        } else {
            selectedIds.value.add(id)
        }
    }

    function selectAll() {
        assets.value.forEach(asset => selectedIds.value.add(asset.id))
    }

    function clearSelection() {
        selectedIds.value.clear()
    }

    return {
        // 状态
        assets,
        total,
        page,
        pageSize,
        loading,
        stats,
        selectedIds,
        // 计算属性
        hasSelection,
        selectionCount,
        // 方法
        fetchAssets,
        fetchStats,
        toggleSelection,
        selectAll,
        clearSelection,
    }
})
