import { useRef, useState } from 'react'
import { Button, Text, View } from '@tarojs/components'
import { useDidHide, useDidShow, useLoad, useUnload } from '@tarojs/taro'

import { errorMessage } from '../../core/request'
import { planManagementApi } from '../../services/plan-management'
import type { GenericProposalReadResponse } from '../../types/plan-management-proposal'
import './index.scss'

const labels: Record<string, string> = {
  age: '年龄', gender: '性别', height_cm: '身高', weight_kg: '体重',
  experience_level: '训练经验', primary_goal: '训练目标',
  training_days_per_week: '训练频率偏好', session_duration_min: '单次训练时长',
  training_location: '训练地点', diet_restriction: '饮食限制',
  injuries: '伤病情况', chronic_conditions: '慢性情况',
  current_weight_kg: '当前体重', recorded_at: '记录时间',
  logged_at: '日期', meal_type: '餐次', food_name: '食品', amount_g: '克数',
  calories: '热量', protein_g: '蛋白质', carbs_g: '碳水', fat_g: '脂肪',
  duration_weeks: '计划周期', days_per_week: '每周训练天数',
  name: '名称', goal: '目标'
}

type DecisionAction = 'confirm' | 'reject'
interface ConfirmationTarget {
  action: DecisionAction
  id: string
  version: number
  fingerprint: string
  revision: number
}
const internalItemFields = new Set(['id', 'food_id', 'custom_food_id', 'custom_food_version'])
const mealItemFieldOrder = ['food_name', 'amount_g', 'calories', 'carbs_g', 'protein_g', 'fat_g']
const mealItemUnits: Record<string, string> = {
  amount_g: 'g', calories: 'kcal', carbs_g: 'g', protein_g: 'g', fat_g: 'g'
}

export default function DomainProposalDetailPage () {
  const [proposalId, setProposalId] = useState('')
  const [proposal, setProposal] = useState<GenericProposalReadResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [reviewed, setReviewed] = useState(false)
  const [deciding, setDeciding] = useState<DecisionAction | ''>('')
  const [error, setError] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [notice, setNotice] = useState('')
  const [verified, setVerified] = useState(false)
  const [inlineConfirmation, setInlineConfirmation] = useState<ConfirmationTarget | null>(null)
  const currentProposal = useRef<GenericProposalReadResponse | null>(null)
  const currentId = useRef('')
  const active = useRef(true)
  const reviewedRef = useRef(false)
  const verifiedRef = useRef(false)
  const interactionLock = useRef(false)
  const confirmationRevision = useRef(0)
  const inlineTarget = useRef<ConfirmationTarget | null>(null)
  const readSequence = useRef(0)
  const reading = useRef(false)
  const refreshOnReturn = useRef(false)

  const clearConfirmation = () => {
    confirmationRevision.current += 1
    inlineTarget.current = null
    setInlineConfirmation(null)
    reviewedRef.current = false
    setReviewed(false)
  }

  const load = async (id: string, refresh = false, reconcile = false) => {
    if (!id || !active.current || (interactionLock.current && !reconcile)) return null
    const sequence = ++readSequence.current
    reading.current = true
    refreshOnReturn.current = false
    clearConfirmation()
    verifiedRef.current = false
    setVerified(false)
    if (refresh) setRefreshing(true)
    else setLoading(true)
    setError('')
    try {
      const value = await planManagementApi.proposal(id)
      if (!active.current || sequence !== readSequence.current || id !== currentId.current) return null
      if (value.id !== id) throw new Error('提案标识不一致，请重新打开详情')
      currentProposal.current = value
      setProposal(value)
      verifiedRef.current = true
      setVerified(true)
      return value
    } catch (requestError) {
      if (active.current && sequence === readSequence.current) setError(errorMessage(requestError, '提案读取失败'))
      return null
    } finally {
      if (sequence === readSequence.current) {
        reading.current = false
        setLoading(false)
        setRefreshing(false)
      }
    }
  }

  useLoad(options => {
    let id = ''
    try { id = typeof options.id === 'string' ? decodeURIComponent(options.id) : '' } catch { /* invalid reference */ }
    active.current = true
    currentId.current = id
    currentProposal.current = null
    setProposal(null)
    setProposalId(id)
    if (!id) {
      setError('缺少提案标识')
      setLoading(false)
      return
    }
    void load(id)
  })

  useDidShow(() => {
    active.current = true
    if (interactionLock.current) refreshOnReturn.current = true
    else if (currentId.current && !reading.current) void load(currentId.current, true)
  })

  const leavePage = () => {
    active.current = false
    readSequence.current += 1
    reading.current = false
    verifiedRef.current = false
    setVerified(false)
    setRefreshing(false)
    clearConfirmation()
  }
  useDidHide(leavePage)
  useUnload(leavePage)

  const targetIsCurrent = (target: ConfirmationTarget) => {
    const current = currentProposal.current
    return Boolean(active.current && current && currentId.current === target.id &&
      verifiedRef.current && reviewedRef.current && target.revision === confirmationRevision.current &&
      current.id === target.id && current.version === target.version &&
      current.payload_fingerprint === target.fingerprint && current.status === 'pending_confirmation' &&
      current.allowed_actions.includes(target.action) && Date.parse(current.expires_at) > Date.now())
  }

  const decide = (action: DecisionAction) => {
    const current = currentProposal.current
    if (!current || interactionLock.current || inlineTarget.current) return
    const target = { action, id: current.id, version: current.version,
      fingerprint: current.payload_fingerprint, revision: confirmationRevision.current }
    if (!targetIsCurrent(target)) return
    setError('')
    setNotice('')
    inlineTarget.current = target
    setInlineConfirmation(target)
  }

  const acceptInlineConfirmation = async (target: ConfirmationTarget) => {
    if (inlineTarget.current !== target || interactionLock.current || !targetIsCurrent(target)) return
    interactionLock.current = true
    readSequence.current += 1
    inlineTarget.current = null
    setInlineConfirmation(null)
    verifiedRef.current = false
    setVerified(false)
    setDeciding(target.action)
    setError('')
    setNotice('')
    try {
      const response = await planManagementApi[target.action](target.id, target.version)
      if (!active.current || currentId.current !== target.id) return
      if (response.id !== target.id || !['applied', 'rejected'].includes(response.status)) {
        throw new Error('提案操作结果尚未确定')
      }
      // Store authoritative status without depending on toast/modal success.
      const updated = { ...currentProposal.current!, status: response.status, version: response.version,
        payload_fingerprint: response.payload_fingerprint, allowed_actions: [], result: response.result_data }
      currentProposal.current = updated
      setProposal(updated)
      verifiedRef.current = true
      setVerified(true)
      clearConfirmation()
      setNotice('已收到服务端处理结果，无需重复提交。')
      refreshOnReturn.current = false
    } catch (requestError) {
      if (!active.current || currentId.current !== target.id) return
      const refreshed = await load(target.id, true, true)
      if (!active.current || currentId.current !== target.id) return
      if (refreshed && ['applied', 'rejected'].includes(refreshed.status)) {
        setNotice('已从服务端核实提案结果，请以当前状态为准，无需重复提交。')
      } else {
        setError(`${errorMessage(requestError, '提案操作结果尚未确定')}。${refreshed ? '已刷新状态；如仍待确认，请重新核对后手动提交。' : '暂未核实结果，请先刷新服务端状态，不要重复提交。'}`)
      }
    } finally {
      interactionLock.current = false
      setDeciding('')
      if (active.current && refreshOnReturn.current) void load(currentId.current, true)
    }
  }

  const toggleReviewed = () => {
    if (interactionLock.current || inlineTarget.current || !verifiedRef.current) return
    reviewedRef.current = !reviewedRef.current
    setReviewed(reviewedRef.current)
  }
  const cancelInlineConfirmation = () => { if (!interactionLock.current) clearConfirmation() }

  if (loading) return <View className='loading-state'>正在读取提案…</View>
  if (!proposal) return <View className='page domain-proposal-page'>
    {error && <View className='error-banner'>{error}</View>}
    <Button className='refresh-button' disabled={!proposalId || refreshing} onClick={() => load(proposalId, true)}>刷新提案</Button>
  </View>
  const payload = proposal.payload
  const before = objectValue(payload.before)
  const after = objectValue(payload.after)
  const pending = proposal.status === 'pending_confirmation'
  const locallyExpired = !(Date.parse(proposal.expires_at) > Date.now())
  const decisionDisabled = (action: DecisionAction) => !reviewed || !verified || locallyExpired ||
    !proposal.allowed_actions.includes(action) || Boolean(deciding) || Boolean(inlineConfirmation)
  const safetyNotes = Array.isArray(payload.safety_notes) ? payload.safety_notes.map(String) : []

  return (
    <View className='page domain-proposal-page'>
      <View className={`proposal-status status-${proposal.status}`}>{statusLabel(proposal.status)}</View>
      <Text className='proposal-kicker'>{titleFor(proposal.proposal_type)}</Text>
      <Text className='proposal-title'>请核对变更前后内容</Text>
      <Text className='proposal-note'>有效期至 {formatTime(proposal.expires_at)}</Text>
      {error && <View className='error-banner'>{error}</View>}
      {notice && <View className='pending-note'>{notice}</View>}

      {before && <ProposalSection title='变更前' value={before} type={proposal.proposal_type} />}
      {after && <ProposalSection title='变更后' value={after} type={proposal.proposal_type} />}
      {!after && <View className='card deletion-card'>确认后将删除上方这条记录；此操作不会静默修改其他数据。</View>}

      {safetyNotes.length > 0 && (
        <View className='card safety-card'>
          <Text className='section-title'>安全与执行说明</Text>
          {safetyNotes.map((item, index) => <Text className='detail-line' key={index}>• {item}</Text>)}
        </View>
      )}

      <View className='decision-panel'>
        {pending ? (
          <>
            <View className={`review-row ${reviewed ? 'selected' : ''}`} onClick={toggleReviewed}>
              <View className='check-box'>{reviewed ? '✓' : ''}</View>
              <Text>我已核对变更前后内容</Text>
            </View>
            {locallyExpired && <View className='pending-note'>提案有效期已到或无法核实，请先刷新服务端状态。</View>}
            {planManagementApi.pendingDecision(proposal.id) && <View className='pending-note'>上次操作结果尚未完全核实，请先刷新状态。再次明确提交会复用同一请求标识。</View>}
            <View className='decision-actions'>
              <Button className='secondary-button' disabled={decisionDisabled('reject')} onClick={() => decide('reject')}>{deciding === 'reject' ? '处理中…' : '拒绝'}</Button>
              <Button className='primary-button' disabled={decisionDisabled('confirm')} onClick={() => decide('confirm')}>{deciding === 'confirm' ? '提交中…' : '确认并执行'}</Button>
            </View>
            {inlineConfirmation && <View className='inline-confirmation-panel'>
              <Text className='section-title'>请再次确认本次操作</Text>
              <Text className='detail-line'>{inlineConfirmation.action === 'reject'
                ? '拒绝这份提案后，不会修改已有业务数据。'
                : after ? '确认后将执行上方展示的变更；服务端仍会校验版本与安全条件。'
                  : '确认后将删除上方记录，请再次核对删除对象；其他数据不会静默修改。'}</Text>
              <Text className='detail-line'>当前尚未提交这次操作；取消不会执行。</Text>
              <View className='decision-actions'>
                <Button className='secondary-button cancel-inline-confirmation' onClick={cancelInlineConfirmation}>取消</Button>
                <Button className='primary-button accept-inline-confirmation' onClick={() => acceptInlineConfirmation(inlineConfirmation)}>{inlineConfirmation.action === 'confirm' ? '确认执行' : '确认拒绝'}</Button>
              </View>
            </View>}
          </>
        ) : <Text className='terminal-copy'>{terminalCopy(proposal.status)}</Text>}
        <Button className='refresh-button' disabled={Boolean(deciding) || refreshing} onClick={() => load(proposal.id, true)}>{refreshing ? '刷新中…' : '刷新状态'}</Button>
      </View>
    </View>
  )
}

function ProposalSection ({ title, value, type }: { title: string, value: Record<string, unknown>, type: string }) {
  const planExercises = Array.isArray(value.exercises) ? value.exercises : []
  const mealItems = Array.isArray(value.items) ? value.items : []
  const meals = Array.isArray(value.meals) ? value.meals.map(item => objectValue(item) || {}) : []
  const dailyTotals = objectValue(value.daily_totals)
  const nutritionTargets = objectValue(value.nutrition_targets)
  const nutritionFit = objectValue(value.nutrition_fit)
  const hidden = new Set(['exercises', 'exercise_options', 'rationale', 'safety_notes', 'items', 'meals', 'nutrition_targets', 'daily_totals', 'nutrition_fit'])
  return (
    <>
      <Text className='section-heading'>{title}</Text>
      <View className='card value-card'>
        {Object.entries(value).filter(([key]) => !hidden.has(key)).map(([key, item]) => (
          <ValueRow key={key} label={labels[key] || key} value={item} />
        ))}
        {mealItems.map((item, index) => <NestedItem key={index} value={objectValue(item) || {}} />)}
        {meals.map((meal, index) => <MealProposalItem key={index} meal={meal} />)}
        {dailyTotals && (
          <Text className='meal-total'>全天合计 {display(dailyTotals.calories)} kcal · 蛋白质 {display(dailyTotals.protein_g)} g · 碳水 {display(dailyTotals.carbs_g)} g · 脂肪 {display(dailyTotals.fat_g)} g</Text>
        )}
        {nutritionTargets && <NutritionTargets value={nutritionTargets} />}
        {nutritionFit && <NutritionFit value={nutritionFit} />}
        {planExercises.length > 0 && <Text className='detail-line'>训练动作共 {planExercises.length} 项，将按提案中的训练日与顺序创建。</Text>}
        {type === 'weight_log_create_v1' && <Text className='detail-line'>确认后会同步更新个人档案中的当前体重和 BMI。</Text>}
      </View>
    </>
  )
}

function NutritionFit ({ value }: { value: Record<string, unknown> }) {
  const warning = value.status === 'acceptable_deviation'
  const deviations = Array.isArray(value.deviations) ? value.deviations.map(item => objectValue(item) || {}) : []
  return (
    <View className={`nutrition-fit ${warning ? 'warning' : 'success'}`}>
      <Text className='nutrition-fit-title'>{warning ? '接近目标，请核对偏差' : '营养指标处于理想范围'}</Text>
      {deviations.map((item, index) => (
        <Text className='nutrition-fit-detail' key={index}>
          • {display(item.label || item.metric)}实际 {display(item.actual)}{display(item.unit)}，理想范围 {display(item.ideal_min)}–{display(item.ideal_max)}{display(item.unit)}
        </Text>
      ))}
    </View>
  )
}

function NutritionTargets ({ value }: { value: Record<string, unknown> }) {
  const calories = objectValue(value.calories_kcal)
  const protein = objectValue(value.protein_g)
  if (!calories && !protein) return null
  return (
    <Text className='detail-line'>目标区间：{calories ? `${display(calories.min)}–${display(calories.max)} kcal` : '—'} · 蛋白质 {protein ? `${display(protein.min)}–${display(protein.max)} g` : '—'}</Text>
  )
}

function MealProposalItem ({ meal }: { meal: Record<string, unknown> }) {
  const items = Array.isArray(meal.items) ? meal.items.map(item => objectValue(item) || {}) : []
  const totals = objectValue(meal.totals)
  return (
    <View className='nested-item meal-proposal-item'>
      <Text className='section-title'>{display(meal.meal_type)}</Text>
      {items.map((item, index) => (
        <Text className='detail-line' key={index}>
          {display(item.food_name)} · {display(item.amount_g)} 克 · {display(item.calories)} kcal
        </Text>
      ))}
      {totals && <Text className='meal-total'>本餐合计 {display(totals.calories)} kcal · 蛋白质 {display(totals.protein_g)} g · 碳水 {display(totals.carbs_g)} g · 脂肪 {display(totals.fat_g)} g</Text>}
    </View>
  )
}

function NestedItem ({ value }: { value: Record<string, unknown> }) {
  const fields = [
    ...mealItemFieldOrder.filter(key => Object.prototype.hasOwnProperty.call(value, key)),
    ...Object.keys(value).filter(key => !internalItemFields.has(key) && !mealItemFieldOrder.includes(key))
  ]
  return (
    <View className='nested-item'>
      {fields.map(key => (
        <ValueRow key={key} label={labels[key] || key} value={value[key]} unit={mealItemUnits[key]} />
      ))}
    </View>
  )
}

function ValueRow ({ label, value, unit }: { label: string, value: unknown, unit?: string }) {
  const numeric = (typeof value === 'number' || (typeof value === 'string' && value.trim() !== '')) && Number.isFinite(Number(value))
  const text = unit && numeric ? `${display(value)} ${unit}` : display(value)
  return (
    <View className='value-row'>
      <Text className='value-label'>{label}</Text>
      <Text className='value-text'>{text}</Text>
    </View>
  )
}

function objectValue (value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}
function display (value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.map(item => typeof item === 'object' ? '详细项目' : String(item)).join('、') : '无'
  if (value == null || value === '') return '未设置'
  if (typeof value === 'object') return '详细内容'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}
function titleFor (type: string): string { return ({ plan_creation_v1: '训练计划创建提案', profile_update_v1: '个人档案与健康更新提案', weight_log_create_v1: '体重记录提案', meal_log_create_v1: '饮食记录提案', daily_meal_log_create_v1: '全天饮食记录提案', meal_log_delete_v1: '饮食删除提案' } as Record<string, string>)[type] || '数据变更提案' }
function statusLabel (status: GenericProposalReadResponse['status']): string { return ({ pending_confirmation: '待你确认', applied: '已应用', rejected: '已拒绝', expired: '已过期', stale: '已失效', failed: '执行失败' })[status] }
function terminalCopy (status: GenericProposalReadResponse['status']): string { if (status === 'applied') return '这项变更已经成功应用。'; if (status === 'rejected') return '提案已拒绝，数据没有变化。'; return '当前提案已经不能执行，请重新发起请求。' }
function formatTime (value: string): string { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? value : `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, '0')}-${String(parsed.getDate()).padStart(2, '0')} ${String(parsed.getHours()).padStart(2, '0')}:${String(parsed.getMinutes()).padStart(2, '0')}` }
