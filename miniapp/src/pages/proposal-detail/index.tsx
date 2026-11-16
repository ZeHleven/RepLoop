import { useRef, useState } from 'react'
import { Button, Text, View } from '@tarojs/components'
import Taro, { useDidShow, useLoad } from '@tarojs/taro'

import {
  projectProposalConfirmation,
  projectProposalErrorPresentation,
  projectProposalManualRetry,
  projectProposalUncertainResult
} from '../../core/proposal-decision-ui'
import type { ProposalUiTone } from '../../core/proposal-decision-ui'
import {
  proposalConfirmationOptions,
  requestProposalConfirmation
} from '../../core/proposal-confirmation'
import type {
  NativeProposalConfirmationOptions
} from '../../core/proposal-confirmation'
import {
  projectProposalStatus,
  proposalLocalExpiryState
} from '../../core/proposal-interaction'
import type { ProposalStatusPresentation } from '../../core/proposal-interaction'
import { proposalChangeValues } from '../../core/proposal-display'
import { errorMessage } from '../../core/request'
import {
  mapProposalClientError,
  ProposalClientError,
  proposalsApi
} from '../../services/proposals'
import type {
  PlanAdjustmentChange,
  PlanAdjustmentExerciseSnapshot,
  PlanAdjustmentPlanSnapshot,
  PlanAdjustmentProposalDecisionAction,
  PlanAdjustmentProposalDecisionResponse,
  PlanAdjustmentProposalReadResponse
} from '../../types/plan-adjustment-proposal'
import './index.scss'


const weekday = (day: number) => (
  ['一', '二', '三', '四', '五', '六', '日'][day - 1] || String(day)
)

type DecisionUiMode =
  | 'idle'
  | 'modal'
  | 'inline_confirmation_required'
  | 'submitting'
  | 'verifying'
  | 'verification_required'
  | 'retry_confirmation_required'
  | 'manual_retry_available'
  | 'client_update_required'

interface DecisionNotice {
  label: string
  tone: ProposalUiTone
}

interface WechatModalRuntime {
  showModal?: (options: NativeProposalConfirmationOptions) => void
}

function nativeWechatModalAdapter () {
  const runtime = (
    globalThis as typeof globalThis & { wx?: WechatModalRuntime }
  ).wx
  if (!runtime || typeof runtime.showModal !== 'function') return undefined
  const showModal = runtime.showModal
  return (options: NativeProposalConfirmationOptions) => {
    showModal.call(runtime, options)
  }
}

export default function ProposalDetailPage () {
  const [proposalId, setProposalId] = useState('')
  const [proposal, setProposal] = useState<PlanAdjustmentProposalReadResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [missing, setMissing] = useState(false)
  const [detailReviewed, setDetailReviewed] = useState(false)
  const [decisionMode, setDecisionMode] = useState<DecisionUiMode>('idle')
  const [decisionAction, setDecisionAction] = useState<PlanAdjustmentProposalDecisionAction | null>(null)
  const [decisionNotice, setDecisionNotice] = useState<DecisionNotice | null>(null)
  const interactionLock = useRef(false)
  const inlineConfirmationRetry = useRef(false)

  const resetDecisionUi = () => {
    setDecisionMode('idle')
    setDecisionAction(null)
    setDecisionNotice(null)
    inlineConfirmationRetry.current = false
  }

  const applyReadProposal = (
    value: PlanAdjustmentProposalReadResponse,
    context?: {
      type: 'uncertain'
      action: PlanAdjustmentProposalDecisionAction
    } | {
      type: 'business_error'
      error: ProposalClientError
    }
  ) => {
    if (!proposal || proposal.version !== value.version) setDetailReviewed(false)
    setProposal(value)
    setMissing(false)

    if (context && context.type === 'uncertain') {
      const projected = projectProposalUncertainResult({
        action: context.action,
        post_result: 'uncertain',
        get_result: value.status
      })
      setDecisionNotice({
        label: projected.label,
        tone: value.status === 'applied'
          ? 'success'
          : value.status === 'stale' || value.status === 'failed'
            ? 'warning'
            : 'neutral'
      })
      if (projected.manual_retry_visible) {
        setDecisionMode('retry_confirmation_required')
        setDecisionAction(context.action)
      } else {
        setDecisionMode('idle')
        setDecisionAction(null)
      }
      return
    }

    if (context && context.type === 'business_error') {
      let projected
      try {
        projected = projectProposalErrorPresentation({
          error_code: context.error.code || 'proposal_client_contract_invalid',
          recovery: context.error.recovery.next_step,
          get_status: value.status
        })
      } catch (_) {
        projected = {
          label: value.status === 'pending_confirmation'
            ? '提案状态已更新，请重新确认'
            : projectProposalStatus(value.status, 'irrelevant').label,
          tone: value.status === 'pending_confirmation' ? 'attention' : 'warning'
        }
      }
      setDecisionNotice({
        label: projected.label,
        tone: projected.tone
      })
      setDecisionMode('idle')
      setDecisionAction(null)
      return
    }

    const pending = value.status === 'pending_confirmation'
      ? proposalsApi.pendingDecision(value.id)
      : null
    if (pending) {
      setDecisionMode('retry_confirmation_required')
      setDecisionAction(pending.action)
      setDecisionNotice({
        label: '上次操作结果未确认',
        tone: 'warning'
      })
      return
    }
    resetDecisionUi()
  }

  const loadProposal = async (id: string, refresh = false) => {
    if (interactionLock.current) return
    if (refresh) setRefreshing(true)
    else setLoading(true)
    setError('')
    try {
      const value = await proposalsApi.get(id)
      applyReadProposal(value)
    } catch (requestError) {
      const candidate = requestError as { code?: unknown }
      if (candidate && candidate.code === 'proposal_not_found') {
        setMissing(true)
        setProposal(null)
      }
      setError(errorMessage(requestError, '暂时无法读取调整提案'))
      if (proposalsApi.pendingDecision(id)) {
        setDecisionMode('verification_required')
        setDecisionNotice({
          label: '正在核实提案状态',
          tone: 'warning'
        })
      }
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useLoad(options => {
    const id = typeof options.id === 'string' ? options.id.trim() : ''
    setProposalId(id)
    if (!id) {
      setError('缺少提案标识，请从 Agent 对话重新打开')
      setLoading(false)
      return
    }
    void loadProposal(id)
  })

  useDidShow(() => {
    if (proposalId && !loading && !interactionLock.current) {
      void loadProposal(proposalId, true)
    }
  })

  const applyDecisionResponse = (
    response: PlanAdjustmentProposalDecisionResponse
  ) => {
    setProposal(current => {
      if (!current) return current
      return {
        ...current,
        status: response.status,
        version: response.version,
        updated_at: response.decided_at,
        allowed_actions: [],
        result: response.status === 'applied' &&
          response.result_plan_id &&
          response.result_plan_fingerprint
          ? {
              plan_id: response.result_plan_id,
              plan_fingerprint: response.result_plan_fingerprint,
              applied_at: response.decided_at
            }
          : null
      }
    })
    setDetailReviewed(false)
    setDecisionMode('idle')
    setDecisionAction(null)
    inlineConfirmationRetry.current = false
    setDecisionNotice({
      label: response.status === 'applied' ? '已应用' : '已拒绝',
      tone: response.status === 'applied' ? 'success' : 'neutral'
    })
  }

  const verifyDecision = async (
    action: PlanAdjustmentProposalDecisionAction,
    decisionError: ProposalClientError
  ) => {
    setDecisionMode('verifying')
    setDecisionNotice({
      label: '正在核实提案状态',
      tone: 'neutral'
    })
    try {
      const value = await proposalsApi.get(proposalId)
      applyReadProposal(
        value,
        decisionError.kind === 'uncertain'
          ? { type: 'uncertain', action }
          : { type: 'business_error', error: decisionError }
      )
      setError('')
    } catch (readError) {
      const mappedReadError = mapProposalClientError(readError)
      if (mappedReadError.code === 'proposal_not_found') {
        setProposal(null)
        setMissing(true)
        setDecisionMode('idle')
        setDecisionAction(null)
        setDecisionNotice(null)
        setError(mappedReadError.message)
        return
      }
      setDecisionMode('verification_required')
      setDecisionAction(action)
      setDecisionNotice({
        label: '正在核实提案状态',
        tone: 'warning'
      })
      setError('暂时无法核实操作结果，请手动刷新状态')
    }
  }

  const handleDecisionError = async (
    action: PlanAdjustmentProposalDecisionAction,
    requestError: unknown
  ) => {
    const mapped = requestError instanceof ProposalClientError
      ? requestError
      : mapProposalClientError(requestError)
    const nextStep = mapped.recovery.next_step
    if (nextStep === 'query_status') {
      await verifyDecision(action, mapped)
      return
    }
    if (nextStep === 'render_unavailable') {
      setProposal(null)
      setMissing(true)
      setDecisionMode('idle')
      setDecisionAction(null)
      setDecisionNotice(null)
      setError(mapped.message)
      return
    }
    if (nextStep === 'wait_for_manual_retry') {
      const projected = projectProposalErrorPresentation({
        error_code: mapped.code || 'proposal_feature_disabled',
        recovery: nextStep,
        get_status: null
      })
      setDecisionMode('manual_retry_available')
      setDecisionAction(action)
      setDecisionNotice({ label: projected.label, tone: projected.tone })
      setError(mapped.message)
      return
    }
    setDecisionMode('client_update_required')
    setDecisionAction(null)
    setDecisionNotice({
      label: '客户端版本暂不支持此操作',
      tone: 'warning'
    })
    setError(mapped.message)
  }

  const decide = async (
    action: PlanAdjustmentProposalDecisionAction,
    retry = false,
    confirmationGranted = false
  ) => {
    if (!proposal || interactionLock.current) return
    const localExpiryState = proposalLocalExpiryState(
      proposal.status,
      proposal.expires_at
    )
    if (!detailReviewed) {
      setDecisionNotice({
        label: '请先阅读完整对比并勾选阅读确认',
        tone: 'warning'
      })
      return
    }
    if (
      proposal.status !== 'pending_confirmation' ||
      localExpiryState !== 'before'
    ) {
      setDecisionNotice({
        label: '需要先刷新服务端状态',
        tone: 'warning'
      })
      await loadProposal(proposal.id, true)
      return
    }

    const pending = proposalsApi.pendingDecision(proposal.id)
    if (retry) {
      if (!pending || pending.action !== action) {
        setDecisionNotice({
          label: '需要先刷新提案状态',
          tone: 'warning'
        })
        return
      }
      const retryProjection = projectProposalManualRetry({
        journal: `reusable_${pending.action}`,
        authoritative_status: proposal.status,
        user_choice: 'accept'
      })
      if (!retryProjection.modal_required) {
        setDecisionNotice({
          label: '需要先刷新提案状态',
          tone: 'warning'
        })
        return
      }
    }

    const confirmationRequired = action === 'reject' || retry
    interactionLock.current = true
    setDecisionMode(confirmationRequired ? 'modal' : 'submitting')
    setDecisionAction(action)
    let userChoice: 'accept' | 'cancel' = 'accept'
    if (confirmationRequired && !confirmationGranted) {
      const confirmation = await requestProposalConfirmation(
        proposalConfirmationOptions(action, proposal.payload.changes.length),
        {
          native: nativeWechatModalAdapter(),
          taro: options => Taro.showModal(options)
        }
      )
      if (confirmation.diagnostics.length > 0) {
        console.warn('[fitness-agent] proposal_confirmation_fallback', {
          selected_channel: confirmation.channel,
          failures: confirmation.diagnostics
        })
      }
      if (confirmation.choice === 'inline_required') {
        interactionLock.current = false
        inlineConfirmationRetry.current = retry
        setDecisionMode('inline_confirmation_required')
        setDecisionNotice({
          label: '系统弹窗不可用，请在本页完成二次确认',
          tone: 'attention'
        })
        return
      }
      userChoice = confirmation.choice
    }
    const projection = projectProposalConfirmation({
      action,
      server_status: proposal.status,
      local_expiry_state: localExpiryState,
      detail_reviewed: detailReviewed,
      confirmation_required: confirmationRequired,
      user_choice: userChoice,
      existing_journal: pending ? 'reusable' : 'none'
    })
    if (projection.post_attempts === 0) {
      interactionLock.current = false
      inlineConfirmationRetry.current = false
      setDecisionMode(retry ? 'retry_confirmation_required' : 'idle')
      if (!retry) setDecisionAction(null)
      return
    }

    setDecisionMode('submitting')
    inlineConfirmationRetry.current = false
    setDecisionNotice({
      label: action === 'confirm' ? '正在应用调整…' : '正在拒绝提案…',
      tone: 'neutral'
    })
    setError('')
    try {
      const response = await proposalsApi[action](proposal.id, proposal.version)
      applyDecisionResponse(response)
    } catch (requestError) {
      await handleDecisionError(action, requestError)
    } finally {
      interactionLock.current = false
    }
  }

  const cancelInlineConfirmation = () => {
    const retry = inlineConfirmationRetry.current
    inlineConfirmationRetry.current = false
    setDecisionMode(retry ? 'retry_confirmation_required' : 'idle')
    if (!retry) setDecisionAction(null)
    setDecisionNotice(retry
      ? { label: '上次操作结果未确认', tone: 'warning' }
      : null)
  }

  if (loading) {
    return <View className='loading-state'>正在读取调整提案…</View>
  }

  const presentation = proposal
    ? projectProposalStatus(
        proposal.status,
        proposalLocalExpiryState(proposal.status, proposal.expires_at)
      )
    : projectProposalStatus('missing', 'irrelevant')

  return (
    <View className='page proposal-detail-page'>
      <View className='proposal-hero'>
        <View className={`proposal-status ${presentation.tone}`}>
          {presentation.label}
        </View>
        <Text className='proposal-kicker'>训练计划调整提案</Text>
        <Text className='proposal-title'>先看清全部变化，再决定是否采用</Text>
        <Text className='proposal-readonly-note'>
          确认前只会读取和预览；只有你二次确认后，系统才会尝试修改训练计划。
        </Text>
      </View>

      {error && <View className='error-banner'>{error}</View>}
      {!proposal && (
        <View className='card proposal-unavailable'>
          <Text className='section-title'>
            {missing ? '这份提案已不可用' : '尚未读取到提案'}
          </Text>
          <Text className='section-copy'>
            请返回 Agent 对话核对提案，或手动刷新当前页面。
          </Text>
          {proposalId && (
            <Button
              className='secondary-button refresh-button'
              disabled={refreshing}
              onClick={() => loadProposal(proposalId, true)}
            >
              {refreshing ? '正在刷新…' : '刷新状态'}
            </Button>
          )}
        </View>
      )}

      {proposal && (
        <ProposalContent
          proposal={proposal}
          presentation={presentation}
          refreshing={refreshing}
          detailReviewed={detailReviewed}
          decisionMode={decisionMode}
          decisionAction={decisionAction}
          decisionNotice={decisionNotice}
          onReviewChange={() => setDetailReviewed(current => !current)}
          onDecision={(action, retry) => decide(action, retry)}
          onConfirmInline={() => {
            if (decisionAction) {
              void decide(
                decisionAction,
                inlineConfirmationRetry.current,
                true
              )
            }
          }}
          onCancelInline={cancelInlineConfirmation}
          onRefresh={() => loadProposal(proposal.id, true)}
        />
      )}
    </View>
  )
}

function ProposalContent ({
  proposal,
  presentation,
  refreshing,
  detailReviewed,
  decisionMode,
  decisionAction,
  decisionNotice,
  onReviewChange,
  onDecision,
  onConfirmInline,
  onCancelInline,
  onRefresh
}: {
  proposal: PlanAdjustmentProposalReadResponse
  presentation: ProposalStatusPresentation
  refreshing: boolean
  detailReviewed: boolean
  decisionMode: DecisionUiMode
  decisionAction: PlanAdjustmentProposalDecisionAction | null
  decisionNotice: DecisionNotice | null
  onReviewChange: () => void
  onDecision: (
    action: PlanAdjustmentProposalDecisionAction,
    retry?: boolean
  ) => void
  onConfirmInline: () => void
  onCancelInline: () => void
  onRefresh: () => void
}) {
  const mutationBusy = (
    decisionMode === 'modal' ||
    decisionMode === 'inline_confirmation_required' ||
    decisionMode === 'submitting' ||
    decisionMode === 'verifying'
  )
  const pending = proposal.status === 'pending_confirmation'
  const beforeLocalExpiry = proposalLocalExpiryState(
    proposal.status,
    proposal.expires_at
  ) === 'before'
  const retryAvailable = (
    decisionMode === 'retry_confirmation_required' ||
    decisionMode === 'manual_retry_available'
  ) && Boolean(decisionAction)
  const freshDecisionAvailable = (
    decisionMode === 'idle' &&
    pending &&
    beforeLocalExpiry &&
    !presentation.refresh_required
  )
  return (
    <>
      <View className='card proposal-meta-card'>
        <View className='meta-row'>
          <Text className='meta-label'>创建时间</Text>
          <Text className='meta-value'>{formatDateTime(proposal.created_at)}</Text>
        </View>
        <View className='meta-row'>
          <Text className='meta-label'>有效期至</Text>
          <Text className='meta-value'>{formatDateTime(proposal.expires_at)}</Text>
        </View>
        <View className='meta-row'>
          <Text className='meta-label'>提案版本</Text>
          <Text className='meta-value'>v{proposal.version}</Text>
        </View>
        {presentation.refresh_required && (
          <Text className='verification-note'>
            本机时间显示提案可能已到期，需要以服务端状态为准。
          </Text>
        )}
        <Button
          className='secondary-button refresh-button'
          disabled={refreshing || mutationBusy}
          onClick={onRefresh}
        >
          {refreshing ? '正在刷新…' : '刷新服务端状态'}
        </Button>
      </View>

      <Text className='section-heading'>调整概要</Text>
      <View className='card changes-card'>
        {proposal.payload.changes.map((change, index) => (
          <ChangeSummary
            change={change}
            index={index}
            key={`${change.stable_display_key}-${change.change_type}-${index}`}
          />
        ))}
      </View>

      <Text className='section-heading'>完整计划对比</Text>
      <PlanSnapshot
        plan={proposal.payload.before}
        stage='before'
        title='调整前'
      />
      <PlanSnapshot
        plan={proposal.payload.after}
        stage='after'
        title='调整后'
      />

      <View className='card rationale-card'>
        <Text className='section-title'>为什么建议这样调整</Text>
        {proposal.payload.rationale.map((item, index) => (
          <Text className='detail-line' key={`rationale-${index}`}>
            {index + 1}. {item}
          </Text>
        ))}
      </View>

      <View className='safety-card'>
        <Text className='safety-title'>安全提示</Text>
        {proposal.payload.safety_notes.length > 0
          ? proposal.payload.safety_notes.map((item, index) => (
              <Text className='safety-line' key={`safety-${index}`}>• {item}</Text>
            ))
          : <Text className='safety-line'>本提案没有额外安全提示，训练中仍应以无痛、可控为前提。</Text>}
      </View>

      <View className='proposal-decision-panel'>
        <Text className='decision-title'>提案决策</Text>
        <Text className='decision-copy'>
          只有你在本页明确确认后，系统才会尝试更新训练计划。
        </Text>

        {decisionNotice && (
          <View className={`decision-notice ${decisionNotice.tone}`}>
            {decisionNotice.label}
          </View>
        )}

        {pending && beforeLocalExpiry && (
          <View
            className={`review-check ${detailReviewed ? 'checked' : ''} ${mutationBusy ? 'disabled' : ''}`}
            onClick={() => {
              if (!mutationBusy) onReviewChange()
            }}
          >
            <View className='review-check-mark'>{detailReviewed ? '✓' : ''}</View>
            <Text className='review-check-copy'>
              我已查看完整调整前后计划、调整理由和安全提示
            </Text>
          </View>
        )}

        {freshDecisionAvailable && (
          <View className='decision-actions'>
            <Button
              className='danger-button reject-button'
              disabled={
                !detailReviewed ||
                refreshing ||
                !proposal.allowed_actions.includes('reject')
              }
              onClick={() => onDecision('reject')}
            >
              拒绝提案
            </Button>
            <Button
              className='primary-button confirm-button'
              disabled={
                !detailReviewed ||
                refreshing ||
                !proposal.allowed_actions.includes('confirm')
              }
              onClick={() => onDecision('confirm')}
            >
              确认并应用
            </Button>
          </View>
        )}

        {retryAvailable && decisionAction && (
          <>
            <Button
              className={decisionAction === 'confirm'
                ? 'primary-button retry-button'
                : 'danger-button retry-button'}
              disabled={!detailReviewed || refreshing || mutationBusy}
              onClick={() => onDecision(decisionAction, true)}
            >
              {decisionAction === 'confirm' ? '再次确认应用' : '再次确认拒绝'}
            </Button>
            <Button
              className='secondary-button verify-button'
              disabled={refreshing || mutationBusy}
              onClick={onRefresh}
            >
              {refreshing ? '正在核实…' : '先刷新服务端状态'}
            </Button>
          </>
        )}

        {decisionMode === 'inline_confirmation_required' && decisionAction && (
          <View className='inline-confirmation-panel'>
            <Text className='inline-confirmation-copy'>
              {decisionAction === 'confirm'
                ? `再次确认后，系统将原子应用上方展示的 ${proposal.payload.changes.length} 项调整。`
                : '再次确认后，这份提案将被拒绝，当前训练计划不会修改。'}
            </Text>
            <View className='decision-actions'>
              <Button
                className='secondary-button'
                disabled={refreshing}
                onClick={onCancelInline}
              >
                取消
              </Button>
              <Button
                className={decisionAction === 'confirm'
                  ? 'primary-button'
                  : 'danger-button'}
                disabled={refreshing}
                onClick={onConfirmInline}
              >
                {decisionAction === 'confirm' ? '二次确认并应用' : '二次确认拒绝'}
              </Button>
            </View>
          </View>
        )}

        {(decisionMode === 'submitting' || decisionMode === 'modal') && (
          <Button className='primary-button retry-button' disabled>
            {decisionAction === 'confirm' ? '正在应用…' : '正在提交拒绝…'}
          </Button>
        )}

        {decisionMode === 'verifying' && (
          <Button className='secondary-button verify-button' disabled>
            正在核实服务端状态…
          </Button>
        )}

        {decisionMode === 'verification_required' && (
          <Button
            className='secondary-button verify-button'
            disabled={refreshing}
            onClick={onRefresh}
          >
            {refreshing ? '正在核实…' : '重新核实操作结果'}
          </Button>
        )}

        {presentation.refresh_required && (
          <Button
            className='secondary-button verify-button'
            disabled={refreshing || mutationBusy}
            onClick={onRefresh}
          >
            {refreshing ? '正在核实…' : '刷新到期状态'}
          </Button>
        )}

        {decisionMode === 'client_update_required' && (
          <Text className='decision-disabled-copy'>
            当前不再尝试写入，请更新小程序后重新打开提案。
          </Text>
        )}

        {!pending && (
          <Text className='decision-disabled-copy'>
            {proposal.status === 'applied'
              ? '这份调整已经应用，不会再次执行。'
              : proposal.status === 'rejected'
                ? '这份提案已被拒绝，训练计划没有因此更改。'
                : '这份提案已不能执行，如有需要请让 Agent 重新评估。'}
          </Text>
        )}
      </View>
    </>
  )
}

function PlanSnapshot ({
  plan,
  stage,
  title
}: {
  plan: PlanAdjustmentPlanSnapshot
  stage: 'before' | 'after'
  title: string
}) {
  const exercises = [...plan.exercises].sort((left, right) => (
    left.day_of_week - right.day_of_week || left.order_index - right.order_index
  ))
  return (
    <View className={`card plan-snapshot ${stage}`}>
      <View className='snapshot-heading'>
        <View>
          <Text className='snapshot-stage'>{title}</Text>
          <Text className='snapshot-name'>{plan.name}</Text>
        </View>
        <Text className='snapshot-count'>{exercises.length} 个动作</Text>
      </View>
      <View className='snapshot-facts'>
        <SnapshotFact label='目标' value={plan.goal || '未设置'} />
        <SnapshotFact label='周期' value={`${plan.duration_weeks} 周`} />
        <SnapshotFact label='频率' value={`每周 ${plan.days_per_week} 天`} />
      </View>
      {exercises.map(exercise => (
        <ExerciseSnapshot
          exercise={exercise}
          key={`${exercise.slot_key}-${exercise.exercise_id}`}
        />
      ))}
    </View>
  )
}

function SnapshotFact ({ label, value }: { label: string, value: string }) {
  return (
    <View className='snapshot-fact'>
      <Text className='fact-label'>{label}</Text>
      <Text className='fact-value'>{value}</Text>
    </View>
  )
}

function ExerciseSnapshot ({
  exercise
}: {
  exercise: PlanAdjustmentExerciseSnapshot
}) {
  return (
    <View className='exercise-snapshot'>
      <View className='exercise-heading'>
        <View>
          <Text className='exercise-name'>{exercise.exercise_name}</Text>
          <Text className='exercise-position'>
            周{weekday(exercise.day_of_week)} · 第 {exercise.order_index + 1} 项 · 位置 {exercise.slot_key}
          </Text>
        </View>
      </View>
      <View className='exercise-prescription'>
        <SnapshotFact label='组数' value={`${exercise.sets} 组`} />
        <SnapshotFact label='次数' value={exercise.reps} />
        <SnapshotFact label='休息' value={`${exercise.rest_seconds} 秒`} />
        <SnapshotFact label='重量' value={formatWeight(exercise.recommended_weight_kg)} />
      </View>
      <Text className='exercise-identity'>动作 ID：{exercise.exercise_id}</Text>
    </View>
  )
}

function ChangeSummary ({
  change,
  index
}: {
  change: PlanAdjustmentChange
  index: number
}) {
  const values = proposalChangeValues(change)
  return (
    <View className='change-summary'>
      <View className='change-heading'>
        <Text className='change-index'>{index + 1}</Text>
        <View className='change-copy'>
          <Text className='change-title'>{changeTitle(change)}</Text>
          <Text className='change-target'>位置：{change.stable_display_key}</Text>
        </View>
        {change.safety_priority && <Text className='safety-priority'>安全优先</Text>}
      </View>
      <View className='change-values'>
        <View className='change-value before'>
          <Text className='change-value-label'>原来</Text>
          <Text className='change-value-copy'>{values.before}</Text>
        </View>
        <Text className='change-arrow'>→</Text>
        <View className='change-value after'>
          <Text className='change-value-label'>建议</Text>
          <Text className='change-value-copy'>{values.after}</Text>
        </View>
      </View>
      <Text className='change-reason'>{change.reason}</Text>
    </View>
  )
}

function changeTitle (change: PlanAdjustmentChange): string {
  if (change.change_type === 'replace_exercise') return '替换训练动作'
  if (change.change_type === 'update_plan_schedule') return '调整计划节奏'
  return '调整动作目标'
}

function formatWeight (value: number | null): string {
  return value === null ? '自重/未指定' : `${value} kg`
}

function formatDateTime (value: string): string {
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return value
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}
