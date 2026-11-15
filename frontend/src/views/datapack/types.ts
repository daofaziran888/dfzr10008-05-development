/** 数据模型封装批量构建相关的前端类型，与后端 datapack_build 服务返回结构对齐。 */

export interface MappingDef {
  source_field: string
  target_field: string
  agg_rule: string
}

export interface SourceField {
  name: string
  type: string
  label: string
}

export interface SourceCatalog {
  name: string
  title: string
  description: string
  fields: SourceField[]
  sample_rows: Record<string, string | number>[]
  supported_rules: string[]
  default_mappings: MappingDef[]
}

export interface MappingCheck extends MappingDef {
  agg_rule_label: string
  valid: boolean
  errors: string[]
  duplicate_target: boolean
}

export interface ResolvedField {
  source: string
  source_field: string
  target_field: string
  agg_rule: string
  agg_rule_label: string
}

export interface SourceResult {
  source: string
  source_title?: string
  status: 'success' | 'failed'
  errors: string[]
  mappings: MappingCheck[]
  resolved_fields: ResolvedField[]
}

export interface Conflict {
  target_field: string
  reason: string
  winner: ResolvedField
  loser: ResolvedField
}

export interface OutputField extends ResolvedField {}

export interface SnapshotMappingGroup {
  source: string
  source_title?: string
  mappings: MappingCheck[]
}

export interface MappingSnapshot {
  snapshot_id: string
  fingerprint: string
  version: string
  source_names: string[]
  output_fields: OutputField[]
  field_mappings: SnapshotMappingGroup[]
  conflicts: Conflict[]
  frozen: boolean
  published_at?: string
}

export interface BuildRecord {
  id: number
  build_no: string
  name: string
  source_names: string[]
  source_results: SourceResult[]
  output_fields: OutputField[]
  conflicts: Conflict[]
  status: '待发布' | '已发布' | '已废弃'
  all_passed: boolean
  success_count: number
  failed_count: number
  fingerprint: string | null
  version: string | null
  snapshot: MappingSnapshot | null
  published_at?: string | null
  reused?: boolean
}

/** 与后端 AGG_RULES 保持一致的中文口径。 */
export const AGG_RULE_LABELS: Record<string, string> = {
  direct: '直接映射',
  sum_daily: '按日汇总求和',
  count_daily: '按日计数',
  avg: '平均值',
  count_distinct: '去重计数',
  lookup: '维度关联取值',
}
