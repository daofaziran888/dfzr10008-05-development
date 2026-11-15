<template>
  <div class="snapshot-panel">
    <p class="panel-hint">
      这里调用接口配置侧接口 <code>/api/apigen/datapack-snapshots</code>，与数据模型封装发布侧读取同一份冻结快照。
    </p>
    <table v-if="snapshots.length" class="data-table">
      <thead>
        <tr>
          <th>快照编号</th>
          <th>版本</th>
          <th>数据源组</th>
          <th>输出字段数</th>
          <th>冲突记录</th>
          <th>冻结时间</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="snapshot in snapshots" :key="snapshot.snapshot_id">
          <td><code>{{ snapshot.snapshot_id }}</code></td>
          <td><strong>{{ snapshot.version }}</strong></td>
          <td>{{ snapshot.source_names.join('、') }}</td>
          <td>{{ snapshot.output_fields.length }}</td>
          <td :class="snapshot.conflicts.length ? 'fail-text' : ''">{{ snapshot.conflicts.length }}</td>
          <td>{{ snapshot.published_at ?? '—' }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else class="empty-state">还没有已发布的字段映射快照；构建发布后会出现在这里。</p>
  </div>
</template>

<script setup lang="ts">
import type { MappingSnapshot } from './types'

defineProps<{ snapshots: MappingSnapshot[] }>()
</script>
