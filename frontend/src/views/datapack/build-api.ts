/** 批量构建相关接口封装：数据源目录、批量提交、重试、发布、快照读取。 */
import { request } from '@/api/client'

import type { BuildRecord, MappingSnapshot, SourceCatalog } from './types'

const BASE = '/api/datapack/builds'

async function parse<T>(response: Response): Promise<T> {
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = payload?.detail
    throw new Error(
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((item: { msg?: string }) => item?.msg).filter(Boolean).join('；')
          : '请求失败，请稍后重试',
    )
  }
  return payload as T
}

export async function fetchSources(): Promise<SourceCatalog[]> {
  const response = await request(`${BASE}/sources`)
  const payload = await parse<{ items: SourceCatalog[] }>(response)
  return payload.items
}

export interface SubmitSource {
  source: string
  mappings?: Array<{ source_field: string; target_field: string; agg_rule: string }>
}

export async function submitBuild(sources: SubmitSource[], name?: string): Promise<BuildRecord> {
  const response = await request(BASE, {
    method: 'POST',
    body: JSON.stringify({ name, sources }),
  })
  return parse<BuildRecord>(response)
}

export async function fetchBuilds(): Promise<BuildRecord[]> {
  const response = await request(BASE)
  const payload = await parse<{ items: BuildRecord[] }>(response)
  return payload.items
}

export async function fetchBuild(id: number): Promise<BuildRecord> {
  const response = await request(`${BASE}/${id}`)
  return parse<BuildRecord>(response)
}

export async function retrySource(
  buildId: number,
  source: string,
  mappings: SubmitSource['mappings'],
): Promise<BuildRecord> {
  const response = await request(`${BASE}/${buildId}/retry`, {
    method: 'POST',
    body: JSON.stringify({ source, mappings }),
  })
  return parse<BuildRecord>(response)
}

export async function publishBuild(buildId: number): Promise<BuildRecord> {
  const response = await request(`${BASE}/${buildId}/publish`, { method: 'POST' })
  return parse<BuildRecord>(response)
}

export async function discardBuild(buildId: number): Promise<BuildRecord> {
  const response = await request(`${BASE}/${buildId}/discard`, { method: 'POST' })
  return parse<BuildRecord>(response)
}

/** 接口配置侧（API接口生成页面）读取的同一份冻结快照。 */
export async function fetchApiSnapshots(): Promise<MappingSnapshot[]> {
  const response = await request('/api/apigen/datapack-snapshots')
  const payload = await parse<{ items: MappingSnapshot[] }>(response)
  return payload.items
}

export async function fetchApiSnapshot(snapshotId: string): Promise<MappingSnapshot> {
  const response = await request(`/api/apigen/datapack-snapshots/${encodeURIComponent(snapshotId)}`)
  return parse<MappingSnapshot>(response)
}
