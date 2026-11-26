/** 档案归并失败恢复相关接口：批量处理单、冲突裁决、规格基线核对。 */
import { request } from './client'
import type { ActionResult, EntryRow } from '@/data/types'

export interface MergeGroupDraft {
  key?: string
  keep: string
  members: string[]
}

export interface MergeGroupResult {
  key: string
  组号?: number
  keep_code?: string
  member_codes?: string[]
  codes?: string[]
  status: string
  blocked?: boolean
  conflict?: boolean
  conflict_codes?: string[]
  reasons?: string[]
  reason?: string
  result?: Record<string, unknown> | null
  [key: string]: unknown
}

export interface MergeOrder {
  id: number
  单号: string
  batch_no?: string
  status: string
  title?: string
  operator?: string
  groups: MergeGroupResult[]
  conflicts?: { code: string; groups: string[]; reason?: string }[]
  duplicate_members?: string[]
  runnable_groups?: number
  created_at?: string
  updated_at?: string
  [key: string]: unknown
}

export interface MergeOrderResponse extends ActionResult {
  order?: MergeOrder
  order_id?: number
  单号?: string
  idempotent?: boolean
  orders?: MergeOrder[]
  items?: MergeOrder[]
  total?: number
  results?: unknown[]
  changed?: boolean
}

async function readJson<T>(response: Response): Promise<T> {
  return (await response.json()) as T
}

export function submitMergeBatch(
  groups: MergeGroupDraft[],
  extra: Record<string, unknown> = {},
): Promise<MergeOrderResponse> {
  return request('/api/goods_registry/merge/orders', {
    method: 'POST',
    body: JSON.stringify({ groups, ...extra }),
  }).then((r) => readJson<MergeOrderResponse>(r))
}

export function listMergeOrders(): Promise<MergeOrderResponse> {
  return request('/api/goods_registry/merge/orders').then((r) =>
    readJson<MergeOrderResponse>(r),
  )
}

export function getMergeOrder(ref: number | string): Promise<MergeOrderResponse> {
  return request(`/api/goods_registry/merge/orders/${encodeURIComponent(String(ref))}`).then(
    (r) => readJson<MergeOrderResponse>(r),
  )
}

export function continueMergeOrder(
  ref: number | string,
  body: Record<string, unknown> = {},
): Promise<MergeOrderResponse> {
  return request(
    `/api/goods_registry/merge/orders/${encodeURIComponent(String(ref))}/continue`,
    { method: 'POST', body: JSON.stringify(body) },
  ).then((r) => readJson<MergeOrderResponse>(r))
}

export function adjudicateMergeOrder(
  ref: number | string,
  body: Record<string, unknown>,
): Promise<MergeOrderResponse> {
  return request(
    `/api/goods_registry/merge/orders/${encodeURIComponent(String(ref))}/adjudicate`,
    { method: 'POST', body: JSON.stringify(body) },
  ).then((r) => readJson<MergeOrderResponse>(r))
}

export interface SpecBaselineView {
  ok: boolean
  message: string
  queried_code: string
  current_code: string
  goods_code: string
  former_codes: string[]
  chain: { from: string; to: string }[]
  specs: string[]
  baseline_price: number | string | null
  has_baseline: boolean
  baseline: Record<string, unknown> | null
  baselines: Record<string, unknown>[]
  matches: Record<string, unknown>[]
}

export function specCheck(code: string, spec?: string): Promise<SpecBaselineView> {
  const query = spec ? `?spec=${encodeURIComponent(spec)}` : ''
  return request(
    `/api/sku_match/spec-check/${encodeURIComponent(code)}${query}`,
  ).then((r) => readJson<SpecBaselineView>(r))
}

export type { EntryRow }
