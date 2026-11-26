<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <!-- 商品登记中心：批量归并工作台 + 分组处理单 -->
    <MergeDesk v-if="meta.key === 'goods_registry'" @changed="reload" />

    <!-- 规格匹配器：确认归属 / 基线重读 / 旧档案依据 -->
    <SpecBaselinePanel v-if="meta.key === 'sku_match'" />

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-if="isGoods" class="pick-col">
            <input type="checkbox" data-testid="pick-all-page" aria-label="全选本页档案"
                   :checked="allPagePicked" @change="togglePickAll" />
            勾选
          </th>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th v-if="isGoods" data-testid="th-package-spec">包装规格</th>
          <th>当前状态</th>
          <th v-if="isSku" data-testid="th-former-goods">原归属档案</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`"
            :data-status="String(row.status)" :data-goods-code="codeOf(row)">
          <td v-if="isGoods" class="pick-col">
            <input
              type="checkbox"
              class="row-pick"
              :id="`select-goods-${row.id}`"
              :data-testid="`pick-${codeOf(row)}`"
              :aria-label="`勾选商品 ${codeOf(row)} ${row['商品名称'] ?? ''}`"
              :checked="mergeDraft.isPicked(codeOf(row))"
              @change="mergeDraft.togglePick(codeOf(row), ($event.target as HTMLInputElement).checked)"
            />
          </td>
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td v-if="isGoods" :data-testid="`row-spec-${row.id}`">{{ row['包装规格'] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td v-if="isSku" :data-testid="`row-former-${row.id}`">{{ row['原商品档案'] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-if="isGoods"
              class="link"
              type="button"
              :data-testid="`batch-pick-${row.id}`"
              :aria-label="`把 ${codeOf(row)} 加入新合并组`"
              @click="quickGroupRow(row)"
            >
              加入合并组
            </button>
            <button
              v-if="isSku"
              class="link"
              type="button"
              :data-testid="`spec-check-${row.id}`"
              @click="checkRowBaseline(row)"
            >
              核对基线
            </button>
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="onAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="emptyColspan" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showKeeperPicker" class="modal-backdrop" data-testid="keeper-modal">
      <form class="modal" @submit.prevent="confirmPairMerge">
        <h3>选择保留档案（逐对合并）</h3>
        <p class="merge-hint">
          将 <b>{{ keeperRow ? codeOf(keeperRow) : '' }}</b>
          （{{ keeperRow?.['商品名称'] }}）并入下列保留档案；品牌或包装规格不同会被拦截。
        </p>
        <label class="create-field">
          <span>保留档案（商品标识）</span>
          <select v-model="keeperCode" data-testid="keeper-select" required>
            <option value="" disabled>请选择保留档案</option>
            <option v-for="c in keeperOptions" :key="c.code" :value="c.code">
              {{ c.code }} · {{ c.name }} · {{ c.brand }}
            </option>
          </select>
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showKeeperPicker = false">取消</button>
          <button class="btn primary" data-testid="confirm-pair-merge" type="submit" :disabled="actionBusy">
            {{ actionBusy ? '合并中…' : '确认合并' }}
          </button>
        </div>
        <p v-if="pairMessage" class="merge-message" :class="{ error: !pairOk }"
           data-testid="pair-merge-message">{{ pairMessage }}</p>
      </form>
    </div>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text" data-testid="page-error">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import MergeDesk from '@/components/MergeDesk.vue'
import SpecBaselinePanel from '@/components/SpecBaselinePanel.vue'
import {
  createEntry,
  downloadEntries,
  listEntries,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import { useMergeDraft } from '@/domain/goods_registry'
import type { EntryRow } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const router = useRouter()
const meta = moduleMeta(props.moduleKey)
const isGoods = computed(() => props.moduleKey === 'goods_registry')
const isSku = computed(() => props.moduleKey === 'sku_match')
const mergeDraft = useMergeDraft()

const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})

const showKeeperPicker = ref(false)
const keeperRow = ref<EntryRow | null>(null)
const keeperCode = ref('')
const actionBusy = ref(false)
const pairMessage = ref('')
const pairOk = ref(true)

function codeOf(row: EntryRow): string {
  return String(row['商品标识'] ?? row.id ?? '')
}

const allPagePicked = computed(
  () => rows.value.length > 0 && rows.value.every((r) => mergeDraft.isPicked(codeOf(r))),
)

const emptyColspan = computed(
  () => meta.fields.length + 2 + (isGoods.value ? 2 : 0) + (isSku.value ? 1 : 0),
)

const keeperOptions = computed(() =>
  rows.value
    .filter((r) => r.id !== keeperRow.value?.id)
    .map((r) => ({
      code: codeOf(r),
      name: String(r['商品名称'] ?? ''),
      brand: String(r['品牌'] ?? ''),
    })),
)

const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

function togglePickAll(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  for (const row of rows.value) mergeDraft.togglePick(codeOf(row), checked)
}

function quickGroupRow(row: EntryRow) {
  const code = codeOf(row)
  mergeDraft.togglePick(code, true)
  mergeDraft.createGroupFromPicked()
  errorMessage.value = ''
  const el = document.querySelector('[data-testid="draft-groups"]')
  el?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
}

function checkRowBaseline(row: EntryRow) {
  const goods = codeOf(row)
  void router.push({ query: { ...router.currentRoute.value.query, goods } })
}

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function confirmPairMerge() {
  if (!keeperRow.value || !keeperCode.value) return
  actionBusy.value = true
  pairMessage.value = ''
  try {
    const result = await applyAction(
      meta.key, Number(keeperRow.value.id), '合并档案',
      { keep_code: keeperCode.value },
    )
    pairOk.value = result.ok
    pairMessage.value = result.message
    if (result.ok) {
      showKeeperPicker.value = false
      await reload()
    }
  } catch (error) {
    pairOk.value = false
    pairMessage.value = error instanceof Error ? error.message : '合并失败'
  } finally {
    actionBusy.value = false
  }
}

async function onAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  if (meta.key === 'goods_registry' && action === '合并档案') {
    keeperRow.value = row
    keeperCode.value = ''
    pairMessage.value = ''
    showKeeperPicker.value = true
    return
  }
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, { ...filters.value, size: '200' })
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

onMounted(reload)
</script>
