"""Validate a sourced date edit before model-expanded dates drive a read."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.services import agent_intent_model as model, agent_query_reports as reports
from app.services.business_clock import BUSINESS_DATE

DATA=json.loads((Path(__file__).parent/'fixtures/history_date_revision_20261108.json').read_text(encoding='utf-8'))


def route(message=DATA['message'], query=None):
    data=deepcopy(DATA['route'])
    if query is not None:data['normalized_request']=query
    data['decision_action']=data['artifact_action']=None
    data['task_update']['trigger']=message
    data['task_update']['requirements']=[{'key':'date_scope','quote':message,'remove':False}]
    return model.IntentRouteDecision.model_validate(data)


@pytest.fixture(autouse=True)
def fixed_business_date():
    token=BUSINESS_DATE.set(date(2026,11,9))
    yield
    BUSINESS_DATE.reset(token)


@pytest.mark.asyncio
async def test_recorded_wrong_day_requires_repair_before_acceptance(monkeypatch):
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic-replay-only')
    wrong=route(); correct=route(query=wrong.normalized_request.replace('2026-11-08','2026-11-07'))
    invoke=AsyncMock(side_effect=[wrong,correct]);monkeypatch.setattr(model,'_invoke_model_route',invoke)
    out=await model.resolve_intent_with_fallback(DATA['message'],context_messages=deepcopy(DATA['context']),use_model=True)
    assert not out.understanding_failed and out.attempt_count==invoke.await_count==2
    assert out.error_category=='history_query_date_scope_mismatch'
    assert '2026-11-07' in out.resolution.resolved_query
    assert invoke.await_args_list[1].kwargs['repair_error']=='history_query_date_scope_mismatch'


@pytest.mark.asyncio
async def test_repeated_wrong_date_does_not_produce_an_executable_success(monkeypatch):
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic-replay-only')
    invoke=AsyncMock(return_value=route());monkeypatch.setattr(model,'_invoke_model_route',invoke)
    out=await model.resolve_intent_with_fallback(DATA['message'],context_messages=deepcopy(DATA['context']),use_model=True)
    assert out.understanding_failed and out.attempt_count==2
    assert out.error_category=='history_query_date_scope_mismatch'


@pytest.mark.parametrize('today,message,expected',[
    ('2026-11-09','日期改为今天，只列已完成场次。',('2026-11-09','2026-11-09')),
    ('2026-11-09','日期改为昨天，只列已完成场次。',('2026-11-08','2026-11-08')),
    ('2026-11-09',DATA['message'],('2026-11-07','2026-11-07')),
    ('2026-11-10',DATA['message'],('2026-11-08','2026-11-08')),
    ('2026-01-01',DATA['message'],('2025-12-30','2025-12-30')),
    ('2024-03-01',DATA['message'],('2024-02-28','2024-02-28')),
    ('2026-03-01','范围改成最近3天，只列已完成场次。',('2026-02-27','2026-03-01')),
    ('2026-11-09','范围改成过去两天，只列已完成场次。',('2026-11-08','2026-11-09')),
    ('2026-11-09','查询范围改成2026-10-01至2026-10-03，只列已完成场次。',('2026-10-01','2026-10-03')),
])
def test_known_source_scope_cannot_be_shifted_or_widened(today,message,expected):
    token=BUSINESS_DATE.set(date.fromisoformat(today))
    try:
        good=model._route_to_resolution(route(message,f'查询{expected[0]}至{expected[1]}的已完成训练。'))
        bad=model._route_to_resolution(route(message,'查询2025-01-01至2025-01-02的已完成训练。'))
        assert not reports.calendar_revision_date_conflict(good,DATA['context'],message)
        assert reports.calendar_revision_date_conflict(bad,DATA['context'],message)
    finally:BUSINESS_DATE.reset(token)


@pytest.mark.parametrize('kind',['no_audit','failed_audit','wrong_task','write_effect','mixed_write','endpoint','complex_scope','no_absolute_expansion'])
def test_scope_validation_does_not_invent_authority_or_unknown_bounds(kind):
    context=deepcopy(DATA['context']);message=DATA['message'];r=route()
    if kind=='no_audit':context[-1].pop('query_context')
    elif kind=='failed_audit':context[-1]['query_context']['successful_query']=False
    elif kind=='wrong_task':r.task_update.task_id='invented'
    elif kind=='write_effect':
        resolution=model._route_to_resolution(r).model_copy(update={'requested_effect':'update'})
        assert not reports.calendar_revision_date_conflict(resolution,context,message);return
    elif kind=='mixed_write':message+='并删除昨天记录。';r=route(message)
    elif kind=='endpoint':message='结束日期改为2026-11-08';r=route(message,'查询2026-11-01至2026-11-08已完成训练。')
    elif kind=='complex_scope':message='范围改为上周，只列已完成场次。';r=route(message)
    else:r=route(query='查询前天已完成训练。')
    # A verified supported edit must expand to explicit dates. Other cases
    # stay with their existing validation; this check grants no new access.
    assert reports.calendar_revision_date_conflict(model._route_to_resolution(r),context,message)==(kind=='no_absolute_expansion')
