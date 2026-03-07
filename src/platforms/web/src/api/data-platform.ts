
import request from './request'

export interface DataAsset {
    name: string
    topic: string
    count: number
}

export interface DataPlatformAssets {
    catalog_data: DataAsset[]
    master_data: DataAsset[]
    metrics_data: DataAsset[]
}

export const getDataPlatformAssets = () => {
    return request<DataPlatformAssets>({
        url: '/data-platform/assets',
        method: 'get'
    })
}
