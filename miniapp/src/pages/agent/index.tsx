import { useEffect, useRef, useState } from 'react'
import { capsuleNavigation, useCapsuleState } from '../../core/capsule-platform'
import { Button, ScrollView, Text, Textarea, View } from '@tarojs/components'
import Taro, { useDidHide, useDidShow, useLoad } from '@tarojs/taro'

import { miniappBuildLabel } from '../../core/build-info'
import AgentMessageContent from '../../components/AgentMessageContent'
import {
  proposalReferenceFromRead,
  proposalReferenceFromUnknown,
  unavailableProposalReference
} from '../../core/proposal-reference'
import {
  projectProposalStatus,
  proposalLocalExpiryState
} from '../../core/proposal-interaction'
import { errorMessage } from '../../core/request'
import {
  clearAgentConversationId,
  clearPendingAgentRequest,
  getAgentConversationId,
  getPendingAgentRequest,
  saveAgentConversationId,
  savePendingAgentRequest
} from '../../core/storage'
import type { PendingAgentRequest } from '../../core/storage'
import { agentApi } from '../../services/agent'
import { proposalsApi } from '../../services/proposals'
import { planManagementApi } from '../../services/plan-management'
import type {
  AgentArtifactAction,
  AgentCard,
  AgentClarificationAction,
  AgentMessage
} from '../../types/api'
import type { PlanAdjustmentProposalReference } from '../../types/plan-adjustment-proposal'
import './index.scss'


interface DisplayMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  cards: AgentCard[]
  proposal: PlanAdjustmentProposalReference | null
}

type AgentCardAction = AgentArtifactAction | AgentClarificationAction


const quickPrompts = [
  '我下一练做什么？',
  '查看我的当前训练计划',
  '查看我的健康筛查资料',
  '我今天吃了什么？',
  '结合我的情况安排今天怎么吃',
  '查看我的体重变化'
]

const welcomeMessage: DisplayMessage = {
  id: 'welcome',
  role: 'assistant',
  content: '你好，我是训练搭子。可以一起看看训练安排、饮食记录，或聊聊你的健身问题。\n\n需要修改数据时，我会先给你一份前后对比提案，等你确认后再执行。今天想聊什么？',
  cards: [],
  proposal: null
}

const MAX_AGENT_WAIT_MS = 180000

const wait = (milliseconds: number) => new Promise(resolve => {
  setTimeout(resolve, milliseconds)
})

function createClientRequestId (): string {
  return `agent-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`
}


export default function AgentPage () {
  const capsuleState = useCapsuleState()
  const [messages, setMessages] = useState<DisplayMessage[]>([welcomeMessage])
  const [conversationId, setConversationId] = useState('')
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [scrollTarget, setScrollTarget] = useState('welcome')
  const [sendingLabel, setSendingLabel] = useState('正在提交…')
  const [newConversationPrompt, setNewConversationPrompt] = useState(false)
  const conversationGeneration = useRef(0)
  const pollGeneration = useRef(0)
  const sendLock = useRef(false)
  const visible = useRef(true)
  const restoredConversation = useRef('')
  const entryInFlight = useRef<Promise<void> | null>(null)
  const activeRequest = useRef<{ id: string, generation: number, promise: Promise<void> } | null>(null)
  const proposalSyncInFlight = useRef(false)
  const proposalSyncQueued = useRef(false)
  const messagesRef = useRef(messages)
  messagesRef.current = messages

  async function restoreConversation (savedId: string) {
    const generation = ++conversationGeneration.current
    setLoading(true)
    setError('')
    try {
      const history = await agentApi.messages(savedId)
      if (conversationGeneration.current !== generation) return false
      const restored = history
        .filter(item => item.role === 'user' || item.role === 'assistant')
        .map(toDisplayMessage)
      setConversationId(savedId)
      restoredConversation.current = savedId
      setMessages(restored.length ? restored : [welcomeMessage])
      if (restored.length) void synchronizeProposalReferences(restored)
    } catch (requestError) {
      if (conversationGeneration.current === generation) {
        setError(errorMessage(requestError, '历史对话加载失败，你仍可开始新对话'))
      }
      return false
    } finally {
      if (conversationGeneration.current === generation) setLoading(false)
    }
    return conversationGeneration.current === generation
  }

  function enterPage (): Promise<void> {
    visible.current = true
    if (entryInFlight.current) return entryInFlight.current
    const task = (async () => {
      // Coalesce useLoad/useDidShow even before the first React render.
      const pending = getPendingAgentRequest()
      const savedId = pending?.conversation_id || getAgentConversationId()
      if (savedId && savedId !== restoredConversation.current) {
        if (!await restoreConversation(savedId)) return
      } else if (!savedId) setLoading(false)
      if (!visible.current) return
      const current = getPendingAgentRequest()
      if (current) void resumePendingRequest(current)
      else void synchronizeProposalReferences(messagesRef.current)
    })()
    entryInFlight.current = task
    void task.finally(() => {
      if (entryInFlight.current === task) entryInFlight.current = null
    })
    return task
  }

  useLoad(() => { void enterPage() })
  useDidShow(() => { capsuleNavigation.show(2); void enterPage() })
  useDidHide(() => {
    visible.current = false
    pollGeneration.current += 1
    setSending(false)
  })

  useEffect(() => {
    const last = messages[messages.length - 1]
    if (last) setScrollTarget(`message-${last.id}`)
  }, [messages])

  useEffect(() => () => {
    visible.current = false
    conversationGeneration.current += 1
    pollGeneration.current += 1
  }, [])

  async function synchronizeProposalReferences (
    sourceMessages: DisplayMessage[]
  ) {
    if (proposalSyncInFlight.current) {
      proposalSyncQueued.current = true
      return
    }
    const references = new Map<string, PlanAdjustmentProposalReference>()
    for (const message of sourceMessages) {
      if (message.proposal) references.set(message.proposal.id, message.proposal)
    }
    if (references.size === 0) return

    proposalSyncInFlight.current = true
    try {
      const updates = new Map<string, PlanAdjustmentProposalReference>()
      await Promise.all([...references.entries()].map(async ([id, reference]) => {
        try {
          if (reference.proposal_type === 'plan_adjustment_v1') {
            const proposal = await proposalsApi.get(id)
            updates.set(id, proposalReferenceFromRead(proposal))
          } else {
            const proposal = await planManagementApi.proposal(id)
            updates.set(id, proposalReferenceFromUnknown(proposal) || reference)
          }
        } catch (requestError) {
          const candidate = requestError as { code?: unknown } | null
          const code = candidate && candidate.code
          if (code === 'proposal_not_found') {
            updates.set(id, unavailableProposalReference(reference))
          }
        }
      }))
      if (updates.size === 0) return
      setMessages(current => current.map(message => {
        if (!message.proposal) return message
        const updated = updates.get(message.proposal.id)
        return updated ? { ...message, proposal: updated } : message
      }))
    } finally {
      proposalSyncInFlight.current = false
      if (proposalSyncQueued.current) {
        proposalSyncQueued.current = false
        void synchronizeProposalReferences(messagesRef.current)
      }
    }
  }

  async function resumePendingRequest (
    initialRequest: PendingAgentRequest
  ) {
    const active = activeRequest.current
    if (active?.id === initialRequest.client_request_id) {
      if (active.generation === pollGeneration.current) return active.promise
      // A hidden page may still have one HTTP request in flight. Do not start
      // a second submission/poll until that request settles.
      await active.promise
      if (!visible.current || getPendingAgentRequest()?.client_request_id !== initialRequest.client_request_id) return
      return resumePendingRequest(getPendingAgentRequest()!)
    }
    if (!visible.current || getPendingAgentRequest()?.client_request_id !== initialRequest.client_request_id) return
    const generation = ++pollGeneration.current
    const promise = runPendingRequest(initialRequest, generation)
    const entry = { id: initialRequest.client_request_id, generation, promise }
    activeRequest.current = entry
    try { await promise } finally {
      if (activeRequest.current === entry) activeRequest.current = null
    }
  }

  async function runPendingRequest (initialRequest: PendingAgentRequest, generation: number) {
    const startedAt = Date.now()
    let pending = initialRequest
    const owner = conversationGeneration.current
    const ownsPending = () => conversationGeneration.current === owner &&
      getPendingAgentRequest()?.client_request_id === initialRequest.client_request_id
    const isCurrent = () => visible.current && pollGeneration.current === generation && ownsPending()

    setError('')
    setSending(true)
    setSendingLabel(pending.run_id ? '正在恢复后台回答…' : '正在提交…')
    const localMessage: DisplayMessage = {
      id: `local-${pending.client_request_id}`,
      role: 'user',
      content: pending.message,
      cards: [],
      proposal: null
    }
    setMessages(current => {
      if (current.some(item => item.id === localMessage.id)) return current
      return [...current, localMessage]
    })

    try {
      if (!pending.run_id) {
        const submission = await agentApi.submit(
          pending.message,
          pending.client_request_id,
          pending.conversation_id,
          pending.artifact_action,
          pending.clarification_action
        )
        // Persist the receipt even if the page was hidden during submission,
        // provided this is still the same request and account session.
        if (!ownsPending()) return
        pending = {
          ...pending,
          run_id: submission.run_id,
          conversation_id: submission.conversation_id
        }
        savePendingAgentRequest(pending)
        if (submission.conversation_id !== conversationId) {
          setConversationId(submission.conversation_id)
          saveAgentConversationId(submission.conversation_id)
          restoredConversation.current = submission.conversation_id
        }
      }

      if (!isCurrent()) return
      if (!pending.run_id) throw new Error('后台任务创建失败，请稍后重试')
      setSendingLabel('正在后台查询并思考…')

      while (isCurrent()) {
        const run = await agentApi.run(pending.run_id)
        if (!isCurrent()) return
        if (run.status === 'completed') {
          if (!run.reply) throw new Error('回答已完成但内容为空，请重新发送')
          setMessages(current => current.some(item => item.id === run.id)
            ? current
            : [
                ...current,
                {
                  id: run.id,
                  role: 'assistant',
                  content: run.reply || '',
                  cards: run.cards || [],
                  proposal: run.proposal || null
                }
              ])
          clearPendingAgentRequest()
          return
        }
        if (run.status === 'failed') {
          clearPendingAgentRequest()
          throw new Error(run.error_message || '训练搭子暂时无法完成请求，请重新发送')
        }
        setSendingLabel(run.status === 'queued' ? '正在排队，等待后台处理…' : '正在后台查询并思考…')
        if (Date.now() - startedAt >= MAX_AGENT_WAIT_MS) {
          setError('暂时停止等待，后台任务没有取消。可点击“继续获取回答”，或稍后返回本页。')
          return
        }
        await wait(Math.max(500, Math.min(run.poll_after_ms || 800, 2000)))
      }
    } catch (requestError) {
      if (visible.current && pollGeneration.current === generation) {
        setError(getPendingAgentRequest()
          ? `连接中断或暂时无法获取结果：${errorMessage(requestError, '请检查网络')}。原请求已保留，可继续获取回答。`
          : errorMessage(requestError, '训练搭子暂时无法完成请求'))
      }
    } finally {
      if (pollGeneration.current === generation) {
        setSending(false)
        setSendingLabel('正在提交…')
      }
    }
  }

  const send = async (
    prompt?: string,
    action?: AgentCardAction
  ) => {
    const content = (prompt || input).trim()
    if (!content || loading || sending || sendLock.current) return
    const generation = conversationGeneration.current
    sendLock.current = true

    try {
      const existingPending = getPendingAgentRequest()
      if (existingPending) {
        setError('上一条请求还未处理完。请先点击“继续获取回答”，或明确开始新对话；当前输入已保留。')
        return
      }

      const pending: PendingAgentRequest = {
        client_request_id: createClientRequestId(),
        message: content,
        ...(conversationId ? { conversation_id: conversationId } : {}),
        ...(action?.action === 'save_as_proposal' ? { artifact_action: action } : {}),
        ...(action?.action === 'select_plan_occurrences' ? { clarification_action: action } : {}),
        created_at: Date.now()
      }
      savePendingAgentRequest(pending)
      setInput('')
      await resumePendingRequest(pending)
    } finally {
      if (conversationGeneration.current === generation) sendLock.current = false
    }
  }

  const switchToNewConversation = () => {
    conversationGeneration.current += 1
    pollGeneration.current += 1
    entryInFlight.current = null
    restoredConversation.current = ''
    sendLock.current = false
    clearPendingAgentRequest()
    clearAgentConversationId()
    setNewConversationPrompt(false)
    setSending(false)
    setSendingLabel('正在提交…')
    setLoading(false)
    setConversationId('')
    setMessages([welcomeMessage])
    setInput('')
    setError('')
  }

  const startNewConversation = () => {
    if (sending || getPendingAgentRequest()) {
      setNewConversationPrompt(true)
      return
    }
    switchToNewConversation()
  }

  return (
    <View className='agent-page'>
      <View className='agent-header'>
        <View>
          <Text className='agent-eyebrow'>查询与提案能力已开启</Text>
          <Text className='agent-title'>和训练搭子聊聊</Text>
          <Text className='agent-build'>{miniappBuildLabel()}</Text>
        </View>
        <View className='agent-header-actions'>
          <View className='chat-history' onClick={() => Taro.navigateTo({ url: '/pages/agent-conversations/index' })}>历史</View>
          <View className='new-chat' onClick={startNewConversation}>新对话</View>
        </View>
      </View>

      {newConversationPrompt && (
        <View className='new-conversation-prompt'>
          <Text className='new-conversation-prompt-title'>后台回答仍在处理</Text>
          <Text className='new-conversation-prompt-copy'>切换不会取消已提交的后台任务。已有对话会保留，你可以从“历史”返回查看。</Text>
          <View className='new-conversation-prompt-actions'>
            <Button className='secondary-button keep-conversation' onClick={() => setNewConversationPrompt(false)}>继续等待</Button>
            <Button className='primary-button confirm-new-conversation' onClick={switchToNewConversation}>开始新对话</Button>
          </View>
        </View>
      )}

      {error && <View className='error-banner agent-error'>{error}</View>}
      {!sending && getPendingAgentRequest() && (
        <View className='agent-recovery'>
          <Text>有一条尚未取得结果的请求，不需要重新发送问题。</Text>
          <Button className='secondary-button resume-agent' onClick={() => { void enterPage() }}>继续获取回答</Button>
        </View>
      )}

      <ScrollView
        className='message-scroll'
        scrollY
        scrollWithAnimation
        scrollIntoView={scrollTarget}
      >
        {loading && <View className='loading-state'>正在加载对话…</View>}
        {!loading && messages.map(message => (
          <View
            id={`message-${message.id}`}
            className={`message-row ${message.role}`}
            key={message.id}
          >
            {message.role === 'assistant' && <View className='assistant-avatar'>练</View>}
            <View className='message-column'>
              <View className='message-bubble'>
                {message.role === 'assistant'
                  ? <AgentMessageContent content={message.content} />
                  : <Text selectable>{message.content}</Text>}
              </View>
              {message.cards.map((card, index) => (
                <AgentDataCard
                  card={card}
                  key={`${message.id}-${card.type}-${index}`}
                  onAction={(prompt, action) => send(prompt, action)}
                />
              ))}
              {message.proposal && (
                <ProposalReferenceCard proposal={message.proposal} />
              )}
            </View>
          </View>
        ))}
        {sending && (
          <View id='message-sending' className='message-row assistant'>
            <View className='assistant-avatar'>练</View>
            <View className='message-bubble typing'>{sendingLabel}</View>
          </View>
        )}
        {!loading && messages.length === 1 && (
          <View className='quick-prompts'>
            {quickPrompts.map(prompt => (
              <View className='quick-prompt' key={prompt} onClick={() => send(prompt)}>
                {prompt}
              </View>
            ))}
          </View>
        )}
        <View className='scroll-spacer' />
      </ScrollView>

      <View className={`composer ${capsuleState.keyboard ? 'composer-keyboard' : ''}`}>
        <Textarea
          className='composer-input'
          value={input}
          maxlength={2000}
          autoHeight
          placeholder='问训练、健康、体重、饮食或让搭子制定方案…'
          disabled={sending || loading}
          onInput={event => setInput(event.detail.value)}
          onFocus={() => capsuleNavigation.keyboard(1)}
          onBlur={() => capsuleNavigation.keyboard(0)}
        />
        <Button
          className='send-button'
          disabled={sending || loading || !input.trim()}
          onClick={() => send()}
        >
          {sending ? '处理中' : '发送'}
        </Button>
        <Text className='composer-hint'>健康建议不能替代医生诊断；未经你的确认不会修改任何数据。</Text>
      </View>
    </View>
  )
}


function toDisplayMessage (message: AgentMessage): DisplayMessage {
  const clientRequestId = message.content_data.client_request_id
  return {
    id: message.role === 'user' && typeof clientRequestId === 'string'
      ? `local-${clientRequestId}`
      : message.role === 'assistant' && message.run_id
        ? message.run_id
        : message.id,
    role: message.role === 'user' ? 'user' : 'assistant',
    content: message.content,
    cards: cardsFromContentData(message.content_data),
    proposal: proposalFromContentData(message.content_data)
  }
}

function proposalFromContentData (
  contentData: Record<string, unknown>
): PlanAdjustmentProposalReference | null {
  return proposalReferenceFromUnknown(contentData.proposal)
}

function ProposalReferenceCard ({
  proposal
}: {
  proposal: PlanAdjustmentProposalReference
}) {
  const localExpiryState = proposal.status === 'missing'
    ? 'irrelevant'
    : proposalLocalExpiryState(proposal.status, proposal.expires_at)
  const presentation = projectProposalStatus(
    proposal.status,
    localExpiryState
  )
  const open = () => Taro.navigateTo({
    url: proposal.proposal_type === 'plan_adjustment_v1'
      ? `/pages/proposal-detail/index?id=${encodeURIComponent(proposal.id)}`
      : proposal.proposal_type === 'plan_adjustment_v2' || proposal.proposal_type === 'plan_deletion_v1'
        ? `/pages/plan-proposal-detail/index?id=${encodeURIComponent(proposal.id)}`
        : `/pages/domain-proposal-detail/index?id=${encodeURIComponent(proposal.id)}`
  })
  return (
    <View className='proposal-reference-card' onClick={open}>
      <View className='proposal-reference-heading'>
        <Text className='proposal-reference-title'>{proposalTitle(proposal.proposal_type)}</Text>
        <Text className={`proposal-reference-status ${presentation.tone}`}>
          {presentation.label}
        </Text>
      </View>
      <Text className='proposal-reference-copy'>
        查看完整前后对比、影响与安全提示
      </Text>
      <View className='proposal-reference-footer'>
        <Text className='proposal-reference-expiry'>
          {presentation.terminal
            ? '状态已与服务端同步'
            : `有效期至 ${formatProposalDate(proposal.expires_at)}`}
        </Text>
        <Text className='proposal-reference-action'>
          {presentation.terminal ? '查看结果 →' : '查看详情 →'}
        </Text>
      </View>
    </View>
  )
}

function proposalTitle (type: PlanAdjustmentProposalReference['proposal_type']): string {
  return ({
    plan_adjustment_v1: '训练计划调整提案',
    plan_adjustment_v2: '训练计划调整提案',
    plan_deletion_v1: '训练计划删除提案',
    plan_creation_v1: '训练计划创建提案',
    profile_update_v1: '个人档案更新提案',
    weight_log_create_v1: '体重记录提案',
    meal_log_create_v1: '饮食记录提案',
    daily_meal_log_create_v1: '全天饮食记录提案',
    meal_log_delete_v1: '饮食删除提案'
  })[type]
}

function formatProposalDate (value: string): string {
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return value
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getMonth() + 1}月${date.getDate()}日 ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function cardsFromContentData (contentData: Record<string, unknown>): AgentCard[] {
  const value = contentData.cards
  if (!Array.isArray(value)) return []
  return value.filter(isAgentCard)
}

function isAgentCard (value: unknown): value is AgentCard {
  if (!value || typeof value !== 'object') return false
  const card = value as { type?: unknown, data?: unknown }
  return typeof card.type === 'string' && Boolean(card.data) && typeof card.data === 'object'
}

function asRecord (value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function asList (value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function textValue (value: unknown, fallback = '—'): string {
  if (typeof value === 'string' && value) return value
  if (typeof value === 'number') return String(value)
  return fallback
}

function clarificationActionFromUnknown (
  value: unknown
): AgentClarificationAction | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const action = value as Partial<AgentClarificationAction>
  if (
    action.action !== 'select_plan_occurrences' ||
    typeof action.origin_run_id !== 'string' ||
    !action.origin_run_id ||
    !Array.isArray(action.choice_ids) ||
    action.choice_ids.length < 1 ||
    action.choice_ids.length > 7 ||
    action.choice_ids.some(item => typeof item !== 'string' || !item.startsWith('planned:'))
  ) return null
  return {
    action: action.action,
    origin_run_id: action.origin_run_id,
    choice_ids: action.choice_ids
  }
}

function evidenceLabel (value: string): string {
  return ({
    profile_summary: '个人档案',
    health_screening: '健康情况',
    weight_history: '体重趋势',
    workout_daily_context: '训练情况',
    nutrition_recent_context: '近期饮食',
    food_catalog: '标准食品库'
  } as Record<string, string>)[value] || value
}

function AgentDataCard ({
  card,
  onAction
}: {
  card: AgentCard
  onAction: (prompt: string, action?: AgentCardAction) => void
}) {
  const data = card.data
  const titleMap: Record<string, string> = {
    'profile.get_summary': '个人训练资料',
    'health.get_screening_summary': '健康筛查摘要',
    'plan.get_active': '当前训练计划',
    'workout.get_next': '下一练',
    'workout.get_active_session': '进行中的训练',
    'workout.list_history': '近期训练记录',
    'workout.get_progress': '训练进度',
    daily_meal_plan: '今日全天饮食方案'
  }
  const title = titleMap[card.type] || '查询结果'

  if (card.type === 'clarification_choices') {
    const options = asList(data.options).map(asRecord)
    return (
      <View className='agent-data-card clarification-card'>
        <Text className='data-card-title'>{textValue(data.title, '请选择')}</Text>
        <View className='clarification-options'>
          {options.map((option, index) => {
            const action = clarificationActionFromUnknown(option.action)
            const message = textValue(option.message, textValue(option.label, '选择'))
            return (
              <Button
                className='clarification-option'
                disabled={!action}
                key={`${textValue(option.label)}-${index}`}
                onClick={() => { if (action) onAction(message, action) }}
              >
                {textValue(option.label, '选择')}
              </Button>
            )
          })}
        </View>
      </View>
    )
  }

  if (card.type === 'daily_meal_plan') {
    const targets = asRecord(data.nutrition_targets)
    const calories = asRecord(targets.calories_kcal)
    const protein = asRecord(targets.protein_g)
    const totals = asRecord(data.daily_totals)
    const fit = asRecord(data.nutrition_fit)
    const fitStatus = String(fit.status || 'within_target')
    const fitDeviations = asList(fit.deviations).map(asRecord)
    const meals = asList(data.meals).map(asRecord)
    const sources = asList(data.evidence_sources).map(item => evidenceLabel(String(item)))
    const assumptions = asList(data.assumptions).map(String)
    const safetyNotes = asList(data.safety_notes).map(String)
    const artifact = asRecord(data.artifact)
    const artifactAction: AgentArtifactAction | null = (
      typeof artifact.id === 'string' &&
      typeof artifact.version === 'number' &&
      typeof artifact.payload_fingerprint === 'string' &&
      /^[0-9a-f]{64}$/.test(artifact.payload_fingerprint)
    )
      ? {
          action: 'save_as_proposal',
          artifact_id: artifact.id,
          expected_version: artifact.version,
          payload_fingerprint: artifact.payload_fingerprint
        }
      : null
    return (
      <View className='agent-data-card daily-meal-card'>
        <Text className='data-card-title'>{title}</Text>
        <Text className={`daily-meal-fit ${fitStatus === 'acceptable_deviation' ? 'warning' : 'success'}`}>
          {fitStatus === 'acceptable_deviation' ? '接近目标' : '理想范围'}
        </Text>
        <Text className='data-card-secondary'>目标 {textValue(calories.min)}–{textValue(calories.max)} kcal · 蛋白质 {textValue(protein.min)}–{textValue(protein.max)} g</Text>
        <View className='data-metrics'>
          <View className='data-metric'><Text className='data-metric-value'>{textValue(totals.calories)}</Text><Text className='data-metric-label'>kcal</Text></View>
          <View className='data-metric'><Text className='data-metric-value'>{textValue(totals.protein_g)}</Text><Text className='data-metric-label'>蛋白质 g</Text></View>
          <View className='data-metric'><Text className='data-metric-value'>{textValue(totals.carbs_g)}</Text><Text className='data-metric-label'>碳水 g</Text></View>
        </View>
        {meals.map((meal, mealIndex) => (
          <View className='daily-meal-row' key={`${textValue(meal.meal_type)}-${mealIndex}`}>
            <Text className='daily-meal-title'>{textValue(meal.meal_type)}</Text>
            {asList(meal.items).map(asRecord).map((item, itemIndex) => (
              <Text className='data-card-line' key={`${textValue(item.food_id)}-${itemIndex}`}>
                {textValue(item.food_name)} · {textValue(item.amount_g)} 克
              </Text>
            ))}
          </View>
        ))}
        {fitDeviations.map((item, index) => (
          <Text className='daily-meal-fit-detail' key={`fit-${index}`}>
            • {textValue(item.label, textValue(item.metric))}实际 {textValue(item.actual)}{textValue(item.unit)}，理想范围 {textValue(item.ideal_min)}–{textValue(item.ideal_max)}{textValue(item.unit)}
          </Text>
        ))}
        <Text className='daily-meal-source'>本次参考：{sources.join('、')}</Text>
        {assumptions.map((item, index) => <Text className='daily-meal-note' key={`assumption-${index}`}>• {item}</Text>)}
        {safetyNotes.map((item, index) => <Text className='daily-meal-note' key={`safety-${index}`}>• {item}</Text>)}
        <Button
          className='daily-meal-save'
          disabled={!artifactAction}
          onClick={() => {
            if (artifactAction) onAction('保存这份方案', artifactAction)
          }}
        >
          保存为待确认提案
        </Button>
      </View>
    )
  }

  if (card.type === 'plan.get_active') {
    const plan = asRecord(data.plan)
    const exercises = asList(plan.exercises)
    return (
      <DataCard title={title}>
        <Text className='data-card-primary'>{textValue(plan.name, '暂无活动计划')}</Text>
        <Text className='data-card-secondary'>共 {exercises.length} 个计划动作 · {textValue(plan.duration_weeks, '0')} 周</Text>
      </DataCard>
    )
  }

  if (card.type === 'workout.get_next') {
    const exercises = asList(data.exercises).map(asRecord)
    return (
      <DataCard title={title}>
        <Text className='data-card-primary'>{textValue(data.plan_name, '下一次计划训练')}</Text>
        <Text className='data-card-secondary'>周{textValue(data.day_of_week)} · {exercises.length} 个动作</Text>
        {exercises.slice(0, 4).map((exercise, index) => (
          <Text className='data-card-line' key={`${textValue(exercise.id)}-${index}`}>
            {textValue(exercise.exercise_name, '未命名动作')} · {textValue(exercise.sets, '0')} 组 × {textValue(exercise.reps)}
          </Text>
        ))}
      </DataCard>
    )
  }

  if (card.type === 'workout.get_progress') {
    return (
      <DataCard title={title}>
        {Boolean(data.range_start && data.range_end) && (
          <Text className='data-card-secondary'>{textValue(data.range_start)} 至 {textValue(data.range_end)}</Text>
        )}
        <View className='data-metrics'>
          <DataMetric value={textValue(data.total_sessions, '0')} label='次训练' />
          <DataMetric value={textValue(data.total_sets, '0')} label='完成组' />
          <DataMetric value={textValue(data.total_volume_kg, '0')} label='kg 容量' />
        </View>
      </DataCard>
    )
  }

  if (card.type === 'workout.get_active_session') {
    const session = asRecord(data.session)
    return (
      <DataCard title={title}>
        <Text className='data-card-primary'>{textValue(session.plan_name, data.found ? '本次训练' : '当前没有进行中的训练')}</Text>
        {Boolean(data.found) && <Text className='data-card-secondary'>已完成 {textValue(session.total_sets, '0')} 组</Text>}
      </DataCard>
    )
  }

  if (card.type === 'workout.list_history') {
    return (
      <DataCard title={title}>
        <Text className='data-card-primary'>找到 {textValue(data.total_count ?? data.count, '0')} 次训练</Text>
        {Boolean(data.range_start && data.range_end) && (
          <Text className='data-card-secondary'>{textValue(data.range_start)} 至 {textValue(data.range_end)}</Text>
        )}
        {Boolean(data.truncated) && (
          <Text className='data-card-secondary'>仅展示最近 {textValue(data.count, '0')} 次记录</Text>
        )}
        <Text className='data-card-secondary'>详细结论见上方回答</Text>
      </DataCard>
    )
  }

  if (card.type === 'profile.get_summary') {
    return (
      <DataCard title={title}>
        <Text className='data-card-primary'>目标：{textValue(data.primary_goal, '尚未填写')}</Text>
        <Text className='data-card-secondary'>经验：{textValue(data.experience_level, '尚未填写')} · 每周 {textValue(data.training_days_per_week, '—')} 天</Text>
      </DataCard>
    )
  }

  if (card.type === 'health.get_screening_summary') {
    return (
      <DataCard title={title}>
        <Text className='data-card-primary'>已记录 {asList(data.injuries).length} 项伤病，{asList(data.chronic_conditions).length} 项慢性情况</Text>
        <Text className='data-card-secondary'>请以训练安全提示和专业医疗意见为准</Text>
      </DataCard>
    )
  }

  return (
    <DataCard title={title}>
      <Text className='data-card-secondary'>已读取最新数据，详细结论见上方回答。</Text>
    </DataCard>
  )
}


function DataCard ({ title, children }: { title: string, children: React.ReactNode }) {
  return (
    <View className='agent-data-card'>
      <Text className='data-card-title'>{title}</Text>
      {children}
    </View>
  )
}


function DataMetric ({ value, label }: { value: string, label: string }) {
  return (
    <View className='data-metric'>
      <Text className='data-metric-value'>{value}</Text>
      <Text className='data-metric-label'>{label}</Text>
    </View>
  )
}
