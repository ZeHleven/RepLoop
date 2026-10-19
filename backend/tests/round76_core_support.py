import copy,json
from pathlib import Path
CORE=json.loads((Path(__file__).parent/'fixtures/round76/core-archived.json').read_text(encoding='utf8'))

def archived_context(id):
    row=CORE[id];turn=1 if id.startswith('HN06') else 5 if id.startswith('HN11') else 0
    context=[]
    if turn:
        b=row['turns'][turn-1]['body']
        # Exactly the persisted conditions used by runtime._load_history.
        assert b['status']=='completed' and b['primary_intent']=='nutrition_history_query'
        assert b['request_kind']=='query' and b['requested_effect']=='read' and b['risk_level']=='low'
        assert b['tool_allowlist']==['nutrition.list_history'] and not b['clarification_required']
        assert b['execution_trace']['evidence_contract']['status']=='complete'
        assert any(t['turn']==turn-1 and t['status']=='success' and t['tool']=='nutrition.list_history' and isinstance(t['result'].get('count'),int) for t in row['tool_attempts'])
        state=b['execution_trace']['task_state']
        context=[{'role':'task_state','content':json.dumps(state,ensure_ascii=False)},
                 {'role':'user','content':row['turns'][turn-1]['message']},
                 {'role':'assistant','content':b['reply'],'query_context':{
                     **{key:b[key] for key in ('primary_intent','resolved_query','request_kind','requested_effect','risk_level')},
                     'source_run_id':b['id'],'successful_query':True}}]
    routes=[json.loads(p['response']['choices'][0]['message']['tool_calls'][0]['function']['arguments']) for p in row['provider'] if p['turn']==turn]
    return row['turns'][turn]['message'],copy.deepcopy(context),routes
