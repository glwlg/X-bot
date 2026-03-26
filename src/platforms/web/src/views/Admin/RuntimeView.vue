<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Loader2, Save } from 'lucide-vue-next'

import { getRuntimeSnapshot, patchRuntimeSnapshot, type RuntimeSnapshot } from '@/api/admin'

const snapshot = ref<RuntimeSnapshot | null>(null)
const loading = ref(false)
const saving = ref(false)
const corsInput = ref('')
const selectedModels = ref<Record<string, string>>({})

const roleOrder = ['primary', 'routing', 'vision', 'image_generation', 'voice']
const roleLabels: Record<string, string> = {
    primary: 'Primary',
    routing: 'Routing',
    vision: 'Vision',
    image_generation: 'Image Generation',
    voice: 'Voice',
}

const platformEntries = computed(() => Object.entries(snapshot.value?.runtime_config.platforms || {}))
const featureEntries = computed(() => Object.entries(snapshot.value?.runtime_config.features || {}))
const modelCards = computed(() =>
    roleOrder
        .filter(role => Object.prototype.hasOwnProperty.call(selectedModels.value, role))
        .map(role => ({
            role,
            label: roleLabels[role] || role,
            current: selectedModels.value[role] || '',
            options: (snapshot.value?.model_catalog.pools?.[role] || []).length
                ? snapshot.value?.model_catalog.pools?.[role] || []
                : snapshot.value?.model_catalog.all || [],
        }))
)

const load = async () => {
    loading.value = true
    try {
        const response = await getRuntimeSnapshot()
        snapshot.value = response.data
        corsInput.value = (response.data.runtime_config.cors.allowed_origins || []).join('\n')
        selectedModels.value = { ...response.data.model_roles }
    } finally {
        loading.value = false
    }
}

const togglePlatform = (name: string, value: boolean) => {
    if (!snapshot.value) return
    snapshot.value.runtime_config.platforms[name] = value
}

const toggleFeature = (name: string, value: boolean) => {
    if (!snapshot.value) return
    snapshot.value.runtime_config.features[name] = value
}

const save = async () => {
    if (!snapshot.value) return
    saving.value = true
    try {
        const response = await patchRuntimeSnapshot({
            platforms: snapshot.value.runtime_config.platforms,
            features: snapshot.value.runtime_config.features,
            cors_allowed_origins: corsInput.value.split('\n').map(item => item.trim()).filter(Boolean),
            model_roles: selectedModels.value,
            memory_provider: snapshot.value.memory.provider,
        })
        snapshot.value = response.data
        corsInput.value = (response.data.runtime_config.cors.allowed_origins || []).join('\n')
        selectedModels.value = { ...response.data.model_roles }
    } finally {
        saving.value = false
    }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6 p-6 md:p-8">
    <section class="flex items-center justify-between rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div>
        <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Runtime</div>
        <h2 class="mt-1 text-2xl font-semibold text-slate-900">运行配置</h2>
        <p class="mt-2 text-sm leading-7 text-slate-500">
          这里按运行语义组织，而不是把 `models.json` 原样平铺到页面上。
        </p>
      </div>
      <button class="inline-flex items-center gap-2 rounded-2xl bg-slate-950 px-4 py-3 text-sm font-medium text-white transition hover:bg-slate-800 disabled:opacity-60" :disabled="saving" @click="save">
        <Loader2 v-if="saving" class="h-4 w-4 animate-spin" />
        <Save v-else class="h-4 w-4" />
        保存变更
      </button>
    </section>

    <div v-if="loading" class="flex items-center gap-2 rounded-[28px] border border-slate-200 bg-white px-5 py-4 text-sm text-slate-500 shadow-sm">
      <Loader2 class="h-4 w-4 animate-spin" />
      正在加载配置
    </div>

    <template v-else-if="snapshot">
      <section class="grid gap-6 xl:grid-cols-2">
        <div class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
          <div class="text-sm font-semibold text-slate-900">平台开关</div>
          <div class="mt-4 space-y-3">
            <label v-for="[name, enabled] in platformEntries" :key="name" class="flex items-center justify-between rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
              <div>
                <div class="text-sm text-slate-700">{{ name }}</div>
                <div class="text-xs text-slate-500">控制对应 channel 是否注册和启动</div>
              </div>
              <input :checked="enabled" type="checkbox" class="h-4 w-4" @change="togglePlatform(name, ($event.target as HTMLInputElement).checked)">
            </label>
          </div>
        </div>

        <div class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
          <div class="text-sm font-semibold text-slate-900">功能开关</div>
          <div class="mt-4 space-y-3">
            <label v-for="[name, enabled] in featureEntries" :key="name" class="flex items-center justify-between rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
              <div>
                <div class="text-sm text-slate-700">{{ name }}</div>
                <div class="text-xs text-slate-500">控制 Web console 与后台功能入口</div>
              </div>
              <input :checked="enabled" type="checkbox" class="h-4 w-4" @change="toggleFeature(name, ($event.target as HTMLInputElement).checked)">
            </label>
          </div>
        </div>
      </section>

      <section class="grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_360px]">
        <div class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
          <div class="flex items-center justify-between gap-4">
            <div>
              <div class="text-sm font-semibold text-slate-900">模型角色</div>
              <div class="mt-1 text-sm text-slate-500">每个角色只展示它自己的模型池，缺省时才回退到全量模型。</div>
            </div>
          </div>

          <div class="mt-4 space-y-4">
            <div v-for="card in modelCards" :key="card.role" class="rounded-2xl border border-slate-200 bg-slate-50 p-4">
              <div class="flex items-center justify-between gap-3">
                <div>
                  <div class="text-xs uppercase tracking-[0.2em] text-slate-400">{{ card.label }}</div>
                  <div class="mt-1 text-sm text-slate-500">当前池内 {{ card.options.length }} 个模型</div>
                </div>
                <span class="rounded-full bg-white px-2.5 py-1 text-xs text-slate-500">{{ card.role }}</span>
              </div>
              <select v-model="selectedModels[card.role]" class="mt-3 w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none focus:border-cyan-400">
                <option v-for="model in card.options" :key="model" :value="model">{{ model }}</option>
              </select>
              <div class="mt-2 text-xs text-slate-500">当前选择：{{ card.current }}</div>
            </div>
          </div>
        </div>

        <div class="space-y-6">
          <div class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
            <div class="text-sm font-semibold text-slate-900">CORS Allowlist</div>
            <div class="mt-1 text-sm text-slate-500">每行一个 Origin，生产环境不要使用宽泛通配。</div>
            <textarea v-model="corsInput" class="mt-4 min-h-[220px] w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-7 outline-none focus:border-cyan-400 focus:bg-white" placeholder="https://app.example.com&#10;http://127.0.0.1:8764" />
          </div>

          <div class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
            <div class="text-sm font-semibold text-slate-900">Memory Provider</div>
            <div class="mt-1 text-sm text-slate-500">这里只切换 provider，不在 Web 里直接改密钥。</div>
            <select v-model="snapshot.memory.provider" class="mt-4 w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none focus:border-cyan-400 focus:bg-white">
              <option v-for="provider in snapshot.memory.providers" :key="provider" :value="provider">{{ provider }}</option>
            </select>
            <div class="mt-4 rounded-2xl bg-slate-950 p-4 text-xs leading-6 text-slate-200">
              {{ JSON.stringify(snapshot.memory.active_settings, null, 2) }}
            </div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
