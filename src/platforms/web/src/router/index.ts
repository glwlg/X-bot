import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
    history: createWebHashHistory(import.meta.env.BASE_URL),
    routes: [
        {
            path: '/',
            redirect: '/home'
        },
        {
            path: '/home',
            name: 'Home',
            component: () => import('@/views/Home/HomeView.vue'),
            meta: { title: '首页' },
        },
        {
            path: '/login',
            name: 'Login',
            component: () => import('@/views/Auth/LoginView.vue'),
            meta: { title: '登录', public: true },
        }
    ]
})

router.beforeEach(async (to, _from, next) => {
    // 设置标题
    document.title = to.meta.title ? `${to.meta.title} - Template` : 'Template Project'

    // 公开页面直接放行
    if (to.meta.public) {
        return next()
    }

    const authStore = useAuthStore()

    // 检查是否有 token
    if (!authStore.token) {
        return next('/login')
    }

    // 获取用户信息
    if (!authStore.user) {
        try {
            await authStore.fetchUser()
        } catch {
            return next('/login')
        }
    }

    next()
})

export default router
