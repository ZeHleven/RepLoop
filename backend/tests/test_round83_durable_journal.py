import asyncio,json
import pytest
from app.services.agent_attempt_journal import _json, journal_read_tool

@pytest.fixture(autouse=True)
def setup_db():yield

@pytest.mark.parametrize('value',[{'name':'a\x00b'},{'name':'\ud800'},None,{'list':[0,False,'中文']}])
def test_result_text_preserves_json_values_without_postgres_unsafe_characters(value):
    encoded=_json(value)
    assert json.loads(encoded)==value and '\x00' not in encoded
    encoded.encode('utf8')

@pytest.mark.asyncio
async def test_unclaimed_legacy_call_keeps_tool_semantics():
    async def call(*args,**kwargs):return args,kwargs
    wrapped=journal_read_tool('synthetic',call)
    assert await wrapped(1,x=2)==((1,),{'x':2})
