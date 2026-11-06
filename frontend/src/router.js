import { createRouter, createWebHistory, createWebHashHistory } from 'vue-router'

import Dashboard from './views/Dashboard.vue'
import Insights from './views/Insights.vue'
import GapMatrix from './views/GapMatrix.vue'

const routes = [
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', name: 'dashboard', component: Dashboard, meta: { title: '产品健康总览' } },
  { path: '/insights', name: 'insights', component: Insights, meta: { title: '优势与痛点清单' } },
  { path: '/gap', name: 'gap', component: GapMatrix, meta: { title: '竞品差距矩阵' } },
]

// 静态演示包用 hash 路由：单文件 HTML 以 file:// 打开时，
// history 模式的 pushState 会被浏览器拒绝，hash 模式则不受限制。
const useHash = typeof window !== 'undefined' && !!window.__SPIKE_STATIC__

export default createRouter({
  history: useHash ? createWebHashHistory() : createWebHistory(),
  routes,
})
