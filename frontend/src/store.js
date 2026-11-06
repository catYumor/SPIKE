import { reactive } from 'vue'

// 极简全局状态：产品列表 + 当前选中的目标产品/竞品
export const store = reactive({
  products: [],
  targetId: '',
  competitorId: '',
  loading: false,
  error: '',
})

export function productById(id) {
  return store.products.find((p) => p.product_id === id) || null
}

export function productLabel(p) {
  if (!p) return ''
  return `${p.brand} ${p.model}`
}
