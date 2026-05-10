<script setup lang="ts">
import axios from 'axios'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
    Bot,
    CheckCircle2,
    FileText,
    Eye,
    Loader2,
    RadioTower,
    Save,
    Settings2,
    ShieldUser,
    Sparkles,
} from 'lucide-vue-next'

import {
    generateRuntimeDoc,
    getRuntimeSnapshot,
    patchRuntimeSnapshot,
    type RuntimeGeneratePayload,
    type RuntimePatchPayload,
    type RuntimeSnapshot,
} from '@/api/runtime'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const loading = ref(false)
const saving = ref(false)
const generatingSoul = ref(false)
const generatingUser = ref(false)
const errorText = ref('')
const successText = ref('')
const restartRequired = ref(false)
const corsInput = ref('')

const form = ref<RuntimeSnapshot | null>(null)
const adminPassword = ref('')
const adminIdsInput = ref('')
const soulBrief = ref('')
const userBrief = ref('')

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

const cloneSnapshot = (payload: RuntimeSnapshot) =>
    JSON.parse(JSON.stringify(payload)) as RuntimeSnapshot

const hydrate = (payload: RuntimeSnapshot) => {
    form.value = cloneSnapshot(payload)
    adminIdsInput.value = (payload.channels.admin_user_ids || []).join('\n')
    corsInput.value = (payload.cors_allowed_origins || []).join('\n')
    restartRequired.value = false
}

const load = async () => {
    loading.value = true
    errorText.value = ''
    try {
        const response = await getRuntimeSnapshot()
        hydrate(response.data)
    } catch (error) {
        errorText.value = parseErrorMessage(error, '运行配置加载失败')
    } finally {
        loading.value = false
    }
}

const checklist = computed(() => {
    if (!form.value) return []
    const status = form.value.status
    return [
        { label: '管理员绑定', ok: status.admin_bound },
        { label: 'Primary 模型', ok: status.primary_ready },
        { label: 'Routing 模型', ok: status.routing_ready },
        { label: 'SOUL.MD', ok: status.soul_ready },
        { label: 'USER.md', ok: status.user_ready },
        { label: '渠道配置', ok: status.channels_ready },
    ]
})

const parseAdminUserIds = () =>
    adminIdsInput.value
        .split(/[\n,]/)
        .map(item => item.trim())
        .filter(Boolean)

const primaryModelKey = computed(() => form.value?.model_status.primary.model_key || '')
const canGenerateDocs = computed(() => Boolean(form.value?.model_status.primary.ready && primaryModelKey.value))

const save = async () => {
    if (!form.value) return
    saving.value = true
    errorText.value = ''
    successText.value = ''
    try {
        const payload: RuntimePatchPayload = {
            admin_user: {
                email: form.value.admin_user.email.trim(),
                username: form.value.admin_user.username?.trim() || '',
                display_name: form.value.admin_user.display_name?.trim() || '',
                ...(adminPassword.value.trim() ? { password: adminPassword.value } : {}),
            },
            docs: {
                soul_content: form.value.docs.soul_content,
                user_content: form.value.docs.user_content,
            },
            channels: {
                admin_user_ids: parseAdminUserIds(),
                telegram: {
                    enabled: form.value.channels.telegram.enabled,
                    bot_token: form.value.channels.telegram.bot_token,
                },
                discord: {
                    enabled: form.value.channels.discord.enabled,
                    bot_token: form.value.channels.discord.bot_token,
                },
                dingtalk: {
                    enabled: form.value.channels.dingtalk.enabled,
                    client_id: form.value.channels.dingtalk.client_id,
                    client_secret: form.value.channels.dingtalk.client_secret,
                },
                weixin: {
                    enabled: form.value.channels.weixin.enabled,
                    base_url: form.value.channels.weixin.base_url,
                    cdn_base_url: form.value.channels.weixin.cdn_base_url,
                },
                web: {
                    enabled: form.value.channels.web.enabled,
                },
            },
            features: form.value.features,
            cors_allowed_origins: corsInput.value
                .split('\n')
                .map(item => item.trim())
                .filter(Boolean),
            memory_provider: form.value.memory.provider,
        }
        const response = await patchRuntimeSnapshot(payload)
        hydrate(response.data.snapshot)
        adminPassword.value = ''
        restartRequired.value = response.data.restart_required
        successText.value = response.data.restart_required
            ? '运行配置已保存，凭证相关改动需要重启 ikaros core。'
            : '运行配置已保存'
        await authStore.fetchUser()
    } catch (error) {
        errorText.value = parseErrorMessage(error, '保存运行配置失败')
    } finally {
        saving.value = false
    }
}

const generateDoc = async (payload: RuntimeGeneratePayload) => {
    if (!form.value) return
    errorText.value = ''
    successText.value = ''
    const isSoul = payload.kind === 'soul'
    if (isSoul) {
        generatingSoul.value = true
    } else {
        generatingUser.value = true
    }
    try {
        const response = await generateRuntimeDoc(payload)
        if (response.data.kind === 'soul') {
            form.value.docs.soul_content = response.data.content
        } else {
            form.value.docs.user_content = response.data.content
        }
        successText.value = `${response.data.kind.toUpperCase()} 文档已生成，确认后记得保存。`
    } catch (error) {
        errorText.value = parseErrorMessage(error, 'AI 生成文档失败')
    } finally {
        if (isSoul) {
            generatingSoul.value = false
        } else {
            generatingUser.value = false
        }
    }
}

onMounted(load)
</script>

<template>
  <div class="runtime-page">
    <section class="runtime-hero">
      <div>
        <h1>运行配置 / Runtime</h1>
        <p>首次安装就在这里完成管理员、文档、渠道和运行项的配置，再进入模型配置补齐或调整模型目录。</p>
      </div>
      <div class="runtime-actions">
        <button type="button" class="secondary-btn" :disabled="loading" @click="router.push('/admin/models')">
          <Settings2 class="h-4 w-4" />
          去模型配置
        </button>
        <button type="button" class="primary-btn" :disabled="saving || loading || !form" @click="save">
          <Loader2 v-if="saving" class="h-4 w-4 animate-spin" />
          <Save v-else class="h-4 w-4" />
          保存运行配置
        </button>
      </div>

      <div v-if="checklist.length" class="runtime-status-row">
        <span class="environment-ready">
          <i />
          环境就绪
        </span>
        <span
          v-for="item in checklist"
          :key="item.label"
          class="runtime-chip"
          :class="{ ready: item.ok }"
        >
          {{ item.label }} {{ item.ok ? '已就绪' : '待完成' }}
        </span>
      </div>

      <div v-if="errorText" class="notice danger">{{ errorText }}</div>
      <div v-if="successText" class="notice success">{{ successText }}</div>
      <div v-if="restartRequired && form" class="notice warning">{{ form.restart_notice }}</div>
    </section>

    <div v-if="loading" class="loading-card">
      <Loader2 class="h-4 w-4 animate-spin" />
      正在加载运行配置
    </div>

    <template v-else-if="form">
      <section class="runtime-main-grid">
        <article class="runtime-card admin-card">
          <div class="card-title-row">
            <div class="card-icon blue">
              <ShieldUser class="h-5 w-5" />
            </div>
            <h2>管理员与访问</h2>
          </div>

          <div class="admin-form-grid">
            <label>
              <span>邮箱</span>
              <input v-model="form.admin_user.email" type="email">
            </label>
            <label>
              <span>用户名</span>
              <input v-model="form.admin_user.username" type="text">
            </label>
            <label>
              <span>显示名称</span>
              <input v-model="form.admin_user.display_name" type="text">
            </label>
            <label>
              <span>重设密码</span>
              <div class="password-field">
                <input v-model="adminPassword" type="password" minlength="8" placeholder="留空表示不修改">
                <Eye class="h-4 w-4" />
              </div>
            </label>
          </div>

          <label class="full-field">
            <span>ADMIN_USER_IDS（每行一个 ID）</span>
            <textarea v-model="adminIdsInput" placeholder="每行一个 ID，也支持逗号分隔" />
          </label>

          <div class="info-strip">
            <CheckCircle2 class="h-4 w-4" />
            当前 Web 管理员用户 ID：<strong>{{ form.admin_user.current_admin_user_id }}</strong>
          </div>
        </article>

        <aside class="runtime-side">
          <article class="runtime-card sequence-card">
            <h2>配置推荐顺序</h2>
            <ol>
              <li class="done"><span>1</span>先完成模型配置并补齐 Primary / Routing <CheckCircle2 class="h-4 w-4" /></li>
              <li class="done"><span>2</span>生成或编辑 SOUL / USER 文档 <CheckCircle2 class="h-4 w-4" /></li>
              <li class="done"><span>3</span>开启你需要的渠道并填写凭证 <CheckCircle2 class="h-4 w-4" /></li>
              <li><span>4</span>保存本页运行配置 <i /></li>
              <li><span>5</span>返回控制面板开始使用系统 <i /></li>
            </ol>
          </article>

          <article class="runtime-card model-status-card">
            <div class="side-card-head">
              <div class="card-title-row compact">
                <div class="card-icon purple">
                  <Bot class="h-5 w-5" />
                </div>
                <h2>模型状态</h2>
              </div>
              <button type="button" @click="router.push('/admin/models')">查看详情</button>
            </div>
            <div class="model-status-table">
              <div>
                <span>Primary 模型</span>
                <strong><i />{{ form.model_status.primary.model_key || '未配置' }}</strong>
              </div>
              <div>
                <span>Routing 模型</span>
                <strong><i />{{ form.model_status.routing.model_key || '未配置' }}</strong>
              </div>
            </div>
          </article>
        </aside>
      </section>

      <section class="doc-grid">
        <article class="runtime-card doc-card">
          <div class="doc-head">
            <div class="card-title-row compact">
              <div class="card-icon green">
                <Sparkles class="h-5 w-5" />
              </div>
              <div>
                <h2>IKAROS SOUL.md</h2>
                <p>文件路径：{{ form.docs.soul_path }}</p>
              </div>
            </div>
            <button type="button" :disabled="generatingSoul || !canGenerateDocs" @click="generateDoc({ kind: 'soul', brief: soulBrief, current_content: form.docs.soul_content, model_key: primaryModelKey })">
              <Loader2 v-if="generatingSoul" class="h-4 w-4 animate-spin" />
              <Sparkles v-else class="h-4 w-4" />
              AI 生成 SOUL
            </button>
          </div>
          <label>
            <span>AI 生成补充要求（可选）</span>
            <textarea v-model="soulBrief" class="brief-field" placeholder="例如：性格设定、价值观、行为准则、核心能力、安全边界等..." />
          </label>
          <textarea v-model="form.docs.soul_content" class="doc-editor" />
          <footer>字数：{{ form.docs.soul_content.length }}</footer>
        </article>

        <article class="runtime-card doc-card">
          <div class="doc-head">
            <div class="card-title-row compact">
              <div class="card-icon purple">
                <FileText class="h-5 w-5" />
              </div>
              <div>
                <h2>管理员 USER.md</h2>
                <p>文件路径：{{ form.docs.user_path }}</p>
              </div>
            </div>
            <button type="button" :disabled="generatingUser || !canGenerateDocs" @click="generateDoc({ kind: 'user', brief: userBrief, current_content: form.docs.user_content, model_key: primaryModelKey })">
              <Loader2 v-if="generatingUser" class="h-4 w-4 animate-spin" />
              <Sparkles v-else class="h-4 w-4" />
              AI 生成 USER
            </button>
          </div>
          <label>
            <span>AI 生成补充要求（可选）</span>
            <textarea v-model="userBrief" class="brief-field" placeholder="例如：我希望称呼我阿伟、沟通风格、注意事项、响应偏好等..." />
          </label>
          <textarea v-model="form.docs.user_content" class="doc-editor" />
          <footer>字数：{{ form.docs.user_content.length }}</footer>
        </article>
      </section>

      <section class="runtime-card channels-card">
        <div class="card-title-row">
          <div class="card-icon amber">
            <RadioTower class="h-5 w-5" />
          </div>
          <h2>渠道与运行项</h2>
        </div>

        <div class="channel-grid">
          <article class="channel-item">
            <header>
              <div><strong>Telegram</strong><span>{{ form.channels.telegram.configured ? '凭证已配置' : '缺少凭证' }}</span></div>
              <input v-model="form.channels.telegram.enabled" type="checkbox">
            </header>
            <label><span>Bot Token</span><input v-model="form.channels.telegram.bot_token" type="text"></label>
          </article>
          <article class="channel-item">
            <header>
              <div><strong>Discord</strong><span>{{ form.channels.discord.configured ? '凭证已配置' : '缺少凭证' }}</span></div>
              <input v-model="form.channels.discord.enabled" type="checkbox">
            </header>
            <label><span>Bot Token</span><input v-model="form.channels.discord.bot_token" type="text"></label>
          </article>
          <article class="channel-item">
            <header>
              <div><strong>DingTalk</strong><span>{{ form.channels.dingtalk.configured ? '凭证已配置' : '缺少凭证' }}</span></div>
              <input v-model="form.channels.dingtalk.enabled" type="checkbox">
            </header>
            <label><span>Client ID</span><input v-model="form.channels.dingtalk.client_id" type="text"></label>
            <label><span>Client Secret</span><input v-model="form.channels.dingtalk.client_secret" type="text"></label>
          </article>
          <article class="channel-item">
            <header>
              <div><strong>Weixin</strong><span>{{ form.channels.weixin.configured ? '连接参数已就绪' : '缺少连接参数' }}</span></div>
              <input v-model="form.channels.weixin.enabled" type="checkbox">
            </header>
            <label><span>Base URL</span><input v-model="form.channels.weixin.base_url" type="text"></label>
            <label><span>CDN Base URL</span><input v-model="form.channels.weixin.cdn_base_url" type="text"></label>
          </article>
          <article class="channel-item web-channel">
            <header>
              <div><strong>Web</strong><span>无需额外凭证</span></div>
              <input v-model="form.channels.web.enabled" type="checkbox">
            </header>
          </article>
        </div>
      </section>

      <section class="runtime-options-grid">
        <article class="runtime-card option-card">
          <h2>功能开关</h2>
          <label v-for="name in Object.keys(form.features)" :key="name" class="toggle-row">
            <span>{{ name }}<small>控制 Web console 与后台功能入口</small></span>
            <input v-model="form.features[name]" type="checkbox">
          </label>
        </article>

        <article class="runtime-card option-card">
          <h2>CORS Allowlist</h2>
          <p>每行一个 Origin，生产环境不要使用宽泛通配。</p>
          <textarea v-model="corsInput" placeholder="https://app.example.com&#10;http://127.0.0.1:8764" />
        </article>

        <article class="runtime-card option-card">
          <h2>Memory Provider</h2>
          <p>这里只切换 provider，不在 Web 里直接改密钥。</p>
          <select v-model="form.memory.provider">
            <option v-for="provider in form.memory.providers" :key="provider" :value="provider">{{ provider }}</option>
          </select>
          <pre>{{ JSON.stringify(form.memory.active_settings, null, 2) }}</pre>
        </article>

        <article class="runtime-card option-card">
          <h2>配置路径</h2>
          <div class="path-list">
            <div><strong>.env</strong><span>{{ form.paths.env }}</span></div>
            <div><strong>models.json</strong><span>{{ form.paths.models }}</span></div>
            <div><strong>memory.json</strong><span>{{ form.paths.memory }}</span></div>
          </div>
        </article>
      </section>
    </template>
  </div>
</template>

<style scoped>
.runtime-page {
  display: grid;
  gap: 20px;
}

.runtime-hero,
.runtime-card,
.loading-card {
  border: 1px solid var(--panel-border);
  border-radius: 14px;
  background: #fff;
  box-shadow: var(--shadow-card);
}

.runtime-hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 22px;
  padding: 28px 30px;
}

.runtime-hero h1 {
  margin: 0;
  color: var(--text-strong);
  font-size: 26px;
  font-weight: 800;
}

.runtime-hero p {
  margin: 10px 0 0;
  color: var(--text-body);
  font-size: 15px;
}

.runtime-actions {
  display: flex;
  gap: 14px;
}

.primary-btn,
.secondary-btn,
.doc-head button,
.side-card-head button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 42px;
  padding: 0 18px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 800;
}

.primary-btn {
  border: 0;
  background: var(--brand-blue);
  color: #fff;
}

.secondary-btn,
.doc-head button,
.side-card-head button {
  border: 1px solid var(--panel-border);
  background: #fff;
  color: var(--text-body);
}

.runtime-status-row {
  grid-column: 1 / -1;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  padding-top: 10px;
}

.environment-ready,
.runtime-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
  border-radius: 999px;
  padding: 0 13px;
  font-size: 13px;
  font-weight: 800;
}

.environment-ready {
  color: #0f8f4e;
}

.environment-ready i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--success);
}

.runtime-chip {
  background: #fff7ed;
  color: #c2410c;
}

.runtime-chip.ready {
  background: #dcfce7;
  color: #15803d;
}

.notice {
  grid-column: 1 / -1;
  border-radius: 8px;
  padding: 12px 14px;
  font-size: 14px;
}

.notice.success { background: #ecfdf3; color: #15803d; }
.notice.warning { background: #fffbeb; color: #b45309; }
.notice.danger { background: #fff1f2; color: #be123c; }

.loading-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 18px 20px;
  color: var(--text-muted);
}

.runtime-main-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 500px;
  gap: 24px;
  align-items: start;
}

.runtime-card {
  padding: 24px;
}

.card-title-row {
  display: flex;
  align-items: center;
  gap: 14px;
}

.card-title-row.compact {
  gap: 12px;
}

.card-title-row h2,
.runtime-card h2 {
  margin: 0;
  color: var(--text-strong);
  font-size: 19px;
  font-weight: 800;
}

.card-icon {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  border-radius: 12px;
}

.card-icon.blue { background: var(--brand-blue-soft); color: var(--brand-blue); }
.card-icon.purple { background: #f1e8ff; color: #7c3aed; }
.card-icon.green { background: #dcfce7; color: #16a34a; }
.card-icon.amber { background: #fff7ed; color: #f59e0b; }

.admin-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px 38px;
  margin-top: 24px;
}

.admin-card label,
.doc-card label,
.channel-item label,
.option-card label {
  display: grid;
  gap: 8px;
}

.admin-card label span,
.doc-card label span,
.channel-item label span {
  color: var(--text-body);
  font-size: 14px;
  font-weight: 700;
}

.admin-card input,
.admin-card textarea,
.doc-card textarea,
.channel-item input,
.option-card textarea,
.option-card select {
  width: 100%;
  border: 1px solid var(--panel-border);
  border-radius: 8px !important;
  padding: 12px 14px;
}

.password-field {
  position: relative;
}

.password-field svg {
  position: absolute;
  top: 50%;
  right: 14px;
  color: var(--text-muted);
  transform: translateY(-50%);
}

.full-field {
  margin-top: 18px;
}

.full-field textarea {
  min-height: 120px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.info-strip {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 18px;
  border: 1px solid #9ec5ff;
  border-radius: 8px;
  background: #f0f7ff;
  color: var(--brand-blue);
  padding: 12px 14px;
  font-size: 14px;
}

.runtime-side {
  display: grid;
  gap: 20px;
}

.sequence-card ol {
  display: grid;
  gap: 18px;
  margin: 22px 0 0;
  padding: 0;
  list-style: none;
}

.sequence-card li {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr) 18px;
  align-items: center;
  gap: 14px;
  color: var(--text-body);
  font-size: 14px;
}

.sequence-card li span {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #eef2f7;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 800;
}

.sequence-card li.done span {
  background: var(--brand-blue);
  color: #fff;
}

.sequence-card li.done svg {
  color: var(--success);
}

.sequence-card li i {
  width: 16px;
  height: 16px;
  border: 1px solid #cbd5e1;
  border-radius: 50%;
}

.side-card-head {
  display: flex;
  justify-content: space-between;
  gap: 14px;
}

.side-card-head button {
  height: 34px;
  padding: 0 10px;
  color: var(--brand-blue);
}

.model-status-table {
  margin-top: 18px;
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  overflow: hidden;
}

.model-status-table div {
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr);
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--panel-border);
}

.model-status-table div:last-child {
  border-bottom: 0;
}

.model-status-table span {
  color: var(--text-body);
}

.model-status-table strong {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--text-body);
  font-weight: 700;
}

.model-status-table i {
  width: 8px;
  height: 8px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--success);
}

.doc-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
}

.doc-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.doc-head p {
  margin: 6px 0 0;
  color: var(--text-muted);
  font-size: 13px;
}

.doc-card label {
  margin-top: 18px;
}

.brief-field {
  min-height: 76px;
}

.doc-editor {
  min-height: 260px;
  margin-top: 14px;
  line-height: 1.7;
}

.doc-card footer {
  margin-top: 12px;
  color: var(--text-muted);
  font-size: 13px;
}

.channels-card {
  display: grid;
  gap: 20px;
}

.channel-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.channel-item {
  display: grid;
  gap: 14px;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: #fbfdff;
  padding: 16px;
}

.channel-item header,
.toggle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}

.channel-item strong {
  display: block;
  color: var(--text-strong);
}

.channel-item header span {
  display: block;
  margin-top: 4px;
  color: var(--text-muted);
  font-size: 12px;
}

.runtime-options-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 20px;
}

.option-card {
  display: grid;
  align-content: start;
  gap: 14px;
}

.option-card p,
.toggle-row small {
  margin: 0;
  color: var(--text-muted);
  font-size: 13px;
}

.toggle-row {
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  background: #fbfdff;
  padding: 12px;
}

.toggle-row span {
  display: grid;
  gap: 4px;
  color: var(--text-strong);
  font-weight: 700;
}

.option-card textarea {
  min-height: 150px;
}

.option-card pre {
  max-height: 160px;
  overflow: auto;
  margin: 0;
  border-radius: 10px;
  background: #101828;
  color: #e2e8f0;
  padding: 14px;
  font-size: 12px;
  line-height: 1.6;
}

.path-list {
  display: grid;
  gap: 12px;
}

.path-list div {
  display: grid;
  gap: 5px;
}

.path-list strong {
  color: var(--text-strong);
}

.path-list span {
  overflow-wrap: anywhere;
  color: var(--text-muted);
  font-size: 13px;
}

@media (max-width: 1500px) {
  .runtime-main-grid,
  .channel-grid,
  .runtime-options-grid {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 980px) {
  .runtime-hero,
  .runtime-main-grid,
  .doc-grid,
  .channel-grid,
  .runtime-options-grid,
  .admin-form-grid {
    grid-template-columns: 1fr;
  }

  .runtime-actions,
  .doc-head {
    flex-wrap: wrap;
  }
}
</style>
