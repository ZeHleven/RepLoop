"""Archived semantic decisions exercise real rule/model consistency boundaries."""
import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from app.config import settings
from app.services import agent_intent_model
from app.services.agent_intent import resolve_intent, route_tools
from app.services.agent_intent_model import IntentRouteDecision, resolve_intent_with_fallback
from app.services.business_clock import BUSINESS_DATE

DATA=json.loads((Path(__file__).parent/'fixtures/round48/archived-routes.json').read_text(encoding='utf8'))['rows']

READS=[
 '分析前天的三餐搭配是否合理，给些改进思路。',
 '帮我评估昨天三餐的营养搭配。',
 '我的午饭记录一共有几条？',
 '列出前天已经记录的热量。',
 '只查询已记录饮食，不做变更。',
 '给我几条减少训练负担的调整建议。',
 '请结合档案提供具体调整规划。',
 '说说日常搭配三餐的一般原则。',
 '介绍健康三餐搭配的通用思路。',
]
WRITES=[
 '帮我记录今天午餐米饭200克。',
 '查看已记录饮食，然后记录晚餐米饭200克。',
 '列出已记录的热量，并删除昨天午餐。',
 '提供调整建议，再把训练天数改成2天。',
 '先评估昨天三餐搭配，再安排今天三餐。',
 '说说搭配三餐的一般原则，并给我制定今天三餐食谱。',
 '给我安排今天三餐。',
 '把体重记录为65公斤。',
 '不得不修改当前训练计划。',
 '不是不修改当前训练计划。',
 '把这份饮食方案保存下来。',
 '请修改规划。',
 '请调整规划。',
 '请更新规划。',
 '给些调整建议，并修改现有规划。',
]

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    token=BUSINESS_DATE.set(date(2026,10,9))
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','offline-r48')
    monkeypatch.setattr(agent_intent_model,'structured_chat_completion',AsyncMock(side_effect=AssertionError('No external model allowed')))
    yield
    BUSINESS_DATE.reset(token)

@pytest.mark.parametrize('row',DATA,ids=lambda r:r['id'])
@pytest.mark.asyncio
async def test_archived_model_reads_are_accepted(row,monkeypatch):
    assert row['routes']
    for raw in row['routes']:
        route=IntentRouteDecision.model_validate(raw)
        monkeypatch.setattr(agent_intent_model,'_invoke_model_route',AsyncMock(return_value=route))
        out=await resolve_intent_with_fallback(row['message'],use_model=True)
        assert not out.understanding_failed,(row['id'],out.error_category)
        assert out.source=='model' and out.attempt_count==1
        assert out.resolution.requested_effect=='read' and not out.resolution.change_requests

@pytest.mark.parametrize('message',READS)
def test_clause_role_read_variants(message):
    assert resolve_intent(message).request_kind in ('query','assessment')

@pytest.mark.parametrize('message',WRITES)
@pytest.mark.asyncio
async def test_read_route_cannot_erase_positive_write_or_generation(message,monkeypatch):
    raw=IntentRouteDecision(intent_domain='general',request_kind='query',requested_effect='read',requested_output='answer',read_targets=[],decision_action=None,artifact_action=None,normalized_request=message,risk_level='low',confidence=.99)
    monkeypatch.setattr(agent_intent_model,'_invoke_model_route',AsyncMock(return_value=raw))
    out=await resolve_intent_with_fallback(message,use_model=True)
    assert out.understanding_failed
    assert not route_tools(out.resolution)
