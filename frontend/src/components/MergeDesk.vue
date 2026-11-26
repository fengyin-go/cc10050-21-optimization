<template>
  <section class="merge-desk" data-testid="merge-desk" aria-label="批量归并工作台">
    <div class="merge-desk-head">
      <h3>批量归并工作台</h3>
      <p class="merge-hint">
        在下方档案列表勾选疑似重复档案并分组；同一档案允许被勾进两个合并组，
        提交后由分组处理单拦截重复入组与双归属。品牌或包装规格不同的组会被列为等候，不能强制合并。
      </p>
    </div>

    <!-- 草稿：已勾选 + 合并组 -->
    <div class="draft-bar" data-testid="draft-bar">
      <span data-testid="picked-count">已勾选 {{ picked.length }} 个档案</span>
      <span v-for="code in picked" :key="code" class="chip picked-chip" :data-testid="`picked-chip-${code}`">
        {{ code }}
        <button type="button" class="chip-x" :data-testid="`unpick-${code}`"
                :aria-label="`取消勾选 ${code}`" @click="togglePick(code, false)">×</button>
      </span>
      <button class="btn" type="button" data-testid="create-group-from-picked"
              :disabled="!picked.length" @click="createGroupFromPicked">
        将勾选档案建为合并组
      </button>
      <span class="quick-add">
        <input v-model="quickCode" class="quick-input" data-testid="quick-pick-input"
               placeholder="输入商品标识，如 G42" aria-label="按商品标识勾选档案"
               @keyup.enter="quickPick" />
        <button class="btn ghost" type="button" data-testid="quick-pick-btn" @click="quickPick">
          勾选该档案
        </button>
      </span>
    </div>

    <div v-if="draftGroups.length" class="draft-groups" data-testid="draft-groups">
      <article v-for="(group, gi) in draftGroups" :key="group.localKey"
               class="draft-group" :data-testid="`draft-group-${gi + 1}`"
               :data-codes="group.codes.join(',')">
        <header class="draft-group-head">
          <strong>合并组 {{ gi + 1 }}</strong>
          <button class="link danger" type="button" :data-testid="`remove-group-${gi + 1}`"
                  @click="removeGroup(group.localKey)">删除整组</button>
        </header>
        <div class="keeper-list">
          <label v-for="code in group.codes" :key="code" class="keeper-option"
                 :data-testid="`keeper-option-${gi + 1}-${code}`">
            <input type="radio" :name="`keeper-${group.localKey}`"
                   :data-testid="`keep-${gi + 1}-${code}`"
                   :value="code" v-model="group.keep" />
            <span class="code-badge" :data-testid="`group-code-${gi + 1}-${code}`">{{ code }}</span>
            <span class="goods-name">{{ nameOf(code) }}</span>
            <span v-if="group.keep === code" class="tag keep-tag">保留档案</span>
            <button type="button" class="chip-x" :data-testid="`remove-code-${gi + 1}-${code}`"
                    :aria-label="`从合并组${gi + 1}移除 ${code}`"
                    @click="removeCode(group, code)">×</button>
          </label>
        </div>
        <div class="add-code-row">
          <input class="quick-input" :data-testid="`add-code-input-${gi + 1}`"
                 :placeholder="`向合并组 ${gi + 1} 加入商品标识`"
                 v-model="addCodeInputs[group.localKey]"
                 @keyup.enter="addCodeTo(group)" />
          <button class="btn ghost" type="button" :data-testid="`add-code-btn-${gi + 1}`"
                  @click="addCodeTo(group)">加入标识</button>
        </div>
      </article>
    </div>

    <div class="draft-actions">
      <button class="btn primary" type="button" data-testid="submit-batch-merge"
              :disabled="submitting || !validGroupCount" @click="submitBatch">
        {{ submitting ? '提交中…' : `批量提交归并（${validGroupCount} 组）` }}
      </button>
      <button class="btn ghost" type="button" data-testid="reset-draft" @click="resetDraft">
        清空分组草稿
      </button>
      <span v-if="!validGroupCount" class="merge-hint">
        每个合并组至少需要 2 个档案，并指定 1 个保留档案。
      </span>
    </div>

    <p v-if="message" class="merge-message" :class="{ error: !messageOk }"
       data-testid="merge-message" role="status">{{ message }}</p>

    <!-- 分组处理单：原单追溯 + 失败恢复 -->
    <section v-if="currentOrder" class="order-panel" data-testid="merge-order-panel"
             :data-order-id="currentOrder.id" :data-order-no="currentOrder.单号">
      <header class="order-head">
        <div>
          <h3>
            分组处理单
            <span class="order-no" data-testid="order-no">{{ currentOrder.单号 }}</span>
          </h3>
          <p class="merge-hint">
            按原提交追溯：提交人 {{ currentOrder.operator || '值班员' }}，
            提交时间 {{ formatTime(currentOrder.created_at) }}。
            已办结组不会因再次提交 / 继续而移动已确认关系。
          </p>
        </div>
        <span class="status-badge" :class="statusClass(currentOrder.status)"
              data-testid="order-status">{{ currentOrder.status }}</span>
      </header>

      <div v-if="currentOrder.conflicts?.length" class="conflict-box" data-testid="conflict-list">
        <strong>重复入组 / 归属冲突：</strong>
        <span v-for="c in currentOrder.conflicts" :key="c.code" class="conflict-item"
              :data-testid="`conflict-${c.code}`">
          <b>{{ c.code }}</b> 同时进入 {{ c.groups.join('、') }} 组，未产生双归属
        </span>
      </div>

      <table class="data-table order-table" data-testid="order-groups-table">
        <thead>
          <tr>
            <th>组号</th><th>保留档案</th><th>入组档案</th>
            <th>处理状态</th><th>重复入组 / 等候或办结说明</th><th>核对规格</th><th>冲突裁决</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="g in currentOrder.groups" :key="String(g.key)"
              :data-testid="`order-group-${g.key}`" :data-status="g.status"
              :data-conflict="g.conflict ? '1' : '0'">
            <td>{{ g.组号 }}（{{ g.key }}）</td>
            <td><span class="code-badge">{{ g.keep_code }}</span></td>
            <td>
              <span v-for="c in g.codes" :key="c" class="code-badge small"
                    :class="{ dup: g.conflict_codes?.includes(c) }">{{ c }}</span>
            </td>
            <td>
              <span class="status-badge" :class="statusClass(String(g.status))"
                    :data-testid="`group-status-${g.key}`">{{ g.status }}</span>
            </td>
            <td class="reason-cell" :data-testid="`group-reason-${g.key}`">
              <span v-if="g.conflict" class="warn-text">重复入组 · 归属冲突　</span>
              {{ g.reason || (g.status === '已办结' ? '已按确认归属办结，可按原提交追溯' : '合格待办') }}
            </td>
            <td>
              <RouterLink class="link" :to="`/sku_match?goods=${g.keep_code}`"
                          :data-testid="`spec-link-${g.key}`">
                规格/基线核对
              </RouterLink>
            </td>
            <td>
              <button v-if="g.conflict" class="link" type="button"
                      :data-testid="`adjudicate-${g.key}`"
                      @click="adjudicate(g)">
                由 {{ g.keep_code }} 保留冲突档案
              </button>
              <span v-else>—</span>
            </td>
          </tr>
        </tbody>
      </table>

      <div class="order-actions">
        <button class="btn primary" type="button" data-testid="continue-order"
                :disabled="working" @click="continueOrder">
          {{ working ? '处理中…' : '继续原批提交（仅办结合格 / 待办组）' }}
        </button>
        <button class="btn" type="button" data-testid="resubmit-same-batch"
                :disabled="working" @click="resubmitSameBatch">
          按原提交重新提交本批
        </button>
        <button class="btn ghost" type="button" data-testid="refresh-order"
                @click="refreshOrder">刷新处理单</button>
      </div>
      <p v-if="actionMessage" class="merge-message" :class="{ error: !actionOk }"
         data-testid="order-message" role="status">{{ actionMessage }}</p>
    </section>

    <!-- 原单列表：离开页面后可重新进入原单 -->
    <section class="order-history" data-testid="merge-order-history">
      <header class="history-head">
        <h3>分组处理单 · 原单追溯</h3>
        <button class="btn ghost" type="button" data-testid="reload-orders" @click="loadOrders">
          刷新原单列表
        </button>
      </header>
      <p v-if="!orders.length" class="merge-hint" data-testid="no-orders">
        暂无处理单，勾选档案分组后批量提交即可生成。
      </p>
      <ul v-else class="order-list">
        <li v-for="o in orders" :key="o.id" class="order-list-item"
            :data-testid="`order-list-item-${o.id}`">
          <button type="button" class="link order-open" :data-testid="`open-order-${o.id}`"
                  @click="openOrder(o.id)">
            {{ o.单号 }}
          </button>
          <span class="status-badge" :class="statusClass(o.status)">{{ o.status }}</span>
          <span class="merge-hint">{{ orderSummary(o) }}</span>
        </li>
      </ul>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { listEntries } from '@/api/backend-service'
import {
  adjudicateMergeOrder,
  continueMergeOrder,
  getMergeOrder,
  listMergeOrders,
  submitMergeBatch,
  type MergeOrder,
  type MergeOrderResponse,
} from '@/api/merge'
import { useMergeDraft, type DraftGroup } from '@/domain/goods_registry'
import type { EntryRow } from '@/data/types'

const emit = defineEmits<{ changed: [] }>()

const route = useRoute()
const router = useRouter()
const {
  picked,
  draftGroups,
  togglePick,
  createGroupFromPicked,
  removeGroup,
  removeCode,
  addCode,
  resetDraft,
  toPayloadGroups,
} = useMergeDraft()

const goods = ref<EntryRow[]>([])
const orders = ref<MergeOrder[]>([])
const currentOrder = ref<MergeOrder | null>(null)
const submitting = ref(false)
const working = ref(false)
const message = ref('')
const messageOk = ref(true)
const actionMessage = ref('')
const actionOk = ref(true)
const quickCode = ref('')
const addCodeInputs = reactive<Record<number, string>>({})
let orderLoadSeq = 0

const validGroupCount = computed(() => toPayloadGroups().length)

function codeOf(row: EntryRow): string {
  return String(row['商品标识'] ?? '')
}

function nameOf(code: string): string {
  const row = goods.value.find((g) => codeOf(g) === code)
  return row ? String(row['商品名称'] ?? '') : '（档案列表外标识）'
}

function formatTime(value?: string): string {
  if (!value) return ''
  return value.replace('T', ' ').slice(0, 19)
}

function statusClass(status: string): string {
  if (status === '已办结' || status === '已匹配') return 'ok'
  if (status === '等候处理' || status.includes('冲突')) return 'warn'
  if (status === '部分办结') return 'partial'
  return 'pending'
}

function orderSummary(o: MergeOrder): string {
  const done = o.groups?.filter((g) => g.status === '已办结').length ?? 0
  const waiting = o.groups?.filter((g) => g.status === '等候处理').length ?? 0
  return `共 ${o.groups?.length ?? 0} 组 · 已办结 ${done} · 等候 ${waiting}`
}

async function loadGoods(): Promise<void> {
  try {
    const page = await listEntries('goods_registry', { size: '200' })
    goods.value = page.items
  } catch {
    goods.value = []
  }
}

async function loadOrders(): Promise<void> {
  try {
    const res = await listMergeOrders()
    orders.value = res.orders ?? res.items ?? []
  } catch {
    orders.value = []
  }
}

async function openOrder(id: number | string): Promise<void> {
  const seq = ++orderLoadSeq
  actionMessage.value = ''
  try {
    const res = await getMergeOrder(id)
    // 过期的“原单恢复”响应不能覆盖更新的提交 / 选择
    if (seq !== orderLoadSeq) return
    if (!res.ok || !res.order) {
      actionOk.value = false
      actionMessage.value = res.message || '原单不存在'
      return
    }
    currentOrder.value = res.order
    window.sessionStorage.setItem('merge-order-last', String(res.order.id))
    await router.replace({ query: { ...route.query, order: String(res.order.id) } })
  } catch (error) {
    if (seq !== orderLoadSeq) return
    actionOk.value = false
    actionMessage.value = error instanceof Error ? error.message : '原单读取失败'
  }
}

async function refreshOrder(): Promise<void> {
  if (currentOrder.value) await openOrder(currentOrder.value.id)
  await loadOrders()
}

function quickPick(): void {
  const code = quickCode.value.trim()
  if (!code) return
  togglePick(code, true)
  quickCode.value = ''
}

function addCodeTo(group: DraftGroup): void {
  const value = addCodeInputs[group.localKey] ?? ''
  if (addCode(group, value)) addCodeInputs[group.localKey] = ''
}

async function submitBatch(): Promise<void> {
  message.value = ''
  const groups = toPayloadGroups()
  if (!groups.length) {
    messageOk.value = false
    message.value = '每个合并组至少需要 2 个档案并指定保留档案'
    return
  }
  submitting.value = true
  try {
    const submitKey = `ui-${Date.now()}`
    const res: MergeOrderResponse = await submitMergeBatch(groups, { submit_key: submitKey })
    messageOk.value = res.ok
    message.value = res.message
    if (res.ok && res.order) {
      orderLoadSeq += 1 // 使尚未返回的“恢复原单”请求失效，避免覆盖新单
      currentOrder.value = res.order
      // 草稿已生成原单，清空草稿避免重复建单；原批可在处理单中追溯 / 重新提交
      resetDraft()
      window.sessionStorage.setItem('merge-order-last', String(res.order.id))
      await router.replace({ query: { ...route.query, order: String(res.order.id) } })
      await loadOrders()
      emit('changed')
    }
  } catch (error) {
    messageOk.value = false
    message.value = error instanceof Error ? error.message : '批量提交失败'
  } finally {
    submitting.value = false
  }
}

async function continueOrder(): Promise<void> {
  if (!currentOrder.value) return
  working.value = true
  actionMessage.value = ''
  try {
    const res = await continueMergeOrder(currentOrder.value.id)
    actionOk.value = res.ok
    actionMessage.value = res.message
    if (res.order) currentOrder.value = res.order
    await loadOrders()
    emit('changed')
  } catch (error) {
    actionOk.value = false
    actionMessage.value = error instanceof Error ? error.message : '继续处理失败'
  } finally {
    working.value = false
  }
}

async function resubmitSameBatch(): Promise<void> {
  if (!currentOrder.value) return
  working.value = true
  actionMessage.value = ''
  try {
    const o = currentOrder.value
    const groups = o.groups.map((g) => ({
      key: String(g.key),
      keep: String(g.keep_code),
      members: (g.member_codes ?? []).map(String),
    }))
    const res = await submitMergeBatch(groups, {
      submit_key: o.submit_key || `resubmit-${o.id}`,
      batch_no: o.单号,
    })
    actionOk.value = res.ok
    actionMessage.value = `${res.message}（已确认关系不会被移动）`
    if (res.order) currentOrder.value = res.order
    await loadOrders()
    emit('changed')
  } catch (error) {
    actionOk.value = false
    actionMessage.value = error instanceof Error ? error.message : '重新提交失败'
  } finally {
    working.value = false
  }
}

async function adjudicate(g: MergeOrder['groups'][number]): Promise<void> {
  if (!currentOrder.value) return
  working.value = true
  try {
    const code = g.conflict_codes?.[0]
    const res = await adjudicateMergeOrder(currentOrder.value.id, {
      group_key: g.key,
      code,
      keep: g.keep_code,
    })
    actionOk.value = res.ok
    actionMessage.value = res.message
    if (res.order) currentOrder.value = res.order
    await loadOrders()
    emit('changed')
  } catch (error) {
    actionOk.value = false
    actionMessage.value = error instanceof Error ? error.message : '冲突裁决失败'
  } finally {
    working.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadGoods(), loadOrders()])
  const queryOrder = route.query.order
  if (queryOrder) {
    await openOrder(String(queryOrder))
  } else {
    const last = window.sessionStorage.getItem('merge-order-last')
    if (last && orders.value.some((o) => String(o.id) === last)) {
      await openOrder(last)
    }
  }
})
</script>
