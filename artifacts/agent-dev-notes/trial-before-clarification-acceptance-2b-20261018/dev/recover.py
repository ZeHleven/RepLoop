"""Actual runner adapter. No score, raw, slot, API or worker writes."""
from session_recovery import recover_session
from sql_observer import SqlObserver
from evals.postgres_test_fixtures import query_eval_database_url

async def recover_started(budget,registry):
    original=budget.path.read_bytes()
    contract=registry.contract(original)
    path,result=await recover_session(registry.root,budget.path,registry.root/'recovery-reports',contract,
                                      SqlObserver(query_eval_database_url()),freeze=registry.header['freeze'],limits=registry.header['limits'])
    if budget.path.read_bytes()!=original:raise RuntimeError('Read-only recovery changed budget')
    return path,result
