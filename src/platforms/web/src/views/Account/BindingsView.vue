<script setup lang="ts">
import axios from 'axios'
import { computed, onMounted, ref, watch } from 'vue'
import { CheckCircle2, Link2, Loader2, RefreshCw, Trash2, TriangleAlert } from 'lucide-vue-next'

import { deleteMyBinding, listMyBindings, saveMyBinding, type ChannelBinding } from '@/api/binding'

type PlatformKey = 'telegram' | 'discord' | 'dingtalk' | 'weixin'

const platformOptions: Array<{ value: PlatformKey; label: string; hint: string }> = [
    { value: 'telegram', label: 'Telegram', hint: '填写 Telegram 机器人看到的用户 ID。' },
    { value: 'discord', label: 'Discord', hint: '填写 Discord 渠道中的用户 ID。' },
    { value: 'dingtalk', label: '钉钉', hint: '填写钉钉会话中的用户标识。' },
    { value: 'weixin', label: '微信 / 企微', hint: '填写微信或企微渠道中的用户标识。' },
]
const defaultPlatformMeta = platformOptions[0]!

const bindings = ref<ChannelBinding[]>([])
const loading = ref(false)
const saving = ref(false)
const deletingId = ref<number | null>(null)
const errorText = ref('')
const successText = ref('')
const form = ref<{ platform: PlatformKey; platform_user_id: string }>({
    platform: 'telegram',
    platform_user_id: '',
})

const bindingsByPlatform = computed(() =>
    new Map(bindings.value.map(item => [item.platform, item]))
)

const selectedMeta = computed(() =>
    platformOptions.find(item => item.value === form.value.platform) || defaultPlatformMeta
)

const selectedBinding = computed(() =>
    bindingsByPlatform.value.get(form.value.platform)
)

const submitLabel = computed(() =>
    selectedBinding.value ? '更新绑定' : '保存绑定'
)

const parseErrorMessage = (error: unknown, fallback: string) => {
    if (axios.isAxiosError(error)) {
        const detail = error.response?.data?.detail
        if (Array.isArray(detail) && detail.length > 0) {
            return String(detail[0]?.msg || fallback)
        }
        if (typeof detail === 'string' && detail.trim()) {
            return detail
        }
    }
    return fallback
}

const syncFormFromSelectedBinding = () => {
    form.value.platform_user_id = selectedBinding.value?.platform_user_id || ''
}

const load = async () => {
    loading.value = true
    errorText.value = ''
    try {
        const response = await listMyBindings()
        bindings.value = Array.isArray(response.data) ? response.data : []
        syncFormFromSelectedBinding()
    } catch (error) {
        errorText.value = parseErrorMessage(error, '绑定信息加载失败')
    } finally {
        loading.value = false
    }
}

const submit = async () => {
    errorText.value = ''
    successText.value = ''
    const platform_user_id = form.value.platform_user_id.trim()
    if (!platform_user_id) {
        errorText.value = '请先填写渠道账户 ID。'
        return
    }

    saving.value = true
    try {
        const existed = Boolean(selectedBinding.value)
        await saveMyBinding({
            platform: form.value.platform,
            platform_user_id,
        })
        successText.value = existed ? '渠道绑定已更新。' : '渠道绑定已保存。'
        await load()
    } catch (error) {
        errorText.value = parseErrorMessage(error, '保存绑定失败')
    } finally {
        saving.value = false
    }
}

const removeBinding = async (binding: ChannelBinding) => {
    errorText.value = ''
    successText.value = ''
    deletingId.value = binding.id
    try {
        await deleteMyBinding(binding.id)
        successText.value = `${binding.platform} 绑定已移除。`
        await load()
    } catch (error) {
        errorText.value = parseErrorMessage(error, '移除绑定失败')
    } finally {
        deletingId.value = null
    }
}

watch(() => form.value.platform, () => {
    errorText.value = ''
    successText.value = ''
    syncFormFromSelectedBinding()
})

onMounted(load)
</script>

<template>
  <div class="grid gap-6 p-6 md:grid-cols-[380px_minmax(0,1fr)] md:p-8">
    <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex items-center gap-3">
        <div class="flex h-12 w-12 items-center justify-center rounded-2xl bg-emerald-100 text-emerald-700">
          <Link2 class="h-5 w-5" />
        </div>
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Channels</div>
          <h2 class="text-xl font-semibold text-slate-900">绑定渠道账户</h2>
        </div>
      </div>

      <form class="mt-6 space-y-4" @submit.prevent="submit">
        <select v-model="form.platform" class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none focus:border-cyan-400 focus:bg-white">
          <option v-for="item in platformOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>

        <input
          v-model="form.platform_user_id"
          type="text"
          class="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 outline-none focus:border-cyan-400 focus:bg-white"
          :placeholder="`${selectedMeta.label} 账户 ID`"
        >

        <div class="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
          {{ selectedMeta.hint }}
        </div>

        <div v-if="selectedBinding" class="rounded-2xl border border-cyan-200 bg-cyan-50 px-4 py-3 text-sm text-cyan-700">
          当前已绑定：{{ selectedBinding.platform_user_id }}
        </div>

        <div v-if="errorText" class="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {{ errorText }}
        </div>

        <div v-if="successText" class="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          {{ successText }}
        </div>

        <div class="flex gap-3">
          <button class="inline-flex flex-1 items-center justify-center gap-2 rounded-2xl bg-slate-950 px-4 py-3 text-sm font-medium text-white transition hover:bg-slate-800 disabled:opacity-60" :disabled="saving">
            <Loader2 v-if="saving" class="h-4 w-4 animate-spin" />
            {{ submitLabel }}
          </button>

          <button type="button" class="inline-flex items-center justify-center rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700 transition hover:bg-slate-100" :disabled="loading" @click="load">
            <RefreshCw class="h-4 w-4" />
          </button>
        </div>
      </form>
    </section>

    <section class="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
      <div class="flex items-center justify-between gap-3">
        <div>
          <div class="text-xs uppercase tracking-[0.24em] text-slate-400">Bindings</div>
          <h2 class="text-xl font-semibold text-slate-900">当前渠道</h2>
        </div>
        <div class="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-600">
          {{ bindings.length }} 个绑定
        </div>
      </div>

      <div v-if="loading" class="mt-6 flex items-center gap-2 text-sm text-slate-500">
        <Loader2 class="h-4 w-4 animate-spin" />
        正在加载绑定信息
      </div>

      <div v-else-if="!bindings.length" class="mt-6 flex min-h-[240px] flex-col items-center justify-center gap-3 rounded-[24px] border border-dashed border-slate-200 bg-slate-50 px-6 text-center text-slate-500">
        <TriangleAlert class="h-5 w-5" />
        <div>当前还没有渠道绑定。</div>
      </div>

      <div v-else class="mt-6 grid gap-4 md:grid-cols-2">
        <article
          v-for="binding in bindings"
          :key="binding.id"
          class="rounded-[24px] border border-slate-200 bg-slate-50 p-5"
        >
          <div class="flex items-start justify-between gap-4">
            <div>
              <div class="text-xs uppercase tracking-[0.24em] text-slate-400">{{ binding.platform }}</div>
              <div class="mt-2 text-lg font-semibold text-slate-900">{{ binding.platform_user_id }}</div>
            </div>

            <button
              type="button"
              class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-700 transition hover:bg-slate-100 disabled:opacity-60"
              :disabled="deletingId === binding.id"
              @click="removeBinding(binding)"
            >
              <Loader2 v-if="deletingId === binding.id" class="h-3.5 w-3.5 animate-spin" />
              <Trash2 v-else class="h-3.5 w-3.5" />
              移除
            </button>
          </div>

          <div class="mt-4 flex items-center gap-2 text-sm text-emerald-700">
            <CheckCircle2 class="h-4 w-4" />
            已关联到当前 Web 账号
          </div>
        </article>
      </div>
    </section>
  </div>
</template>
