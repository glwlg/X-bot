import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getCurrentUser, logout as apiLogout, type UserInfo } from '../api/auth'

export const useAuthStore = defineStore('auth', () => {
    const user = ref<UserInfo | null>(null)
    const token = ref<string | null>(localStorage.getItem('access_token'))

    const isAuthenticated = computed(() => !!token.value)
    const isAdmin = computed(() => user.value?.role === 'admin')
    const isOperator = computed(() => user.value?.role === 'operator' || isAdmin.value)

    async function fetchUser() {
        if (!token.value) return null
        try {
            const res = await getCurrentUser()
            user.value = res.data
            return user.value
        } catch (error) {
            user.value = null
            token.value = null
            localStorage.removeItem('access_token')
            throw error
        }
    }

    function setToken(newToken: string) {
        token.value = newToken
        localStorage.setItem('access_token', newToken)
    }

    function clearToken() {
        token.value = null
        user.value = null
        localStorage.removeItem('access_token')
    }

    async function logout() {
        try {
            await apiLogout()
        } finally {
            clearToken()
        }
    }

    return {
        user,
        token,
        isAuthenticated,
        isAdmin,
        isOperator,
        fetchUser,
        setToken,
        clearToken,
        logout
    }
})
