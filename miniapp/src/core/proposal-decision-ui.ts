export type ProposalDecisionAction = 'confirm' | 'reject'
export type ProposalUiTone = 'attention' | 'success' | 'neutral' | 'warning'

export interface ProposalConfirmationInput {
  action: ProposalDecisionAction
  server_status: string
  local_expiry_state: string
  detail_reviewed: boolean
  confirmation_required: boolean
  user_choice: 'accept' | 'cancel'
  existing_journal: 'none' | 'reusable'
}

export interface ProposalConfirmationProjection {
  modal: string
  post_attempts: number
  lock: 'acquired' | 'not_acquired'
  next_ui_state: string
  request_id: 'create' | 'reuse' | 'none'
}

export interface ProposalButtonLockInput {
  event: string
  initial_lock: string
  completion: string
}

export interface ProposalButtonLockProjection {
  new_post_attempts: number
  new_get_attempts: number
  ignored_gestures: number
  next_lock: string
  next_ui_state: string
}

export interface ProposalUncertainResultInput {
  action: ProposalDecisionAction
  post_result: 'uncertain'
  get_result: string
}

export interface ProposalUncertainResultProjection {
  automatic_post_attempts: number
  get_attempts: number
  journal: 'keep' | 'clear'
  ui_state: string
  label: string
  manual_retry_visible: boolean
  may_claim_applied: boolean
}

export interface ProposalManualRetryInput {
  journal: string
  authoritative_status: string
  user_choice: 'accept' | 'cancel'
}

export interface ProposalManualRetryProjection {
  modal_required: boolean
  post_attempts: number
  request_id: 'reuse' | 'none'
  new_request_id_count: number
  next_ui_state: string
}

export interface ProposalErrorPresentationInput {
  error_code: string
  recovery: string
  get_status: string | null
}

export interface ProposalErrorPresentationProjection {
  get_attempts: number
  journal: 'keep' | 'clear'
  ui_state: string
  label: string
  tone: ProposalUiTone
  actions: string[]
  automatic_post_attempts: number
  may_claim_applied: boolean
}

export function projectProposalConfirmation (
  input: ProposalConfirmationInput
): ProposalConfirmationProjection {
  if (
    input.server_status !== 'pending_confirmation' ||
    input.local_expiry_state !== 'before'
  ) {
    return {
      modal: 'not_shown',
      post_attempts: 0,
      lock: 'not_acquired',
      next_ui_state: 'refresh_required',
      request_id: 'none'
    }
  }
  if (!input.detail_reviewed) {
    return {
      modal: 'blocked_until_full_detail',
      post_attempts: 0,
      lock: 'not_acquired',
      next_ui_state: 'review_required',
      request_id: 'none'
    }
  }
  const modal = input.confirmation_required
    ? input.action === 'confirm'
      ? 'confirm_plan_adjustment'
      : 'confirm_rejection'
    : 'not_required'
  if (input.user_choice === 'cancel') {
    return {
      modal,
      post_attempts: 0,
      lock: 'not_acquired',
      next_ui_state: 'pending_confirmation',
      request_id: 'none'
    }
  }
  return {
    modal,
    post_attempts: 1,
    lock: 'acquired',
    next_ui_state: 'submitting',
    request_id: input.existing_journal === 'reusable' ? 'reuse' : 'create'
  }
}

export function projectProposalButtonLock (
  input: ProposalButtonLockInput
): ProposalButtonLockProjection {
  if (input.completion === 'none') {
    return {
      new_post_attempts: 0,
      new_get_attempts: 0,
      ignored_gestures: 1,
      next_lock: input.initial_lock,
      next_ui_state: 'submitting'
    }
  }
  if (input.completion === 'applied') {
    return {
      new_post_attempts: 0,
      new_get_attempts: 0,
      ignored_gestures: 0,
      next_lock: 'unlocked',
      next_ui_state: 'applied'
    }
  }
  if (input.completion === 'uncertain') {
    return {
      new_post_attempts: 0,
      new_get_attempts: 1,
      ignored_gestures: 0,
      next_lock: 'verification_in_flight',
      next_ui_state: 'verifying'
    }
  }
  if (input.completion === 'feature_disabled') {
    return {
      new_post_attempts: 0,
      new_get_attempts: 0,
      ignored_gestures: 0,
      next_lock: 'unlocked',
      next_ui_state: 'manual_retry_available'
    }
  }
  throw new Error(`unknown lock completion: ${input.completion}`)
}

export function projectProposalUncertainResult (
  input: ProposalUncertainResultInput
): ProposalUncertainResultProjection {
  if (input.post_result !== 'uncertain') {
    throw new Error('uncertain recovery requires an uncertain POST result')
  }
  const shared = {
    automatic_post_attempts: 0,
    get_attempts: 1,
    may_claim_applied: false
  }
  const terminal: Record<string, [string, string, boolean]> = {
    applied: ['applied', '已应用', true],
    rejected: ['rejected', '已拒绝', false],
    expired: ['expired', '已过期', false],
    stale: ['stale', '计划情况已变化', false],
    failed: ['failed', '调整未完成', false]
  }
  if (terminal[input.get_result]) {
    const projected = terminal[input.get_result]
    return {
      ...shared,
      journal: 'clear',
      ui_state: projected[0],
      label: projected[1],
      manual_retry_visible: false,
      may_claim_applied: projected[2]
    }
  }
  if (input.get_result === 'pending_confirmation') {
    return {
      ...shared,
      journal: 'keep',
      ui_state: 'retry_confirmation_required',
      label: '上次操作结果未确认',
      manual_retry_visible: true
    }
  }
  if (input.get_result === 'transport_error') {
    return {
      ...shared,
      journal: 'keep',
      ui_state: 'verifying',
      label: '正在核实提案状态',
      manual_retry_visible: false
    }
  }
  throw new Error(`unknown uncertain GET result: ${input.get_result}`)
}

export function projectProposalManualRetry (
  input: ProposalManualRetryInput
): ProposalManualRetryProjection {
  if (input.authoritative_status !== 'pending_confirmation') {
    return {
      modal_required: false,
      post_attempts: 0,
      request_id: 'none',
      new_request_id_count: 0,
      next_ui_state: input.authoritative_status
    }
  }
  if (input.journal === 'none') {
    return {
      modal_required: false,
      post_attempts: 0,
      request_id: 'none',
      new_request_id_count: 0,
      next_ui_state: 'fresh_decision_required'
    }
  }
  if (input.user_choice === 'cancel') {
    return {
      modal_required: true,
      post_attempts: 0,
      request_id: 'none',
      new_request_id_count: 0,
      next_ui_state: 'retry_confirmation_required'
    }
  }
  return {
    modal_required: true,
    post_attempts: 1,
    request_id: 'reuse',
    new_request_id_count: 0,
    next_ui_state: 'submitting'
  }
}

export function projectProposalErrorPresentation (
  input: ProposalErrorPresentationInput
): ProposalErrorPresentationProjection {
  const shared = {
    automatic_post_attempts: 0,
    may_claim_applied: false
  }
  if (input.recovery === 'render_unavailable') {
    return {
      ...shared,
      get_attempts: 0,
      journal: 'clear',
      ui_state: 'unavailable',
      label: '提案不可用',
      tone: 'neutral',
      actions: []
    }
  }
  if (input.recovery === 'wait_for_manual_retry') {
    return {
      ...shared,
      get_attempts: 0,
      journal: 'keep',
      ui_state: 'manual_retry_available',
      label: '操作暂时不可用',
      tone: 'warning',
      actions: ['manual_retry_same_request']
    }
  }
  if (input.recovery === 'fix_client_contract') {
    return {
      ...shared,
      get_attempts: 0,
      journal: 'clear',
      ui_state: 'client_update_required',
      label: '客户端版本暂不支持此操作',
      tone: 'warning',
      actions: []
    }
  }
  if (input.recovery !== 'query_status' || !input.get_status) {
    throw new Error('query recovery requires an authoritative GET status')
  }
  const terminal: Record<string, [string, ProposalUiTone, boolean]> = {
    applied: ['已应用', 'success', true],
    rejected: ['已拒绝', 'neutral', false],
    expired: ['已过期', 'neutral', false],
    stale: ['计划情况已变化', 'warning', false],
    failed: ['调整未完成', 'warning', false]
  }
  if (terminal[input.get_status]) {
    const projected = terminal[input.get_status]
    return {
      ...shared,
      get_attempts: 1,
      journal: 'clear',
      ui_state: input.get_status,
      label: projected[0],
      tone: projected[1],
      actions: [],
      may_claim_applied: projected[2]
    }
  }
  if (input.get_status !== 'pending_confirmation') {
    throw new Error(`unknown authoritative GET status: ${input.get_status}`)
  }
  const labels: Record<string, string> = {
    proposal_version_conflict: '提案已更新，请重新确认',
    proposal_execution_conflict: '计划正在更新，请重新确认',
    proposal_idempotency_conflict: '操作标识冲突，请重新确认'
  }
  if (!labels[input.error_code]) {
    throw new Error(`unknown pending error presentation: ${input.error_code}`)
  }
  return {
    ...shared,
    get_attempts: 1,
    journal: 'clear',
    ui_state: 'pending_confirmation',
    label: labels[input.error_code],
    tone: 'attention',
    actions: ['start_new_decision']
  }
}
