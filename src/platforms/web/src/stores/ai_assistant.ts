/**
 * AI Assistant Store
 * 管理 AI 助手的页面上下文和 UI 状态
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

// 统一的页面上下文接口
interface PageContext {
    page: string        // 页面标识: 'asset', 'topology', 'alerts', 'sla', 'calendar'
    summary: string     // 页面数据摘要 (直接发送给 AI)
    hasIssues?: boolean // 是否存在问题（用于头像表情）
    issueLevel?: 'warning' | 'critical'  // 问题级别
}

// 告警统计 (仅用于 UI 状态，不发送给 AI)
interface AlertStats {
    activeCount: number
    criticalCount: number
    warningCount: number
}

export const useAiAssistantStore = defineStore('aiAssistant', () => {
    // 当前页面上下文 (各页面主动注入)
    const pageContext = ref<PageContext | null>(null)

    // 告警统计 (后台轮询，仅用于头像表情)
    const alertStats = ref<AlertStats | null>(null)

    // 用户确认状态
    const isAlertAcknowledged = ref(false)

    // 系统状态
    const hasActiveAlerts = computed(() => {
        return (alertStats.value?.criticalCount || 0) > 0
    })

    const hasCriticalIssues = computed(() => {
        const criticalAlerts = alertStats.value?.criticalCount || 0
        const pageHasCritical = pageContext.value?.issueLevel === 'critical'
        return criticalAlerts > 0 || pageHasCritical
    })

    // 推荐的头像状态
    const recommendedAvatarState = computed(() => {
        // 1. 页面上下文优先
        if (pageContext.value?.hasIssues) {
            if (pageContext.value.issueLevel === 'critical') return 'alert'
            return 'worried'
        }

        // 2. 如果用户已确认告警，保持平静
        if (isAlertAcknowledged.value) {
            return 'idle'
        }

        // 3. 全局告警状态
        if (hasActiveAlerts.value) return 'alert'

        // 4. 有页面上下文时显示开心
        if (pageContext.value) return 'happy'

        // 默认状态
        return 'idle'
    })

    // 设置页面上下文 (各业务页面调用)
    function setPageContext(context: PageContext) {
        pageContext.value = context
        // 重置告警确认状态，让头像重新反映当前页面状态
        isAlertAcknowledged.value = false
    }

    // 清除页面上下文 (路由切换时调用)
    function clearPageContext() {
        pageContext.value = null
    }

    // 更新告警统计 (仅用于UI状态)
    function setAlertStats(data: AlertStats) {
        alertStats.value = data
    }

    // 确认告警
    function acknowledgeAlert() {
        isAlertAcknowledged.value = true
    }

    // 获取页面数据摘要 (发送给 AI)
    function getPageDataSummary(): string {
        return pageContext.value?.summary || ''
    }

    return {
        pageContext,
        alertStats,
        hasActiveAlerts,
        hasCriticalIssues,
        recommendedAvatarState,
        setPageContext,
        clearPageContext,
        setAlertStats,
        acknowledgeAlert,
        getPageDataSummary
    }
})
