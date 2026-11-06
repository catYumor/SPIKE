<script setup>
import { computed, ref, watch } from 'vue'

import api from '../api'
import EChart from '../components/EChart.vue'
import { fmtNum, fmtPct } from '../format'
import { productLabel, store } from '../store'

const loading = ref(false)
const error = ref('')
const data = ref(null)

async function load() {
  if (!store.targetId) return
  loading.value = true
  error.value = ''
  try {
    data.value = await api.gap(store.targetId, store.competitorId || undefined)
  } catch (e) {
    error.value = e.message
    data.value = null
  } finally {
    loading.value = false
  }
}

watch(() => [store.targetId, store.competitorId], load, { immediate: true })

const targetId = computed(() => data.value?.target?.product_id)
const rivalId = computed(() => data.value?.competitors?.[0]?.product_id)
const rival = computed(() => data.value?.competitors?.[0] || null)

// 每个共同属性：目标 vs 竞品的正面率及差值
const rows = computed(() => {
  if (!data.value || !targetId.value || !rivalId.value) return []
  return data.value.matrix
    .map((m) => {
      const t = m.values[targetId.value]
      const c = m.values[rivalId.value]
      return {
        aspect_name: m.aspect_name,
        target: t,
        competitor: c,
        delta: t.pos_rate - c.pos_rate,
        deltaNeg: t.neg_rate - c.neg_rate,
      }
    })
    .sort((a, b) => b.delta - a.delta)
})

const leadCount = computed(() => rows.value.filter((r) => r.delta > 0.02).length)
const lagCount = computed(() => rows.value.filter((r) => r.delta < -0.02).length)
const best = computed(() => rows.value[0] || null)
const worst = computed(() => rows.value[rows.value.length - 1] || null)

const chartOption = computed(() => {
  if (!rows.value.length) return null
  const list = [...rows.value].sort((a, b) => a.target.pos_rate - b.target.pos_rate)
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      valueFormatter: (v) => `${(v * 100).toFixed(1)}%`,
    },
    legend: { top: 0, right: 0, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 12 } },
    grid: { left: 78, right: 40, top: 42, bottom: 24 },
    xAxis: {
      type: 'value',
      max: 1,
      axisLabel: { formatter: (v) => `${Math.round(v * 100)}%` },
      splitLine: { lineStyle: { color: '#eef2f7' } },
    },
    yAxis: {
      type: 'category',
      data: list.map((r) => r.aspect_name),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#e3e8f0' } },
    },
    series: [
      {
        name: productLabel(data.value.target),
        type: 'bar',
        barMaxWidth: 16,
        itemStyle: { color: '#2563eb', borderRadius: [0, 3, 3, 0] },
        data: list.map((r) => Number(r.target.pos_rate.toFixed(4))),
      },
      {
        name: productLabel(rival.value),
        type: 'bar',
        barMaxWidth: 16,
        itemStyle: { color: '#f59e0b', borderRadius: [0, 3, 3, 0] },
        data: list.map((r) => Number(r.competitor.pos_rate.toFixed(4))),
      },
    ],
  }
})

function conclusion(delta) {
  if (delta > 0.05) return '领先'
  if (delta < -0.05) return '落后'
  if (delta > 0.02) return '略优'
  if (delta < -0.02) return '略劣'
  return '持平'
}

function conclusionClass(delta) {
  if (delta > 0.02) return 'delta-up'
  if (delta < -0.02) return 'delta-down'
  return ''
}

function fmtDelta(x) {
  const v = x * 100
  const sign = v > 0 ? '+' : ''
  return `${sign}${v.toFixed(1)}pp`
}
</script>

<template>
  <div>
    <h1 class="page-title">竞品差距矩阵</h1>
    <p class="page-desc">
      行是属性、列是产品，单元格为正面率。差值 = 目标 − 竞品，正数表示本方更好。
      <template v-if="data">
        <strong>{{ productLabel(data.target) }}</strong> vs
        <strong>{{ productLabel(rival) }}</strong>
      </template>
    </p>

    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="loading" class="loading">加载中…</div>

    <template v-else-if="data">
      <div v-if="!rows.length" class="card">
        <div class="empty">
          <span class="big">🔍</span>
          两个产品没有共同被提及的属性，无法对比。<br />
          可能需要先补充数据量，或检查属性字典是否覆盖了这些评论。
        </div>
      </div>

      <template v-else>
        <div class="grid grid-kpi" style="margin-bottom: 16px">
          <div class="kpi">
            <div class="kpi-label">可比属性数</div>
            <div class="kpi-value">{{ rows.length }}</div>
            <div class="kpi-foot">规格要求 ≥ 5，达标</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">领先属性</div>
            <div class="kpi-value pos">{{ leadCount }}</div>
            <div class="kpi-foot">正面率高出 2pp 以上</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">落后属性</div>
            <div class="kpi-value neg">{{ lagCount }}</div>
            <div class="kpi-foot">正面率低 2pp 以上</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">最大优势</div>
            <div class="kpi-value" style="font-size: 20px">
              {{ best?.aspect_name || '—' }}
            </div>
            <div class="kpi-foot delta-up">{{ best ? fmtDelta(best.delta) : '' }}</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">最大短板</div>
            <div class="kpi-value" style="font-size: 20px">
              {{ worst?.aspect_name || '—' }}
            </div>
            <div class="kpi-foot delta-down">{{ worst ? fmtDelta(worst.delta) : '' }}</div>
          </div>
        </div>

        <div class="card" style="margin-bottom: 16px">
          <h2 class="card-title">
            属性正面率对照
            <span class="hint">蓝 = 目标产品，橙 = 竞品</span>
          </h2>
          <EChart :option="chartOption" height="340px" />
        </div>

        <div class="card">
          <h2 class="card-title">
            差距矩阵
            <span class="hint">按差值降序，绿底为该属性最优</span>
          </h2>
          <table class="data matrix">
            <thead>
              <tr>
                <th>属性</th>
                <th class="num">{{ productLabel(data.target) }}</th>
                <th class="num">{{ productLabel(rival) }}</th>
                <th class="num">差值</th>
                <th class="num">提及量（目标 / 竞品）</th>
                <th>结论</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in rows" :key="r.aspect_name">
                <td><strong>{{ r.aspect_name }}</strong></td>
                <td
                  class="cell"
                  :class="r.target.pos_rate >= r.competitor.pos_rate ? 'best' : 'worst'"
                >
                  {{ fmtPct(r.target.pos_rate) }}
                </td>
                <td
                  class="cell"
                  :class="r.competitor.pos_rate > r.target.pos_rate ? 'best' : 'worst'"
                >
                  {{ fmtPct(r.competitor.pos_rate) }}
                </td>
                <td class="num" :class="conclusionClass(r.delta)" style="text-align: right">
                  {{ fmtDelta(r.delta) }}
                </td>
                <td class="num" style="color: var(--text-dim)">
                  {{ fmtNum(r.target.total_count) }} / {{ fmtNum(r.competitor.total_count) }}
                </td>
                <td>
                  <span
                    class="tag"
                    :class="
                      r.delta > 0.02
                        ? 'tag-advantage'
                        : r.delta < -0.02
                          ? 'tag-painpoint'
                          : 'tag-neutral'
                    "
                  >
                    {{ conclusion(r.delta) }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </template>

    <div v-else class="empty">
      <span class="big">📭</span>
      暂无数据，请先执行 <code>bash scripts/run_pipeline.sh</code>
    </div>
  </div>
</template>
