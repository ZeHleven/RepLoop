import { ApiRequestError, apiRequest } from '../core/request'
import {
  createProposalDecisionRequestId,
  isPlanAdjustmentProposalBusinessErrorCode,
  markProposalDecisionAttempt,
  projectProposalDecisionRecovery,
  proposalDecisionInputError,
  resolveProposalDecisionJournal
} from '../core/proposal-interaction'
import {
  clearPendingProposalDecision,
  getPendingProposalDecision,
  savePendingProposalDecision
} from '../core/storage'
import type {
  PlanAdjustmentProposalBusinessErrorCode,
  PlanAdjustmentProposalDecisionAction,
  PlanAdjustmentProposalDecisionRequest,
  PlanAdjustmentProposalDecisionResponse,
  PlanAdjustmentProposalReadResponse,
  ProposalDecisionRecovery
} from '../types/plan-adjustment-proposal'


export type ProposalClientErrorCode =
  | PlanAdjustmentProposalBusinessErrorCode
  | 'proposal_client_contract_invalid'
  | 'proposal_decision_pending'

export type ProposalClientFailureKind =
  | 'business_error'
  | 'validation_error'
  | 'uncertain'

export class ProposalClientError extends Error {
  readonly kind: ProposalClientFailureKind
  readonly code: ProposalClientErrorCode | null
  readonly httpStatus: number | null
  readonly recovery: ProposalDecisionRecovery
  readonly originalError: unknown

  constructor (options: {
    message: string
    kind: ProposalClientFailureKind
    code?: ProposalClientErrorCode | null
    httpStatus?: number | null
    recovery: ProposalDecisionRecovery
    originalError?: unknown
  }) {
    super(options.message)
    this.name = 'ProposalClientError'
    this.kind = options.kind
    this.code = options.code || null
    this.httpStatus = options.httpStatus || null
    this.recovery = options.recovery
    this.originalError = options.originalError
  }
}

interface InFlightProposalDecision {
  action: PlanAdjustmentProposalDecisionAction
  expectedVersion: number
  promise: Promise<PlanAdjustmentProposalDecisionResponse>
}

const inFlightDecisions = new Map<string, InFlightProposalDecision>()

function clearDecisionBestEffort (proposalId: string): void {
  try {
    clearPendingProposalDecision(proposalId)
  } catch (_) {
    // A stale local journal remains safe: a later retry reuses the same request id.
  }
}

function validationError (message: string): ProposalClientError {
  return new ProposalClientError({
    message,
    kind: 'validation_error',
    code: 'proposal_client_contract_invalid',
    recovery: projectProposalDecisionRecovery({
      transportResult: 'validation_error'
    })
  })
}

function pendingDecisionError (): ProposalClientError {
  return new ProposalClientError({
    message: '上一次提案决策的结果尚未核实，请先刷新提案状态',
    kind: 'uncertain',
    code: 'proposal_decision_pending',
    recovery: projectProposalDecisionRecovery({ transportResult: 'uncertain' })
  })
}

export function mapProposalClientError (error: unknown): ProposalClientError {
  if (error instanceof ProposalClientError) return error

  if (error instanceof ApiRequestError) {
    if (isPlanAdjustmentProposalBusinessErrorCode(error.code)) {
      return new ProposalClientError({
        message: error.message,
        kind: 'business_error',
        code: error.code,
        httpStatus: error.statusCode,
        recovery: projectProposalDecisionRecovery({
          transportResult: 'business_error',
          errorCode: error.code
        }),
        originalError: error
      })
    }
    if (error.statusCode === 422) {
      return new ProposalClientError({
        message: error.message,
        kind: 'validation_error',
        code: 'proposal_client_contract_invalid',
        httpStatus: error.statusCode,
        recovery: projectProposalDecisionRecovery({
          transportResult: 'validation_error'
        }),
        originalError: error
      })
    }
  }

  const message = error instanceof Error && error.message
    ? error.message
    : '提案操作结果尚未确定'
  return new ProposalClientError({
    message,
    kind: 'uncertain',
    recovery: projectProposalDecisionRecovery({ transportResult: 'uncertain' }),
    originalError: error
  })
}

async function readProposal (
  proposalId: string
): Promise<PlanAdjustmentProposalReadResponse> {
  const inputError = proposalDecisionInputError(proposalId, 1)
  if (inputError) throw validationError(inputError)

  try {
    const proposal = await apiRequest<PlanAdjustmentProposalReadResponse>(
      `/agent/proposals/${encodeURIComponent(proposalId)}`
    )
    if (proposal.status !== 'pending_confirmation') {
      clearDecisionBestEffort(proposalId)
    }
    return proposal
  } catch (error) {
    const mapped = mapProposalClientError(error)
    if (mapped.code === 'proposal_not_found') {
      clearDecisionBestEffort(proposalId)
    }
    throw mapped
  }
}

async function executeProposalDecision (
  proposalId: string,
  action: PlanAdjustmentProposalDecisionAction,
  expectedVersion: number
): Promise<PlanAdjustmentProposalDecisionResponse> {
  const existing = getPendingProposalDecision(proposalId)
  const resolution = resolveProposalDecisionJournal(
    existing,
    proposalId,
    action,
    expectedVersion,
    createProposalDecisionRequestId(),
    Date.now()
  )
  if (resolution.outcome === 'conflict' || !resolution.record) {
    throw pendingDecisionError()
  }

  const attempted = markProposalDecisionAttempt(resolution.record, Date.now())
  savePendingProposalDecision(attempted)
  const body: PlanAdjustmentProposalDecisionRequest = {
    expected_version: attempted.expected_version,
    client_request_id: attempted.client_request_id
  }

  try {
    const response = await apiRequest<PlanAdjustmentProposalDecisionResponse>(
      `/agent/proposals/${encodeURIComponent(proposalId)}/${action}`,
      { method: 'POST', data: body }
    )
    clearDecisionBestEffort(proposalId)
    return response
  } catch (error) {
    const mapped = mapProposalClientError(error)
    if (mapped.recovery.journal === 'clear') {
      clearDecisionBestEffort(proposalId)
    }
    throw mapped
  }
}

function decideProposal (
  proposalId: string,
  action: PlanAdjustmentProposalDecisionAction,
  expectedVersion: number
): Promise<PlanAdjustmentProposalDecisionResponse> {
  const inputError = proposalDecisionInputError(proposalId, expectedVersion)
  if (inputError) return Promise.reject(validationError(inputError))

  const active = inFlightDecisions.get(proposalId)
  if (active) {
    if (active.action === action && active.expectedVersion === expectedVersion) {
      return active.promise
    }
    return Promise.reject(pendingDecisionError())
  }

  const promise = executeProposalDecision(
    proposalId,
    action,
    expectedVersion
  ).finally(() => {
    const current = inFlightDecisions.get(proposalId)
    if (current && current.promise === promise) {
      inFlightDecisions.delete(proposalId)
    }
  })
  inFlightDecisions.set(proposalId, { action, expectedVersion, promise })
  return promise
}

export const proposalsApi = {
  get: readProposal,
  confirm: (proposalId: string, expectedVersion: number) => (
    decideProposal(proposalId, 'confirm', expectedVersion)
  ),
  reject: (proposalId: string, expectedVersion: number) => (
    decideProposal(proposalId, 'reject', expectedVersion)
  ),
  pendingDecision: getPendingProposalDecision,
  clearPendingDecision: clearPendingProposalDecision
}
