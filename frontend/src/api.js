// 后端接口封装（规格 §10）。所有请求都走 /api，开发时由 Vite 代理到 Flask。
//
// 静态演示包：打包时会注入 window.__SPIKE_STATIC__ = true 和 window.__SPIKE_DATA__，
// 此时不再发 HTTP 请求，直接从内嵌数据里取，因此单文件 HTML 双击即可打开
// （file:// 下 fetch 会被浏览器拦掉，所以数据必须内嵌成 JS 对象）。

const STATIC = typeof window !== 'undefined' && !!window.__SPIKE_STATIC__

function staticLookup(path, params = {}) {
  const D = window.__SPIKE_DATA__ || {}
  if (path === '/api/health') return D.health
  if (path === '/api/products') return D.products
  if (path === '/api/meta/aspects') return D.aspect_dict
  if (path === '/api/competitive-gap') {
    return (D.gap || {})[params.target_id] || { matrix: [], common_aspects: [], gaps: [], common_aspect_count: 0 }
  }

  const m = path.match(/^\/api\/products\/([^/]+)\/(overview|advantages|painpoints|aspects)$/)
  if (m) {
    const [, pid, kind] = m
    const item = (D.product_data || {})[pid] || {}
    return item[kind] || { product_id: pid, items: [], count: 0 }
  }

  if (path === '/api/evidence') {
    // 按 商品|属性|情感 预取，前端只做分页切片
    const key = [params.product_id, params.aspect_name, params.sentiment || ''].join('|')
    const all = (D.evidence || {})[key] || []
    const page = Number(params.page || 1)
    const size = Number(params.page_size || 10)
    const start = (page - 1) * size
    const items = all.slice(start, start + size)
    return {
      total: all.length,
      page,
      page_size: size,
      total_pages: Math.max(1, Math.ceil(all.length / size)),
      has_next: start + size < all.length,
      items,
    }
  }

  throw new Error(`静态演示包缺少接口数据: ${path}`)
}

async function request(path, params) {
  if (STATIC) return staticLookup(path, params)

  const url = new URL(path, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v)
    })
  }
  const res = await fetch(url.toString(), { headers: { Accept: 'application/json' } })
  if (!res.ok) {
    let detail = ''
    try {
      detail = JSON.stringify(await res.json())
    } catch {
      detail = await res.text()
    }
    throw new Error(`HTTP ${res.status} ${path} ${detail}`)
  }
  return res.json()
}

export const api = {
  health: () => request('/api/health'),
  products: () => request('/api/products'),
  overview: (pid) => request(`/api/products/${pid}/overview`),
  advantages: (pid) => request(`/api/products/${pid}/advantages`),
  painpoints: (pid) => request(`/api/products/${pid}/painpoints`),
  aspects: (pid) => request(`/api/products/${pid}/aspects`),
  gap: (targetId, competitorIds) =>
    request('/api/competitive-gap', { target_id: targetId, competitor_ids: competitorIds }),
  evidence: (params) => request('/api/evidence', params),
  aspectDict: () => request('/api/meta/aspects'),
}

export default api
