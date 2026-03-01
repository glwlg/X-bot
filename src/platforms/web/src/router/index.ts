import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
    history: createWebHistory(import.meta.env.BASE_URL),
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
            path: '/modules/rss',
            name: 'ModuleRss',
            component: () => import('@/views/Modules/RssView.vue'),
            meta: { title: 'RSS 订阅', fullscreen: true },
        },
        {
            path: '/modules/scheduler',
            name: 'ModuleScheduler',
            component: () => import('@/views/Modules/SchedulerView.vue'),
            meta: { title: '定时任务', fullscreen: true },
        },
        {
            path: '/modules/monitor',
            name: 'ModuleMonitor',
            component: () => import('@/views/Modules/MonitorView.vue'),
            meta: { title: '心跳监控', fullscreen: true },
        },
        {
            path: '/modules/watchlist',
            name: 'ModuleWatchlist',
            component: () => import('@/views/Modules/WatchlistView.vue'),
            meta: { title: '自选股管理', fullscreen: true },
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
                    path: 'stats/category',
                    name: 'StatsCategoryDetail',
                    component: () => import('@/views/Accounting/StatsCategoryDetailView.vue'),
                    meta: { title: '分类统计详情' },
                },
                {
                    path: 'stats/trend',
                    name: 'StatsTrendDetail',
                    component: () => import('@/views/Accounting/StatsTrendDetailView.vue'),
                    meta: { title: '年度统计详情' },
                },
                {
                    path: 'stats/team',
                    name: 'StatsTeamDetail',
                    component: () => import('@/views/Accounting/StatsTeamDetailView.vue'),
                    meta: { title: '多人统计详情' },
                },
                {
                    path: 'stats/panels',
                    name: 'StatsPanelManage',
                    component: () => import('@/views/Accounting/StatsPanelManageView.vue'),
                    meta: { title: '统计面板管理' },
                },
                {
                    path: 'stats/panels/edit/:id?',
                    name: 'StatsPanelEdit',
                    component: () => import('@/views/Accounting/StatsPanelEditView.vue'),
                    meta: { title: '编辑统计' },
                    props: true,
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
                {
                    path: 'manage/:kind',
                    name: 'ProfileManage',
                    component: () => import('@/views/Accounting/ManageCenterView.vue'),
                    meta: { title: '管理中心' },
                    props: true,
                },
                {
                    path: 'settings/:kind',
                    name: 'ProfileSettings',
                    component: () => import('@/views/Accounting/ProfileSettingsView.vue'),
                    meta: { title: '设置' },
                    props: true,
                },
                {
                    path: 'records',
                    name: 'RecordList',
                    component: () => import('@/views/Accounting/RecordListView.vue'),
                    meta: { title: '交易明细' },
                },
                {
                    path: 'records/:id',
                    name: 'RecordDetail',
                    component: () => import('@/views/Accounting/RecordDetailView.vue'),
                    meta: { title: '交易详情' },
                    props: true,
                },
                {
                    path: 'budgets',
                    name: 'BudgetList',
                    component: () => import('@/views/Accounting/BudgetView.vue'),
                    meta: { title: '预算管理' },
                },
                {
                    path: 'debts',
                    name: 'DebtList',
                    component: () => import('@/views/Accounting/DebtView.vue'),
                    meta: { title: '往来管理' },
                },
                {
                    path: 'scheduled-tasks',
                    name: 'ScheduledTaskList',
                    component: () => import('@/views/Accounting/ScheduledTaskView.vue'),
                    meta: { title: '计划管理' },
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
