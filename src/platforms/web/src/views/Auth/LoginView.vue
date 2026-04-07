<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Bot, Loader2, ShieldUser } from 'lucide-vue-next'

import { bootstrapAdmin, getBootstrapStatus, getCurrentUser, login, type BootstrapStatus } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const loading = ref(false)
const checking = ref(true)
const error = ref('')
const bootstrapStatus = ref<BootstrapStatus | null>(null)

const email = ref('')
const password = ref('')
const displayName = ref('')

const bootstrapMode = computed(() => bootstrapStatus.value?.needs_bootstrap === true)

onMounted(async () => {
    const token = localStorage.getItem('access_token')
    if (token) {
        try {
            await getCurrentUser()
            await authStore.fetchUser()
            await router.push('/chat')
            return
        } catch {
            localStorage.removeItem('access_token')
        }
    }

    try {
        const response = await getBootstrapStatus()
        bootstrapStatus.value = response.data
    } catch (err: any) {
        error.value = err?.response?.data?.detail || '无法获取初始化状态'
    } finally {
        checking.value = false
    }
})

const handleSubmit = async () => {
    if (!email.value.trim() || !password.value.trim()) {
        error.value = '请输入邮箱和密码'
        return
    }

    loading.value = true
    error.value = ''
    try {
        if (bootstrapMode.value) {
            await bootstrapAdmin({
                email: email.value.trim(),
                password: password.value,
                display_name: displayName.value.trim() || undefined,
                username: email.value.split('@')[0],
            })
        }

        const response = await login(email.value.trim(), password.value)
        authStore.setToken(response.data.access_token)
        await authStore.fetchUser()
        await router.push(bootstrapMode.value && authStore.isAdmin ? '/admin/runtime' : '/chat')
    } catch (err: any) {
        error.value = err?.response?.data?.detail || (bootstrapMode.value ? '初始化失败' : '登录失败')
    } finally {
        loading.value = false
    }
}
</script>

<template>
  <div class="login-page">
    <div class="login-container">
      <section class="login-hero">
        <div class="login-badge">
          <Bot class="h-4 w-4" />
          Web Channel Console
        </div>
        <h1 class="login-title">
          Ikaros
        </h1>
      </section>

      <section class="login-form-section">
        <div v-if="checking" class="login-loading">
          <Loader2 class="h-8 w-8 animate-spin text-cyan-600" />
        </div>

        <template v-else>
          <div class="login-form-header">
            <div>
              <div class="login-form-label">
                {{ bootstrapMode ? 'Bootstrap' : 'Sign In' }}
              </div>
              <h2 class="login-form-title">
                {{ bootstrapMode ? '初始化首个管理员' : '登录 Ikaros' }}
              </h2>
            </div>
            <div class="login-form-icon">
              <ShieldUser class="h-6 w-6" />
            </div>
          </div>

          <p class="login-form-hint">
            {{ bootstrapMode
              ? '当前系统还没有管理员。完成一次初始化后，将自动进入普通登录流。'
              : '请输入管理员创建的账号。Web 端不再提供开放注册。' }}
          </p>

          <div v-if="error" class="login-error">
            {{ error }}
          </div>

          <form class="login-form" @submit.prevent="handleSubmit">
            <div v-if="bootstrapMode">
              <label class="login-label">显示名称</label>
              <input
                v-model="displayName"
                type="text"
                class="login-input"
                placeholder="例如：系统管理员"
              >
            </div>

            <div>
              <label class="login-label">邮箱</label>
              <input
                v-model="email"
                type="email"
                class="login-input"
                placeholder="admin@example.com"
                required
              >
            </div>

            <div>
              <label class="login-label">密码</label>
              <input
                v-model="password"
                type="password"
                minlength="8"
                class="login-input"
                placeholder="至少 8 位"
                required
              >
            </div>

            <button
              type="submit"
              :disabled="loading"
              class="login-submit"
            >
              <Loader2 v-if="loading" class="h-4 w-4 animate-spin" />
              <span>{{ bootstrapMode ? '初始化并登录' : '登录' }}</span>
            </button>
          </form>
        </template>
      </section>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  width: 100%;
  min-height: 100vh;
  background:
    radial-gradient(circle at top, rgba(8, 145, 178, 0.24), transparent 26%),
    linear-gradient(180deg, #020617 0%, #0f172a 50%, #082f49 100%);
  color: #f1f5f9;
  padding: 1rem;
  padding-bottom: 2rem;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}

@media (max-width: 640px) {
  .login-page {
    padding: 0.75rem;
    padding-bottom: 1.5rem;
  }
}

.login-container {
  display: grid;
  gap: 1.5rem;
  max-width: 72rem;
  margin: 0 auto;
  min-height: 0;
}

@media (max-width: 1023px) {
  .login-container {
    gap: 1rem;
  }
}

@media (min-width: 1024px) {
  .login-container {
    grid-template-columns: 1.1fr 0.9fr;
    align-items: start;
  }
}

.login-hero {
  overflow: hidden;
  border-radius: 1.5rem;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(255, 255, 255, 0.06);
  padding: 1.25rem;
  backdrop-filter: blur(12px);
  box-shadow: 0 36px 90px rgba(2, 6, 23, 0.42);
}

@media (min-width: 640px) {
  .login-hero {
    padding: 2rem;
    border-radius: 2rem;
  }
}

@media (max-width: 480px) {
  .login-hero {
    padding: 1rem;
    border-radius: 1.25rem;
  }
}

.login-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  border-radius: 999px;
  border: 1px solid rgba(103, 232, 249, 0.2);
  background: rgba(103, 232, 249, 0.1);
  padding: 0.25rem 0.75rem;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.24em;
  color: #a5f3fc;
}

.login-title {
  margin-top: 1.25rem;
  max-width: 48rem;
  font-size: 1.5rem;
  font-weight: 600;
  line-height: 1.25;
  color: #fff;
}

@media (min-width: 768px) {
  .login-title {
    font-size: 2.25rem;
    margin-top: 1.5rem;
  }
}

@media (max-width: 480px) {
  .login-title {
    font-size: 1.25rem;
    margin-top: 1rem;
  }
}

.login-desc {
  margin-top: 1rem;
  max-width: 40rem;
  font-size: 0.875rem;
  line-height: 1.75;
  color: #cbd5e1;
}

.login-features {
  margin-top: 1.5rem;
  display: grid;
  gap: 1rem;
}

@media (min-width: 640px) {
  .login-features {
    grid-template-columns: 1fr 1fr;
  }
}

.login-feature-card {
  border-radius: 1.25rem;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(2, 6, 23, 0.4);
  padding: 1rem;
}

.login-feature-card.alt {
  background: rgba(255, 255, 255, 0.05);
}

@media (min-width: 640px) {
  .login-feature-card {
    border-radius: 1.75rem;
    padding: 1.25rem;
  }
}

@media (max-width: 480px) {
  .login-feature-card {
    padding: 0.875rem;
  }
}

.login-form-section {
  border-radius: 1.5rem;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: #fff;
  padding: 1.25rem;
  color: #0f172a;
  box-shadow: 0 36px 90px rgba(2, 6, 23, 0.3);
  min-height: auto;
}

@media (min-width: 640px) {
  .login-form-section {
    padding: 2rem;
    border-radius: 2rem;
  }
}

@media (max-width: 480px) {
  .login-form-section {
    padding: 1rem;
    border-radius: 1.25rem;
  }
}

.login-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 20rem;
}

.login-form-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.login-form-label {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.24em;
  color: #94a3b8;
}

.login-form-title {
  margin-top: 0.5rem;
  font-size: 1.375rem;
  font-weight: 600;
}

@media (max-width: 480px) {
  .login-form-title {
    font-size: 1.25rem;
  }
}

.login-form-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2.75rem;
  height: 2.75rem;
  border-radius: 0.875rem;
  background: #cffafe;
  color: #0e7490;
  flex-shrink: 0;
}

@media (min-width: 640px) {
  .login-form-icon {
    width: 3rem;
    height: 3rem;
    border-radius: 1rem;
  }
}

.login-form-hint {
  margin-top: 1rem;
  font-size: 0.875rem;
  line-height: 1.75;
  color: #64748b;
}

.login-error {
  margin-top: 1.25rem;
  border-radius: 1rem;
  border: 1px solid #fecdd3;
  background: #fff1f2;
  padding: 0.75rem 1rem;
  font-size: 0.875rem;
  color: #e11d48;
}

.login-form {
  margin-top: 1.5rem;
  display: grid;
  gap: 1.25rem;
}

.login-label {
  display: block;
  margin-bottom: 0.5rem;
  font-size: 0.875rem;
  font-weight: 500;
  color: #334155;
}

.login-input {
  width: 100%;
  border-radius: 0.875rem;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  padding: 0.875rem 1rem;
  outline: none;
  transition: border-color 0.15s, background-color 0.15s;
  font-size: 1rem;
  min-height: 44px;
}

@media (min-width: 640px) {
  .login-input {
    border-radius: 1rem;
  }
}

.login-input:focus {
  border-color: #22d3ee;
  background: #fff;
}

.login-submit {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  width: 100%;
  border-radius: 0.875rem;
  background: #020617;
  padding: 1rem 1.25rem;
  font-size: 0.9375rem;
  font-weight: 500;
  color: #fff;
  border: none;
  cursor: pointer;
  transition: background-color 0.15s;
  min-height: 48px;
  -webkit-tap-highlight-color: transparent;
}

@media (min-width: 640px) {
  .login-submit {
    border-radius: 1rem;
    padding: 0.875rem 1.25rem;
    font-size: 0.875rem;
  }
}

.login-submit:hover:not(:disabled) {
  background: #1e293b;
}

.login-submit:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
</style>
