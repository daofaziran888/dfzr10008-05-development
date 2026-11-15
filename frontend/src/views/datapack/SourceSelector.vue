<template>
  <div class="build-panel">
    <h3 class="panel-title">1. 多选原始数据源</h3>
    <p class="panel-hint">一次可勾选多个原始数据源整组提交；每个源默认带出目录中的字段映射，可在提交前调整。</p>
    <div class="source-grid">
      <label
        v-for="source in sources"
        :key="source.name"
        class="source-card"
        :class="{ active: isSelected(source.name) }"
      >
        <input
          :checked="isSelected(source.name)"
          type="checkbox"
          @change="toggle(source.name)"
        />
        <div class="source-meta">
          <strong>{{ source.title }}（{{ source.name }}）</strong>
          <span>{{ source.description }}</span>
          <span class="source-fields">字段：{{ source.fields.map((f) => f.name).join('、') }}</span>
        </div>
      </label>
    </div>

    <template v-if="drafts.length">
      <h3 class="panel-title">2. 校验字段映射</h3>
      <p class="panel-hint">
        每条映射先校验目标字段与聚合规则；同一输出字段出现多个聚合写法时，提交后按数据源顺序裁决唯一口径并列出冲突项。
      </p>
      <div v-for="draft in drafts" :key="draft.source" class="mapping-box">
        <header class="mapping-head">
          <strong>{{ titleOf(draft.source) }}</strong>
          <span class="rule-hint">
            支持规则：{{ draft.supportedRules.map((r) => AGG_RULE_LABELS[r] ?? r).join('、') }}
          </span>
        </header>
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
            <tr v-for="(mapping, index) in draft.mappings" :key="index">
              <td>
                <select v-model="mapping.source_field">
                  <option v-for="field in fieldOptions(draft.source)" :key="field" :value="field">
                    {{ field }}
                  </option>
                </select>
              </td>
              <td>
                <input v-model.trim="mapping.target_field" placeholder="输出字段名" />
              </td>
              <td>
                <select v-model="mapping.agg_rule">
                  <option v-for="rule in draft.supportedRules" :key="rule" :value="rule">
                    {{ AGG_RULE_LABELS[rule] ?? rule }}
                  </option>
                </select>
              </td>
              <td>
                <button class="link danger" type="button" @click="removeMapping(draft, index)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div class="mapping-foot">
          <button class="btn ghost" type="button" @click="addMapping(draft)">新增映射</button>
          <button class="btn ghost" type="button" @click="resetMappings(draft)">恢复默认映射</button>
        </div>
      </div>
    </template>

    <div class="submit-row">
      <button class="btn primary" type="button" :disabled="!drafts.length || submitting" @click="emitSubmit">
        {{ submitting ? '构建中…' : `整组提交构建（${drafts.length} 个数据源）` }}
      </button>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

import { AGG_RULE_LABELS } from './types'
import type { MappingDef, SourceCatalog } from './types'

interface Draft {
  source: string
  supportedRules: string[]
  mappings: MappingDef[]
}

const props = defineProps<{
  sources: SourceCatalog[]
  submitting: boolean
  errorMessage: string
}>()

const emit = defineEmits<{
  (e: 'submit', payload: Array<{ source: string; mappings: MappingDef[] }>): void
}>()

const selected = ref<string[]>([])
const drafts = ref<Draft[]>([])

function isSelected(name: string): boolean {
  return selected.value.includes(name)
}

function catalogOf(name: string): SourceCatalog | undefined {
  return props.sources.find((item) => item.name === name)
}

function titleOf(name: string): string {
  return catalogOf(name)?.title ?? name
}

function fieldOptions(name: string): string[] {
  return catalogOf(name)?.fields.map((field) => field.name) ?? []
}

function toggle(name: string) {
  if (isSelected(name)) {
    selected.value = selected.value.filter((item) => item !== name)
    drafts.value = drafts.value.filter((item) => item.source !== name)
    return
  }
  const catalog = catalogOf(name)
  if (!catalog) return
  selected.value = [...selected.value, name]
  drafts.value.push({
    source: name,
    supportedRules: [...catalog.supported_rules],
    mappings: catalog.default_mappings.map((item) => ({ ...item })),
  })
}

function addMapping(draft: Draft) {
  const firstField = fieldOptions(draft.source)[0] ?? ''
  const firstRule = draft.supportedRules[0] ?? 'direct'
  draft.mappings.push({ source_field: firstField, target_field: '', agg_rule: firstRule })
}

function removeMapping(draft: Draft, index: number) {
  draft.mappings.splice(index, 1)
}

function resetMappings(draft: Draft) {
  const catalog = catalogOf(draft.source)
  if (catalog) {
    draft.mappings = catalog.default_mappings.map((item) => ({ ...item }))
  }
}

function emitSubmit() {
  emit('submit', drafts.value.map((draft) => ({ source: draft.source, mappings: draft.mappings })))
}

defineExpose({ clear: () => {
  selected.value = []
  drafts.value = []
} })
</script>
