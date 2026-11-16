import type { PlanExerciseSnapshotV2 } from '../types/plan-management-proposal'

// Compare editable values, not API metadata/object identities. Invalid numbers
// must remain distinguishable from an intentionally unspecified (null) weight.
export function planDraftSignature (duration: number, days: number[], exercises: PlanExerciseSnapshotV2[]) {
  const numberValue = (value: number | null) => typeof value === 'number' && !Number.isFinite(value) ? String(value) : value
  return JSON.stringify({
    duration, days: [...days].sort((a, b) => a - b),
    exercises: [...exercises].sort((a, b) => a.day_of_week - b.day_of_week || a.order_index - b.order_index).map(item => ({
      key: item.item_key, exercise: item.exercise_id, day: item.day_of_week,
      sets: numberValue(item.sets), reps: item.reps.trim(), rest: numberValue(item.rest_seconds),
      weight: numberValue(item.recommended_weight_kg), order: item.order_index
    }))
  })
}
