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
            path: '/accounting',
            component: () => import('@/layouts/AccountingLayout.vue'),
            meta: { fullscreen: true },
            children: [
                {
                    path: '',
                    redirect: '/accounting/overview'
                },
                {
                    path: 'overview',
                    name: 'AccountingOverview',
                    component: () => import('@/views/Accounting/OverviewView.vue'),
                    meta: { title: '记账首页' },
                },
                {
                    path: 'assets',
                    name: 'AccountingAssets',
                    component: () => import('@/views/Accounting/AssetsView.vue'),
                    meta: { title: '资产' },
                },
                {
                    path: 'account/:id',
                    name: 'AccountDetail',
                    component: () => import('@/views/Accounting/AccountDetailView.vue'),
                    meta: { title: '账户详情' },
                    props: true,
                },
                {
                    path: 'stats',
                    name: 'AccountingStats',
                    component: () => import('@/views/Accounting/StatsView.vue'),
                    meta: { title: '统计' },
                },
                {
                    path: 'more',
                    name: 'AccountingMore',
                    component: () => import('@/views/Accounting/MoreView.vue'),
                    meta: { title: '更多' },
                },
                {
                    path: 'profile',
                    name: 'AccountingProfile',
                    component: () => import('@/views/Accounting/ProfileView.vue'),
                    meta: { title: '我的' },
                },
            ],
        },
        {
            path: '/login',
            name: 'Login',
            component: () => import('@/views/Auth/LoginView.vue'),
            meta: { title: '登录', public: true },
        },
    ]
})

router.beforeEach(async (to, _from, next) => {
    document.title = to.meta.title ? `${to.meta.title} - X-Bot` : 'X-Bot'

    if (to.meta.public) {
        return next()
    }

    const authStore = useAuthStore()

    if (!authStore.token) {
        return next('/login')
    }

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
