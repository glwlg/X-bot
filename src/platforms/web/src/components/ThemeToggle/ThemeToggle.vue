<script setup lang="ts">
/**
 * 主题切换组件
 * 
 * 提供多种主题切换方式：
 * 1. 快捷切换按钮：在亮色/暗色之间快速切换
 * 2. 下拉选择器：选择亮色/暗色/跟随系统
 * 3. 图标按钮：仅显示图标，点击切换
 * 
 * 使用方式：
 * <ThemeToggle />
 * <ThemeToggle variant="dropdown" />
 * <ThemeToggle variant="icon" />
 * <ThemeToggle variant="button" show-label />
 */
import { computed, ref } from 'vue'
import { Sun, Moon, Monitor, Check } from 'lucide-vue-next'
import { useThemeStore } from '@/stores/theme'
import { Button } from '@/components/ui/button'

// 组件属性定义
interface Props {
    /**
     * 组件变体类型
     * - button: 按钮形式（带图标和可选文字）
     * - icon: 纯图标按钮
     * - dropdown: 下拉选择器
     */
    variant?: 'button' | 'icon' | 'dropdown'
    
    /**
     * 是否显示标签文字（仅button和dropdown有效）
     */
    showLabel?: boolean
    
    /**
     * 按钮尺寸
     */
    size?: 'sm' | 'md' | 'lg'
    
    /**
     * 自定义类名
     */
    className?: string
}

const props = withDefaults(defineProps<Props>(), {
    variant: 'button',
    showLabel: false,
    size: 'md',
    className: ''
})

// 主题 store
const themeStore = useThemeStore()

// 下拉菜单状态
const isDropdownOpen = ref(false)

// 计算属性
const currentIcon = computed(() => {
    switch (themeStore.themeMode) {
        case 'dark':
            return Moon
        case 'light':
            return Sun
        case 'auto':
        default:
            return Monitor
    }
})

const currentLabel = computed(() => {
    return themeStore.themeLabel
})

const iconSize = computed(() => {
    const sizes: Record<string, number> = {
        sm: 14,
        md: 16,
        lg: 20
    }
    return sizes[props.size] || 16
})

// 按钮尺寸映射
const buttonSizeClass = computed(() => {
    const sizes: Record<string, string> = {
        sm: 'h-8 px-2 text-xs',
        md: 'h-9 px-3 text-sm',
        lg: 'h-10 px-4 text-base'
    }
    return sizes[props.size] || sizes.md
})

// 方法
function toggleTheme() {
    themeStore.toggleTheme()
}

function setThemeMode(mode: 'light' | 'dark' | 'auto') {
    themeStore.setThemeMode(mode)
    isDropdownOpen.value = false
}

function isActive(mode: string): boolean {
    return themeStore.themeMode === mode
}

function toggleDropdown() {
    isDropdownOpen.value = !isDropdownOpen.value
}

// 点击外部关闭下拉菜单
function onClickOutside(event: MouseEvent) {
    const target = event.target as HTMLElement
    if (!target.closest('.theme-dropdown')) {
        isDropdownOpen.value = false
    }
}

// 添加/移除全局点击监听
if (typeof window !== 'undefined') {
    window.addEventListener('click', onClickOutside)
}
</script>

<template>
    <!-- 按钮变体 -->
    <template v-if="variant === 'button'">
        <Button
            variant="ghost"
            @click="toggleTheme"
            :class="[buttonSizeClass, 'gap-2', className]"
            :title="currentLabel"
        >
            <component :is="currentIcon" :size="iconSize" />
            <span v-if="showLabel">{{ currentLabel }}</span>
        </Button>
    </template>

    <!-- 纯图标变体 -->
    <template v-else-if="variant === 'icon'">
        <Button
            variant="ghost"
            @click="toggleTheme"
            :class="['p-2', className]"
            :title="currentLabel"
        >
            <component :is="currentIcon" :size="iconSize" />
        </Button>
    </template>

    <!-- 下拉选择器变体 -->
    <template v-else-if="variant === 'dropdown'">
        <div class="theme-dropdown relative inline-block">
            <Button
                variant="ghost"
                @click="toggleDropdown"
                :class="[buttonSizeClass, 'gap-2', className]"
            >
                <component :is="currentIcon" :size="iconSize" />
                <span v-if="showLabel">{{ currentLabel }}</span>
                <svg class="w-4 h-4 ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
            </Button>
            
            <!-- 下拉菜单 -->
            <div
                v-if="isDropdownOpen"
                class="absolute z-50 mt-1 w-40 rounded-md shadow-lg bg-theme-elevated border border-theme-primary py-1"
            >
                <button
                    @click="setThemeMode('light')"
                    class="w-full flex items-center px-4 py-2 text-sm text-theme-secondary hover:bg-theme-secondary transition-colors"
                    :class="{ 'bg-theme-secondary': isActive('light') }"
                >
                    <Sun class="mr-2 h-4 w-4" />
                    <span>浅色模式</span>
                    <Check v-if="isActive('light')" class="ml-auto h-4 w-4" />
                </button>
                <button
                    @click="setThemeMode('dark')"
                    class="w-full flex items-center px-4 py-2 text-sm text-theme-secondary hover:bg-theme-secondary transition-colors"
                    :class="{ 'bg-theme-secondary': isActive('dark') }"
                >
                    <Moon class="mr-2 h-4 w-4" />
                    <span>深色模式</span>
                    <Check v-if="isActive('dark')" class="ml-auto h-4 w-4" />
                </button>
                <button
                    @click="setThemeMode('auto')"
                    class="w-full flex items-center px-4 py-2 text-sm text-theme-secondary hover:bg-theme-secondary transition-colors"
                    :class="{ 'bg-theme-secondary': isActive('auto') }"
                >
                    <Monitor class="mr-2 h-4 w-4" />
                    <span>跟随系统</span>
                    <Check v-if="isActive('auto')" class="ml-auto h-4 w-4" />
                </button>
            </div>
        </div>
    </template>
</template>

<style scoped>
/* 下拉菜单动画 */
.theme-dropdown > div:last-child {
    animation: dropdown-enter 0.1s ease-out;
}

@keyframes dropdown-enter {
    from {
        opacity: 0;
        transform: translateY(-4px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}
</style>
