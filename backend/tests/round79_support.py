import copy,json
from pathlib import Path
DATA=json.loads((Path(__file__).parent/'fixtures/round79/hn07.json').read_text(encoding='utf8'))
MESSAGE=DATA['rows'][0]['message']

def comparison(**changes):
    return dict(metric='protein_g',left_date='2026-09-27',right_date='2026-10-02',left_date_quote='2026年9月27日',right_date_quote='10月2日',comparison_quote='再比较蛋白质差多少')|changes

def plan(**changes):
    original=next(c['payload'] for c in DATA['rows'][0]['calls'] if c['name']=='submit_nutrition_history_query')
    return copy.deepcopy(original)|dict(supported=True,date_comparisons=[comparison()])|changes

def observed():
    rows=[{'date':r['date'],**{'total_'+k:v for k,v in r.items() if k!='date'}} for r in DATA['case']['query_gold'][0]['rows']]
    return {'scope':dict(as_of='2026-10-09',selection='specific_dates',logged_dates=['2026-09-27','2026-10-02'],start_date=None,end_date=None,meal_types=[],returned_dates=len(rows),first_logged_date='2026-09-27',last_logged_date='2026-10-02'),'days':rows}
