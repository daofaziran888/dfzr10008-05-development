<template>
  <section class="page" data-module="datapack">
    <header class="page-head">
      <div>
        <h2>数据模型封装管理</h2>
        <p class="page-desc">
          多选原始数据源一次提交批量构建：逐条校验目标字段与聚合规则、裁决同输出字段的冲突口径，
          失败源单独列出且可单独重试；发布后冻结字段映射快照，接口配置侧读取同一份快照，同组数据源重跑复用原快照。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出模型封装清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="item.tone">{{ item.value }}</strong>
      </article>
    </div>

    <nav class="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="tab-item"
        :class="{ active: activeTab === tab.key }"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
      </button>
    </nav>

    <!-- 批量构建：多选数据源 + 映射编辑 + 整组提交 -->
    <div v-if="activeTab === 'build'" class="tab-panel">
      <SourceSelector
        ref="selectorRef"
        :sources="sources"
        :submitting="submitting"
        :error-message="errorMessage"
        @submit="handleSubmit"
      />
      <BuildDetail
        v-if="currentBuild"
        :build="currentBuild"
        :sources="sources"
        :busy="busy"
        :action-message="actionMessage"
        @retry="handleRetry"
        @publish="handlePublish"
        @discard="handleDiscard"
      />
    </div>

    <!-- 构建记录：每次批量构建一条，含每源处理结果入口 -->
    <div v-else-if="activeTab === 'history'" class="tab-panel">
      <BuildHistory :builds="builds" :current-id="currentBuild?.id ?? null" @view="viewBuild" />
      <BuildDetail
        v-if="currentBuild"
        :build="currentBuild"
        :sources="sources"
        :busy="busy"
        :action-message="actionMessage"
        @retry="handleRetry"
        @publish="handlePublish"
        @discard="handleDiscard"
      />
    </div>

    <!-- 接口配置侧视角：读取同一份冻结快照 -->
    <div v-else-if="activeTab === 'snapshot'" class="tab-panel">
      <SnapshotPanel :snapshots="snapshots" />
    </div>

    <!-- 原有模型封装清单（登记/状态动作保持不变） -->
    <div v-else class="tab-panel">
      <form class="filter-bar" @submit.prevent="reloadLegacy">
        <label v-for="field in legacyFilterFields" :key="field" class="filter-item">
          <span>{{ field }}</span>
          <input v-model="legacyFilters[field]" :placeholder="`按${field}检索`" />
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetLegacyFilters">重置条件</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in legacyColumns" :key="column">{{ column }}</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in legacyRows" :key="String(row.id)">
            <td v-for="column in legacyColumns" :key="column">{{ row[column] ?? '—' }}</td>
            <td class="row-actions">
              <button
                v-for="action in legacyActions"
                :key="action"
                class="link"
                type="button"
                @click="runLegacyAction(action, row)"
              >
                {{ action }}
              </button>
            </td>
          </tr>
          <tr v-if="!legacyRows.length">
            <td :colspan="legacyColumns.length + 1" class="empty-state">暂无模型封装登记数据</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ legacyTotal }} 条模型封装记录</span>
        <span v-if="legacyError" class="error-text">{{ legacyError }}</span>
      </footer>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'
import BuildDetail from './BuildDetail.vue'
import BuildHistory from './BuildHistory.vue'
import SnapshotPanel from './SnapshotPanel.vue'
import SourceSelector from './SourceSelector.vue'
import {
  discardBuild,
  fetchApiSnapshots,
  fetchBuild,
  fetchBuilds,
  fetchSources,
  publishBuild,
  retrySource,
  submitBuild,
} from './build-api'
import type { BuildRecord, MappingDef, MappingSnapshot, SourceCatalog } from './types'

type TabKey = 'build' | 'history' | 'snapshot' | 'legacy'

const tabs: Array<{ key: TabKey; label: string }> = [
  { key: 'build', label: '批量构建' },
  { key: 'history', label: '构建记录' },
  { key: 'snapshot', label: '字段映射快照（接口配置侧）' },
  { key: 'legacy', label: '封装清单' },
]

const activeTab = ref<TabKey>('build')
const sources = ref<SourceCatalog[]>([])
const builds = ref<BuildRecord[]>([])
const snapshots = ref<MappingSnapshot[]>([])
const currentBuild = ref<BuildRecord | null>(null)
const selectorRef = ref<InstanceType<typeof SourceSelector> | null>(null)

const submitting = ref(false)
const busy = ref(false)
const errorMessage = ref('')
const actionMessage = ref('')

const stats = computed(() => [
  { label: '构建总数', value: builds.value.length, tone: '' },
  { label: '待发布构建', value: builds.value.filter((b) => b.status === '待发布').length, tone: 'warn-text' },
  { label: '已发布版本', value: builds.value.filter((b) => b.status === '已发布').length, tone: 'ok-text' },
  { label: '冻结快照数', value: snapshots.value.length, tone: '' },
])

async function loadSources() {
  sources.value = await fetchSources()
}

async function loadBuilds(selectId?: number) {
  builds.value = await fetchBuilds()
  if (selectId !== undefined) {
    currentBuild.value = (await fetchBuild(selectId)) ?? null
  }
}

async function loadSnapshots() {
  snapshots.value = await fetchApiSnapshots()
}

async function switchTab(tab: TabKey) {
  activeTab.value = tab
  actionMessage.value = ''
  if (tab === 'history' && !builds.value.length) {
    await safeLoad(loadBuilds)
  }
  if (tab === 'snapshot') {
    await safeLoad(loadSnapshots)
  }
  if (tab === 'legacy') {
    await reloadLegacy()
  }
}

async function safeLoad(loader: () => Promise<void>) {
  try {
    await loader()
  } catch (error) {
    actionMessage.value = error instanceof Error ? error.message : '数据加载失败'
  }
}

async function handleSubmit(payload: Array<{ source: string; mappings: MappingDef[] }>) {
  submitting.value = true
  errorMessage.value = ''
  actionMessage.value = ''
  try {
    const record = await submitBuild(payload)
    await loadBuilds(record.id)
    actionMessage.value = record.reused
      ? `同一组数据源已构建过，复用原快照 ${record.build_no}（版本 ${record.version ?? '待发布'}），未新增版本。`
      : `构建 ${record.build_no} 已完成：成功 ${record.success_count} 个源、失败 ${record.failed_count} 个源、冲突 ${record.conflicts.length} 项。`
    if (record.status === '待发布' && record.all_passed) {
      actionMessage.value += ' 全部源校验通过，可直接发布冻结快照。'
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量构建提交失败'
  } finally {
    submitting.value = false
  }
}

async function viewBuild(id: number) {
  try {
    currentBuild.value = await fetchBuild(id)
    actionMessage.value = ''
  } catch (error) {
    actionMessage.value = error instanceof Error ? error.message : '构建结果读取失败'
  }
}

async function handleRetry(payload: { buildId: number; source: string; mappings: MappingDef[] }) {
  busy.value = true
  actionMessage.value = ''
  try {
    const record = await retrySource(payload.buildId, payload.source, payload.mappings)
    currentBuild.value = record
    await loadBuilds()
    currentBuild.value = (await fetchBuild(payload.buildId)) ?? record
    actionMessage.value = record.all_passed
      ? `数据源「${payload.source}」重新提交通过，整组校验已全部通过，可以发布。`
      : `数据源「${payload.source}」已重新提交，仍有 ${record.failed_count} 个失败源，请继续修正。`
  } catch (error) {
    actionMessage.value = error instanceof Error ? error.message : '失败源重新提交失败'
  } finally {
    busy.value = false
  }
}

async function handlePublish(buildId: number) {
  busy.value = true
  actionMessage.value = ''
  try {
    const record = await publishBuild(buildId)
    currentBuild.value = record
    await Promise.all([loadBuilds(), loadSnapshots()])
    currentBuild.value = (await fetchBuild(buildId)) ?? record
    actionMessage.value = `已发布并冻结字段映射快照 ${record.snapshot?.snapshot_id ?? ''}（版本 ${record.version ?? ''}），接口配置侧读取同一份快照。`
  } catch (error) {
    actionMessage.value = error instanceof Error ? error.message : '发布失败'
  } finally {
    busy.value = false
  }
}

async function handleDiscard(buildId: number) {
  busy.value = true
  actionMessage.value = ''
  try {
    const record = await discardBuild(buildId)
    currentBuild.value = record
    await loadBuilds()
    actionMessage.value = `构建 ${record.build_no} 已废弃。`
  } catch (error) {
    actionMessage.value = error instanceof Error ? error.message : '废弃失败'
  } finally {
    busy.value = false
  }
}

// ---------- 原有封装清单（legacy /api/datapack） ----------
const LEGACY_ENDPOINT = '/api/datapack'
const legacyColumns = ['封装编号', '封装名称', '原始数据源', '输出结构', '字段映射', '聚合规则', '缓存策略', '封装状态']
const legacyActions = ['发布封装', '废弃封装', '删除封装']
const legacyFilterFields = legacyColumns.slice(0, 3)
const legacyRows = ref<Array<Record<string, string | number | null>>>([])
const legacyTotal = ref(0)
const legacyError = ref('')
const legacyFilters = ref<Record<string, string>>({})

function resetLegacyFilters() {
  legacyFilters.value = {}
  void reloadLegacy()
}

function exportRows() {
  window.open(`${LEGACY_ENDPOINT}/export`, '_blank')
}

async function runLegacyAction(action: string, row: Record<string, string | number | null>) {
  legacyError.value = ''
  try {
    const response = await request(`${LEGACY_ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) throw new Error('模型封装动作未生效，请稍后重试')
    await reloadLegacy()
  } catch (error) {
    legacyError.value = error instanceof Error ? error.message : '模型封装操作失败'
  }
}

async function reloadLegacy() {
  legacyError.value = ''
  const query = new URLSearchParams(legacyFilters.value as Record<string, string>).toString()
  try {
    const response = await request(`${LEGACY_ENDPOINT}?${query}`)
    if (!response.ok) throw new Error('模型封装列表读取失败')
    const payload = await response.json()
    legacyRows.value = payload.items ?? []
    legacyTotal.value = payload.total ?? legacyRows.value.length
  } catch (error) {
    legacyError.value = error instanceof Error ? error.message : '模型封装列表读取失败'
  }
}

onMounted(() => {
  void safeLoad(loadSources)
  void safeLoad(loadBuilds)
})
</script>
