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
  <div v-if="isPublicLayout" class="w-full min-h-screen">
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
html, body {
  margin: 0;
  padding: 0;
  width: 100%;
}

*, *::before, *::after {
  box-sizing: border-box;
}

:root {
  --font-display: Inter, "SF Pro Display", "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
  --font-body: Inter, "SF Pro Text", "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
  --panel-border: #e5ebf3;
  --panel-muted: #f6f9fd;
  --panel-soft: #fbfdff;
  --text-strong: #101828;
  --text-body: #344054;
  --text-muted: #667085;
  --text-subtle: #98a2b3;
  --brand-blue: #2f7cf6;
  --brand-blue-dark: #1469f2;
  --brand-blue-soft: #e8f1ff;
  --success: #22c55e;
  --warning: #f59e0b;
  --danger: #ef4444;
  --shadow-card: 0 18px 44px rgba(16, 24, 40, 0.04);
}

:root.dark {
  --color-bg-primary: #ffffff;
  --color-bg-secondary: #f7f9fc;
  --color-bg-tertiary: #f1f5f9;
  --color-bg-elevated: #ffffff;
  --color-text-primary: #101828;
  --color-text-secondary: #344054;
  --color-text-tertiary: #667085;
  --color-text-muted: #98a2b3;
  --color-border-primary: #e5ebf3;
  --color-border-secondary: #edf2f7;
}

body {
  background: #f7f9fc;
  color: var(--text-body);
  font-family: var(--font-body);
  letter-spacing: 0;
}

button,
input,
textarea,
select {
  font: inherit;
  letter-spacing: 0;
}

button {
  cursor: pointer;
}

button:disabled {
  cursor: not-allowed;
}

.font-display,
.font-body {
  font-family: var(--font-display);
}

.rounded-\[30px\],
.rounded-\[28px\],
.rounded-\[24px\] {
  border-radius: 14px !important;
}

.rounded-2xl {
  border-radius: 10px !important;
}

.rounded-xl {
  border-radius: 8px !important;
}

.shadow-sm,
.shadow-lg,
[class*='shadow-'] {
  box-shadow: var(--shadow-card) !important;
}

input:not([type='checkbox']):not([type='radio']):not([type='file']),
textarea,
select {
  border-color: var(--panel-border) !important;
  background: #ffffff !important;
  color: var(--text-strong) !important;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.02);
}

input::placeholder,
textarea::placeholder {
  color: var(--text-subtle);
}

input:focus,
textarea:focus,
select:focus {
  border-color: #7fb2ff !important;
  box-shadow: 0 0 0 3px rgba(47, 124, 246, 0.10) !important;
}

table {
  border-collapse: separate;
  border-spacing: 0;
}

thead {
  background: #f8fafc;
}

tbody {
  background: #ffffff;
}

.bg-slate-950 {
  background: #101828 !important;
}

.bg-blue-500,
.bg-orange-500,
.bg-purple-500,
.bg-red-500 {
  background: var(--brand-blue) !important;
  color: #ffffff !important;
}

.hover\:bg-blue-600:hover,
.hover\:bg-orange-600:hover,
.hover\:bg-purple-600:hover,
.hover\:bg-red-600:hover {
  background: var(--brand-blue-dark) !important;
}

.bg-slate-950 .text-slate-500,
.bg-slate-950 .text-slate-400 {
  color: #cbd5e1 !important;
}

.text-cyan-600,
.text-blue-600,
.text-purple-600 {
  color: var(--brand-blue) !important;
}

.bg-cyan-50,
.bg-blue-50,
.bg-indigo-100,
.bg-violet-100,
.bg-purple-50 {
  background: var(--brand-blue-soft) !important;
}

.border-cyan-200,
.border-cyan-300,
.border-blue-200 {
  border-color: #9ec5ff !important;
}

.text-emerald-700,
.text-green-600 {
  color: #16a34a !important;
}

.bg-emerald-50,
.bg-green-50 {
  background: #ecfdf3 !important;
}

.border-emerald-200 {
  border-color: #b7efc6 !important;
}

@media (max-width: 768px) {
  .p-6,
  .md\:p-8 {
    padding: 16px !important;
  }
}
</style>
