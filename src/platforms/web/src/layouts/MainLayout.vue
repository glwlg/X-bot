<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import {
    Activity,
    Cable,
    Cctv,
    ChevronsLeft,
    Gauge,
    HeartPulse,
    Home,
    KeyRound,
    LayoutGrid,
    Link2,
    LogOut,
    Menu,
    MessageSquareText,
    Moon,
    Puzzle,
    Radio,
    Settings2,
    ShieldUser,
    Sun,
    X,
    Zap,
} from 'lucide-vue-next'

import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'

const route = useRoute()
const authStore = useAuthStore()
const themeStore = useThemeStore()

const isSidebarOpen = ref(false)
const isMobile = ref(false)
const isSidebarCollapsed = ref(false)

const checkMobile = () => {
    isMobile.value = window.innerWidth <= 1024
    isSidebarOpen.value = !isMobile.value
    if (isMobile.value) {
        isSidebarCollapsed.value = false
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

const toggleSidebarCollapsed = () => {
    if (!isMobile.value) {
        isSidebarCollapsed.value = !isSidebarCollapsed.value
    }
}

onMounted(() => {
    checkMobile()
    window.addEventListener('resize', checkMobile)
})

onUnmounted(() => {
    window.removeEventListener('resize', checkMobile)
})

const identityPrimary = computed(() =>
    authStore.user?.display_name || authStore.user?.username || authStore.user?.email || '管理员'
)

const identityEmail = computed(() => authStore.user?.email || 'admin@example.com')

const identityInitial = computed(() => {
    const source = String(identityPrimary.value).trim()
    return source ? source.charAt(0).toUpperCase() : 'A'
})

const etherealPrimaryNav = computed(() => [
    { label: '控制面板', to: '/home', icon: LayoutGrid },
    { label: '聊天对话', to: '/chat', icon: MessageSquareText },
    { label: '模块绑定', to: '/bindings', icon: Link2 },
    { label: '凭据管理', to: '/credentials', icon: KeyRound },
    { label: '订阅源', to: '/modules/rss', icon: Radio },
    { label: '任务调度', to: '/modules/scheduler', icon: Cable },
    { label: '心跳监控', to: '/modules/monitor', icon: HeartPulse },
    { label: '实时监控', to: '/modules/cameras', icon: Cctv },
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

const routeAlias = computed(() => {
    const aliasByName: Record<string, string> = {
        Home: 'Dashboard',
        Chat: 'Chat',
        Bindings: 'Bind',
        Credentials: 'Keys',
        ModuleRss: 'RSS',
        ModuleScheduler: 'Scheduling',
        ModuleMonitor: 'Heartbeat',
        ModuleCameras: 'Cameras',
        ModuleWatchlist: 'Stocks',
        AdminRuntime: 'Runtime',
        AdminModels: 'Models',
        AdminUsers: 'Users',
        AdminDiagnostics: 'Diagnostics',
        AdminSkills: 'Skills',
    }

    return aliasByName[String(route.name || '')] || 'Console'
})

const currentTitle = computed(() => String(route.meta.title || '控制面板'))

const shellRoot = computed(() =>
    route.path.startsWith('/admin') ? '管理中心' : '控制面板'
)

const isNavActive = (to: string) =>
    route.path === to || route.path.startsWith(`${to}/`)

const handleNavClick = () => {
    if (isMobile.value) {
        isSidebarOpen.value = false
    }
}

const handleLogout = async () => {
    await authStore.logout()
    window.location.href = '/login'
}
</script>

<template>
  <div class="ethereal-shell">
    <header class="mobile-header">
      <button type="button" class="mobile-menu-btn" @click="toggleSidebar" aria-label="切换菜单">
        <Menu v-if="!isSidebarOpen" class="h-5 w-5" />
        <X v-else class="h-5 w-5" />
      </button>
      <div class="mobile-brand">
        <span class="brand-cube">◆</span>
        IKAROS
      </div>
      <button type="button" class="ethereal-icon-button compact" aria-label="切换主题" @click="themeStore.toggleTheme()">
        <Sun v-if="themeStore.isDark" class="h-4 w-4" />
        <Moon v-else class="h-4 w-4" />
      </button>
    </header>

    <div
      v-if="isMobile && isSidebarOpen"
      class="sidebar-overlay"
      @click="closeSidebar"
    />

    <aside class="ethereal-sidebar" :class="{ 'is-open': isSidebarOpen, 'is-collapsed': isSidebarCollapsed }">
      <div class="ethereal-brand">
        <div class="ethereal-logo">
          <div class="ethereal-logo-mark">
            <span>◆</span>
          </div>
          <div>
            <div class="ethereal-brand-mark">IKAROS</div>
            <div class="ethereal-brand-subtitle">ETHEREAL SENTINEL</div>
          </div>
        </div>
        <button
          type="button"
          class="sidebar-collapse"
          :class="{ 'is-collapsed': isSidebarCollapsed }"
          :aria-label="isSidebarCollapsed ? '展开菜单' : '收起菜单'"
          :title="isSidebarCollapsed ? '展开菜单' : '收起菜单'"
          @click="toggleSidebarCollapsed"
        >
          <ChevronsLeft class="h-4 w-4" />
        </button>
      </div>

      <nav class="ethereal-nav">
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
      </nav>

      <div class="ethereal-sidebar-footer">
        <div class="ethereal-identity">
          <div class="ethereal-avatar">{{ identityInitial }}</div>
          <div class="ethereal-identity-copy">
            <div class="ethereal-identity-name">{{ identityPrimary }}</div>
            <div class="ethereal-identity-email">{{ identityEmail }}</div>
            <div class="ethereal-identity-role">
              <span />
              在线
            </div>
          </div>
        </div>

        <button type="button" class="ethereal-logout" @click="handleLogout">
          <LogOut class="h-4 w-4" />
          退出登录
        </button>
      </div>
    </aside>

    <section class="ethereal-main-shell">
      <header class="ethereal-topbar">
        <div class="ethereal-trail">
          <Home class="ethereal-trail-home h-4 w-4" />
          <span>{{ shellRoot }}</span>
          <span class="ethereal-trail-divider">/</span>
          <strong>{{ currentTitle }}</strong>
          <span class="ethereal-trail-divider">/</span>
          <span>{{ routeAlias }}</span>
        </div>

        <div class="ethereal-topbar-actions">
          <div class="ethereal-top-avatar">{{ identityInitial }}</div>
        </div>
      </header>

      <main class="ethereal-main-scroll">
        <div class="ethereal-view-slot">
          <RouterView />
        </div>
      </main>

      <footer class="ethereal-footer">
        <span>© 2025 IKAROS Ethereal Sentinel. 保留所有权利。</span>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.ethereal-shell {
  display: flex;
  height: 100vh;
  overflow: hidden;
  background: #f7f9fc;
  color: #0f172a;
}

.ethereal-sidebar {
  position: relative;
  width: 296px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: 24px 16px 22px;
  background: rgba(255, 255, 255, 0.92);
  border-right: 1px solid #e6ebf2;
  box-shadow: 8px 0 32px rgba(15, 23, 42, 0.03);
  overflow-y: auto;
  transition: width 0.22s ease, padding 0.22s ease;
}

.ethereal-sidebar.is-collapsed {
  width: 86px;
  padding-inline: 12px;
  overflow-x: hidden;
}

.ethereal-brand {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  min-height: 72px;
  padding: 0 8px;
}

.ethereal-sidebar.is-collapsed .ethereal-brand {
  display: grid;
  justify-items: center;
  gap: 10px;
  min-height: 90px;
  padding: 0;
}

.ethereal-logo {
  display: flex;
  align-items: center;
  gap: 12px;
}

.ethereal-sidebar.is-collapsed .ethereal-logo {
  justify-content: center;
}

.ethereal-logo-mark {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: linear-gradient(135deg, #2877ff, #5ca2ff);
  color: white;
  box-shadow: 0 14px 28px rgba(38, 113, 255, 0.22);
}

.ethereal-logo-mark span,
.brand-cube {
  font-size: 14px;
  line-height: 1;
}

.ethereal-brand-mark {
  font-size: 27px;
  line-height: 1;
  font-weight: 800;
  letter-spacing: 0;
  color: #07111f;
}

.ethereal-brand-subtitle {
  margin-top: 6px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 1.2px;
  color: #8a97aa;
}

.ethereal-sidebar.is-collapsed .ethereal-logo > div:last-child,
.ethereal-sidebar.is-collapsed .ethereal-nav-label,
.ethereal-sidebar.is-collapsed .ethereal-nav-item span,
.ethereal-sidebar.is-collapsed .ethereal-identity-copy,
.ethereal-sidebar.is-collapsed .ethereal-logout {
  display: none;
}

.sidebar-collapse {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border: 0;
  background: transparent;
  color: #667085;
  cursor: pointer;
}

.sidebar-collapse.is-collapsed {
  position: static;
  width: 28px;
  height: 28px;
  border: 1px solid #e0e7f0;
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 6px 14px rgba(15, 23, 42, 0.06);
}

.sidebar-collapse.is-collapsed svg {
  transform: rotate(180deg);
}

.ethereal-nav {
  display: grid;
  gap: 34px;
  padding-top: 18px;
}

.ethereal-sidebar.is-collapsed .ethereal-nav {
  gap: 20px;
  padding-top: 16px;
}

.ethereal-nav-block {
  display: grid;
  gap: 9px;
}

.ethereal-nav-label {
  padding: 0 14px 4px;
  font-size: 13px;
  color: #98a2b3;
}

.ethereal-nav-item {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 48px;
  padding: 0 16px;
  border-radius: 8px;
  border: 1px solid transparent;
  color: #344054;
  font-size: 15px;
  font-weight: 600;
  text-decoration: none;
  transition: background-color 0.18s ease, border-color 0.18s ease, color 0.18s ease;
}

.ethereal-sidebar.is-collapsed .ethereal-nav-item {
  justify-content: center;
  gap: 0;
  min-height: 44px;
  padding: 0;
}

.ethereal-nav-item:hover {
  background: #f4f8ff;
  color: #156df5;
}

.ethereal-nav-item.is-active {
  border-color: #2f7cf6;
  background: linear-gradient(180deg, #f7fbff 0%, #eef6ff 100%);
  color: #1469f2;
  box-shadow: 0 8px 18px rgba(40, 119, 255, 0.09);
}

.ethereal-nav-icon {
  width: 17px;
  height: 17px;
}

.ethereal-sidebar.is-collapsed .ethereal-nav-icon {
  width: 18px;
  height: 18px;
}

.ethereal-sidebar-footer {
  margin-top: auto;
  display: grid;
  gap: 18px;
  padding: 22px 4px 0;
}

.ethereal-sidebar.is-collapsed .ethereal-sidebar-footer {
  justify-items: center;
  padding-inline: 0;
}

.ethereal-identity {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  padding: 14px;
  border: 1px solid #e6ebf2;
  border-radius: 14px;
  background: #fbfdff;
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.04);
}

.ethereal-sidebar.is-collapsed .ethereal-identity {
  grid-template-columns: 42px;
  padding: 8px;
}

.ethereal-avatar,
.ethereal-top-avatar {
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: #e8f1ff;
  color: #1f6fff;
  font-weight: 800;
}

.ethereal-avatar {
  width: 42px;
  height: 42px;
  border-radius: 50%;
}

.ethereal-top-avatar {
  width: 40px;
  height: 40px;
}

.ethereal-identity-copy {
  min-width: 0;
}

.ethereal-identity-name {
  font-size: 14px;
  font-weight: 700;
  color: #101828;
}

.ethereal-identity-email {
  margin-top: 2px;
  overflow: hidden;
  color: #667085;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ethereal-identity-role {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  color: #22c55e;
  font-size: 12px;
  font-weight: 700;
}

.ethereal-identity-role span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #22c55e;
}

.ethereal-logout {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  width: max-content;
  border: 0;
  background: transparent;
  color: #344054;
  font-size: 14px;
  cursor: pointer;
}

.ethereal-main-shell {
  display: flex;
  min-width: 0;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  background: #f7f9fc;
}

.ethereal-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 22px;
  min-height: 78px;
  padding: 0 32px;
  border-bottom: 1px solid #e6ebf2;
  background: rgba(255, 255, 255, 0.86);
  backdrop-filter: blur(18px);
}

.ethereal-trail {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  color: #475467;
  font-size: 17px;
  white-space: nowrap;
}

.ethereal-trail-home {
  color: #667085;
}

.ethereal-trail strong {
  color: #101828;
  font-size: 18px;
}

.ethereal-trail-divider {
  color: #98a2b3;
}

.ethereal-topbar-actions {
  display: flex;
  align-items: center;
  gap: 0;
}

.ethereal-icon-button {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: #344054;
  cursor: pointer;
}

.ethereal-icon-button.compact {
  width: 34px;
  height: 34px;
}

.ethereal-icon-button:hover {
  background: #f2f7ff;
  color: #1469f2;
}

.ethereal-main-scroll {
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 24px 28px;
}

.ethereal-view-slot {
  min-height: 100%;
}

.ethereal-footer {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  min-height: 52px;
  padding: 0 32px;
  border-top: 1px solid #e6ebf2;
  background: #ffffff;
  color: #7d8da3;
  font-size: 13px;
}

.mobile-header,
.sidebar-overlay {
  display: none;
}

@media (max-width: 1280px) {
  .ethereal-sidebar {
    width: 270px;
  }

  .ethereal-sidebar.is-collapsed {
    width: 86px;
  }
}

@media (max-width: 1024px) {
  .mobile-header {
    position: fixed;
    z-index: 70;
    inset: 0 0 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 58px;
    padding: 0 14px;
    border-bottom: 1px solid #e6ebf2;
    background: rgba(255, 255, 255, 0.94);
    backdrop-filter: blur(18px);
  }

  .mobile-brand {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-weight: 800;
    letter-spacing: 0;
  }

  .brand-cube {
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
    border-radius: 8px;
    background: #2877ff;
    color: #fff;
  }

  .mobile-menu-btn {
    display: grid;
    place-items: center;
    width: 36px;
    height: 36px;
    border: 1px solid #e0e7f0;
    border-radius: 10px;
    background: #fff;
    color: #344054;
  }

  .sidebar-overlay {
    position: fixed;
    z-index: 50;
    inset: 58px 0 0;
    display: block;
    background: rgba(15, 23, 42, 0.28);
    backdrop-filter: blur(4px);
  }

  .ethereal-shell {
    padding-top: 58px;
  }

  .ethereal-sidebar {
    position: fixed;
    z-index: 60;
    top: 58px;
    bottom: 0;
    left: 0;
    width: 284px;
    transform: translateX(-100%);
    transition: transform 0.24s ease;
  }

  .ethereal-sidebar.is-collapsed {
    width: 284px;
    padding: 24px 16px 22px;
  }

  .ethereal-sidebar.is-collapsed .ethereal-brand {
    justify-content: space-between;
    padding: 0 8px;
  }

  .ethereal-sidebar.is-collapsed .ethereal-logo > div:last-child,
  .ethereal-sidebar.is-collapsed .ethereal-nav-label,
  .ethereal-sidebar.is-collapsed .ethereal-nav-item span,
  .ethereal-sidebar.is-collapsed .ethereal-identity-copy,
  .ethereal-sidebar.is-collapsed .ethereal-logout {
    display: block;
  }

  .ethereal-sidebar.is-collapsed .ethereal-nav-item {
    justify-content: flex-start;
    gap: 14px;
    min-height: 48px;
    padding: 0 16px;
  }

  .ethereal-sidebar.is-collapsed .ethereal-identity {
    grid-template-columns: 42px minmax(0, 1fr);
    padding: 14px;
  }

  .ethereal-sidebar.is-collapsed .sidebar-collapse {
    position: static;
  }

  .ethereal-sidebar.is-collapsed .sidebar-collapse svg {
    transform: none;
  }

  .ethereal-sidebar.is-open {
    transform: translateX(0);
  }

  .ethereal-topbar {
    min-height: 64px;
    padding: 0 18px;
  }

  .ethereal-topbar-actions {
    gap: 8px;
  }

  .ethereal-trail {
    font-size: 14px;
  }

  .ethereal-trail strong {
    font-size: 15px;
  }

  .ethereal-main-scroll {
    padding: 16px;
  }

  .ethereal-footer {
    display: none;
  }
}

@media (max-width: 640px) {
  .ethereal-top-avatar {
    display: none;
  }

  .ethereal-topbar {
    align-items: flex-start;
    flex-direction: column;
    gap: 10px;
    padding: 12px 16px;
  }

  .ethereal-trail {
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .ethereal-main-scroll {
    padding: 12px;
  }
}
</style>
