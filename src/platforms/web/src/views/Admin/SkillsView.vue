<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
    Bot,
    Braces,
    Cloud,
    Code2,
    Download,
    Github,
    Import,
    Loader2,
    MoreHorizontal,
    Plus,
    RefreshCw,
    Search,
    KeyRound,
    SlidersHorizontal,
    Terminal,
    Wrench,
} from 'lucide-vue-next'

import { getSkills, setSkillEnabled, type SkillInfo } from '@/api/skills'

const skills = ref<SkillInfo[]>([])
const loading = ref(false)
const toggling = ref<string | null>(null)

const skillRows = computed(() =>
    [...skills.value].sort((a, b) => a.name.localeCompare(b.name))
)

const categoryFor = (skill: SkillInfo) => {
    if (skill.name.includes('credential')) return '安全与凭证'
    if (skill.name.includes('deploy')) return '部署与运维'
    if (skill.name.includes('docker')) return '容器与镜像'
    if (skill.name.includes('git') || skill.name.includes('gh')) return '版本控制'
    if (skill.name.includes('video')) return '媒体工具'
    if (skill.name.includes('coding')) return '开发工具'
    if (skill.source === 'learned') return '已学习技能'
    return '命令行工具'
}

const iconFor = (skill: SkillInfo) => {
    if (skill.name.includes('credential')) return KeyRound
    if (skill.name.includes('deploy')) return Cloud
    if (skill.name.includes('docker')) return Bot
    if (skill.name.includes('download')) return Download
    if (skill.name.includes('gh')) return Github
    if (skill.name.includes('git')) return Braces
    if (skill.name.includes('opencli')) return Terminal
    if (skill.name.includes('coding')) return Code2
    return Wrench
}

const load = async () => {
    loading.value = true
    try {
        const response = await getSkills()
        skills.value = response.data.skills || []
    } finally {
        loading.value = false
    }
}

const toggleSkill = async (skill: SkillInfo) => {
    toggling.value = skill.name
    try {
        const response = await setSkillEnabled(skill.name, !skill.enabled)
        skill.enabled = response.data.enabled
    } finally {
        toggling.value = null
    }
}

onMounted(load)
</script>

<template>
  <div class="skills-page">
    <section class="skills-hero">
      <div>
        <h1>技能管理 / Skills</h1>
        <p>管理系统中的技能模块，启用或禁用特定技能，配置能力与权限。</p>
      </div>
      <div class="hero-actions">
        <button type="button" class="primary-action">
          <Plus class="h-4 w-4" />
          新增技能
        </button>
        <button type="button" class="secondary-action">
          <Import class="h-4 w-4" />
          导入技能
        </button>
      </div>
    </section>

    <section class="filter-panel">
      <button type="button" class="filter-select">
        <span>分类</span>
        全部分类
        <SlidersHorizontal class="h-4 w-4" />
      </button>
      <button type="button" class="filter-select">
        <span>状态</span>
        全部状态
        <SlidersHorizontal class="h-4 w-4" />
      </button>
      <button type="button" class="filter-select">
        <span>能力类型</span>
        全部类型
        <SlidersHorizontal class="h-4 w-4" />
      </button>
      <label class="skills-search">
        <Search class="h-4 w-4" />
        <input type="search" placeholder="搜索技能名称、描述或标签...">
      </label>
      <button type="button" class="refresh-btn" @click="load">
        <RefreshCw class="h-4 w-4" :class="{ 'animate-spin': loading }" />
      </button>
    </section>

    <section class="skills-table-panel">
      <div class="table-meta">共 {{ skillRows.length }} 个技能</div>

      <div v-if="loading" class="loading-row">
        <Loader2 class="h-4 w-4 animate-spin" />
        正在加载技能列表
      </div>

      <div v-else class="skills-table-wrap">
        <table>
          <thead>
            <tr>
              <th>技能名称</th>
              <th>分类</th>
              <th>能力标签</th>
              <th>来源 / 上次更新</th>
              <th>状态</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="skill in skillRows" :key="skill.name">
              <td>
                <div class="skill-name-cell">
                  <div class="skill-icon" :class="{ learned: skill.source === 'learned' }">
                    <component :is="iconFor(skill)" class="h-5 w-5" />
                  </div>
                  <div>
                    <strong>{{ skill.name }}</strong>
                    <p>{{ skill.description || '暂无描述' }}</p>
                  </div>
                </div>
              </td>
              <td>{{ categoryFor(skill) }}</td>
              <td>
                <div class="tag-list">
                  <span v-for="trigger in skill.triggers.slice(0, 4)" :key="trigger">{{ trigger }}</span>
                  <span v-if="skill.triggers.length > 4">+{{ skill.triggers.length - 4 }}</span>
                </div>
              </td>
              <td>
                <div class="source-cell">
                  <strong>{{ skill.source === 'builtin' ? '系统内置' : '已学习' }}</strong>
                  <span>admin</span>
                </div>
              </td>
              <td>
                <button
                  type="button"
                  class="switch"
                  :class="{ on: skill.enabled }"
                  :disabled="toggling === skill.name"
                  @click="toggleSkill(skill)"
                >
                  <span />
                </button>
              </td>
              <td>
                <button type="button" class="row-menu">
                  <MoreHorizontal class="h-4 w-4" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>

        <div v-if="!skillRows.length" class="empty-state">
          暂无技能
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.skills-page {
  display: grid;
  gap: 18px;
}

.skills-hero,
.filter-panel,
.skills-table-panel {
  border: 1px solid var(--panel-border);
  border-radius: 14px;
  background: #fff;
  box-shadow: var(--shadow-card);
}

.skills-hero {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  padding: 24px 26px;
}

.skills-hero h1 {
  margin: 0;
  color: var(--text-strong);
  font-size: 26px;
  font-weight: 800;
}

.skills-hero p {
  margin: 10px 0 0;
  color: var(--text-muted);
  font-size: 15px;
}

.hero-actions {
  display: flex;
  gap: 12px;
}

.primary-action,
.secondary-action,
.refresh-btn,
.row-menu {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 800;
}

.primary-action {
  gap: 8px;
  height: 42px;
  padding: 0 18px;
  border: 0;
  background: var(--brand-blue);
  color: #fff;
}

.secondary-action {
  gap: 8px;
  height: 42px;
  padding: 0 16px;
  border: 1px solid var(--panel-border);
  background: #fff;
  color: var(--text-body);
}

.filter-panel {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr minmax(260px, 1.5fr) 44px;
  gap: 12px;
  padding: 18px 22px;
}

.filter-select,
.skills-search,
.refresh-btn {
  height: 42px;
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  background: #fff;
  color: var(--text-body);
}

.filter-select {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) 16px;
  align-items: center;
  gap: 12px;
  padding: 0 14px;
  text-align: left;
}

.filter-select span {
  color: var(--text-muted);
}

.skills-search {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 14px;
  color: var(--text-subtle);
}

.skills-search input {
  min-width: 0;
  width: 100%;
  border: 0 !important;
  outline: 0;
  box-shadow: none !important;
}

.refresh-btn,
.row-menu {
  width: 42px;
  color: var(--text-body);
}

.skills-table-panel {
  padding: 22px 24px;
}

.table-meta {
  margin-bottom: 18px;
  color: var(--text-body);
  font-size: 15px;
}

.loading-row {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
}

.skills-table-wrap {
  overflow-x: auto;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
}

table {
  width: 100%;
  min-width: 980px;
  border-collapse: collapse;
  font-size: 14px;
}

th {
  padding: 16px 18px;
  border-bottom: 1px solid var(--panel-border);
  color: var(--text-muted);
  font-weight: 800;
  text-align: left;
}

td {
  padding: 14px 18px;
  border-bottom: 1px solid #eef2f7;
  color: var(--text-body);
  vertical-align: middle;
}

tbody tr:last-child td {
  border-bottom: 0;
}

.skill-name-cell {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  align-items: center;
  gap: 14px;
}

.skill-icon {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: var(--brand-blue-soft);
  color: var(--brand-blue);
}

.skill-icon.learned {
  background: #ecfdf3;
  color: #16a34a;
}

.skill-name-cell strong {
  color: var(--text-strong);
  font-size: 16px;
}

.skill-name-cell p {
  max-width: 520px;
  margin: 6px 0 0;
  overflow: hidden;
  color: var(--text-muted);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.tag-list span {
  border-radius: 7px;
  background: #f2f4f7;
  color: #667085;
  padding: 5px 9px;
  font-size: 12px;
}

.source-cell {
  display: grid;
  gap: 4px;
}

.source-cell strong {
  color: var(--text-body);
  font-size: 13px;
}

.source-cell span {
  color: var(--text-muted);
  font-size: 12px;
}

.switch {
  position: relative;
  width: 46px;
  height: 26px;
  border: 0;
  border-radius: 999px;
  background: #d0d5dd;
  padding: 2px;
}

.switch span {
  display: block;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #fff;
  transition: transform 0.18s ease;
}

.switch.on {
  background: var(--brand-blue);
}

.switch.on span {
  transform: translateX(20px);
}

.empty-state {
  padding: 48px;
  color: var(--text-muted);
  text-align: center;
}

@media (max-width: 980px) {
  .skills-hero {
    flex-direction: column;
  }

  .filter-panel {
    grid-template-columns: 1fr;
  }
}
</style>
