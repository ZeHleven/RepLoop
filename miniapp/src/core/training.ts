import type { WorkoutPlan, WorkoutSetRecord } from '../types/api'

export function trainingDayState (plan: WorkoutPlan, day: number) {
  return plan.weekly_sessions?.find(item => item.day_of_week === day)
}

export function trainingProgressLabel (plan: WorkoutPlan): string {
  const total = new Set(plan.exercises.map(item => item.day_of_week)).size
  const done = plan.weekly_completed_days || 0
  return total > 0 && done >= total ? '本周计划已完成' : `本周已完成 ${done}/${total} 天`
}

export function actualRestLabel (set: WorkoutSetRecord): string {
  if (set.rest_end_reason === 'workout_ended') return '训练结束，无下一组'
  if (set.actual_rest_seconds == null) return '未记录'
  return `${set.actual_rest_seconds} 秒`
}
