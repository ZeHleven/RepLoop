"""Bounded recovery for the three deterministic read reports only."""
import asyncio
import copy
import json
import time
from contextlib import nullcontext
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError
from app.config import settings


ERROR_REPLIES = {
    'read_tool_argument_invalid': '查询条件未能通过校验，本次没有取得查询结果。请重新说明日期范围和筛选条件。',
    'read_tool_timeout': '查询记录超时，本次未能取得查询结果。请稍后再试。',
    'read_tool_permission_denied': '暂时无法读取这项记录，请确认访问权限后重试。',
    'read_tool_execution_failed': '读取记录时发生异常，本次未能完成查询。请稍后重试。',
    'read_tool_budget_exhausted': '本次查询已达到执行次数上限，尚未取得查询结果。请稍后重试。',
}


def error_code(exc: Exception) -> str:
    if isinstance(exc, ValidationError): return 'read_tool_argument_invalid'
    if isinstance(exc, TimeoutError): return 'read_tool_timeout'
    if isinstance(exc, PermissionError): return 'read_tool_permission_denied'
    return 'read_tool_execution_failed'


async def invoke_report_read(tool, arguments: dict, *, db=None, propagate=(), max_attempts=None):
    """Keep each attempted call, including errors; never retry writes or cancellation.

    The caller validates the read report/tool pair. Runtime commits its task
    checkpoint before entering here; rollback therefore discards read work only.
    It must refresh its ORM objects when transaction_reset is returned.
    """
    messages = []
    transaction_reset = False
    code = 'read_tool_budget_exhausted'
    limit = max(0, min(2, settings.AGENT_MAX_TOOL_CALLS))
    if max_attempts is not None:
        limit = max(0, min(limit, max_attempts))
    for attempt in range(limit):
        call_id = 'report-' + uuid4().hex
        original = copy.deepcopy(arguments)
        messages.append(AIMessage(content='', additional_kwargs={'controller_generated': True}, tool_calls=[{
            'name': tool.name, 'args': original, 'id': call_id,
        }]))
        started = time.perf_counter()
        cause = None
        try:
            async with db.begin_nested() if db is not None else nullcontext():
                try:
                    async with asyncio.timeout(settings.AGENT_QUERY_READ_TIMEOUT_SECONDS):
                        data = await tool.ainvoke(copy.deepcopy(original))
                except Exception as exc:
                    # A cancelled SQL request may also fail savepoint cleanup.
                    cause = exc
                    raise
        except Exception as exc:
            if isinstance(cause if cause is not None else exc, propagate):
                raise cause if cause is not None else exc
            code = error_code(cause if cause is not None else exc)
            if db is not None:
                await db.rollback()
                transaction_reset = True
            messages.append(ToolMessage(
                content=json.dumps({'error_code': code}), name=tool.name,
                tool_call_id=call_id, status='error',
                additional_kwargs={'duration_ms': round((time.perf_counter()-started)*1000)},
            ))
            if code != 'read_tool_timeout' or attempt + 1 >= limit:
                break
            await asyncio.sleep(.05)
        else:
            messages.append(ToolMessage(
                content=json.dumps(data, ensure_ascii=False, default=str),
                name=tool.name, tool_call_id=call_id,
                additional_kwargs={'duration_ms': round((time.perf_counter()-started)*1000)},
            ))
            return data, messages, transaction_reset, None
    return None, messages, transaction_reset, code
