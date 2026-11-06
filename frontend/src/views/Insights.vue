<script setup>
import { computed, ref, watch } from 'vue'

import api from '../api'
import {
  fmtPct,
  kanoTagClass,
  kanoText,
  labelTagClass,
  labelText,
  rateColor,
} from '../format'
import { productLabel, store } from '../store'

const loading = ref(false)
const error = ref('')
const advantages = ref([])
const painpoints = ref([])
const painFallback = ref(false)
const painNote = ref('')

const selected = ref(null) // { aspect_name, sentiment }
const evidence = ref([])
const evidenceLoading = ref(false)

async function load() {
  if (!store.targetId) return
  loading.value = true
  error.value = ''
  selected.value = null
  evidence.value = []
  try {
    const [adv, pain] = await Promise.all([
      api.advantages(store.targetId),
      api.painpoints(store.targetId),
    ])
    advantages.value = adv.items || []
    painpoints.value = pain.items || []
    painFallback.value = !!pain.fallback
    painNote.value = pain.note || ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

watch(() => store.targetId, load, { immediate: true })

async function showEvidence(aspectName, sentiment) {
  if (selected.value?.aspect_name === aspectName && selected.value?.sentiment === sentiment) {
    selected.value = null
    evidence.value = []
    return
  }
  selected.value = { aspect_name: aspectName, sentiment }
  evidenceLoading.value = true
  try {
    const data = await api.evidence({
      product_id: store.targetId,
      aspect_name: aspectName,
      sentiment,
      page_size: 8,
    })
    evidence.value = data.items || []
  } catch (e) {
    evidence.value = []
    error.value = e.message
  } finally {
    evidenceLoading.value = false
  }
}

const product = computed(() => store.products.find((p) => p.product_id === store.targetId))
const total = computed(() => advantages.value.length + painpoints.value.length)
</script>

<template>
  <div>
    <h1 class="page-title">优势与痛点清单</h1>
    <p class="page-desc">
      按规格 §8.4 的阈值判定：提及量 ≥ 5 时，正面率 ≥ 75% 记为优势，负面率 ≥ 30% 记为痛点。
      痛点按 <code>priority_score</code> 排序，分数越高越该优先改进。
      <template v-if="product">当前：<strong>{{ productLabel(product) }}</strong></template>
    </p>

    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="loading" class="loading">加载中…</div>

    <template v-else>
      <div v-if="!total" class="card">
        <div class="empty">
          <span class="big">🎯</span>
          当前产品没有达到阈值的优势或痛点属性。<br />
          这本身是个结论：没有明显长板，也没有致命短板。
        </div>
      </div>

      <div v-else class="grid grid-2">
        <!-- 优势 -->
        <div>
          <div class="card" style="margin-bottom: 16px">
            <h2 class="card-title">
              <span class="tag tag-advantage">优势</span>
              共 {{ advantages.length }} 项
            </h2>
            <div v-if="!advantages.length" class="empty" style="padding: 24px">
              暂无达到正面率 75% 的属性
            </div>
            <div
              v-for="a in advantages"
              :key="a.aspect_name"
              style="border-bottom: 1px solid #f1f5f9; padding: 12px 0"
            >
              <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">
                <strong style="font-size: 15px">{{ a.aspect_name }}</strong>
                <span :class="labelTagClass(a.label)">{{ labelText(a.label) }}</span>
                <span :class="kanoTagClass(a.kano_type)">{{ kanoText(a.kano_type) }}</span>
                <span style="margin-left: auto; font-size: 12px; color: var(--text-dim)">
                  提及 {{ a.total_count }} 次
                </span>
              </div>
              <div style="display: flex; align-items: center; gap: 10px; margin: 8px 0">
                <div class="bar" style="flex: 1">
                  <span
                    :style="{
                      width: `${Math.round(a.pos_rate * 100)}%`,
                      background: rateColor(a.pos_rate),
                    }"
                  ></span>
                </div>
                <span class="num" style="font-size: 13px; font-weight: 600; color: var(--pos)">
                  {{ fmtPct(a.pos_rate) }}
                </span>
              </div>
              <button class="select" style="font-size: 12px; padding: 4px 10px" @click="showEvidence(a.aspect_name, 'positive')">
                {{ selected?.aspect_name === a.aspect_name && selected?.sentiment === 'positive' ? '收起证据' : '查看用户原话' }}
              </button>
              <div v-if="selected?.aspect_name === a.aspect_name && selected?.sentiment === 'positive'">
                <div v-if="evidenceLoading" class="loading" style="padding: 14px">加载证据…</div>
                <div v-for="e in evidence" :key="e.review_id + e.evidence_text" class="quote positive">
                  {{ e.evidence_text }}
                  <div class="meta">
                    {{ e.create_time }} ｜ {{ e.score }} 星 ｜ 置信度
                    {{ (e.confidence ?? 0).toFixed(2) }}
                  </div>
                </div>
                <div v-if="!evidenceLoading && !evidence.length" class="empty" style="padding: 12px">
                  暂无证据
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 痛点 -->
        <div>
          <div class="card" style="margin-bottom: 16px">
            <h2 class="card-title">
              <span class="tag tag-painpoint">{{ painFallback ? '相对短板' : '痛点' }}</span>
              共 {{ painpoints.length }} 项
              <span class="hint">{{ painFallback ? '未达阈值，按负面率排序' : '按优先级降序' }}</span>
            </h2>
            <div
              v-if="painFallback && painNote"
              style="
                background: #fff7ed;
                border: 1px solid #fed7aa;
                color: #9a3412;
                border-radius: 8px;
                padding: 9px 12px;
                font-size: 12px;
                line-height: 1.6;
                margin-bottom: 10px;
              "
            >
              ⚠️ {{ painNote }}
            </div>
            <div v-if="!painpoints.length" class="empty" style="padding: 24px">
              暂无达到负面率 30% 的属性
            </div>
            <div
              v-for="(a, i) in painpoints"
              :key="a.aspect_name"
              style="border-bottom: 1px solid #f1f5f9; padding: 12px 0"
            >
              <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">
                <span
                  class="tag"
                  style="background: var(--brand-soft); color: var(--brand); min-width: 24px; text-align: center"
                >
                  P{{ i + 1 }}
                </span>
                <strong style="font-size: 15px">{{ a.aspect_name }}</strong>
                <span :class="kanoTagClass(a.kano_type)">{{ kanoText(a.kano_type) }}</span>
                <span style="margin-left: auto; font-size: 12px; color: var(--text-dim)">
                  优先级 <strong class="num">{{ Number(a.priority_score || 0).toFixed(2) }}</strong>
                </span>
              </div>
              <div style="display: flex; gap: 18px; margin: 8px 0; font-size: 12px; color: var(--text-dim)">
                <span>提及 <strong class="num">{{ a.total_count }}</strong></span>
                <span>负面 <strong class="num" style="color: var(--neg)">{{ a.neg_count }}</strong></span>
                <span>负面率 <strong class="num" style="color: var(--neg)">{{ fmtPct(a.neg_rate) }}</strong></span>
              </div>
              <button class="select" style="font-size: 12px; padding: 4px 10px" @click="showEvidence(a.aspect_name, 'negative')">
                {{ selected?.aspect_name === a.aspect_name && selected?.sentiment === 'negative' ? '收起证据' : '查看用户原话' }}
              </button>
              <div v-if="selected?.aspect_name === a.aspect_name && selected?.sentiment === 'negative'">
                <div v-if="evidenceLoading" class="loading" style="padding: 14px">加载证据…</div>
                <div v-for="e in evidence" :key="e.review_id + e.evidence_text" class="quote negative">
                  {{ e.evidence_text }}
                  <div class="meta">
                    {{ e.create_time }} ｜ {{ e.score }} 星 ｜ 置信度
                    {{ (e.confidence ?? 0).toFixed(2) }}
                  </div>
                </div>
                <div v-if="!evidenceLoading && !evidence.length" class="empty" style="padding: 12px">
                  暂无证据
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
