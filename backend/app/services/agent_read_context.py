"""Transient response contracts from tools actually executed in this run."""
import json
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage
from app.services.agent_symptom_context import symptom_response_context
from app.services.agent_training_constraints import training_adjustment_context


class ReadResponseContext(AgentMiddleware):
    def __init__(self, *, query, resolution, journal, task_state=None):
        self.query = query
        self.resolution = resolution
        self.journal = journal
        self.task_state = task_state

    def context(self):
        # Do not use request.messages: history and controller messages are not
        # observations made by this run. Journal retains the unprojected data.
        evidence = self.journal.evidence_messages
        from app.services.agent_nutrition_goals import nutrition_goal_context
        return (symptom_response_context(self.query, self.resolution, evidence)
                + training_adjustment_context(self.query, self.resolution,
                                              evidence, self.task_state)
                + nutrition_goal_context(self.query, self.resolution))

    def pending_read_context(self, tools):
        """Tell the existing executor what is missing; never choose arguments.

        Only this run's journal can satisfy an obligation. This context is not
        included in final-only repairs, which have no callable tools.
        """
        from app.services.agent_intent import required_read_tools
        resolution = self.resolution
        if (not tools or not resolution or resolution.requested_effect != 'read'
                or resolution.request_kind not in {'query', 'assessment'}
                or resolution.risk_level not in {'low', 'medium'}
                or resolution.change_requests or resolution.clarification_required
                or (resolution.read_scope_quotes and not all(
                    q and q in self.query for q in resolution.read_scope_quotes))):
            return ''
        available = {tool.name.replace('_', '.', 1) for tool in tools
                     if getattr(tool, 'name', None)}
        required = required_read_tools(resolution)
        observations = self.journal.evidence_messages
        attempted = {m.name.replace('_', '.', 1) for m in observations if m.name}
        successful = {m.name.replace('_', '.', 1) for m in observations
                      if m.name and m.status == 'success'
                      and m.additional_kwargs.get('executed') is not False}
        pending = [t for t in required if t in available and t not in attempted]
        failed = [t for t in required if t in attempted and t not in successful]
        if not pending and not failed:
            return ''
        return ('\n本轮必需读取进度：' + json.dumps({
            'pending_required_reads': pending, 'failed_required_reads': failed,
        }, ensure_ascii=False) + '\n'
            '请在现有预算内先取得尚未尝试的必需证据，再完整回答原请求；其他摘要不能代替缺失项。'
            '参数依据用户原文及工具定义，不得猜测日期、状态或扩大筛选；范围不明确时澄清，不假称已完成查询。'
            '不要重复已成功的读取，也不要仅因清单而重试失败读取；失败和缺失必须如实说明。'
            '历史记录不证明当前恢复或实际器械握持，也不替代当前症状的就医判断。')

    async def awrap_model_call(self, request, handler):
        context = self.context() + self.pending_read_context(request.tools)
        if not context:
            return await handler(request)
        system = request.system_message or SystemMessage(content='')
        content = system.content
        content = content + context if isinstance(content, str) else [
            *content, {'type': 'text', 'text': context}]
        return await handler(request.override(system_message=system.model_copy(
            update={'content': content}, deep=True)))
