<template>
  <section class="spec-desk" data-testid="spec-baseline-desk" aria-label="规格与基线核对">
    <div class="spec-desk-head">
      <h3>规格匹配 · 确认归属与基线核对</h3>
      <p class="merge-hint">
        归并后匹配记录按新的确认归属重读价格基线；同时保留“原商品档案 / 原商品标识”，
        仍可查到之前所属档案与历史基线依据。
      </p>
    </div>

    <form class="spec-query" data-testid="spec-query-bar" @submit.prevent="submitCheck">
      <label>
        <span>商品档案标识</span>
        <input v-model="code" class="quick-input" data-testid="spec-query-input"
               placeholder="如 G42 / G51 / G52" aria-label="按商品档案查询规格" />
      </label>
      <label>
        <span>归一规格（可选）</span>
        <input v-model="spec" class="quick-input" data-testid="spec-filter-input"
               placeholder="如 750ml / 500ml" />
      </label>
      <button class="btn primary" type="submit" data-testid="spec-query-btn" :disabled="loading">
        {{ loading ? '核对中…' : '按档案核对规格与基线' }}
      </button>
    </form>

    <p v-if="error" class="merge-message error" data-testid="spec-error" role="alert">{{ error }}</p>

    <div v-if="view" class="spec-result" data-testid="spec-result"
         :data-current="view.current_code" :data-queried="view.queried_code">
      <p class="spec-summary" data-testid="spec-summary">{{ view.message }}</p>

      <div class="spec-grid">
        <div class="spec-card" data-testid="attribution-card">
          <h4>商品归属</h4>
          <dl>
            <dt>查询标识</dt>
            <dd data-testid="queried-code">{{ view.queried_code }}</dd>
            <dt>当前确认归属</dt>
            <dd><span class="code-badge current" data-testid="current-goods">{{ view.current_code }}</span></dd>
            <dt>之前所属档案（依据保留）</dt>
            <dd data-testid="former-goods">
              <span v-if="!view.former_codes.length" class="merge-hint">无（未发生归并）</span>
              <span v-for="c in view.former_codes" :key="c" class="code-badge old"
                    :data-testid="`former-code-${c}`">{{ c }}</span>
            </dd>
            <dt v-if="view.chain.length">归属轨迹</dt>
            <dd v-if="view.chain.length" data-testid="attribution-chain">
              <span v-for="(hop, i) in view.chain" :key="i" class="chain-hop">
                {{ hop.from }} → {{ hop.to }}
              </span>
            </dd>
          </dl>
        </div>

        <div class="spec-card" data-testid="specs-card">
          <h4>包装 / 候选规格</h4>
          <ul class="spec-list" data-testid="spec-list">
            <li v-for="s in view.specs" :key="s" :data-testid="`spec-item-${s}`">{{ s }}</li>
            <li v-if="!view.specs.length" class="merge-hint">暂未登记规格</li>
          </ul>
        </div>

        <div class="spec-card baseline-card" data-testid="baseline-card">
          <h4>重读的价格基线</h4>
          <p v-if="view.baseline_price !== null" class="baseline-price"
             data-testid="baseline-price">
            ¥<b>{{ view.baseline_price }}</b>
            <span class="merge-hint">{{ String(view.baseline?.['归一规格'] ?? '') }}</span>
          </p>
          <p v-else class="merge-hint" data-testid="no-baseline">当前归属下暂无生效基线</p>
          <ul class="baseline-list" data-testid="baseline-list">
            <li v-for="b in view.baselines" :key="String(b.id)"
                :data-testid="`baseline-row-${b.id}`">
              <span class="code-badge small">{{ String(b['商品标识']) }}</span>
              <span>{{ String(b['归一规格']) }}</span>
              <b>{{ String(b['基线价格']) }}</b>
              <span class="merge-hint">{{ String(b.status) }}</span>
              <span v-if="b['原商品标识']" class="old-ref"
                    :data-testid="`baseline-former-${b.id}`">
                原商品标识：{{ String(b['原商品标识']) }}
              </span>
            </li>
          </ul>
        </div>
      </div>

      <div class="spec-card" data-testid="matches-card">
        <h4>该归属下的匹配记录（含原商品档案依据）</h4>
        <table class="data-table">
          <thead>
            <tr><th>ID</th><th>匹配批次</th><th>当前商品档案</th><th>原商品档案</th>
              <th>候选规格</th><th>置信度</th><th>状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="m in view.matches" :key="String(m.id)"
                :data-testid="`spec-match-row-${m.id}`">
              <td>{{ String(m.id) }}</td>
              <td>{{ String(m['匹配批次']) }}</td>
              <td><span class="code-badge">{{ String(m['商品档案']) }}</span></td>
              <td :data-testid="`match-former-${m.id}`">
                {{ m['原商品档案'] ? String(m['原商品档案']) : '—' }}
              </td>
              <td>{{ String(m['候选规格']) }}</td>
              <td>{{ String(m.confidence ?? m['匹配置信度'] ?? '') }}</td>
              <td>{{ String(m.status ?? '') }}</td>
            </tr>
            <tr v-if="!view.matches.length">
              <td colspan="7" class="empty-state">该档案暂无可核对的匹配记录</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { specCheck, type SpecBaselineView } from '@/api/merge'

const route = useRoute()
const code = ref('')
const spec = ref('')
const loading = ref(false)
const error = ref('')
const view = ref<SpecBaselineView | null>(null)

async function runCheck(target?: string): Promise<void> {
  const wanted = (target ?? code.value).trim()
  if (!wanted) {
    error.value = '请输入商品档案标识，例如 G42'
    return
  }
  code.value = wanted
  loading.value = true
  error.value = ''
  try {
    view.value = await specCheck(wanted, spec.value.trim() || undefined)
    if (!view.value.ok) error.value = view.value.message
  } catch (e) {
    error.value = e instanceof Error ? e.message : '规格基线核对失败'
    view.value = null
  } finally {
    loading.value = false
  }
}

function submitCheck(): void {
  void runCheck()
}

watch(
  () => route.query.goods,
  (goods) => {
    if (typeof goods === 'string' && goods) {
      code.value = goods
      void runCheck(goods)
    }
  },
  { immediate: true },
)

defineExpose({ runCheck })
</script>
