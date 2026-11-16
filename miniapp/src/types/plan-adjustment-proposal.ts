export type PlanAdjustmentProposalStatus =
  | 'pending_confirmation'
  | 'applied'
  | 'rejected'
  | 'expired'
  | 'stale'
  | 'failed'

export type PlanAdjustmentProposalDecisionAction = 'confirm' | 'reject'
export type PlanAdjustmentProposalAllowedAction =
  PlanAdjustmentProposalDecisionAction

export type PlanAdjustmentProposalBusinessErrorCode =
  | 'proposal_not_found'
  | 'proposal_not_pending'
  | 'proposal_version_conflict'
  | 'proposal_expired'
  | 'proposal_feature_disabled'
  | 'proposal_idempotency_conflict'
  | 'proposal_base_plan_changed'
  | 'proposal_health_context_changed'
  | 'proposal_payload_invalid'
  | 'proposal_candidate_unavailable'
  | 'proposal_execution_conflict'
  | 'proposal_execution_failed'

export interface PlanAdjustmentProposalReference {
  id: string
  proposal_type:
    | 'plan_adjustment_v1'
    | 'plan_creation_v1'
    | 'plan_adjustment_v2'
    | 'plan_deletion_v1'
    | 'profile_update_v1'
    | 'weight_log_create_v1'
    | 'meal_log_create_v1'
    | 'daily_meal_log_create_v1'
    | 'meal_log_delete_v1'
  status: PlanAdjustmentProposalStatus | 'missing'
  version: number
  expires_at: string
  payload_fingerprint: string
}

export interface PlanAdjustmentProposalTarget {
  resource_type: 'workout_plan'
  base_plan_id: string
  base_plan_fingerprint: string
}

export interface PlanAdjustmentExerciseSnapshot {
  slot_key: string
  exercise_id: string
  exercise_name: string
  day_of_week: number
  sets: number
  reps: string
  rest_seconds: number
  recommended_weight_kg: number | null
  order_index: number
}

export interface PlanAdjustmentPlanSnapshot {
  name: string
  goal: string | null
  duration_weeks: number
  days_per_week: number
  exercises: PlanAdjustmentExerciseSnapshot[]
}

export interface PlanAdjustmentExerciseTargetValues {
  sets?: number | null
  reps?: string | null
  rest_seconds?: number | null
  recommended_weight_kg?: number | null
}

export interface PlanAdjustmentExerciseIdentity {
  exercise_id: string
  exercise_name: string
}

export interface PlanAdjustmentScheduleValues {
  duration_weeks?: number | null
  days_per_week?: number | null
}

interface PlanAdjustmentChangeBase {
  stable_display_key: string
  reason: string
  safety_priority: boolean
}

export interface PlanAdjustmentExerciseTargetChange
  extends PlanAdjustmentChangeBase {
  change_type: 'adjust_exercise_target'
  before: PlanAdjustmentExerciseTargetValues
  after: PlanAdjustmentExerciseTargetValues
}

export interface PlanAdjustmentExerciseReplacementChange
  extends PlanAdjustmentChangeBase {
  change_type: 'replace_exercise'
  before: PlanAdjustmentExerciseIdentity
  after: PlanAdjustmentExerciseIdentity
}

export interface PlanAdjustmentScheduleChange
  extends PlanAdjustmentChangeBase {
  change_type: 'update_plan_schedule'
  before: PlanAdjustmentScheduleValues
  after: PlanAdjustmentScheduleValues
}

export type PlanAdjustmentChange =
  | PlanAdjustmentExerciseTargetChange
  | PlanAdjustmentExerciseReplacementChange
  | PlanAdjustmentScheduleChange

export interface PlanAdjustmentProposalEvidence {
  tool_id: string
  result_fingerprint: string
  observed_at: string
}

export interface PlanAdjustmentProposalPayload {
  schema_version: '1.0.0'
  proposal_type: 'plan_adjustment_v1'
  target: PlanAdjustmentProposalTarget
  before: PlanAdjustmentPlanSnapshot
  after: PlanAdjustmentPlanSnapshot
  changes: PlanAdjustmentChange[]
  evidence: PlanAdjustmentProposalEvidence[]
  rationale: string[]
  safety_notes: string[]
}

export interface PlanAdjustmentProposalAppliedResult {
  plan_id: string
  plan_fingerprint: string
  applied_at: string
}

export interface PlanAdjustmentProposalReadResponse {
  id: string
  proposal_type: 'plan_adjustment_v1'
  status: PlanAdjustmentProposalStatus
  version: number
  payload_fingerprint: string
  payload: PlanAdjustmentProposalPayload
  expires_at: string
  created_at: string
  updated_at: string
  allowed_actions: PlanAdjustmentProposalAllowedAction[]
  result: PlanAdjustmentProposalAppliedResult | null
}

export interface PlanAdjustmentProposalDecisionRequest {
  expected_version: number
  client_request_id: string
}

export interface PlanAdjustmentProposalDecisionResponse {
  id: string
  proposal_type: 'plan_adjustment_v1'
  status: 'applied' | 'rejected'
  version: number
  applied: boolean
  payload_fingerprint: string
  result_plan_id: string | null
  result_plan_fingerprint: string | null
  decided_at: string
}

export interface PlanAdjustmentProposalBusinessErrorPayload {
  code: PlanAdjustmentProposalBusinessErrorCode
  message: string
}

export interface PendingPlanAdjustmentProposalDecision {
  schema_version: '1.0.0'
  proposal_id: string
  action: PlanAdjustmentProposalDecisionAction
  expected_version: number
  client_request_id: string
  created_at: number
  last_attempt_at: number | null
  attempt_count: number
}

export type ProposalDecisionRecoveryNextStep =
  | 'render_terminal'
  | 'query_status'
  | 'wait_for_manual_retry'
  | 'render_unavailable'
  | 'fix_client_contract'

export interface ProposalDecisionRecovery {
  journal: 'keep' | 'clear'
  next_step: ProposalDecisionRecoveryNextStep
  retry_request_id: 'reuse' | 'none'
  automatic_post_count: 0
  may_claim_terminal: boolean
}
