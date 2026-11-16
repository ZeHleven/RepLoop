import { apiRequest } from '../core/request'
import { invalidateReadCache } from '../core/read-cache'
import type {
  AgentArtifactAction,
  AgentClarificationAction,
  AgentConversationSummary,
  AgentMessage,
  AgentRunStatus,
  AgentRunSubmission
} from '../types/api'


export const agentApi = {
  submit: (
    message: string,
    clientRequestId: string,
    conversationId?: string,
    artifactAction?: AgentArtifactAction,
    clarificationAction?: AgentClarificationAction
  ) => apiRequest<AgentRunSubmission>(
    '/agent/runs',
    {
      method: 'POST',
      data: {
        message,
        client_request_id: clientRequestId,
        ...(conversationId ? { conversation_id: conversationId } : {}),
        ...(artifactAction ? { artifact_action: artifactAction } : {}),
        ...(clarificationAction ? { clarification_action: clarificationAction } : {})
      }
    }
  ),
  run: async (runId: string) => {
    const result = await apiRequest<AgentRunStatus>(`/agent/runs/${runId}`)
    // A conversation can confirm a proposal asynchronously after submission.
    if (result.status === 'completed' || result.status === 'failed') invalidateReadCache()
    return result
  },
  messages: (conversationId: string) => apiRequest<AgentMessage[]>(
    `/agent/conversations/${conversationId}/messages`,
    { query: { limit: 100 } }
  ),
  conversations: (limit = 50) => apiRequest<AgentConversationSummary[]>(
    '/agent/conversations',
    { query: { limit } }
  )
}
