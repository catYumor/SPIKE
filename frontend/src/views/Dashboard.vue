<script setup>
import { computed, ref, watch } from 'vue'

import api from '../api'
import EChart from '../components/EChart.vue'
import {
  fmtNum,
  fmtPct,
  kanoTagClass,
  kanoText,
  labelTagClass,
  labelText,
  rateColor,
} from '../format'
import { productById, productLabel, store } from '../store'

const loading = ref(false)
const error = ref('')
const overview = ref(null)
const rival = ref(null)

async function load() {
  if (!store.targetId) return
  loading.value = true
  error.value = ''
  try {
    overview.value = await api.overview(store.targetId)
    rival.value = store.competitorId ? await api.overview(store.competitorId) : null
  } catch (e) {
    error.value = e.message
    overview.value = null
  } finally {
    loading.value = false
  }
}

watch(() => [store.targetId, store.competitorId], load, { immediate: true })

const target = computed(() => overview.value?.product || null)
const summary = computed(() => overview.value?.summary || null)
const aspects = computed(() => overview.value?.aspects || [])

// 堆叠条形图：各属性的正面/中性/负面提及量
const barOption = computed(() => {
  const list = [...aspects.value].sort((a, b) => a.total_count - b.total_count)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { top: 0, right: 0, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 12 } },
    grid: { left: 78, right: 26, top: 42, bottom: 24 },
    xAxis: { type: 'value', splitLine: { lineStyle: { color: '#eef2f7' } } },
    yAxis: {
      type: 'category',
      data: list.map((a) => a.aspect_name),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#e3e8f0' } },
    },
    series: [
      {
        name: '正面',
        type: 'bar',
        stack: 'total',
        barMaxWidth: 26,
        itemStyle: { color: '#16a34a' },
        data: list.map((a) => a.pos_count),
      },
      {
        name: '中性',
        type: 'bar',
        stack: 'total',
        itemStyle: { color: '#cbd5e1' },
        data: list.map((a) => a.neu_count),
      },
      {
        name: '负面',
        type: 'bar',
        stack: 'total',
        itemStyle: { color: '#dc2626' },
        data: list.map((a) => a.neg_count),
      },
    ],
  }
})

// 雷达图：目标产品 vs 竞品 的属性正面率
const radarOption = computed(() => {
  const names = aspects.value.map((a) => a.aspect_name)
  if (!names.length) return null
  const rivalMap = new Map((rival.value?.aspects || []).map((a) => [a.aspect_name, a]))
  const series = [
    {
      value: aspects.value.map((a) => Number(a.pos_rate.toFixed(3))),
      name: productLabel(target.value),
      areaStyle: { opacity: 0.18 },
      lineStyle: { width: 2 },
      itemStyle: { color: '#2563eb' },
    },
  ]
  if (rival.value) {
    series.push({
      value: names.map((n) => Number((rivalMap.get(n)?.pos_rate ?? 0).toFixed(3))),
      name: productLabel(rival.value.product),
      areaStyle: { opacity: 0.12 },
      lineStyle: { width: 2 },
      itemStyle: { color: '#f59e0b' },
    })
  }
  return {
    tooltip: { trigger: 'item' },
    legend: { top: 0, left: 'center', itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 12 } },
    radar: {
      indicator: names.map((n) => ({ name: n, max: 1 })),
      radius: '64%',
      center: ['50%', '56%'],
      axisName: { fontSize: 12, color: '#6b7280' },
      splitLine: { lineStyle: { color: '#e8edf5' } },
      splitArea: { areaStyle: { color: ['#fff', '#fbfcfe'] } },
      axisLine: { lineStyle: { color: '#e8edf5' } },
    },
    series: [{ type: 'radar', emphasis: { focus: 'series' }, data: series }],
  }
})
</script>

<template>
  <div>
    <h1 class="page-title">产品健康总览</h1>
    <p class="page-desc">
      单产品视角：属性覆盖度、正负情感分布、各属性健康度评级。
      <template v-if="target">
        当前：<strong>{{ target.brand }} {{ target.model }}</strong>（SKU {{ target.product_id }}）
      </template>
    </p>

    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="loading" class="loading">加载中…</div>

    <template v-else-if="overview">
      <div class="grid grid-kpi" style="margin-bottom: 16px">
        <div class="kpi">
          <div class="kpi-label">覆盖评论数</div>
          <div class="kpi-value">{{ fmtNum(summary.covered_reviews) }}</div>
          <div class="kpi-foot">被识别出属性的评论条数</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">属性提及总数</div>
          <div class="kpi-value">{{ fmtNum(summary.total_aspects) }}</div>
          <div class="kpi-foot">{{ summary.aspect_count }} 个属性维度</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">整体正面率</div>
          <div class="kpi-value pos">{{ fmtPct(summary.positive_rate) }}</div>
          <div class="kpi-foot">全部属性提及中正面占比</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">整体负面率</div>
          <div class="kpi-value neg">{{ fmtPct(summary.negative_rate) }}</div>
          <div class="kpi-foot">越低越好</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">痛点属性 / 优势属性</div>
          <div class="kpi-value">
            {{ summary.painpoint_count }}
            <span style="font-size: 16px; color: var(--text-dim)">/</span>
            {{ summary.advantage_count }}
          </div>
          <div class="kpi-foot">基于负/正面率阈值判定</div>
        </div>
      </div>

      <div class="grid grid-2" style="margin-bottom: 16px">
        <div class="card">
          <h2 class="card-title">
            属性提及量分布
            <span class="hint">正面 / 中性 / 负面堆叠</span>
          </h2>
          <EChart :option="barOption" height="330px" />
        </div>
        <div class="card">
          <h2 class="card-title">
            属性正面率雷达
            <span class="hint">与竞品对照，越靠外越好</span>
          </h2>
          <EChart v-if="radarOption" :option="radarOption" height="330px" />
          <div v-else class="empty">暂无数据</div>
        </div>
      </div>

      <div class="card">
        <h2 class="card-title">
          属性明细
          <span class="hint">按提及量降序</span>
        </h2>
        <table class="data">
          <thead>
            <tr>
              <th>属性</th>
              <th class="num">提及</th>
              <th class="num">正面</th>
              <th class="num">中性</th>
              <th class="num">负面</th>
              <th style="width: 160px">正面率</th>
              <th>评级</th>
              <th>Kano 类型</th>
              <th class="num">优先级</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in aspects" :key="a.aspect_name">
              <td><strong>{{ a.aspect_name }}</strong></td>
              <td class="num">{{ a.total_count }}</td>
              <td class="num" style="color: var(--pos)">{{ a.pos_count }}</td>
              <td class="num" style="color: var(--text-dim)">{{ a.neu_count }}</td>
              <td class="num" style="color: var(--neg)">{{ a.neg_count }}</td>
              <td>
                <div style="display: flex; align-items: center; gap: 8px">
                  <div class="bar" style="flex: 1">
                    <span
                      :style="{
                        width: `${Math.round(a.pos_rate * 100)}%`,
                        background: rateColor(a.pos_rate),
                      }"
                    ></span>
                  </div>
                  <span class="num" style="font-size: 12px; width: 44px">{{ fmtPct(a.pos_rate) }}</span>
                </div>
              </td>
              <td><span :class="labelTagClass(a.label)">{{ labelText(a.label) }}</span></td>
              <td><span :class="kanoTagClass(a.kano_type)">{{ kanoText(a.kano_type) }}</span></td>
              <td class="num">{{ Number(a.priority_score || 0).toFixed(2) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <div v-else class="empty">
      <span class="big">📭</span>
      暂无数据，请先执行 <code>bash scripts/run_pipeline.sh</code>
    </div>
  </div>
</template>
