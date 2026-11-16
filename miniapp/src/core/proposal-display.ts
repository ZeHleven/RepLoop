import type { PlanAdjustmentChange } from '../types/plan-adjustment-proposal'


const CHANGE_LABELS: Record<string, string> = {
  sets: '组数',
  reps: '次数',
  rest_seconds: '休息秒数',
  recommended_weight_kg: '建议重量',
  duration_weeks: '计划周数',
  days_per_week: '每周天数'
}

const EMPTY_CHANGE_VALUE = '未提供有效变更'

export function proposalChangeValues (change: PlanAdjustmentChange): {
  before: string
  after: string
} {
  if (change.change_type === 'replace_exercise') {
    return {
      before: change.before.exercise_name,
      after: change.after.exercise_name
    }
  }

  const before = change.before as Record<string, unknown>
  const after = change.after as Record<string, unknown>
  const keys = Array.from(new Set([
    ...Object.keys(before),
    ...Object.keys(after)
  ])).filter(key => before[key] != null || after[key] != null)

  if (keys.length === 0) {
    return {
      before: EMPTY_CHANGE_VALUE,
      after: EMPTY_CHANGE_VALUE
    }
  }

  return {
    before: keys
      .map(key => `${CHANGE_LABELS[key] || key}：${formatChangeValue(key, before[key])}`)
      .join(' · '),
    after: keys
      .map(key => `${CHANGE_LABELS[key] || key}：${formatChangeValue(key, after[key])}`)
      .join(' · ')
  }
}

function formatChangeValue (key: string, value: unknown): string {
  if (key === 'recommended_weight_kg') {
    return typeof value === 'number' ? `${value} kg` : '自重/未指定'
  }
  if (value == null) return '未提供'
  if (key === 'rest_seconds') return `${String(value)} 秒`
  if (key === 'duration_weeks') return `${String(value)} 周`
  if (key === 'days_per_week') return `${String(value)} 天`
  if (key === 'sets') return `${String(value)} 组`
  return String(value)
}
