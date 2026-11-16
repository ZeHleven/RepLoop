import type {
  PlanAdjustmentProposalAllowedAction,
  PlanAdjustmentProposalStatus
} from './plan-adjustment-proposal'

export interface PlanExerciseCandidateV2 {
  item_key: string
  exercise_id: string
  day_of_week: number
  sets: number
  reps: string
  rest_seconds: number
  recommended_weight_kg: number | null
  order_index: number
}

export interface PlanExerciseSnapshotV2 extends PlanExerciseCandidateV2 {
  exercise_name: string
  category: string
}

export interface PlanSnapshotV2 {
  name: string
  goal: string | null
  duration_weeks: number
  days_per_week: number
  training_days: number[]
  exercises: PlanExerciseSnapshotV2[]
}

export interface PlanCandidateV2 {
  duration_weeks: number
  training_days: number[]
  exercises: PlanExerciseCandidateV2[]
}

export interface PlanExerciseOptionV2 {
  body_parts?: string[]
  search_aliases?: string[]
  counting_note?: string | null
  exercise_id: string
  exercise_name: string
  category: string
  difficulty: string
  equipment: string[]
  safety_notice?: string | null
}

export interface PlanEditContext {
  base_plan: PlanSnapshotV2
  base_plan_fingerprint: string
  health_context_fingerprint: string
  exercise_options: PlanExerciseOptionV2[]
  exercise_notices?: Record<string, string>
  constraints: {
    duration_weeks_min: number
    duration_weeks_max: number
    sets_min: number
    sets_max: number
    rest_seconds_min: number
    rest_seconds_max: number
    recommended_weight_kg_max: number
    total_exercises_max: number
  }
  active_session: boolean
  proposals_enabled: boolean
}

export type PlanChangeTypeV2 =
  | 'update_schedule'
  | 'add_exercise'
  | 'remove_exercise'
  | 'replace_exercise'
  | 'move_exercise'
  | 'adjust_exercise_target'

export interface PlanChangeV2 {
  change_type: PlanChangeTypeV2
  stable_display_key: string
  before: Record<string, unknown>
  after: Record<string, unknown>
  reason: string
  safety_priority: boolean
}

export interface PlanProposalTargetV2 {
  resource_type: 'workout_plan'
  base_plan_id: string
  base_plan_fingerprint: string
  health_context_fingerprint: string
}

export interface PlanAdjustmentPayloadV2 {
  schema_version: '2.0.0'
  proposal_type: 'plan_adjustment_v2'
  target: PlanProposalTargetV2
  before: PlanSnapshotV2
  after: PlanSnapshotV2
  changes: PlanChangeV2[]
  rationale: string[]
  safety_notes: string[]
}

export interface PlanDeletionPayloadV1 {
  schema_version: '1.0.0'
  proposal_type: 'plan_deletion_v1'
  target: PlanProposalTargetV2
  before: PlanSnapshotV2
  consequences: string[]
  safety_notes: string[]
}

export type ManualPlanProposalPayload =
  | PlanAdjustmentPayloadV2
  | PlanDeletionPayloadV1

export interface PlanProposalReference {
  id: string
  proposal_type: 'plan_adjustment_v2' | 'plan_deletion_v1'
  status: PlanAdjustmentProposalStatus
  version: number
  expires_at: string
  payload_fingerprint: string
}

export interface ManualPlanProposalReadResponse {
  id: string
  proposal_type: 'plan_adjustment_v2' | 'plan_deletion_v1'
  origin: 'manual_editor' | 'agent_chat' | 'workout_completion'
  status: PlanAdjustmentProposalStatus
  version: number
  payload_fingerprint: string
  payload: ManualPlanProposalPayload
  expires_at: string
  created_at: string
  updated_at: string
  allowed_actions: PlanAdjustmentProposalAllowedAction[]
  result: Record<string, unknown> | null
}

export interface GenericProposalDecisionResponse {
  id: string
  proposal_type: string
  status: 'applied' | 'rejected'
  version: number
  applied: boolean
  payload_fingerprint: string
  result_plan_id: string | null
  result_plan_fingerprint: string | null
  result_data: Record<string, unknown> | null
  decided_at: string
}

export interface GenericProposalReadResponse {
  id: string
  proposal_type: string
  origin: 'manual_editor' | 'agent_chat' | 'workout_completion'
  status: PlanAdjustmentProposalStatus
  version: number
  payload_fingerprint: string
  payload: Record<string, unknown>
  expires_at: string
  created_at: string
  updated_at: string
  allowed_actions: PlanAdjustmentProposalAllowedAction[]
  result: Record<string, unknown> | null
}
