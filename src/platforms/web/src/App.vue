<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import MainLayout from '@/layouts/MainLayout.vue'

const route = useRoute()
const isPublicLayout = computed(() => route.meta.public === true)
</script>

<template>
  <div v-if="isPublicLayout" class="h-full w-full">
    <RouterView />
  </div>
  <template v-else>
    <MainLayout />
  </template>
</template>

<style>
/* Only keep box-sizing for consistent sizing */
*, *::before, *::after {
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
}
</style>
