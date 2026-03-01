<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { Loader2 } from 'lucide-vue-next'
import { login, register, getCurrentUser } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import logoPng from '@/assets/images/logo.png'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const loading = ref(false)
const error = ref('')
const checkingAuth = ref(true)

const email = ref('')
const password = ref('')
const isRegister = ref(false)

// 检查是否已登录
onMounted(async () => {
    const token = localStorage.getItem('access_token')
    if (token) {
        try {
            await getCurrentUser()
            // 已登录，跳转到首页
            router.push('/')
        } catch {
            // Token 无效，清除
            localStorage.removeItem('access_token')
        }
    }
    checkingAuth.value = false
    
    // 检查 OAuth 回调参数
    const accessToken = route.query.access_token as string
    if (accessToken) {
        localStorage.setItem('access_token', accessToken)
        router.push('/')
    }
    
    // 检查错误
    const errorMsg = route.query.error as string
    if (errorMsg) {
        error.value = `登录失败: ${errorMsg}`
    }
})

const handleAuth = async () => {
    if (!email.value || !password.value) {
        error.value = '请输入邮箱和密码'
        return
    }
    
    loading.value = true
    error.value = ''
    try {
        if (isRegister.value) {
            // 注册流程
            await register(email.value, password.value)
        }
        
        // 登录流程
        const res = await login(email.value, password.value)
        authStore.setToken(res.data.access_token)
        await authStore.fetchUser()
        
        // 成功，跳转
        router.push('/')
    } catch (err: any) {
        if (isRegister.value) {
            error.value = err?.response?.data?.detail || '注册失败，可能邮箱已被占用'
        } else {
            error.value = err?.response?.data?.detail || '登录失败，请检查账号和密码'
        }
    } finally {
        loading.value = false
    }
}
</script>

<template>
    <div class="min-h-screen bg-slate-900 flex items-center justify-center p-4">
        <div class="w-full max-w-md">
            <!-- Logo & Title -->
            <div class="text-center mb-8">
                <img :src="logoPng" alt="X-Bot Logo" class="w-20 h-20 rounded-2xl mb-4 mx-auto shadow-lg bg-slate-800 object-contain p-2" />
                <h1 class="text-3xl font-bold text-white mb-2 shadow-sm">X-Bot</h1>
                <p class="text-gray-200 font-medium shadow-sm">多平台私人智能助理</p>
            </div>

            <!-- Login/Register Card -->
            <div class="bg-white/10 backdrop-blur-lg rounded-2xl p-8 border border-white/10 shadow-2xl">
                <div v-if="checkingAuth" class="flex justify-center py-8">
                    <Loader2 class="w-8 h-8 text-indigo-400 animate-spin" />
                </div>
                
                <template v-else>
                    <div v-if="error" class="mb-6 p-4 bg-red-500/20 border border-red-500/30 rounded-lg">
                        <p class="text-red-300 text-sm">{{ error }}</p>
                    </div>

                    <form @submit.prevent="handleAuth" class="space-y-6">
                        <div>
                            <label class="block text-sm font-medium text-gray-200 mb-1">账号 (邮箱)</label>
                            <input 
                                v-model="email" 
                                type="text"
                                class="w-full px-4 py-3 rounded-xl bg-slate-800/50 border border-slate-700 text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 transition-colors"
                                placeholder="请输入账号"
                                required
                            />
                        </div>
                        
                        <div>
                            <label class="block text-sm font-medium text-gray-200 mb-1">密码</label>
                            <input 
                                v-model="password" 
                                type="password"
                                class="w-full px-4 py-3 rounded-xl bg-slate-800/50 border border-slate-700 text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 transition-colors"
                                placeholder="请输入密码"
                                required
                                minlength="8"
                            />
                            <p v-if="isRegister" class="text-xs text-gray-400 mt-2">密码长度不能少于 8 位</p>
                        </div>

                        <button
                            type="submit"
                            :disabled="loading"
                            class="w-full flex items-center justify-center gap-3 px-6 py-4 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-medium rounded-xl transition-all shadow-lg hover:shadow-indigo-500/25 mt-4"
                        >
                            <span v-if="loading" class="flex items-center gap-2">
                                <Loader2 class="w-5 h-5 animate-spin" />
                                {{ isRegister ? '注册中...' : '登录中...' }}
                            </span>
                            <span v-else>{{ isRegister ? '注册并登录' : '登录' }}</span>
                        </button>

                        <div class="mt-4 text-center">
                            <button 
                                type="button" 
                                @click="isRegister = !isRegister; error = ''" 
                                class="text-indigo-400 hover:text-indigo-300 text-sm transition-colors"
                            >
                                {{ isRegister ? '已有账号？去登录' : '没有账号？去注册' }}
                            </button>
                        </div>
                    </form>
                </template>
            </div>

            <!-- Footer -->
            <p class="text-center text-slate-400 text-xs mt-8 opacity-60">
                &copy; 2026 X-Bot. All rights reserved.
            </p>
        </div>
    </div>
</template>
