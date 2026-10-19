import copy,json
from pathlib import Path
DATA=json.loads((Path(__file__).parent/'fixtures/round80/hm02.json').read_text(encoding='utf8'))
MESSAGE=DATA['case']['turns'][0]

def payload(repeat,turn,name):
    return copy.deepcopy(next(c['payload'] for c in DATA['rows'][repeat-1]['calls'] if c['turn']==turn and c['name']==name))

def bad_route(message=MESSAGE):
    raw=payload(2,0,'submit_semantic_route')
    if message!=MESSAGE:raw.update(normalized_request=message,task_update={'action':'new','task_id':'','trigger':message,'requirements':[]})
    return raw

def partial():return payload(1,0,'submit_domain_changes')
