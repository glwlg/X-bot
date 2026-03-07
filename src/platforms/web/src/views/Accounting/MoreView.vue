<script setup lang="ts">
import {
    ArrowDownToLine, ArrowUpFromLine, Receipt, Users,
    CalendarClock, CreditCard, Target, PiggyBank
} from 'lucide-vue-next'

const sections = [
    {
        title: '往来管理',
        subtitle: '让每一次往来，都留下回忆',
        items: [
            { icon: ArrowDownToLine, label: '借入', desc: '¥0 待还', color: 'bg-indigo-500', route: '/accounting/debts?type=借入' },
            { icon: ArrowUpFromLine, label: '借出', desc: '¥0 待收', color: 'bg-indigo-500', route: '/accounting/debts?type=借出' },
            { icon: Receipt, label: '报销', desc: '¥0 待报', color: 'bg-indigo-500', route: '/accounting/debts?type=报销' },
            { icon: Users, label: '往来', desc: '管理', color: 'bg-indigo-500', route: '/accounting/debts' },
        ],
    },
    {
        title: '计划管理',
        subtitle: '让每一步计划，都通向未来',
        items: [
            { icon: CalendarClock, label: '周期', desc: '计划', color: 'bg-indigo-500', route: '/accounting/scheduled-tasks' },
            { icon: CreditCard, label: '分期', desc: '项目管理', color: 'bg-indigo-500', route: '/accounting/manage/project' },
            { icon: Target, label: '预算', desc: '管理', color: 'bg-indigo-500', route: '/accounting/budgets' },
            { icon: PiggyBank, label: '存钱', desc: '预算计划', color: 'bg-indigo-500', route: '/accounting/budgets' },
        ],
    },
]
</script>

<template>
  <div class="pb-4">
    <div class="px-4 pt-4 pb-2">
      <h2 class="text-lg font-bold text-theme-primary text-center">更多</h2>
    </div>

    <div v-for="section in sections" :key="section.title" class="px-4 mt-4">
      <h3 class="text-base font-bold text-indigo-600 dark:text-indigo-400">{{ section.title }}</h3>
      <p class="text-xs text-theme-muted mb-3">{{ section.subtitle }}</p>

      <div class="grid grid-cols-2 gap-3">
        <RouterLink
          v-for="item in section.items"
          :key="item.label"
          :to="item.route"
          class="bg-white dark:bg-slate-800 rounded-2xl p-4 shadow-sm border border-gray-100 dark:border-slate-700 hover:shadow-md transition cursor-pointer"
        >
          <div class="flex items-center gap-3">
            <div :class="['w-10 h-10 rounded-xl flex items-center justify-center', item.color]">
              <component :is="item.icon" class="w-5 h-5 text-white" />
            </div>
            <div>
              <p class="font-semibold text-theme-primary text-sm">{{ item.label }}</p>
              <p class="text-xs text-theme-muted">{{ item.desc }}</p>
            </div>
          </div>
        </RouterLink>
      </div>
    </div>
  </div>
</template>
