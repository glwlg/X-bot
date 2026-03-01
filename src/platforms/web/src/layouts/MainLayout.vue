<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter, RouterLink, RouterView } from 'vue-router'
import { Menu, X, LogOut, LayoutDashboard, User } from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { storeToRefs } from 'pinia'
import logoSvg from '@/assets/images/logo.svg'
import { ThemeToggle } from '@/components/ThemeToggle'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const { user: currentUser } = storeToRefs(authStore)

const sidebarOpen = ref(true)

const menuItems = computed(() => {
  return [
    {
      path: '/home',
      label: '首页',
      icon: LayoutDashboard,
    }
  ]
})

function isActive(path: string) { return route.path.startsWith(path) }

const handleLogout = async () => {
  await authStore.logout()
  router.push('/login')
}

const roleLabels: Record<string, string> = {
  admin: '管理员',
  operator: '运维人员',
  viewer: '访客'
}

onMounted(async () => {
  if (!currentUser.value) {
    await authStore.fetchUser()
  }
})
</script>

<template>
  <div class="flex h-screen bg-theme-primary">
    <!-- Sidebar -->
    <aside
      :class="['relative flex flex-col bg-theme-elevated border-r border-theme-primary transition-all duration-300', sidebarOpen ? 'w-60' : 'w-[72px]']"
    >
      <!-- Logo -->
      <div class="h-16 flex items-center px-5 border-b border-theme-primary">
        <img :src="logoSvg" alt="Template" class="w-9 h-9 shadow-sm" />
        <span v-if="sidebarOpen" class="ml-3 text-lg font-bold text-theme-primary">Template App</span>
      </div>

      <!-- Toggle -->
      <button
        @click="sidebarOpen = !sidebarOpen"
        class="absolute -right-3 top-20 w-6 h-6 rounded-full bg-theme-elevated border border-theme-primary flex items-center justify-center text-theme-muted hover:text-primary-600 hover:border-primary-300 transition-all z-10"
      >
        <Menu v-if="!sidebarOpen" class="h-3.5 w-3.5" />
        <X v-else class="h-3.5 w-3.5" />
      </button>

      <!-- Navigation -->
      <nav class="flex-1 py-5 px-3 space-y-1 overflow-y-auto">
        <template v-for="item in menuItems" :key="item.path">
            <RouterLink
                :to="item.path"
                :class="[
                    'flex items-center gap-3 px-3 py-3 my-1 rounded-lg font-medium transition-all',
                    isActive(item.path)
                    ? 'bg-primary-100/50 text-primary-600 dark:bg-primary-900/30 dark:text-primary-400'
                    : 'text-theme-secondary hover:text-theme-primary hover:bg-theme-secondary'
                ]"
            >
                <component :is="item.icon" class="h-5 w-5 flex-shrink-0" />
                <span v-if="sidebarOpen" class="truncate">{{ item.label }}</span>
            </RouterLink>
        </template>
      </nav>

      <!-- Theme Toggle & User & Logout Footer -->
      <div class="p-3 border-t border-theme-primary">
        <div class="mb-3 px-1">
          <div v-if="sidebarOpen" class="flex items-center gap-2">
            <ThemeToggle variant="dropdown" show-label size="sm" class-name="w-full justify-start" />
          </div>
          <div v-else class="flex justify-center">
            <ThemeToggle variant="icon" size="sm" />
          </div>
        </div>

        <div v-if="currentUser && sidebarOpen" class="flex items-center gap-3 px-3 py-2 mb-2">
          <div class="w-8 h-8 rounded-full bg-violet-100 flex items-center justify-center">
            <User class="w-4 h-4 text-violet-600" />
          </div>
          <div class="flex-1 overflow-hidden">
            <p class="text-sm font-medium text-theme-secondary truncate">{{ currentUser.display_name || currentUser.email }}</p>
            <p class="text-xs text-theme-muted">{{ roleLabels[currentUser.role] || currentUser.role }}</p>
          </div>
        </div>
        <button
          @click="handleLogout"
          v-if="sidebarOpen"
          class="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-theme-muted hover:text-red-600 hover:bg-red-50 transition-colors"
        >
          <LogOut class="h-5 w-5" />
          <span>退出登录</span>
        </button>
        <button
          @click="handleLogout"
          v-else
          class="w-full flex items-center justify-center p-2.5 rounded-lg text-theme-muted hover:text-red-600 hover:bg-red-50 transition-colors"
        >
          <LogOut class="h-5 w-5" />
        </button>
      </div>
    </aside>

    <!-- Main Content -->
    <main class="flex-1 overflow-auto">
      <RouterView />
    </main>
  </div>
</template>
