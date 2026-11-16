import Taro from '@tarojs/taro'

import { ApiRequestError, apiRequest } from '../core/request'
import {
  createProposalDecisionRequestId,
  markProposalDecisionAttempt,
  resolveProposalDecisionJournal
} from '../core/proposal-interaction'
import {
  clearPendingProposalDecision,
  getPendingProposalDecision,
  savePendingProposalDecision
} from '../core/storage'
import type {
  GenericProposalDecisionResponse,
  GenericProposalReadResponse,
  ManualPlanProposalReadResponse,
  PlanCandidateV2,
  PlanEditContext,
  PlanProposalReference
} from '../types/plan-management-proposal'

const CREATION_JOURNAL_KEY = 'fitness_plan_proposal_creation_journal_v1'

interface CreationJournal {
  plan_id: string
  action: 'adjust' | 'delete'
  signature: string
  client_request_id: string
  created_at: number
}

function creationJournal (
  planId: string,
  action: CreationJournal['action'],
  signature: string
): CreationJournal {
  const existing = Taro.getStorageSync<CreationJournal>(CREATION_JOURNAL_KEY)
  if (
    existing &&
    existing.plan_id === planId &&
    existing.action === action &&
    existing.signature === signature &&
    existing.client_request_id
  ) return existing
  const next: CreationJournal = {
    plan_id: planId,
    action,
    signature,
    client_request_id: createProposalDecisionRequestId(),
    created_at: Date.now()
  }
  Taro.setStorageSync(CREATION_JOURNAL_KEY, next)
  return next
}

function clearCreationJournal (): void {
  Taro.removeStorageSync(CREATION_JOURNAL_KEY)
}

async function createAdjustment (
  planId: string,
  expectedFingerprint: string,
  candidate: PlanCandidateV2
): Promise<PlanProposalReference> {
  const signature = JSON.stringify({ expectedFingerprint, candidate })
  const journal = creationJournal(planId, 'adjust', signature)
  const response = await apiRequest<PlanProposalReference>(
    `/workouts/plans/${encodeURIComponent(planId)}/adjustment-proposals`,
    {
      method: 'POST',
      data: {
        client_request_id: journal.client_request_id,
        expected_base_fingerprint: expectedFingerprint,
        candidate
      }
    }
  )
  clearCreationJournal()
  return response
}

async function createDeletion (
  planId: string,
  expectedFingerprint: string
): Promise<PlanProposalReference> {
  const signature = expectedFingerprint
  const journal = creationJournal(planId, 'delete', signature)
  const response = await apiRequest<PlanProposalReference>(
    `/workouts/plans/${encodeURIComponent(planId)}/deletion-proposals`,
    {
      method: 'POST',
      data: {
        client_request_id: journal.client_request_id,
        expected_base_fingerprint: expectedFingerprint
      }
    }
  )
  clearCreationJournal()
  return response
}

const inFlight = new Map<string, Promise<GenericProposalDecisionResponse>>()

async function decide (
  proposalId: string,
  action: 'confirm' | 'reject',
  expectedVersion: number
): Promise<GenericProposalDecisionResponse> {
  const active = inFlight.get(proposalId)
  if (active) return active
  const existing = getPendingProposalDecision(proposalId)
  const resolution = resolveProposalDecisionJournal(
    existing,
    proposalId,
    action,
    expectedVersion,
    createProposalDecisionRequestId(),
    Date.now()
  )
  if (!resolution.record || resolution.outcome === 'conflict') {
    throw new Error('上一次提案决策尚未核实，请先刷新状态')
  }
  const attempted = markProposalDecisionAttempt(resolution.record, Date.now())
  savePendingProposalDecision(attempted)
  const promise = apiRequest<GenericProposalDecisionResponse>(
    `/proposals/${encodeURIComponent(proposalId)}/${action}`,
    {
      method: 'POST',
      data: {
        expected_version: expectedVersion,
        client_request_id: attempted.client_request_id
      }
    }
  ).then(response => {
    clearPendingProposalDecision(proposalId)
    return response
  }).catch(error => {
    if (error instanceof ApiRequestError && error.statusCode < 500) {
      clearPendingProposalDecision(proposalId)
    }
    throw error
  }).finally(() => {
    inFlight.delete(proposalId)
  })
  inFlight.set(proposalId, promise)
  return promise
}

export const planManagementApi = {
  editContext: (planId: string) => apiRequest<PlanEditContext>(
    `/workouts/plans/${encodeURIComponent(planId)}/edit-context`
  ),
  createAdjustment,
  createDeletion,
  proposal: (proposalId: string) => apiRequest<GenericProposalReadResponse>(
    `/proposals/${encodeURIComponent(proposalId)}`
  ),
  manualProposal: (proposalId: string) => apiRequest<ManualPlanProposalReadResponse>(
    `/proposals/${encodeURIComponent(proposalId)}`
  ),
  confirm: (proposalId: string, version: number) => decide(proposalId, 'confirm', version),
  reject: (proposalId: string, version: number) => decide(proposalId, 'reject', version),
  pendingDecision: getPendingProposalDecision
}
