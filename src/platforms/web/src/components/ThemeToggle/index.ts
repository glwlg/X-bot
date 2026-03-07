/**
 * 主题切换组件导出
 * 
 * 使用示例：
 * ```vue
 * <template>
 *   <!-- 默认按钮形式 -->
 *   <ThemeToggle />
 *   
 *   <!-- 纯图标按钮 -->
 *   <ThemeToggle variant="icon" />
 *   
 *   <!-- 下拉选择器 -->
 *   <ThemeToggle variant="dropdown" show-label />
 *   
 *   <!-- 自定义尺寸 -->
 *   <ThemeToggle size="sm" />
 *   <ThemeToggle size="lg" />
 * </template>
 * 
 * <script setup>
 * import { ThemeToggle } from '@/components/ThemeToggle'
 * </script>
 * ```
 */

export { default as ThemeToggle } from './ThemeToggle.vue'

// 重新导出类型（如果需要）
export type { AppliedTheme } from '@/stores/theme'
