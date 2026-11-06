export function fmtPct(x, digits = 1) {
  if (x === null || x === undefined || Number.isNaN(x)) return '—'
  return `${(x * 100).toFixed(digits)}%`
}

export function fmtNum(x) {
  if (x === null || x === undefined || Number.isNaN(x)) return '—'
  return Number(x).toLocaleString('zh-CN')
}

export function labelText(label) {
  return { advantage: '优势', painpoint: '痛点', neutral: '中性' }[label] || '中性'
}

export function kanoText(k) {
  return {
    must_be: '必备型',
    one_dimensional: '期望型',
    attractive: '兴奋型',
    indifferent: '无差异',
  }[k] || k || '—'
}

export function kanoTagClass(k) {
  return k === 'must_be' ? 'tag tag-must' : 'tag tag-kano'
}

export function labelTagClass(label) {
  return `tag tag-${label || 'neutral'}`
}

// 正面率对应的颜色（越高越绿）
export function rateColor(rate) {
  if (rate === null || rate === undefined) return '#94a3b8'
  if (rate >= 0.75) return '#16a34a'
  if (rate >= 0.6) return '#65a30d'
  if (rate >= 0.5) return '#d97706'
  return '#dc2626'
}
