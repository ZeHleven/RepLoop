import type {
  PendingPlanAdjustmentProposalDecision,
  PlanAdjustmentProposalBusinessErrorCode,
  PlanAdjustmentProposalDecisionAction,
  PlanAdjustmentProposalStatus,
  ProposalDecisionRecovery
} from '../types/plan-adjustment-proposal'


const BUSINESS_ERROR_CODES: ReadonlySet<string> = new Set([
  'proposal_not_found',
  'proposal_not_pending',
  'proposal_version_conflict',
  'proposal_expired',
  'proposal_feature_disabled',
  'proposal_idempotency_conflict',
  'proposal_base_plan_changed',
  'proposal_health_context_changed',
  'proposal_payload_invalid',
  'proposal_candidate_unavailable',
  'proposal_execution_conflict',
  'proposal_execution_failed'
])

export type ProposalDecisionTransportResult =
  | 'success'
  | 'uncertain'
  | 'business_error'
  | 'validation_error'

export interface ProposalDecisionRecoveryInput {
  transportResult: ProposalDecisionTransportResult
  responseStatus?: 'applied' | 'rejected' | null
  errorCode?: PlanAdjustmentProposalBusinessErrorCode | null
}

export interface ProposalDecisionJournalResolution {
  outcome: 'created' | 'reused' | 'conflict'
  record: PendingPlanAdjustmentProposalDecision | null
}

export type ProposalStatusTone =
  | 'attention'
  | 'success'
  | 'neutral'
  | 'warning'

export type ProposalLocalExpiryState =
  | 'before'
  | 'at_or_after'
  | 'irrelevant'

export interface ProposalStatusPresentation {
  label: string
  tone: ProposalStatusTone
  terminal: boolean
  actions: PlanAdjustmentProposalDecisionAction[]
  detail_required: boolean
  refresh_required: boolean
  may_claim_applied: boolean
}

export function projectProposalStatus (
  serverStatus: PlanAdjustmentProposalStatus | 'missing',
  localExpiryState: ProposalLocalExpiryState
): ProposalStatusPresentation {
  if (serverStatus === 'pending_confirmation') {
    if (localExpiryState === 'at_or_after') {
      return {
        label: '正在核实提案状态',
        tone: 'neutral',
        terminal: false,
        actions: [],
        detail_required: true,
        refresh_required: true,
        may_claim_applied: false
      }
    }
    return {
      label: '待你确认',
      tone: 'attention',
      terminal: false,
      actions: ['confirm', 'reject'],
      detail_required: true,
      refresh_required: false,
      may_claim_applied: false
    }
  }

  const terminal: Record<
    Exclude<PlanAdjustmentProposalStatus, 'pending_confirmation'>,
    Pick<ProposalStatusPresentation, 'label' | 'tone' | 'may_claim_applied'>
  > = {
    applied: {
      label: '已应用',
      tone: 'success',
      may_claim_applied: true
    },
    rejected: {
      label: '已拒绝',
      tone: 'neutral',
      may_claim_applied: false
    },
    expired: {
      label: '已过期',
      tone: 'neutral',
      may_claim_applied: false
    },
    stale: {
      label: '计划情况已变化',
      tone: 'warning',
      may_claim_applied: false
    },
    failed: {
      label: '调整未完成',
      tone: 'warning',
      may_claim_applied: false
    }
  }
  const value = serverStatus === 'missing'
    ? { label: '提案不可用', tone: 'neutral' as const, may_claim_applied: false }
    : terminal[serverStatus]
  return {
    ...value,
    terminal: true,
    actions: [],
    detail_required: serverStatus !== 'missing',
    refresh_required: false
  }
}

export function proposalLocalExpiryState (
  status: PlanAdjustmentProposalStatus,
  expiresAt: string,
  now: number = Date.now()
): ProposalLocalExpiryState {
  if (status !== 'pending_confirmation') return 'irrelevant'
  const expiresAtMilliseconds = Date.parse(expiresAt)
  if (!Number.isFinite(expiresAtMilliseconds)) return 'at_or_after'
  return now < expiresAtMilliseconds ? 'before' : 'at_or_after'
}

export function isPlanAdjustmentProposalBusinessErrorCode (
  value: unknown
): value is PlanAdjustmentProposalBusinessErrorCode {
  return typeof value === 'string' && BUSINESS_ERROR_CODES.has(value)
}

export function projectProposalDecisionRecovery (
  input: ProposalDecisionRecoveryInput
): ProposalDecisionRecovery {
  if (input.transportResult === 'success') {
    if (input.responseStatus !== 'applied' && input.responseStatus !== 'rejected') {
      throw new Error('successful proposal decision requires a terminal status')
    }
    return {
      journal: 'clear',
      next_step: 'render_terminal',
      retry_request_id: 'none',
      automatic_post_count: 0,
      may_claim_terminal: true
    }
  }

  if (input.transportResult === 'uncertain') {
    return {
      journal: 'keep',
      next_step: 'query_status',
      retry_request_id: 'reuse',
      automatic_post_count: 0,
      may_claim_terminal: false
    }
  }

  if (input.transportResult === 'validation_error') {
    return {
      journal: 'clear',
      next_step: 'fix_client_contract',
      retry_request_id: 'none',
      automatic_post_count: 0,
      may_claim_terminal: false
    }
  }

  if (!isPlanAdjustmentProposalBusinessErrorCode(input.errorCode)) {
    throw new Error('proposal business error requires a known error code')
  }
  if (input.errorCode === 'proposal_feature_disabled') {
    return {
      journal: 'keep',
      next_step: 'wait_for_manual_retry',
      retry_request_id: 'reuse',
      automatic_post_count: 0,
      may_claim_terminal: false
    }
  }
  if (input.errorCode === 'proposal_not_found') {
    return {
      journal: 'clear',
      next_step: 'render_unavailable',
      retry_request_id: 'none',
      automatic_post_count: 0,
      may_claim_terminal: false
    }
  }
  return {
    journal: 'clear',
    next_step: 'query_status',
    retry_request_id: 'none',
    automatic_post_count: 0,
    may_claim_terminal: false
  }
}

export function proposalDecisionInputError (
  proposalId: string,
  expectedVersion: number
): string | null {
  if (!proposalId || proposalId.length > 100) {
    return '提案标识不符合客户端契约'
  }
  if (!Number.isInteger(expectedVersion) || expectedVersion < 1) {
    return '提案版本不符合客户端契约'
  }
  return null
}

export function createProposalDecisionRequestId (
  now: number = Date.now(),
  randomValue: number = Math.random()
): string {
  const randomPart = Math.max(0, Math.min(randomValue, 0.999999999999))
    .toString(36)
    .slice(2, 12)
    .padEnd(10, '0')
  return `proposal-${now.toString(36)}-${randomPart}`
}

export function resolveProposalDecisionJournal (
  existing: PendingPlanAdjustmentProposalDecision | null,
  proposalId: string,
  action: PlanAdjustmentProposalDecisionAction,
  expectedVersion: number,
  clientRequestId: string,
  now: number
): ProposalDecisionJournalResolution {
  if (proposalDecisionInputError(proposalId, expectedVersion)) {
    return { outcome: 'conflict', record: null }
  }
  if (existing) {
    const reusable = (
      existing.proposal_id === proposalId &&
      existing.action === action &&
      existing.expected_version === expectedVersion
    )
    return reusable
      ? { outcome: 'reused', record: existing }
      : { outcome: 'conflict', record: existing }
  }
  return {
    outcome: 'created',
    record: {
      schema_version: '1.0.0',
      proposal_id: proposalId,
      action,
      expected_version: expectedVersion,
      client_request_id: clientRequestId,
      created_at: now,
      last_attempt_at: null,
      attempt_count: 0
    }
  }
}

export function markProposalDecisionAttempt (
  record: PendingPlanAdjustmentProposalDecision,
  now: number
): PendingPlanAdjustmentProposalDecision {
  return {
    ...record,
    last_attempt_at: now,
    attempt_count: record.attempt_count + 1
  }
}

export function isPendingProposalDecision (
  value: unknown
): value is PendingPlanAdjustmentProposalDecision {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<PendingPlanAdjustmentProposalDecision>
  return (
    record.schema_version === '1.0.0' &&
    typeof record.proposal_id === 'string' &&
    record.proposal_id.length > 0 &&
    record.proposal_id.length <= 100 &&
    (record.action === 'confirm' || record.action === 'reject') &&
    typeof record.expected_version === 'number' &&
    Number.isInteger(record.expected_version) &&
    record.expected_version >= 1 &&
    typeof record.client_request_id === 'string' &&
    record.client_request_id.length >= 8 &&
    record.client_request_id.length <= 120 &&
    typeof record.created_at === 'number' &&
    Number.isFinite(record.created_at) &&
    (record.last_attempt_at === null || (
      typeof record.last_attempt_at === 'number' &&
      Number.isFinite(record.last_attempt_at)
    )) &&
    typeof record.attempt_count === 'number' &&
    Number.isInteger(record.attempt_count) &&
    record.attempt_count >= 0
  )
}
