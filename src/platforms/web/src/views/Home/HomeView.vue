<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import axios from 'axios'

const authStore = useAuthStore()
const healthStatus = ref('Checking...')

onMounted(async () => {
  try {
    const res = await axios.get('/api/v1/health')
    healthStatus.value = `Backend connection successful: ${res.data.status} - ${res.data.message}`
  } catch (error) {
    if (error instanceof Error) {
        healthStatus.value = `Backend connection failed: ${error.message}`
    } else {
        healthStatus.value = `Backend connection failed: An unknown error occurred`
    }
  }
})
</script>

<template>
  <div class="p-8">
    <h1 class="text-3xl font-bold mb-4">Welcome to Template Project</h1>
    <div class="bg-white p-6 rounded-lg shadow-sm border">
      <h2 class="text-xl font-semibold mb-2">Health Check</h2>
      <p class="text-gray-700">{{ healthStatus }}</p>
    </div>
    <div class="mt-8 bg-white p-6 rounded-lg shadow-sm border">
      <h2 class="text-xl font-semibold mb-2">Current User</h2>
      <pre class="bg-gray-100 p-4 rounded text-sm overflow-auto">{{ authStore.user }}</pre>
    </div>
  </div>
</template>
