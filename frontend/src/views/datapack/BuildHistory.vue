<template>
  <div class="history-panel">
    <table class="data-table">
      <thead>
        <tr>
          <th>构建编号</th>
          <th>构建名称</th>
          <th>数据源组</th>
          <th>成功/失败</th>
          <th>冲突</th>
          <th>状态</th>
          <th>版本/快照</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in builds" :key="item.id" :class="{ active: item.id === currentId }">
          <td>{{ item.build_no }}</td>
          <td>{{ item.name }}</td>
          <td>{{ item.source_names.join('、') }}</td>
          <td>
            <span class="ok-text">{{ item.success_count }}</span> /
            <span :class="item.failed_count ? 'fail-text' : ''">{{ item.failed_count }}</span>
          </td>
          <td :class="item.conflicts.length ? 'fail-text' : ''">{{ item.conflicts.length }}</td>
          <td><span class="badge" :class="statusClass[item.status] ?? 'muted'">{{ item.status }}</span></td>
          <td>
            <template v-if="item.version">{{ item.version }}</template>
            <template v-else>—</template>
          </td>
          <td>
            <button class="link" type="button" @click="emit('view', item.id)">查看结果</button>
          </td>
        </tr>
        <tr v-if="!builds.length">
          <td colspan="8" class="empty-state">暂无构建记录，请先在「批量构建」页多选数据源并提交。</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
import type { BuildRecord } from './types'

defineProps<{ builds: BuildRecord[]; currentId: number | null }>()
const emit = defineEmits<{ (e: 'view', id: number): void }>()

const statusClass: Record<string, string> = {
  待发布: 'pending',
  已发布: 'ok',
  已废弃: 'muted',
}
</script>
