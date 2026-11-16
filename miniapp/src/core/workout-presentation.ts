import type { SessionExercise, WorkoutSession, WorkoutSetRecord } from '../types/api'

export const plannedExerciseSummary = (exercise: SessionExercise): string =>
  `${exercise.target_sets ?? '—'}组 × ${exercise.target_reps?.trim() || '—'}次`

export function inlineSetRest (set: WorkoutSetRecord): string {
  if (set.rest_end_reason === 'workout_ended') return '组间歇：训练结束，无下一组'
  return set.actual_rest_seconds == null ? '组间歇未记录' : `组间歇${set.actual_rest_seconds}秒`
}

export function historyExerciseSummary (session: WorkoutSession) {
  const trained = session.exercises.filter(exercise => exercise.sets_data.length > 0)
  const untrained = session.exercises.length - trained.length
  return {
    shown: trained.slice(0, 2),
    label: `已练 ${trained.length} 项${untrained ? ` · 未训练 ${untrained} 项` : ''}${trained.length > 2 ? ' · 更多动作见详情' : ''}`
  }
}

export function workoutClock (startedAt: string): string {
  if (!startedAt) return ''
  // Server timestamps are UTC; keep the same Beijing-date presentation policy.
  const timestamp = /(?:Z|[+-]\d\d:\d\d)$/i.test(startedAt) ? startedAt : `${startedAt}Z`
  const time = Date.parse(timestamp)
  if (!Number.isFinite(time)) return ''
  const date = new Date(time + 8 * 60 * 60 * 1000)
  return `${String(date.getUTCHours()).padStart(2, '0')}:${String(date.getUTCMinutes()).padStart(2, '0')}`
}

export function proposalStatusLabel (session: WorkoutSession): string {
  return ({ pending_confirmation: '待确认', applied: '已应用', rejected: '已拒绝', expired: '已过期', stale: '已失效', failed: '生成失败', blocked_by_existing: '已有提案', not_needed: '无需调整' } as const)[session.adaptive_adjustment_status] || '状态未知'
}
