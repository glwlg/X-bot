<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { Gitlab, Loader2 } from 'lucide-vue-next'
import { redirectToGitLabAuth, getCurrentUser } from '@/api/auth'
import logoSvg from '@/assets/images/logo.svg'
import loginBg from '@/assets/images/login_bg.png'

const router = useRouter()
const route = useRoute()
const loading = ref(false)
const error = ref('')
const checkingAuth = ref(true)

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

const handleGitLabLogin = async () => {
    loading.value = true
    try {
        // 获取授权 URL 并重定向到 GitLab
        await redirectToGitLabAuth()
    } catch (err) {
        error.value = '获取授权链接失败，请重试'
        loading.value = false
    }
}
</script>

<template>
    <div class="min-h-screen bg-slate-900 bg-cover bg-center flex items-center justify-center p-4" :style="{ backgroundImage: `url(${loginBg})` }">
        <div class="w-full max-w-md">
            <!-- Logo & Title -->
            <div class="text-center mb-8">
                <img :src="logoSvg" alt="OpsCore Logo" class="w-20 h-20 rounded-2xl mb-4 mx-auto shadow-lg bg-slate-800" />
                <h1 class="text-3xl font-bold text-white mb-2 shadow-sm">OpsCore</h1>
                <p class="text-gray-200 font-medium shadow-sm">统一运维管理平台</p>
            </div>

            <!-- Login Card -->
            <div class="bg-theme-elevated/10 backdrop-blur-lg rounded-2xl p-8 border border-white/10 shadow-2xl">
                <div v-if="checkingAuth" class="flex justify-center py-8">
                    <Loader2 class="w-8 h-8 text-purple-400 animate-spin" />
                </div>
                
                <template v-else>
                    <div v-if="error" class="mb-6 p-4 bg-red-500/20 border border-red-500/30 rounded-lg">
                        <p class="text-red-300 text-sm">{{ error }}</p>
                    </div>

                    <div class="space-y-4">
                        <button
                            @click="handleGitLabLogin"
                            :disabled="loading"
                            class="w-full flex items-center justify-center gap-3 px-6 py-4 bg-orange-600 hover:bg-orange-700 disabled:opacity-50 text-white font-medium rounded-xl transition-all shadow-lg hover:shadow-orange-500/25"
                        >
                            <Gitlab class="w-5 h-5" />
                            <span v-if="loading">跳转中...</span>
                            <span v-else>使用 GitLab 账号登录</span>
                        </button>
                    </div>

                    <div class="mt-8 pt-6 border-t border-white/10">
                        <p class="text-center text-theme-muted text-sm">
                            使用集团 GitLab 账号进行身份验证
                        </p>
                    </div>
                </template>
            </div>

            <!-- Footer -->
            <p class="text-center text-slate-300 text-xs mt-8 opacity-60">
                &copy; 2026 OpsCore. All rights reserved.
            </p>
        </div>
    </div>
</template>
