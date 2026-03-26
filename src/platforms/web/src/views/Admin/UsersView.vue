<script setup lang="ts">
import axios from 'axios'
import { computed, onMounted, ref } from 'vue'
import { CheckCircle2, Loader2, PencilLine, Plus, ShieldUser, TriangleAlert } from 'lucide-vue-next'

import { createUser, listUsers, updateUser } from '@/api/admin'
import type { UserInfo } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()
const users = ref<UserInfo[]>([])
const loading = ref(false)
const creating = ref(false)
const formError = ref('')
const listError = ref('')
const successText = ref('')
const form = ref({
    email: '',
    password: '',
    display_name: '',
    username: '',
    role: 'viewer',
})

const normalizedUsers = computed(() => {
    const rows = [...users.value]
    const current = authStore.user
    if (current && !rows.some(item => item.id === current.id)) {
        rows.unshift(current)
    }
    return rows
})

const normalizeCreatePayload = () => {
    const payload = {
        email: form.value.email.trim(),
        password: form.value.password,
        role: form.value.role,
        username: form.value.username.trim() || undefined,
        display_name: form.value.display_name.trim() || undefined,
    }
    return payload
}

const parseErrorMessage = (error: unknown, fallback: string) => {
    if (axios.isAxiosError(error)) {
        const detail = error.response?.data?.detail
        if (Array.isArray(detail) && detail.length > 0) {
            return String(detail[0]?.msg || fallback)
        }
        if (typeof detail === 'string' && detail.trim()) {
            return detail
        }
    }
    return fallback
}

const load = async () => {
    loading.value = true
    listError.value = ''
    try {
        const response = await listUsers()
        users.value = Array.isArray(response.data) ? response.data : []
    } catch (error) {
        listError.value = parseErrorMessage(error, '用户列表加载失败')
    } finally {
        loading.value = false
    }
}

const submit = async () => {
    formError.value = ''
    successText.value = ''
    const payload = normalizeCreatePayload()
    if (!payload.email.includes('.') || !payload.email.includes('@')) {
        formError.value = '邮箱格式不正确，请使用类似 name@example.com 的地址。'
        return
    }
    creating.value = true
    try {
        await createUser(payload)
        form.value = {
            email: '',
            password: '',
            display_name: '',
            username: '',
            role: 'viewer',
        }
        successText.value = '用户已创建'
        await load()
    } catch (error) {
        formError.value = parseErrorMessage(error, '创建用户失败')
    } finally {
        creating.value = false
    }
}

const cycleRole = async (user: UserInfo) => {
    const order: Array<UserInfo['role']> = ['viewer', 'operator', 'admin']
    const nextRole = order[(order.indexOf(user.role) + 1) % order.length]
    await updateUser(user.id, { role: nextRole })
    await load()
}

const toggleActive = async (user: UserInfo) => {
    await updateUser(user.id, { is_active: !user.is_active })
    await load()
}

onMounted(load)
</script>

<template>
  <div class="grid gap-6 p-6 md:grid-cols-[380px_minmax(0,1fr)] md:p-8">
    <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex items-center gap-3">
        <div class="flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-100 text-amber-700">
          <Plus class="h-5 w-5" />
        </div>
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Admin</div>
          <h2 class="text-xl font-semibold text-slate-900">创建用户</h2>
        </div>
      </div>

      <p class="mt-4 text-sm leading-7 text-slate-500">
        这里只做管理员创建，不保留公开注册。邮箱需要是合法地址，例如 `name@example.com`。
      </p>

      <form class="mt-6 space-y-4" @submit.prevent="submit">
        <input v-model="form.display_name" type="text" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none focus:border-cyan-400 focus:bg-white" placeholder="显示名称">
        <input v-model="form.username" type="text" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none focus:border-cyan-400 focus:bg-white" placeholder="用户名（可选）">
        <input v-model="form.email" type="email" required class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none focus:border-cyan-400 focus:bg-white" placeholder="邮箱，例如 name@example.com">
        <input v-model="form.password" type="password" required minlength="8" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none focus:border-cyan-400 focus:bg-white" placeholder="临时密码">
        <select v-model="form.role" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none focus:border-cyan-400 focus:bg-white">
          <option value="viewer">viewer</option>
          <option value="operator">operator</option>
          <option value="admin">admin</option>
        </select>

        <div v-if="formError" class="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {{ formError }}
        </div>
        <div v-if="successText" class="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          {{ successText }}
        </div>

        <button class="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-slate-950 px-4 py-3 text-sm font-medium text-white transition hover:bg-slate-800 disabled:opacity-60" :disabled="creating">
          <Loader2 v-if="creating" class="h-4 w-4 animate-spin" />
          创建用户
        </button>
      </form>
    </section>

    <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex items-center justify-between">
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Users</div>
          <h2 class="text-xl font-semibold text-slate-900">用户列表</h2>
        </div>
        <button class="rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-100" @click="load">
          刷新
        </button>
      </div>

      <div v-if="loading" class="mt-6 flex items-center gap-2 text-sm text-slate-500">
        <Loader2 class="h-4 w-4 animate-spin" />
        正在加载用户
      </div>

      <div v-else-if="listError" class="mt-6 rounded-[24px] border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">
        {{ listError }}
      </div>

      <div v-else class="mt-6 overflow-hidden rounded-[24px] border border-slate-200">
        <table class="min-w-full divide-y divide-slate-200 text-sm">
          <thead class="bg-slate-50 text-left text-slate-500">
            <tr>
              <th class="px-4 py-3 font-medium">用户</th>
              <th class="px-4 py-3 font-medium">角色</th>
              <th class="px-4 py-3 font-medium">状态</th>
              <th class="px-4 py-3 font-medium">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-100 bg-white">
            <tr v-for="user in normalizedUsers" :key="user.id">
              <td class="px-4 py-4">
                <div class="flex items-center gap-3">
                  <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-slate-100 text-slate-700">
                    <ShieldUser class="h-4 w-4" />
                  </div>
                  <div>
                    <div class="font-medium text-slate-900">
                      {{ user.display_name || user.username || user.email }}
                      <span v-if="authStore.user?.id === user.id" class="ml-2 rounded-full bg-cyan-50 px-2 py-0.5 text-[11px] text-cyan-700">当前用户</span>
                    </div>
                    <div class="text-xs text-slate-500">{{ user.email }}</div>
                  </div>
                </div>
              </td>
              <td class="px-4 py-4 text-slate-600">{{ user.role }}</td>
              <td class="px-4 py-4">
                <span class="rounded-full px-2.5 py-1 text-xs" :class="user.is_active ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'">
                  {{ user.is_active ? 'active' : 'disabled' }}
                </span>
              </td>
              <td class="px-4 py-4">
                <div class="flex flex-wrap gap-2">
                  <button class="inline-flex items-center gap-2 rounded-full border border-slate-200 px-3 py-1.5 text-xs text-slate-700 transition hover:bg-slate-50" @click="cycleRole(user)">
                    <PencilLine class="h-3.5 w-3.5" />
                    切换角色
                  </button>
                  <button class="rounded-full border border-slate-200 px-3 py-1.5 text-xs text-slate-700 transition hover:bg-slate-50" @click="toggleActive(user)">
                    {{ user.is_active ? '停用' : '启用' }}
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>

        <div v-if="!normalizedUsers.length" class="flex flex-col items-center justify-center gap-3 px-6 py-12 text-center text-slate-500">
          <TriangleAlert class="h-5 w-5" />
          <div>当前没有可展示的用户数据。</div>
        </div>
      </div>

      <div class="mt-4 rounded-[24px] border border-slate-200 bg-slate-50 p-4">
        <div class="flex items-center gap-2 text-sm font-medium text-slate-900">
          <CheckCircle2 class="h-4 w-4 text-emerald-600" />
          当前管理员
        </div>
        <div class="mt-2 text-sm text-slate-600">
          {{ authStore.user?.display_name || authStore.user?.email || '未知用户' }}
        </div>
      </div>
    </section>
  </div>
</template>
