<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import MainLayout from '@/layouts/MainLayout.vue'

const route = useRoute()
const isPublicLayout = computed(() => route.meta.public === true)
const isFullscreen = computed(() =>
    route.matched.some(r => r.meta.fullscreen === true)
)
</script>

<template>
  <!-- Public pages (login etc) -->
  <div v-if="isPublicLayout" class="h-full w-full">
    <RouterView />
  </div>
  <!-- Fullscreen modules (accounting etc) — no sidebar -->
  <div v-else-if="isFullscreen" class="h-screen w-full">
    <RouterView />
  </div>
  <!-- Normal pages with sidebar -->
  <template v-else>
    <MainLayout />
  </template>
</template>

<style>
*, *::before, *::after {
  box-sizing: border-box;
}

body {
  font-family: "Avenir Next", "SF Pro Display", "PingFang SC", "Microsoft YaHei", sans-serif;
}
</style>
