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
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@500;600;700;800&display=swap');

*, *::before, *::after {
  box-sizing: border-box;
}

:root {
  --font-display: "Plus Jakarta Sans", "Manrope", "PingFang SC", "Microsoft YaHei", sans-serif;
  --font-body: "Manrope", "Plus Jakarta Sans", "PingFang SC", "Microsoft YaHei", sans-serif;
  --ethereal-surface: #10131a;
  --ethereal-surface-low: #191c22;
  --ethereal-surface-high: #272a31;
  --ethereal-primary: #ffcbd5;
  --ethereal-secondary: #c6c6c6;
  --ethereal-tertiary: #66eaff;
  --ethereal-text-strong: rgba(245, 247, 251, 0.92);
  --ethereal-text-soft: rgba(245, 247, 251, 0.76);
  --ethereal-text-muted: rgba(245, 247, 251, 0.52);
  --ethereal-ghost: rgba(255, 255, 255, 0.08);
  --ethereal-ghost-strong: rgba(255, 255, 255, 0.12);
}

body {
  background:
    radial-gradient(circle at 18% 18%, rgba(255, 203, 213, 0.08), transparent 22%),
    radial-gradient(circle at 84% 82%, rgba(102, 234, 255, 0.08), transparent 28%),
    linear-gradient(180deg, #10131a 0%, #0f141c 100%);
  color: var(--ethereal-text-strong);
  font-family: var(--font-body);
}

.font-display {
  font-family: var(--font-display);
}

.font-body {
  font-family: var(--font-body);
}

.ethereal-page-scope {
  color: var(--ethereal-text-soft);
}

.ethereal-page-scope [class*='bg-white'],
.ethereal-page-scope [class*='bg-slate-50'],
.ethereal-page-scope [class*='bg-[linear-gradient'] {
  background: rgba(25, 28, 34, 0.92);
}

.ethereal-page-scope [class*='bg-slate-950'] {
  background: linear-gradient(135deg, rgba(20, 23, 30, 0.98), rgba(39, 42, 49, 0.96));
}

.ethereal-page-scope [class*='bg-cyan-'],
.ethereal-page-scope [class*='bg-blue-'] {
  background: rgba(102, 234, 255, 0.12);
}

.ethereal-page-scope [class*='bg-rose-'],
.ethereal-page-scope [class*='bg-pink-'] {
  background: rgba(255, 203, 213, 0.14);
}

.ethereal-page-scope [class*='bg-amber-'],
.ethereal-page-scope [class*='bg-orange-'] {
  background: rgba(255, 220, 163, 0.14);
}

.ethereal-page-scope [class*='bg-emerald-'],
.ethereal-page-scope [class*='bg-green-'] {
  background: rgba(126, 225, 179, 0.14);
}

.ethereal-page-scope [class*='border-slate-'],
.ethereal-page-scope [class*='border-cyan-'],
.ethereal-page-scope [class*='border-blue-'],
.ethereal-page-scope [class*='border-rose-'],
.ethereal-page-scope [class*='border-amber-'],
.ethereal-page-scope [class*='border-orange-'],
.ethereal-page-scope [class*='border-emerald-'],
.ethereal-page-scope [class*='border-purple-'] {
  border-color: var(--ethereal-ghost-strong);
}

.ethereal-page-scope [class*='divide-slate-'] > :not([hidden]) ~ :not([hidden]) {
  border-color: var(--ethereal-ghost);
}

.ethereal-page-scope [class*='text-slate-95'],
.ethereal-page-scope [class*='text-slate-9'],
.ethereal-page-scope [class*='text-slate-8'] {
  color: var(--ethereal-text-strong);
}

.ethereal-page-scope [class*='text-slate-7'],
.ethereal-page-scope [class*='text-slate-6'] {
  color: var(--ethereal-text-soft);
}

.ethereal-page-scope [class*='text-slate-5'],
.ethereal-page-scope [class*='text-slate-4'],
.ethereal-page-scope [class*='text-slate-3'] {
  color: var(--ethereal-text-muted);
}

.ethereal-page-scope [class*='text-cyan-'],
.ethereal-page-scope [class*='text-blue-'] {
  color: var(--ethereal-tertiary);
}

.ethereal-page-scope [class*='text-rose-'],
.ethereal-page-scope [class*='text-pink-'] {
  color: var(--ethereal-primary);
}

.ethereal-page-scope [class*='text-amber-'],
.ethereal-page-scope [class*='text-orange-'] {
  color: #ffd9a3;
}

.ethereal-page-scope [class*='rounded-[28px]'],
.ethereal-page-scope [class*='rounded-[24px]'],
.ethereal-page-scope [class*='rounded-2xl'],
.ethereal-page-scope [class*='rounded-xl'] {
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.04);
}

.ethereal-page-scope [class*='shadow-'],
.ethereal-page-scope .shadow-sm,
.ethereal-page-scope .shadow-lg {
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.04);
}

.ethereal-page-scope input:not([type='checkbox']):not([type='radio']):not([type='file']),
.ethereal-page-scope textarea,
.ethereal-page-scope select {
  background: rgba(39, 42, 49, 0.94);
  border-color: var(--ethereal-ghost-strong);
  color: var(--ethereal-text-strong);
  caret-color: var(--ethereal-tertiary);
}

.ethereal-page-scope input::placeholder,
.ethereal-page-scope textarea::placeholder {
  color: var(--ethereal-text-muted);
}

.ethereal-page-scope input:focus,
.ethereal-page-scope textarea:focus,
.ethereal-page-scope select:focus {
  border-color: rgba(102, 234, 255, 0.28);
  box-shadow: 0 0 0 1px rgba(102, 234, 255, 0.2);
}

.ethereal-page-scope button,
.ethereal-page-scope a {
  transition: transform 0.2s ease, background-color 0.2s ease, color 0.2s ease;
}

.ethereal-page-scope button:hover,
.ethereal-page-scope a:hover {
  transform: translateY(-1px);
}

.ethereal-page-scope button[class*='bg-slate-950'],
.ethereal-page-scope a[class*='bg-slate-950'],
.ethereal-page-scope button[class*='bg-orange-500'],
.ethereal-page-scope a[class*='bg-orange-500'],
.ethereal-page-scope button[class*='bg-blue-500'],
.ethereal-page-scope a[class*='bg-blue-500'],
.ethereal-page-scope button[class*='bg-purple-500'],
.ethereal-page-scope a[class*='bg-purple-500'],
.ethereal-page-scope button[class*='bg-red-500'],
.ethereal-page-scope a[class*='bg-red-500'] {
  background: linear-gradient(135deg, rgba(255, 203, 213, 0.96), rgba(102, 234, 255, 0.86));
  color: #10131a;
  box-shadow: none;
}

.ethereal-page-scope button[class*='bg-white'],
.ethereal-page-scope a[class*='bg-white'] {
  color: var(--ethereal-text-soft);
}

.ethereal-page-scope table {
  color: var(--ethereal-text-soft);
}

.ethereal-page-scope thead,
.ethereal-page-scope tbody {
  background: transparent;
}

.ethereal-page-scope [class*='fixed inset-0'] {
  backdrop-filter: blur(18px);
}

.ethereal-page-scope audio {
  filter: saturate(0) brightness(1.05);
}
</style>
