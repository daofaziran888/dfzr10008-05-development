<template>
  <div v-if="build" class="build-detail">
    <header class="detail-head">
      <div>
        <h3>{{ build.build_no }} · {{ build.name }}</h3>
        <p class="panel-hint">
          数据源：{{ build.source_names.join('、') }} ｜ 状态：
          <span class="badge" :class="statusClass">{{ build.status }}</span>
          <template v-if="build.reused"> ｜ <span class="badge reused">命中快照复用，未新增版本</span></template>
        </p>
      </div>
      <div class="detail-actions">
        <button
          v-if="build.status === '待发布'"
          class="btn primary"
          type="button"
          :disabled="!build.all_passed || busy"
          :title="build.all_passed ? '' : '仍有失败数据源，请先逐条重新提交'"
          @click="onPublish"
        >
          发布并冻结快照
        </button>
        <button
          v-if="build.status === '待发布'"
          class="btn ghost"
          type="button"
          :disabled="busy"
          @click="onDiscard"
        >
          废弃构建
        </button>
      </div>
    </header>

    <div class="stat-row compact">
      <article class="stat-card">
        <span class="stat-label">数据源总数</span>
        <strong class="stat-value">{{ build.source_results.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">处理成功</span>
        <strong class="stat-value ok">{{ build.success_count }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">失败源（单独列出）</span>
        <strong class="stat-value" :class="{ fail: build.failed_count > 0 }">{{ build.failed_count }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">冲突项</span>
        <strong class="stat-value" :class="{ fail: build.conflicts.length > 0 }">{{ build.conflicts.length }}</strong>
      </article>
    </div>

    <h4 class="section-title">① 每个源的处理情况</h4>
    <div v-for="result in build.source_results" :key="result.source" class="source-result">
      <header class="result-head">
        <span class="badge" :class="result.status === 'success' ? 'ok' : 'fail'">
          {{ result.status === 'success' ? '成功' : '失败' }}
        </span>
        <strong>{{ result.source_title ?? result.source }}（{{ result.source }}）</strong>
        <button
          v-if="result.status === 'failed' && build.status === '待发布'"
          class="link"
          type="button"
          @click="toggleRetry(result.source)"
        >
          {{ retrySource === result.source ? '收起重试' : '单独重新提交' }}
        </button>
      </header>

      <table class="mapping-table">
        <thead>
          <tr>
            <th>来源字段</th>
            <th>目标字段</th>
            <th>聚合规则</th>
            <th>校验结果</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(mapping, index) in result.mappings" :key="index" :class="{ invalid: !mapping.valid }">
            <td>{{ mapping.source_field || '—' }}</td>
            <td>{{ mapping.target_field || '—' }}</td>
            <td>{{ mapping.agg_rule_label }}</td>
            <td>
              <span v-if="mapping.valid" class="ok-text">通过</span>
              <ul v-else class="error-list">
                <li v-for="(err, i) in mapping.errors" :key="i">{{ err }}</li>
              </ul>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="result.status === 'failed'" class="fail-text">该源未通过校验，已从输出结构中隔离；其余源不受影响。</p>

      <div v-if="retrySource === result.source && build.status === '待发布'" class="retry-box">
        <p class="panel-hint">修正映射后仅重新提交该源，同批次其他源保持原处理结果。</p>
        <table class="mapping-table">
          <thead>
            <tr>
              <th>来源字段</th>
              <th>目标字段</th>
              <th>聚合规则</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(mapping, index) in retryMappings" :key="index">
              <td>
                <select v-model="mapping.source_field">
                  <option v-for="field in fieldOptions(result.source)" :key="field" :value="field">{{ field }}</option>
                </select>
              </td>
              <td><input v-model.trim="mapping.target_field" placeholder="输出字段名" /></td>
              <td>
                <select v-model="mapping.agg_rule">
                  <option v-for="rule in supportedRules(result.source)" :key="rule" :value="rule">
                    {{ AGG_RULE_LABELS[rule] ?? rule }}
                  </option>
                </select>
              </td>
              <td><button class="link danger" type="button" @click="retryMappings.splice(index, 1)">删除</button></td>
            </tr>
          </tbody>
        </table>
        <div class="mapping-foot">
          <button class="btn ghost" type="button" @click="addRetryMapping(result.source)">新增映射</button>
          <button class="btn primary" type="button" :disabled="busy" @click="onRetry(result.source)">
            重新提交该源
          </button>
        </div>
      </div>
    </div>

    <h4 class="section-title">② 冲突裁决（同一输出字段多个聚合写法）</h4>
    <table v-if="build.conflicts.length" class="data-table conflict-table">
      <thead>
        <tr>
          <th>输出字段</th>
          <th>生效口径（唯一）</th>
          <th>被裁决写法</th>
          <th>裁决说明</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(conflict, index) in build.conflicts" :key="index">
          <td>{{ conflict.target_field }}</td>
          <td class="ok-text">
            {{ conflict.winner.source }}.{{ conflict.winner.source_field }}（{{ conflict.winner.agg_rule_label }}）
          </td>
          <td class="fail-text">
            {{ conflict.loser.source }}.{{ conflict.loser.source_field }}（{{ conflict.loser.agg_rule_label }}）
          </td>
          <td>{{ conflict.reason }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else class="panel-hint">本次构建没有输出字段冲突。</p>

    <h4 class="section-title">③ 最终输出结构（{{ build.output_fields.length }} 个字段）</h4>
    <div class="output-fields">
      <span v-for="field in build.output_fields" :key="field.target_field" class="output-chip">
        {{ field.target_field }}
        <small>← {{ field.source }}.{{ field.source_field }} · {{ field.agg_rule_label }}</small>
      </span>
      <p v-if="!build.output_fields.length" class="panel-hint">暂无通过校验的输出字段。</p>
    </div>

    <template v-if="build.snapshot">
      <h4 class="section-title">④ 字段映射快照（发布后冻结）</h4>
      <div class="snapshot-box">
        <p>
          快照编号 <code>{{ build.snapshot.snapshot_id }}</code> ｜ 版本
          <strong>{{ build.snapshot.version }}</strong> ｜ 指纹
          <code>{{ build.snapshot.fingerprint }}</code> ｜
          <span class="badge ok">frozen: {{ String(build.snapshot.frozen) }}</span>
          <template v-if="build.snapshot.published_at"> ｜ 冻结时间 {{ build.snapshot.published_at }}</template>
        </p>
        <p class="panel-hint">
          接口配置侧通过 <code>/api/apigen/datapack-snapshots/{{ build.snapshot.snapshot_id }}</code>
          读取的就是这一份快照，重复构建同一组数据源会按指纹复用，不会产生第二份版本。
        </p>
      </div>
    </template>

    <p v-if="actionMessage" class="error-text">{{ actionMessage }}</p>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'

import { AGG_RULE_LABELS } from './types'
import type { BuildRecord, MappingDef, SourceCatalog } from './types'

const props = defineProps<{
  build: BuildRecord | null
  sources: SourceCatalog[]
  busy: boolean
  actionMessage: string
}>()

const emit = defineEmits<{
  (e: 'retry', payload: { buildId: number; source: string; mappings: MappingDef[] }): void
  (e: 'publish', buildId: number): void
  (e: 'discard', buildId: number): void
}>()

const retrySource = ref('')
const retryMappings = ref<MappingDef[]>([])

// 重试的源转为成功后自动收起重试面板，避免残留一个已无入口的编辑框；
// 切换到另一条构建记录时同样收起。
watch(
  () => [
    props.build?.id,
    props.build?.source_results.find((item) => item.source === retrySource.value)?.status,
  ],
  ([, status]) => {
    if (status === undefined || status === 'success') {
      retrySource.value = ''
      retryMappings.value = []
    }
  },
)

const statusClass: Record<string, string> = {
  待发布: 'pending',
  已发布: 'ok',
  已废弃: 'muted',
}

function catalogOf(name: string): SourceCatalog | undefined {
  return props.sources.find((item) => item.name === name)
}

function fieldOptions(source: string): string[] {
  return catalogOf(source)?.fields.map((field) => field.name) ?? []
}

function supportedRules(source: string): string[] {
  return catalogOf(source)?.supported_rules ?? ['direct']
}

function toggleRetry(source: string) {
  if (retrySource.value === source) {
    retrySource.value = ''
    retryMappings.value = []
    return
  }
  retrySource.value = source
  const result = props.build?.source_results.find((item) => item.source === source)
  // 重试表单默认带出该源当前提交的映射，修正非法规则后即可重新提交。
  retryMappings.value = (result?.mappings ?? []).map((item) => ({
    source_field: item.source_field,
    target_field: item.target_field,
    agg_rule: item.agg_rule,
  }))
}

function addRetryMapping(source: string) {
  retryMappings.value.push({
    source_field: fieldOptions(source)[0] ?? '',
    target_field: '',
    agg_rule: supportedRules(source)[0] ?? 'direct',
  })
}

function onRetry(source: string) {
  if (!props.build) return
  emit('retry', { buildId: props.build.id, source, mappings: retryMappings.value })
}

function onPublish() {
  if (props.build) emit('publish', props.build.id)
}

function onDiscard() {
  if (props.build) emit('discard', props.build.id)
}
</script>
