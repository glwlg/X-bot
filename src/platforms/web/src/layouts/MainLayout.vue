<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import {
    Activity,
    Bell,
    Cable,
    CircleHelp,
    Gauge,
    HeartPulse,
    KeyRound,
    LayoutGrid,
    Link2,
    LogOut,
    Menu,
    MessageSquareText,
    Puzzle,
    Radio,
    Search,
    Settings2,
    ShieldUser,
    X,
    Zap,
} from 'lucide-vue-next'

import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const authStore = useAuthStore()

const isSidebarOpen = ref(false)
const isMobile = ref(false)

const checkMobile = () => {
    isMobile.value = window.innerWidth <= 1024
    if (!isMobile.value) {
        isSidebarOpen.value = true
    }
}

const toggleSidebar = () => {
    isSidebarOpen.value = !isSidebarOpen.value
}

const closeSidebar = () => {
    if (isMobile.value) {
        isSidebarOpen.value = false
    }
}

onMounted(() => {
    checkMobile()
    window.addEventListener('resize', checkMobile)
})

onUnmounted(() => {
    window.removeEventListener('resize', checkMobile)
})

const isHomeRoute = computed(() =>
    route.path === '/home' || route.path.startsWith('/home/')
)

const identityPrimary = computed(() =>
    authStore.user?.display_name || authStore.user?.username || authStore.user?.email || '未登录'
)

const identityInitial = computed(() => {
    const source = String(identityPrimary.value).trim()
    return source ? source.charAt(0).toUpperCase() : 'I'
})

const showIdentityEmail = computed(() =>
    Boolean(authStore.user?.email) && authStore.user?.email !== identityPrimary.value
)

const handleLogout = async () => {
    await authStore.logout()
    window.location.href = '/login'
}

const etherealPrimaryNav = computed(() => [
    { label: '控制面板', to: '/home', icon: LayoutGrid },
    { label: '聊天对话', to: '/chat', icon: MessageSquareText },
    { label: '模块绑定', to: '/bindings', icon: Link2 },
    { label: '凭据管理', to: '/credentials', icon: KeyRound },
    { label: '订阅源', to: '/modules/rss', icon: Radio },
    { label: '任务调度', to: '/modules/scheduler', icon: Cable },
    { label: '心跳监控', to: '/modules/monitor', icon: HeartPulse },
    { label: '市场追踪', to: '/modules/watchlist', icon: Activity },
])

const etherealAdminNav = computed(() => {
    const items = []

    if (authStore.isAdmin) {
        items.push({ label: '运行配置', to: '/admin/runtime', icon: Zap })
        items.push({ label: '模型配置', to: '/admin/models', icon: Settings2 })
    }

    if (authStore.isOperator) {
        items.push({ label: '用户管理', to: '/admin/users', icon: ShieldUser })
        items.push({ label: '技能管理', to: '/admin/skills', icon: Puzzle })
        items.push({ label: '系统诊断', to: '/admin/diagnostics', icon: Gauge })
    }

    return items
})

const currentTitle = computed(() => String(route.meta.title || 'Ikaros'))

const routeAlias = computed(() => {
    const aliasByName: Record<string, string> = {
        Home: 'Home',
        Chat: 'Chat',
        Bindings: 'Bindings',
        Credentials: 'Credentials',
        ModuleRss: 'RSS',
        ModuleScheduler: 'Scheduler',
        ModuleMonitor: 'Heartbeat',
        ModuleWatchlist: 'Stocks',
        AdminRuntime: 'Runtime',
        AdminModels: 'Models',
        AdminUsers: 'Users',
        AdminDiagnostics: 'Diagnostics',
        AdminSkills: 'Skills',
    }

    return aliasByName[String(route.name || '')] || 'Console'
})

const shellTrail = computed(() => `${currentTitle.value} / ${routeAlias.value}`)

const isNavActive = (to: string) =>
    route.path === to || route.path.startsWith(`${to}/`)

const handleNavClick = () => {
    if (isMobile.value) {
        isSidebarOpen.value = false
    }
}

</script>

<template>
  <div class="ethereal-shell">
    <!-- Mobile Header with Menu Button -->
    <header class="mobile-header">
      <div class="mobile-brand">IKAROS</div>
      <button type="button" class="mobile-menu-btn" @click="toggleSidebar" aria-label="Toggle menu">
        <Menu v-if="!isSidebarOpen" class="h-6 w-6" />
        <X v-else class="h-6 w-6" />
      </button>
    </header>

    <!-- Overlay for mobile -->
    <div
      v-if="isMobile && isSidebarOpen"
      class="sidebar-overlay"
      @click="closeSidebar"
    />

    <aside class="ethereal-sidebar" :class="{ 'is-open': isSidebarOpen }">
      <div class="ethereal-brand">
        <div class="font-display ethereal-brand-mark">IKAROS</div>
        <div class="ethereal-brand-subtitle">ETHEREAL SENTINEL V2.4</div>
      </div>

      <div class="ethereal-nav-block">
        <div class="ethereal-nav-label">工作空间</div>
        <RouterLink
          v-for="item in etherealPrimaryNav"
          :key="item.to"
          :to="item.to"
          class="ethereal-nav-item"
          :class="{ 'is-active': isNavActive(item.to) }"
          @click="handleNavClick"
        >
          <component :is="item.icon" class="ethereal-nav-icon" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </div>

      <div v-if="etherealAdminNav.length" class="ethereal-nav-block">
        <div class="ethereal-nav-label">管理员</div>
        <RouterLink
          v-for="item in etherealAdminNav"
          :key="item.to"
          :to="item.to"
          class="ethereal-nav-item"
          :class="{ 'is-active': isNavActive(item.to) }"
          @click="handleNavClick"
        >
          <component :is="item.icon" class="ethereal-nav-icon" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </div>

      <div class="ethereal-sidebar-footer">
        <div class="ethereal-identity">
          <div class="ethereal-avatar">{{ identityInitial }}</div>
          <div class="ethereal-identity-copy">
            <div class="ethereal-identity-name">{{ identityPrimary }}</div>
            <div v-if="showIdentityEmail" class="ethereal-identity-email">{{ authStore.user?.email }}</div>
            <div class="ethereal-identity-role">{{ authStore.user?.role || 'viewer' }}</div>
          </div>
        </div>

        <button type="button" class="ethereal-logout" @click="handleLogout">
          <LogOut class="h-4 w-4" />
          退出登录
        </button>
      </div>
    </aside>

    <section class="ethereal-main-shell" :class="{ 'sidebar-open': isSidebarOpen && isMobile }">
      <header class="ethereal-topbar">
        <div class="ethereal-trail">
          <span class="ethereal-trail-label">指挥中心</span>
          <span class="ethereal-trail-divider" />
          <span class="ethereal-trail-path">{{ shellTrail }}</span>
        </div>

        <div class="ethereal-topbar-actions">
          <button type="button" class="ethereal-icon-button" aria-label="通知">
            <Bell class="h-4 w-4" />
          </button>
          <button type="button" class="ethereal-icon-button" aria-label="加速">
            <Zap class="h-4 w-4" />
          </button>
          <button type="button" class="ethereal-icon-button" aria-label="帮助">
            <CircleHelp class="h-4 w-4" />
          </button>

          <label class="ethereal-search">
            <Search class="h-4 w-4" />
            <input type="search" placeholder="全局扫描..." />
          </label>
        </div>
      </header>

      <main class="ethereal-main-scroll" :class="{ 'is-home-route': isHomeRoute }">
        <div
          class="ethereal-view-slot"
          :class="isHomeRoute ? 'ethereal-home-slot' : 'ethereal-page-frame ethereal-page-scope'"
        >
          <RouterView />
        </div>
      </main>
    </section>
  </div>
</template>

<style scoped>
.ethereal-shell {
  position: relative;
  display: flex;
  height: 100vh;
  overflow: hidden;
  background:
    radial-gradient(circle at 18% 78%, rgba(102, 234, 255, 0.08), transparent 24%),
    radial-gradient(circle at 78% 18%, rgba(255, 203, 213, 0.08), transparent 20%),
    linear-gradient(180deg, #10131a 0%, #0f141c 100%);
  color: #f5f7fb;
}

.ethereal-shell::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, rgba(255, 203, 213, 0.02), transparent 35%, rgba(102, 234, 255, 0.02));
  pointer-events: none;
}

.ethereal-sidebar {
  position: relative;
  z-index: 1;
  width: 320px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 2.25rem;
  padding: 2rem 1.2rem 1.5rem;
  background: rgba(16, 19, 26, 0.78);
  backdrop-filter: blur(28px);
  box-shadow: inset -1px 0 0 rgba(255, 255, 255, 0.04);
  overflow-y: auto;
}

.ethereal-brand {
  padding: 0.5rem 0.6rem 0 0.7rem;
}

.ethereal-brand-mark {
  font-size: 2.35rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: #ffcbd5;
}

.ethereal-brand-subtitle {
  margin-top: 0.55rem;
  font-size: 0.82rem;
  letter-spacing: 0.34em;
  color: rgba(255, 255, 255, 0.72);
}

.ethereal-nav-block {
  display: grid;
  gap: 0.6rem;
  padding: 0 0.35rem;
}

.ethereal-nav-label {
  margin-bottom: 0.4rem;
  font-size: 0.74rem;
  letter-spacing: 0.24em;
  text-transform: uppercase;
  color: rgba(255, 255, 255, 0.42);
}

.ethereal-nav-item {
  display: flex;
  align-items: center;
  gap: 0.95rem;
  min-height: 3.5rem;
  padding: 0 1.05rem;
  border-radius: 1.45rem;
  background: transparent;
  color: rgba(255, 255, 255, 0.74);
  text-decoration: none;
  transition: transform 0.2s ease, background-color 0.2s ease, color 0.2s ease, box-shadow 0.2s ease;
}

.ethereal-nav-item:hover {
  transform: translateX(2px);
  background: rgba(39, 42, 49, 0.82);
  color: #fff;
}

.ethereal-nav-item.is-active {
  background: linear-gradient(135deg, rgba(255, 203, 213, 0.18), rgba(102, 234, 255, 0.08));
  color: #fff;
  box-shadow: inset 0 0 0 1px rgba(255, 203, 213, 0.12), 0 24px 48px rgba(0, 0, 0, 0.18);
}

.ethereal-nav-icon {
  width: 1.1rem;
  height: 1.1rem;
  color: currentColor;
  opacity: 0.92;
}

.ethereal-sidebar-footer {
  margin-top: auto;
  display: grid;
  gap: 1rem;
  padding: 0.65rem 0.35rem 0;
}

.ethereal-identity {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 1rem 1rem 1rem 0.95rem;
  border-radius: 1.7rem;
  background: rgba(32, 36, 44, 0.92);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.04);
}

.ethereal-avatar {
  display: grid;
  place-items: center;
  width: 3.35rem;
  height: 3.35rem;
  border-radius: 999px;
  background: linear-gradient(135deg, rgba(255, 203, 213, 0.88), rgba(255, 157, 173, 0.78));
  color: #10131a;
  font-weight: 800;
}

.ethereal-identity-copy {
  min-width: 0;
}

.ethereal-identity-name {
  font-size: 1.06rem;
  font-weight: 700;
  color: #f8f8fb;
}

.ethereal-identity-email,
.ethereal-identity-role {
  margin-top: 0.2rem;
  font-size: 0.84rem;
  color: rgba(255, 255, 255, 0.64);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ethereal-logout {
  display: inline-flex;
  align-items: center;
  gap: 0.7rem;
  width: max-content;
  padding: 0.2rem 0.2rem 0.2rem 0.35rem;
  background: transparent;
  color: rgba(255, 255, 255, 0.78);
  border: none;
  font: inherit;
  cursor: pointer;
}

.ethereal-logout:hover {
  color: #fff;
}

.ethereal-main-shell {
  position: relative;
  z-index: 1;
  flex: 1;
  min-width: 0;
  min-height: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  padding: 1.2rem 1.6rem 1.35rem;
  overflow: hidden;
}

.ethereal-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1.25rem;
  padding: 0.15rem 0.35rem 1.1rem;
}

.ethereal-trail {
  display: flex;
  align-items: center;
  gap: 1rem;
  min-width: 0;
}

.ethereal-trail-label {
  font-size: 0.95rem;
  font-weight: 700;
  letter-spacing: 0.16em;
  color: #66eaff;
  text-transform: uppercase;
}

.ethereal-trail-divider {
  width: 1px;
  height: 1.5rem;
  background: linear-gradient(180deg, transparent, rgba(255, 255, 255, 0.26), transparent);
}

.ethereal-trail-path {
  font-size: 1.55rem;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.88);
}

.ethereal-topbar-actions {
  display: flex;
  align-items: center;
  gap: 0.85rem;
}

.ethereal-icon-button {
  display: grid;
  place-items: center;
  width: 2.75rem;
  height: 2.75rem;
  border-radius: 999px;
  background: rgba(25, 28, 34, 0.92);
  color: rgba(255, 203, 213, 0.86);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.04);
  border: none;
  cursor: pointer;
  transition: transform 0.2s ease, background-color 0.2s ease, color 0.2s ease;
}

.ethereal-icon-button:hover {
  transform: translateY(-1px);
  background: rgba(39, 42, 49, 0.96);
  color: #fff;
}

.ethereal-search {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  min-width: 19rem;
  height: 3rem;
  padding: 0 1rem;
  border-radius: 999px;
  background: rgba(25, 28, 34, 0.92);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.04);
  color: rgba(255, 255, 255, 0.48);
}

.ethereal-search input {
  width: 100%;
  background: transparent;
  border: none;
  outline: none;
  color: rgba(255, 255, 255, 0.88);
  font: inherit;
}

.ethereal-search input::placeholder {
  color: rgba(255, 255, 255, 0.42);
}

.ethereal-main-scroll {
  position: relative;
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 0 0.35rem 2.75rem;
  scrollbar-gutter: stable both-edges;
  overscroll-behavior: contain;
}

.ethereal-main-scroll.is-home-route {
  overflow-y: scroll;
}

.ethereal-main-scroll::-webkit-scrollbar {
  width: 10px;
}

.ethereal-main-scroll::-webkit-scrollbar-track {
  background: transparent;
}

.ethereal-main-scroll::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.14);
  border-radius: 999px;
}

.ethereal-main-scroll::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.22);
}

.ethereal-view-slot {
  min-height: 100%;
}

.ethereal-home-slot {
  min-height: 100%;
}

.ethereal-page-frame {
  min-height: 100%;
  border-radius: 2.15rem;
  background:
    linear-gradient(180deg, rgba(16, 19, 26, 0.62), rgba(16, 19, 26, 0.5)),
    radial-gradient(circle at top left, rgba(255, 203, 213, 0.05), transparent 34%);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.04), 0 32px 72px rgba(0, 0, 0, 0.14);
  overflow: hidden;
}

/* Mobile Header - only visible on mobile */
.mobile-header {
  display: none;
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 100;
  height: 56px;
  align-items: center;
  justify-content: space-between;
  padding: 0 1rem;
  background: rgba(16, 19, 26, 0.95);
  backdrop-filter: blur(20px);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.mobile-brand {
  font-size: 1.25rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: #ffcbd5;
}

.mobile-menu-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.08);
  border: none;
  color: #f5f7fb;
  cursor: pointer;
  transition: background-color 0.2s;
}

.mobile-menu-btn:hover {
  background: rgba(255, 255, 255, 0.12);
}

/* Sidebar overlay for mobile */
.sidebar-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 40;
  backdrop-filter: blur(4px);
}

/* Mobile responsive styles */
@media (max-width: 1024px) {
  .mobile-header {
    display: flex;
  }

  .ethereal-shell {
    flex-direction: column;
    height: 100vh;
    overflow: hidden;
    padding-top: 56px;
  }

  .ethereal-sidebar {
    position: fixed;
    top: 56px;
    left: 0;
    bottom: 0;
    width: 280px;
    z-index: 50;
    transform: translateX(-100%);
    transition: transform 0.3s ease;
    padding: 1.5rem 1rem 1rem;
    gap: 1.5rem;
  }

  .ethereal-sidebar.is-open {
    transform: translateX(0);
  }

  .ethereal-brand {
    padding: 0.25rem 0.5rem 0;
  }

  .ethereal-brand-mark {
    font-size: 1.75rem;
  }

  .ethereal-nav-item {
    min-height: 3rem;
    padding: 0 0.875rem;
    border-radius: 1rem;
  }

  .ethereal-avatar {
    width: 2.75rem;
    height: 2.75rem;
  }

  .ethereal-identity-name {
    font-size: 0.95rem;
  }

  .ethereal-identity-email,
  .ethereal-identity-role {
    font-size: 0.75rem;
  }

  .ethereal-main-shell {
    height: calc(100vh - 56px);
    padding: 0.75rem;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .ethereal-topbar {
    padding: 0.5rem 0.25rem 0.75rem;
    flex-direction: row;
    align-items: center;
  }

  .ethereal-trail-path {
    font-size: 1.1rem;
  }

  .ethereal-trail-label {
    font-size: 0.75rem;
  }

  .ethereal-topbar-actions {
    gap: 0.5rem;
  }

  .ethereal-icon-button {
    width: 2.25rem;
    height: 2.25rem;
  }

  .ethereal-search {
    display: none;
  }

  .ethereal-main-scroll {
    padding: 0 0.25rem 1rem;
  }

  .ethereal-page-frame {
    border-radius: 1.25rem;
  }
}

/* Tablet responsive styles */
@media (max-width: 1200px) and (min-width: 1025px) {
  .ethereal-shell {
    flex-direction: row;
  }

  .ethereal-sidebar {
    width: 260px;
    padding: 1.5rem 1rem;
  }

  .ethereal-main-shell {
    padding: 1rem;
  }

  .ethereal-search {
    min-width: 12rem;
  }
}

@media (max-width: 780px) {
  .ethereal-brand-mark {
    font-size: 1.9rem;
  }

  .ethereal-trail-path {
    font-size: 1.2rem;
  }

  .ethereal-main-shell {
    padding: 0.75rem;
  }
}

@media (max-width: 480px) {
  .ethereal-topbar {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.75rem;
  }

  .ethereal-trail {
    gap: 0.5rem;
  }

  .ethereal-trail-divider {
    height: 1.25rem;
  }

  .ethereal-trail-path {
    font-size: 1rem;
  }

  .ethereal-page-frame {
    border-radius: 1rem;
  }
}
</style>
