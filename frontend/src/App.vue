<script setup>
import { computed, onMounted } from 'vue'

import api from './api'
import { productLabel, store } from './store'

const targets = computed(() => store.products.filter((p) => p.is_target))
const competitors = computed(() => store.products.filter((p) => !p.is_target))

onMounted(async () => {
  store.loading = true
  try {
    const data = await api.products()
    store.products = data.products || []
    const t = store.products.find((p) => p.is_target)
    store.targetId = t ? t.product_id : store.products[0]?.product_id || ''
    const c = store.products.find((p) => !p.is_target)
    store.competitorId = c ? c.product_id : ''
    if (!store.products.length) {
      store.error = '数据库里还没有产品数据，请先执行：bash scripts/run_pipeline.sh'
    }
  } catch (e) {
    store.error =
      `无法连接后端：${e.message}\n\n` +
      '请确认后端已启动：bash scripts/run_backend.sh start'
  } finally {
    store.loading = false
  }
})
</script>

<template>
  <header class="app-header">
    <div class="app-header-inner">
      <div class="logo">
        <span class="logo-mark">S</span>
        <span>SPIKE</span>
        <span class="logo-sub">电商评论竞争力分析</span>
      </div>

      <div class="field">
        <label>目标产品</label>
        <select v-model="store.targetId" class="select">
          <option v-for="p in targets" :key="p.product_id" :value="p.product_id">
            {{ productLabel(p) }}
          </option>
        </select>
      </div>

      <div class="field">
        <label>对比竞品</label>
        <select v-model="store.competitorId" class="select">
          <option v-for="p in competitors" :key="p.product_id" :value="p.product_id">
            {{ productLabel(p) }}
          </option>
        </select>
      </div>

      <nav class="nav">
        <router-link to="/dashboard">健康总览</router-link>
        <router-link to="/insights">优势与痛点</router-link>
        <router-link to="/gap">竞品差距</router-link>
      </nav>
    </div>
  </header>

  <main class="main">
    <div v-if="store.error" class="error-box">{{ store.error }}</div>
    <div v-if="store.loading" class="loading">加载中…</div>
    <router-view v-else />
  </main>

  <footer class="app-foot">
    SPIKE · 评论驱动竞品分析 ｜ 数据链路：
    <code>crawler → HDFS → Spark(4 jobs) → MySQL → Flask → Vue</code>
  </footer>
</template>
