"""R48 retained peak estimate; integer 1e-8 CNY units, not an invoice."""
import json


def usage_cost_units(row):
    if not row.get('complete') or not row.get('lifecycle_complete') or row.get('status')!=200:raise ValueError('provider_completion_unknown')
    u=(row.get('response') or {}).get('usage')
    if not isinstance(u,dict):raise ValueError('usage_missing')
    prompt=u.get('prompt_tokens');completion=u.get('completion_tokens')
    hit=u.get('prompt_cache_hit_tokens',(u.get('prompt_tokens_details') or {}).get('cached_tokens',0))
    if any(type(v) is not int or v<0 for v in [prompt,completion,hit]) or hit>prompt:raise ValueError('usage_invalid')
    if 'total_tokens' in u and u['total_tokens']!=prompt+completion:raise ValueError('usage_total_mismatch')
    return hit*4+(prompt-hit)*200+completion*800


def reconcile_costs(budget,root):
    if 'max_estimated_cny' not in budget.limits:return
    seen=set()
    for file in sorted((root/'runs').glob('*.json')):
        rec=json.loads(file.read_text(encoding='utf-8'))
        for row in rec.get('provider',[]):
            index=row.get('index')
            if index in seen:budget.stop('duplicate_provider_identity');return
            seen.add(index);budget.record_provider_cost(row)
    if seen!=set(range(1,budget.state['requests']+1)):budget.stop('provider_cost_missing_attempt')
