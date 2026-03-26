<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Activity, FileCheck2, Loader2, MemoryStick, ShieldCheck, Waypoints } from 'lucide-vue-next'

import { getAdminAudit, getDiagnostics } from '@/api/admin'

const loading = ref(false)
const diagnostics = ref<Record<string, any> | null>(null)
const auditItems = ref<Array<Record<string, any>>>([])

const platformRows = computed(() =>
    Object.entries(diagnostics.value?.platforms || {}).map(([name, enabled]) => ({
        name,
        enabled: Boolean(enabled),
        configured: Boolean(diagnostics.value?.platform_env?.[name]?.configured),
    }))
)

const configRows = computed(() =>
    Object.entries(diagnostics.value?.config_files || {}).map(([key, value]) => ({
        key,
        value,
        ok: typeof value === 'boolean' ? value : true,
    }))
)

const auditTable = computed(() => auditItems.value.slice(0, 20))

const load = async () => {
    loading.value = true
    try {
        const [diagResponse, auditResponse] = await Promise.all([getDiagnostics(), getAdminAudit()])
        diagnostics.value = diagResponse.data
        auditItems.value = auditResponse.data.items
    } finally {
        loading.value = false
    }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6 p-6 md:p-8">
    <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex items-center justify-between">
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Diagnostics</div>
          <h2 class="mt-1 text-2xl font-semibold text-slate-900">运行诊断</h2>
          <p class="mt-2 text-sm leading-7 text-slate-500">
            这里不是原始 JSON 倾倒区，而是给管理员快速判断“当前能不能跑、哪里没配、最近谁改过”的入口。
          </p>
        </div>
        <button class="rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-100" @click="load">
          刷新
        </button>
      </div>

      <div v-if="loading" class="mt-6 flex items-center gap-2 text-sm text-slate-500">
        <Loader2 class="h-4 w-4 animate-spin" />
        正在加载诊断信息
      </div>

      <template v-else-if="diagnostics">
        <div class="mt-6 grid gap-4 xl:grid-cols-4">
          <div class="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
            <div class="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <Activity class="h-4 w-4 text-cyan-600" />
              平台状态
            </div>
            <div class="mt-4 text-3xl font-semibold text-slate-950">
              {{ platformRows.filter(item => item.enabled).length }}
            </div>
            <div class="mt-1 text-sm text-slate-500">已启用平台</div>
          </div>

          <div class="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
            <div class="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <ShieldCheck class="h-4 w-4 text-emerald-600" />
              环境配置
            </div>
            <div class="mt-4 text-3xl font-semibold text-slate-950">
              {{ platformRows.filter(item => item.configured).length }}
            </div>
            <div class="mt-1 text-sm text-slate-500">已配置平台</div>
          </div>

          <div class="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
            <div class="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <FileCheck2 class="h-4 w-4 text-amber-600" />
              配置文件
            </div>
            <div class="mt-4 text-3xl font-semibold text-slate-950">
              {{ configRows.filter(item => item.ok).length }}
            </div>
            <div class="mt-1 text-sm text-slate-500">可用检查项</div>
          </div>

          <div class="rounded-[24px] border border-slate-200 bg-slate-950 p-4 text-slate-100">
            <div class="flex items-center gap-2 text-sm font-semibold">
              <MemoryStick class="h-4 w-4 text-violet-300" />
              Memory
            </div>
            <div class="mt-4 text-2xl font-semibold">{{ diagnostics.memory?.provider || 'unknown' }}</div>
            <div class="mt-1 text-sm text-slate-300">当前 provider</div>
          </div>
        </div>

        <div class="mt-6 grid gap-6 xl:grid-cols-2">
          <section class="rounded-[24px] border border-slate-200 bg-slate-50 p-5">
            <div class="text-sm font-semibold text-slate-900">平台诊断</div>
            <div class="mt-4 space-y-3">
              <div
                v-for="item in platformRows"
                :key="item.name"
                class="flex items-center justify-between rounded-2xl border border-slate-200 bg-white px-4 py-3"
              >
                <div>
                  <div class="text-sm font-medium text-slate-900">{{ item.name }}</div>
                  <div class="text-xs text-slate-500">
                    {{ item.configured ? '环境已配置' : '环境未配置' }}
                  </div>
                </div>
                <span class="rounded-full px-2.5 py-1 text-xs" :class="item.enabled ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'">
                  {{ item.enabled ? 'enabled' : 'disabled' }}
                </span>
              </div>
            </div>
          </section>

          <section class="rounded-[24px] border border-slate-200 bg-slate-50 p-5">
            <div class="text-sm font-semibold text-slate-900">配置与版本</div>
            <div class="mt-4 space-y-3">
              <div
                v-for="item in configRows"
                :key="item.key"
                class="rounded-2xl border border-slate-200 bg-white px-4 py-3"
              >
                <div class="flex items-center justify-between gap-3">
                  <div class="text-sm font-medium text-slate-900">{{ item.key }}</div>
                  <span
                    v-if="typeof item.value === 'boolean'"
                    class="rounded-full px-2.5 py-1 text-xs"
                    :class="item.value ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'"
                  >
                    {{ item.value ? 'ok' : 'missing' }}
                  </span>
                </div>
                <div class="mt-2 break-all text-xs leading-6 text-slate-500">{{ item.value }}</div>
              </div>

              <div class="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                <div class="text-sm font-medium text-slate-900">Git Head</div>
                <div class="mt-2 break-all text-xs leading-6 text-slate-500">{{ diagnostics.version?.git_head || 'unknown' }}</div>
              </div>

              <div class="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                <div class="text-sm font-medium text-slate-900">Memory Providers</div>
                <div class="mt-2 text-xs leading-6 text-slate-500">
                  {{ (diagnostics.memory?.providers || []).join(', ') || 'none' }}
                </div>
              </div>
            </div>
          </section>
        </div>
      </template>
    </section>

    <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex items-center gap-2 text-sm font-semibold text-slate-900">
        <Waypoints class="h-4 w-4 text-cyan-600" />
        管理员审计
      </div>
      <div class="mt-6 overflow-hidden rounded-[24px] border border-slate-200">
        <table class="min-w-full divide-y divide-slate-200 text-sm">
          <thead class="bg-slate-50 text-left text-slate-500">
            <tr>
              <th class="px-4 py-3 font-medium">时间</th>
              <th class="px-4 py-3 font-medium">Actor</th>
              <th class="px-4 py-3 font-medium">Action</th>
              <th class="px-4 py-3 font-medium">Summary</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-100 bg-white">
            <tr v-for="item in auditTable" :key="String(item.ts) + String(item.action)">
              <td class="px-4 py-4 text-slate-500">{{ item.ts }}</td>
              <td class="px-4 py-4 text-slate-700">{{ item.actor }}</td>
              <td class="px-4 py-4 text-slate-700">{{ item.action }}</td>
              <td class="px-4 py-4 text-slate-700">{{ item.summary }}</td>
            </tr>
          </tbody>
        </table>

        <div v-if="!auditTable.length && !loading" class="px-6 py-12 text-center text-sm text-slate-500">
          暂时还没有管理员审计记录。
        </div>
      </div>
    </section>
  </div>
</template>
