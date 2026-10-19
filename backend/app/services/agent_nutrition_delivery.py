"""Source-bound food contributions and a finite, explicit action budget.

Own only a fully consumed food-detail request. Unknown goals stay on the
semantic path. This renderer does not certify intake or personal adequacy.
"""
import json
import re
from datetime import date, timedelta
from app.schemas.agent_trace import AgentNutritionDeliveryTrace
from app.services.business_clock import business_today
from app.services.agent_nutrition_obligations import advice_limit
from app.services.agent_nutrition_evidence import _complete_day, _dated_records
from app.services.agent_training_delivery import _observations, _label, _number


def detail_request(query, today):
    from app.services.nutrition_query_scope import _SCOPE, _explicit_scope
    if not isinstance(query,str) or len(query)>1200:return None
    clauses=[x.strip() for x in re.split(r'[，,。；;！？!?\n]',query) if x.strip()]
    goal=re.compile(rf'(?:请|帮我)?(?:只)?(?:核对|查询|查看|列出)(?P<scope>{_SCOPE}|今天|今日|昨天|昨日|前天)'
        r'(?:的)?(?P<meal>早餐|午餐|晚餐|加餐)(?:的)?(?:各(?:种|项)?食物|食品|食物)(?:的)?蛋白质(?:分项|明细)?')
    advice=re.compile(r'(?:再|并|然后)?(?:请)?(?:给|给出)(?:我)?(?:最多|不超过|仅|只)?(?:一|1)条(?:一般|具体)?(?:搭配)?建议')
    none=re.compile(r'(?:不要|不用|无需|别)(?:给)?(?:任何|额外)?(?:搭配)?建议')
    annotations={
        '若记录不足以判断全天是否达标','如果记录不足以判断全天是否达标','请说明','说明局限',
        '不把其他日期纳入','不评价全天是否够','不判断全天是否达标','只给建议','不要保存','不保存',
    }
    selected=[];advice_count=0
    for clause in clauses:
        match=goal.fullmatch(clause)
        if match:selected.append(match)
        elif advice.fullmatch(clause) or none.fullmatch(clause):advice_count+=1
        elif clause not in annotations:return None
    if len(selected)!=1 or advice_count>1:return None
    limit=advice_limit(query)
    if advice_count and (limit['status']!='explicit' or limit['max_actions'] not in {0,1}):return None
    m=selected[0];raw=m['scope']
    if raw in {'今天','今日','昨天','昨日','前天'}:
        days=[(today-timedelta(days={'今天':0,'今日':0,'昨天':1,'昨日':1,'前天':2}[raw])).isoformat()]
    else:
        try:
            scope=_explicit_scope(raw,today)
            if not scope:return None
            if scope.get('logged_dates'):days=sorted(set(scope['logged_dates']))
            else:
                first,last=date.fromisoformat(scope['start_date']),date.fromisoformat(scope['end_date'])
                if not 0<=(last-first).days<31:return None
                days=[(first+timedelta(days=i)).isoformat() for i in range((last-first).days+1)]
        except (ValueError,KeyError,TypeError):return None
    if not days or len(days)>31 or max(days)>today.isoformat():return None
    return {'dates':days,'meal':m['meal'],'max_actions':limit['max_actions'] if advice_count else 0,
            'scope_quote':m.group(),'advice_quotes':limit['source_quotes']}


def _history_days(data,args,request,today):
    """Validate the exact selected date/meal coverage, including empty dates."""
    from app.schemas.nutrition_history import NutritionHistoryArguments
    try:
        actual=NutritionHistoryArguments.model_validate(args)
        scope=data['scope'];days=data['days'];dates=[d['date'] for d in days]
        if (scope['as_of']!=today or scope['timezone']!='Asia/Shanghai'
            or scope['start_date']!=(actual.start_date or None) or scope['end_date']!=(actual.end_date or None)
            or scope['logged_dates']!=actual.logged_dates or scope['meal_types']!=actual.meal_types
            or scope['requested_limit']!=(None if actual.start_date or actual.logged_dates else actual.days)
            or data['count']!=len(days) or scope['returned_dates']!=len(days) or len(set(dates))!=len(dates)
            or scope['first_logged_date']!=min(dates,default=None) or scope['last_logged_date']!=max(dates,default=None)
            or actual.meal_types not in ([],[request['meal']])):return None
        expected=request['dates']
        if actual.logged_dates:
            if set(actual.logged_dates)!=set(expected):return None
        elif actual.start_date and actual.end_date:
            first,last=date.fromisoformat(actual.start_date),date.fromisoformat(actual.end_date)
            if not 0<=(last-first).days<31:return None
            covered={(first+timedelta(days=i)).isoformat() for i in range((last-first).days+1)}
            if covered!=set(expected):return None
        else:return None
        if any(d not in expected for d in dates):return None
        if any(not _complete_day(d) for d in days):return None
        if actual.meal_types and any(m['meal_type']!=request['meal'] for d in days for m in d['meals']):return None
        return {d['date']:d for d in days}
    except (ValueError,TypeError,KeyError,AttributeError):return None


def _render(request,records,today):
    for name in ('nutrition.list_history','nutrition.get_today','nutrition.get_recent_context'):
        if name not in records:continue
        data,args,cid,fingerprint=records[name]
        limits=data.get('evidence_limits',{})
        if (limits.get('intake_basis')!='logged_items_only' or limits.get('whole_day_completeness')!='unknown'
            or limits.get('unlogged_intake_is_zero') is not False):continue
        try:
            days=(_history_days(data,args,request,today) if name=='nutrition.list_history'
                  else _dated_records(data,name,args,today))
        except (ValueError,TypeError,KeyError,AttributeError):continue
        if days is None:continue
        # A latest-logged window cannot prove absent historical dates.
        if name!='nutrition.list_history' and not set(request['dates'])<=days.keys():continue
        lines=['核对范围：'+'、'.join(request['dates'])+'，'+request['meal']+'；以下均为已保存记录。']
        facts=[];empty=[]
        for day in request['dates']:
            meals=[m for m in days.get(day,{}).get('meals',[]) if m['meal_type']==request['meal']]
            if not meals:
                lines.append(f'{day}：查询范围内未找到{request["meal"]}记录；不等于当天没有进食。');empty.append(day);continue
            lines.append(f'{day} {request["meal"]}：')
            total=0
            for meal in meals:
                for item in meal['items']:
                    # Labels remain quoted data; control text never becomes instructions.
                    label=_label(item['food_name']);amount=item['amount_g'];protein=item['protein_g']
                    if not label or not _number(amount) or not _number(protein):return None
                    lines.append(f'- 食物“{label}”，记录量{amount:g}g，记录蛋白质{protein:g}g。')
                    total+=protein
                    facts.append({'date':day,'meal_type':request['meal'],'meal_id':meal['id'],'item_id':item['id'],
                                  'food_name':item['food_name'],'amount_g':amount,'protein_g':protein})
            lines.append(f'{day}该餐已记录蛋白质合计：{round(total,1):g}g。')
        lines.append('只汇总上述日期和餐次，其他日期或餐次没有计入这些数字。保存记录不能证明实际摄入已记齐；没有以这些记录判断实际全天或个人蛋白质是否达标。')
        actions=[]
        if request['max_actions']==1:
            actions=[{'kind':'add_one_vegetable','scope':'next_matching_meal','precondition':'fits_diet_restrictions_and_previously_tolerated'}]
            lines.append('一条搭配建议：下次这餐可搭配一份符合你饮食限制、且平时能耐受的蔬菜，例如青菜。这是一般搭配建议，不表示已判断你缺少某种营养或保证达到个人目标。')
        lines.append('本次仅核对和提供建议，没有保存或修改记录。')
        try:
            receipt=AgentNutritionDeliveryTrace(source_call_ids=[cid],source_fingerprints=[fingerprint],
                requested_dates=request['dates'],meal_type=request['meal'],scope_quote=request['scope_quote'],
                advice_quotes=request['advice_quotes'],max_actions=request['max_actions'],actions=actions,
                item_facts=facts,empty_dates=empty)
        except (ValueError,TypeError):return None
        return '\n'.join(lines),receipt
    return None


def ground_nutrition_delivery(reply,trace,*,result,query,resolution):
    if (result.get('read_error_code') or trace.terminal_action!='answer' or trace.execution_mode!='direct'
        or trace.risk_level=='high' or not trace.request_context or trace.request_context.as_of!=business_today().isoformat()
        or not trace.evidence_contract or trace.evidence_contract.status!='complete'
        or resolution.request_kind not in {'query','assessment'} or resolution.requested_effect!='read'
        or resolution.change_requests or resolution.clarification_required):return reply,trace
    request=detail_request(query,business_today())
    if request is None:return reply,trace
    observed_result={**result,'messages':[m for m in result.get('messages',[])
        if getattr(m,'additional_kwargs',{}).get('executed') is not False]}
    rendered=_render(request,_observations(trace,observed_result),business_today().isoformat())
    if rendered is None:return reply,trace
    text,receipt=rendered
    return text,trace.model_copy(update={'nutrition_delivery':receipt})
