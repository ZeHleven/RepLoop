import { useRef, useState } from 'react'
import { Button, Text, View } from '@tarojs/components'
import Taro, { useDidHide, useDidShow, useLoad } from '@tarojs/taro'

import { errorMessage } from '../../core/request'
import { planManagementApi } from '../../services/plan-management'
import type {
  ManualPlanProposalReadResponse,
  PlanChangeV2,
  PlanSnapshotV2
} from '../../types/plan-management-proposal'
import './index.scss'

const weekday = (day: number) => ['一', '二', '三', '四', '五', '六', '日'][day - 1]
type DecisionAction = 'confirm' | 'reject'
interface ConfirmationTarget {
  action: DecisionAction
  id: string
  version: number
  fingerprint: string
  revision: number
}

export default function PlanProposalDetailPage () {
  const [proposalId, setProposalId] = useState('')
  const [proposal, setProposal] = useState<ManualPlanProposalReadResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [reviewed, setReviewed] = useState(false)
  const [deciding, setDeciding] = useState<'confirm' | 'reject' | ''>('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [verified, setVerified] = useState(false)
  const [inlineConfirmation, setInlineConfirmation] = useState<ConfirmationTarget | null>(null)
  const currentProposal = useRef<ManualPlanProposalReadResponse | null>(null)
  const reviewedRef = useRef(false)
  const verifiedRef = useRef(false)
  const interactionLock = useRef(false)
  const navigationLock = useRef(false)
  const confirmationRevision = useRef(0)
  const readSequence = useRef(0)
  const inlineTarget = useRef<ConfirmationTarget | null>(null)

  const clearConfirmation = () => {
    confirmationRevision.current += 1
    inlineTarget.current = null
    setInlineConfirmation(null)
    reviewedRef.current = false
    setReviewed(false)
  }

  const load = async (id: string, refresh = false, reconcile = false) => {
    if (interactionLock.current && !reconcile) return null
    const sequence = ++readSequence.current
    clearConfirmation()
    verifiedRef.current = false
    setVerified(false)
    if (refresh) setRefreshing(true)
    else setLoading(true)
    setError('')
    try {
      const value = await planManagementApi.manualProposal(id)
      if (sequence !== readSequence.current) return null
      currentProposal.current = value
      setProposal(value)
      verifiedRef.current = true
      setVerified(true)
      return value
    } catch (requestError) {
      if (sequence === readSequence.current) setError(errorMessage(requestError, '提案读取失败'))
      return null
    } finally {
      if (sequence === readSequence.current) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }

  useLoad(options => {
    const id = typeof options.id === 'string' ? decodeURIComponent(options.id) : ''
    setProposalId(id)
    if (!id) {
      setError('缺少提案标识')
      setLoading(false)
      return
    }
    void load(id)
  })

  useDidShow(() => {
    if (proposalId && proposal) void load(proposalId, true)
  })

  useDidHide(clearConfirmation)

  const targetIsCurrent = (target: ConfirmationTarget) => {
    const current = currentProposal.current
    return Boolean(current && verifiedRef.current && reviewedRef.current &&
      target.revision === confirmationRevision.current && current.id === target.id &&
      current.version === target.version && current.payload_fingerprint === target.fingerprint &&
      current.status === 'pending_confirmation' && current.allowed_actions.includes(target.action) &&
      Date.parse(current.expires_at) > Date.now())
  }

  const returnToTraining = async () => {
    if (navigationLock.current) return
    navigationLock.current = true
    try {
      await Taro.switchTab({ url: '/pages/workouts/index' })
    } catch {
      setNotice('自动返回失败，请点击“返回训练页”重试。提案结果已保留，无需重复提交。')
    } finally {
      navigationLock.current = false
    }
  }

  const executeDecision = async (target: ConfirmationTarget) => {
    if (!targetIsCurrent(target)) return
    verifiedRef.current = false
    setVerified(false)
    setError('')
    let response
    try {
      response = await planManagementApi[target.action](target.id, target.version)
    } catch (requestError) {
      const refreshed = await load(target.id, true, true)
      if (refreshed && ['applied', 'rejected'].includes(refreshed.status)) {
        setNotice('已从服务端核实提案结果，请以当前状态为准，无需重复提交。')
      } else {
        setError(`${errorMessage(requestError, '提案操作结果尚未确定')}。${refreshed ? '已刷新状态；如仍待确认，请重新核对后手动提交。' : '暂未核实结果，请先刷新服务端状态，不要重复提交。'}`)
      }
      return
    }
    // Commit the authoritative result before any optional native navigation.
    readSequence.current += 1
    const current = currentProposal.current!
    const updated = { ...current, status: response.status, version: response.version,
      payload_fingerprint: response.payload_fingerprint, allowed_actions: [], result: response.result_data }
    currentProposal.current = updated
    setProposal(updated)
    verifiedRef.current = true
    setVerified(true)
    clearConfirmation()
    setNotice('')
    await returnToTraining()
  }

  const decide = (action: DecisionAction) => {
    const current = currentProposal.current
    if (!current || interactionLock.current || inlineTarget.current) return
    const target = { action, id: current.id, version: current.version,
      fingerprint: current.payload_fingerprint, revision: confirmationRevision.current }
    if (!targetIsCurrent(target)) return
    setError('')
    // This screen's second confirmation is always inline, not dependent on native dialogs.
    inlineTarget.current = target
    setInlineConfirmation(target)
  }

  const acceptInlineConfirmation = async () => {
    const target = inlineTarget.current
    if (!target || interactionLock.current || !targetIsCurrent(target)) return
    interactionLock.current = true
    inlineTarget.current = null
    setInlineConfirmation(null)
    setDeciding(target.action)
    try { await executeDecision(target) }
    finally { interactionLock.current = false; setDeciding('') }
  }

  const cancelInlineConfirmation = () => {
    if (interactionLock.current) return
    inlineTarget.current = null
    setInlineConfirmation(null)
  }

  const toggleReviewed = () => {
    if (interactionLock.current || inlineTarget.current || !verifiedRef.current) return
    reviewedRef.current = !reviewedRef.current
    setReviewed(reviewedRef.current)
  }

  if (loading) return <View className='loading-state'>正在读取计划提案…</View>
  if (!proposal) {
    return (
      <View className='page manual-proposal-page'>
        {error && <View className='error-banner'>{error}</View>}
        <Button className='secondary-button' disabled={!proposalId || refreshing} onClick={() => load(proposalId, true)}>刷新提案</Button>
      </View>
    )
  }

  const payload = proposal.payload
  const deletion = payload.proposal_type === 'plan_deletion_v1'
  const pending = proposal.status === 'pending_confirmation'
  const locallyExpired = !(Date.parse(proposal.expires_at) > Date.now())
  const decisionDisabled = (action: DecisionAction) => !reviewed || !verified || locallyExpired ||
    !proposal.allowed_actions.includes(action) || Boolean(deciding) || Boolean(inlineConfirmation)
  return (
    <View className='page manual-proposal-page'>
      <View className={`proposal-status status-${proposal.status}`}>{statusLabel(proposal.status)}</View>
      <Text className='proposal-kicker'>{deletion ? '训练计划删除提案' : '训练计划调整提案'}</Text>
      <Text className='proposal-title'>{deletion ? '确认永久删除前，请核对影响' : '核对完整变化后再决定'}</Text>
      <Text className='proposal-note'>创建时间 {formatTime(proposal.created_at)} · 有效期至 {formatTime(proposal.expires_at)}</Text>
      {error && <View className='error-banner'>{error}</View>}
      {notice && <View className='pending-note'>{notice}</View>}

      {deletion ? (
        <>
          <View className='card deletion-warning'>
            <Text className='section-title'>删除后的结果</Text>
            {payload.consequences.map((item, index) => <Text className='detail-line' key={index}>• {item}</Text>)}
          </View>
          <Text className='section-heading'>将被删除的计划</Text>
          <PlanSnapshot plan={payload.before} />
        </>
      ) : (
        <>
          <Text className='section-heading'>变更概要</Text>
          <View className='card change-list'>
            {payload.changes.map((change, index) => <ChangeCard change={change} key={`${change.stable_display_key}-${index}`} />)}
          </View>
          <Text className='section-heading'>调整前</Text>
          <PlanSnapshot plan={payload.before} />
          <Text className='section-heading'>调整后</Text>
          <PlanSnapshot plan={payload.after} />
        </>
      )}

      <View className='card safety-block'>
        <Text className='section-title'>安全与执行说明</Text>
        {payload.safety_notes.map((item, index) => <Text className='detail-line' key={index}>• {item}</Text>)}
      </View>

      <View className='decision-panel'>
        {pending ? (
          <>
            <View className={`review-row ${reviewed ? 'selected' : ''}`} onClick={toggleReviewed}>
              <View className='check-box'>{reviewed ? '✓' : ''}</View>
              <Text>我已查看完整计划、全部变化及安全说明</Text>
            </View>
            {planManagementApi.pendingDecision(proposal.id) && <View className='pending-note'>上次操作结果尚未完全核实；再次提交会复用同一请求标识。</View>}
            {locallyExpired && <View className='pending-note'>提案有效期已到或无法核实，请先刷新服务端状态。</View>}
            <View className='decision-actions'>
              <Button className='secondary-button' disabled={decisionDisabled('reject')} onClick={() => decide('reject')}>{deciding === 'reject' ? '处理中…' : '拒绝提案'}</Button>
              <Button className={deletion ? 'danger-button' : 'primary-button'} disabled={decisionDisabled('confirm')} onClick={() => decide('confirm')}>{deciding === 'confirm' ? '处理中…' : deletion ? '确认永久删除' : '确认并应用'}</Button>
            </View>
            {inlineConfirmation && <View className='inline-confirmation-panel'>
              <Text className='section-title'>请再次确认本次操作</Text>
              <Text className='detail-line'>当前计划尚未修改。</Text>
              <Text className='detail-line'>{confirmationOptions(inlineConfirmation.action, deletion).title}</Text>
              <Text className='detail-line'>{confirmationOptions(inlineConfirmation.action, deletion).content}</Text>
              <View className='decision-actions'>
                <Button className='secondary-button cancel-inline-confirmation' disabled={Boolean(deciding)} onClick={cancelInlineConfirmation}>暂不操作</Button>
                <Button className={`${deletion || inlineConfirmation.action === 'reject' ? 'danger-button' : 'primary-button'} accept-inline-confirmation`} disabled={Boolean(deciding) || locallyExpired || !verified} onClick={acceptInlineConfirmation}>{confirmationOptions(inlineConfirmation.action, deletion).confirmText}</Button>
              </View>
            </View>}
          </>
        ) : <Text className='terminal-copy'>{terminalCopy(proposal.status, deletion)}</Text>}
        <Button className='refresh-button' disabled={refreshing || Boolean(deciding)} onClick={() => load(proposal.id, true)}>{refreshing ? '正在刷新…' : '刷新服务端状态'}</Button>
        {!pending && <Button className='secondary-button return-to-training' onClick={returnToTraining}>返回训练页</Button>}
      </View>
    </View>
  )
}

function confirmationOptions (action: DecisionAction, deletion: boolean) {
  return {
    title: action === 'confirm' ? deletion ? '永久删除当前计划？' : '确认应用调整？' : '拒绝这份提案？',
    content: action === 'confirm'
      ? deletion ? '计划定义和动作编排将永久删除；历史训练记录仍会保留。'
        : '系统将原子创建新计划并切换活动版本，本次进行中的训练不会变化。'
      : '拒绝后当前活动计划不会发生变化。',
    confirmText: action === 'confirm' ? deletion ? '永久删除' : '确认应用' : '确认拒绝',
  }
}

function PlanSnapshot ({ plan }: { plan: PlanSnapshotV2 }) {
  return (
    <View className='card compact-snapshot'>
      <Text className='snapshot-name'>{plan.name}</Text>
      <Text className='snapshot-meta'>{plan.duration_weeks} 周 · 每周 {plan.days_per_week} 天 · {plan.exercises.length} 个动作</Text>
      {plan.training_days.map(day => (
        <View className='snapshot-day' key={day}>
          <Text className='snapshot-day-title'>周{weekday(day)}</Text>
          {plan.exercises.filter(item => item.day_of_week === day).sort((left, right) => left.order_index - right.order_index).map(item => (
            <Text className='snapshot-exercise' key={item.item_key}>{item.exercise_name} · {item.sets} 组 × {item.reps} · 休息 {item.rest_seconds} 秒{item.recommended_weight_kg == null ? '' : ` · ${item.recommended_weight_kg} kg`}</Text>
          ))}
        </View>
      ))}
    </View>
  )
}

function ChangeCard ({ change }: { change: PlanChangeV2 }) {
  return (
    <View className='change-card'>
      <Text className='change-title'>{changeLabel(change.change_type)}</Text>
      <Text className='change-reason'>{change.reason}</Text>
      <Text className='change-json'>原来：{displayValues(change.before)}</Text>
      <Text className='change-json after'>调整后：{displayValues(change.after)}</Text>
    </View>
  )
}

function displayValues (values: Record<string, unknown>): string {
  const labels: Record<string, string> = { duration_weeks: '周期', days_per_week: '每周天数', training_days: '训练日', exercise_name: '动作', day_of_week: '周', order_index: '顺序', sets: '组数', reps: '次数', rest_seconds: '休息秒', recommended_weight_kg: '重量' }
  return Object.entries(values).map(([key, value]) => `${labels[key] || key} ${Array.isArray(value) ? value.map(day => `周${weekday(Number(day))}`).join('、') : value == null ? '未指定' : value}`).join(' · ') || '无'
}

function changeLabel (value: PlanChangeV2['change_type']): string {
  return ({ update_schedule: '调整训练周期或训练日', add_exercise: '新增动作', remove_exercise: '删除动作', replace_exercise: '替换动作', move_exercise: '移动或排序动作', adjust_exercise_target: '调整动作目标' })[value]
}
function statusLabel (status: ManualPlanProposalReadResponse['status']): string { return ({ pending_confirmation: '待你确认', applied: '已应用', rejected: '已拒绝', expired: '已过期', stale: '已失效', failed: '执行失败' })[status] }
function terminalCopy (status: ManualPlanProposalReadResponse['status'], deletion: boolean): string { if (status === 'applied') return deletion ? '计划已永久删除，训练历史仍保留。' : '新活动计划已经生效。'; if (status === 'rejected') return '提案已拒绝，原计划没有变化。'; return '当前提案已不能执行，请返回计划页重新操作。' }
function formatTime (value: string): string { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}` }
