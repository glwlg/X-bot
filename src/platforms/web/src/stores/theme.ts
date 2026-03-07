import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'

// 主题类型定义
type ThemeMode = 'light' | 'dark' | 'auto'

// 实际应用的主题类型
export type AppliedTheme = 'light' | 'dark'

// 本地存储键名
const STORAGE_KEY = 'theme_preference'

export const useThemeStore = defineStore('theme', () => {
    // ============ State ============
    
    // 用户选择的主题模式：light | dark | auto
    const themeMode = ref<ThemeMode>('auto')
    
    // 实际应用的主题（auto模式下根据系统偏好决定）
    const appliedTheme = ref<AppliedTheme>('light')
    
    // 系统偏好媒体查询
    const mediaQuery = ref<MediaQueryList | null>(null)

    // ============ Getters ============
    
    /**
     * 是否为暗黑模式
     */
    const isDark = computed(() => appliedTheme.value === 'dark')
    
    /**
     * 是否为亮色模式
     */
    const isLight = computed(() => appliedTheme.value === 'light')
    
    /**
     * 当前主题标签（用于显示）
     */
    const themeLabel = computed(() => {
        const labels: Record<ThemeMode, string> = {
            light: '浅色模式',
            dark: '深色模式',
            auto: '跟随系统'
        }
        return labels[themeMode.value]
    })
    
    /**
     * 当前应用主题的标签
     */
    const appliedThemeLabel = computed(() => {
        return appliedTheme.value === 'dark' ? '深色模式' : '浅色模式'
    })

    // ============ Actions ============
    
    /**
     * 设置主题模式
     */
    function setThemeMode(mode: ThemeMode) {
        themeMode.value = mode
        updateAppliedTheme()
        savePreference()
    }
    
    /**
     * 切换到浅色模式
     */
    function setLightMode() {
        setThemeMode('light')
    }
    
    /**
     * 切换到深色模式
     */
    function setDarkMode() {
        setThemeMode('dark')
    }
    
    /**
     * 切换到自动模式
     */
    function setAutoMode() {
        setThemeMode('auto')
    }
    
    /**
     * 切换主题（light <-> dark，仅在手动模式下有效）
     */
    function toggleTheme() {
        if (themeMode.value === 'auto') {
            // 如果从auto切换，根据当前应用的主题切换到相反模式
            setThemeMode(appliedTheme.value === 'dark' ? 'light' : 'dark')
        } else {
            // 直接切换
            setThemeMode(themeMode.value === 'dark' ? 'light' : 'dark')
        }
    }
    
    /**
     * 更新实际应用的主题
     */
    function updateAppliedTheme() {
        if (themeMode.value === 'auto') {
            // 根据系统偏好
            const prefersDark = mediaQuery.value?.matches ?? false
            appliedTheme.value = prefersDark ? 'dark' : 'light'
        } else {
            appliedTheme.value = themeMode.value
        }
        applyThemeToDOM()
    }
    
    /**
     * 将主题应用到DOM
     */
    function applyThemeToDOM() {
        const html = document.documentElement
        
        if (appliedTheme.value === 'dark') {
            html.classList.add('dark')
            html.classList.remove('light')
        } else {
            html.classList.add('light')
            html.classList.remove('dark')
        }
        
        // 设置 color-scheme 以影响原生组件（如滚动条、表单控件等）
        html.style.colorScheme = appliedTheme.value
    }
    
    /**
     * 保存偏好设置到本地存储
     */
    function savePreference() {
        try {
            const data = {
                themeMode: themeMode.value,
                timestamp: Date.now()
            }
            localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
        } catch (_err) {
            // 静默处理存储错误
        }
    }
    
    /**
     * 从本地存储加载偏好设置
     */
    function loadPreference() {
        try {
            const stored = localStorage.getItem(STORAGE_KEY)
            if (stored) {
                const data = JSON.parse(stored)
                if (data.themeMode) {
                    themeMode.value = data.themeMode
                }
            }
        } catch (_err) {
            // 静默处理读取错误
        }
    }
    
    /**
     * 初始化系统偏好监听
     */
    function initMediaQuery() {
        if (typeof window === 'undefined') return
        
        // 创建媒体查询
        mediaQuery.value = window.matchMedia('(prefers-color-scheme: dark)')
        
        // 监听系统主题变化
        const handleChange = (_event: MediaQueryListEvent | MediaQueryList) => {
            if (themeMode.value === 'auto') {
                updateAppliedTheme()
            }
        }
        
        // 现代浏览器使用 addEventListener
        if (mediaQuery.value.addEventListener) {
            mediaQuery.value.addEventListener('change', handleChange)
        } else {
            // 旧版浏览器兼容
            mediaQuery.value.addListener(handleChange)
        }
    }
    
    /**
     * 初始化主题系统
     */
    function init() {
        loadPreference()
        initMediaQuery()
        updateAppliedTheme()
    }

    // ============ Watch ============
    
    // 监听应用主题变化
    watch(appliedTheme, (newTheme) => {
        // 触发自定义事件，供其他组件监听
        if (typeof window !== 'undefined') {
            window.dispatchEvent(new CustomEvent('themechange', { 
                detail: { theme: newTheme } 
            }))
        }
    })

    return {
        // State
        themeMode,
        appliedTheme,
        
        // Getters
        isDark,
        isLight,
        themeLabel,
        appliedThemeLabel,
        
        // Actions
        setThemeMode,
        setLightMode,
        setDarkMode,
        setAutoMode,
        toggleTheme,
        init,
        updateAppliedTheme,
        savePreference,
        loadPreference
    }
})
