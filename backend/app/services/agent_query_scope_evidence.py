"""Executed query scope facts. No inferred intake, user intent or new reads.

Each proof belongs to one observation, never a union of unrelated queries.
Invalid metadata yields no certificate; an unknown date is not a zero date.
"""
import copy
from datetime import date, timedelta
from app.services.history_status_scope import STATUS_LABELS


def _day(value):
    if not isinstance(value, str):
        raise ValueError('date must be a string')
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('date must be canonical')
    return parsed


def nutrition_date_coverage(data):
    """Partition only an actual bounded history query; latest-N has no gaps."""
    try:
        scope, rows = data['scope'], data['days']
        if not isinstance(scope, dict) or not isinstance(rows, list):return None
        if scope.get('timezone') != 'Asia/Shanghai':return None
        as_of = _day(scope['as_of'])
        selected_meals = scope['meal_types']
        if (not isinstance(selected_meals, list)
                or any(m not in {'早餐','午餐','晚餐','加餐'} for m in selected_meals)
                or len(set(selected_meals)) != len(selected_meals)):return None
        selection = scope['selection']
        if selection == 'calendar_range':
            start, end = _day(scope['start_date']), _day(scope['end_date'])
            if not 0 <= (end-start).days < 366 or scope.get('logged_dates'):return None
            requested = {start+timedelta(days=i) for i in range((end-start).days+1)}
            source_fields = ['#/scope/start_date','#/scope/end_date','#/scope/as_of']
        elif selection == 'specific_dates':
            dates = scope['logged_dates']
            if not isinstance(dates,list) or not dates or len(dates)>366:return None
            requested = {_day(d) for d in dates}
            if len(requested)!=len(dates) or scope.get('start_date') or scope.get('end_date'):return None
            source_fields = ['#/scope/logged_dates','#/scope/as_of']
        elif selection == 'latest_logged_dates':
            if scope.get('start_date') or scope.get('end_date') or scope.get('logged_dates'):return None
            requested = None
            source_fields = ['#/scope/requested_limit','#/scope/as_of']
        else:return None
        returned = {}
        for i,row in enumerate(rows):
            if not isinstance(row,dict):return None
            day = _day(row['date'])
            if day in returned or day>as_of or requested is not None and day not in requested:return None
            meals = row['meals']
            if not isinstance(meals,list) or not meals:return None
            if any(not isinstance(m,dict) or not isinstance(m.get('meal_type'),str) for m in meals):return None
            meal_types = sorted({m['meal_type'] for m in meals})
            if selected_meals and not set(meal_types)<=set(selected_meals):return None
            returned[day] = {'date':day.isoformat(), 'record_status':'present',
                'observed_meal_types':meal_types, 'sources':[f'#/days/{i}/date',f'#/days/{i}/meals'],
                'meal_scope':'selected_meals' if selected_meals else 'all_stored_meals',
                'other_meals_record_presence':'not_queried' if selected_meals else 'no_other_stored_meals_in_this_date',
                'actual_intake_completeness':'unknown'}
        if type(scope.get('returned_dates')) is not int or scope['returned_dates']!=len(returned):return None
        checked = sorted(d for d in requested if d<=as_of) if requested is not None else sorted(returned)
        absent = [d.isoformat() for d in checked if d not in returned]
        future = sorted(d.isoformat() for d in requested if d>as_of) if requested is not None else []
        return {'version':1,'selection':selection,'as_of':as_of.isoformat(),
            'selected_meal_types':copy.deepcopy(selected_meals),'sources':source_fields+['#/scope/meal_types','#/scope/returned_dates'],
            'queried_dates':[d.isoformat() for d in checked],
            'recorded_dates':[returned[d] for d in sorted(returned)],
            'queried_empty_dates':absent, 'future_excluded_dates':future,
            'future_record_presence':'not_established_by_this_query',
            'outside_query_record_presence':'unknown',
            'missing_calendar_dates_are_empty':requested is not None,
            'empty_record_is_zero_actual_intake':False,
            'actual_intake_completeness':'unknown'}
    except (KeyError,ValueError,TypeError,OverflowError):
        return None


def workout_count_coverage(tool, data):
    """Describe which statuses and counts the actual observation establishes."""
    try:
        if tool=='workout.list_history':
            statuses=data['status_filter'];start=data['range_start'];end=data['range_end']
            as_of=data['as_of'];timezone=data['timezone'];total=data['total_count'];rows=data['sessions']
            if (not isinstance(rows,list) or type(data.get('count')) is not int or data['count']!=len(rows)
                    or type(total) is not int or total<len(rows)
                    or type(data.get('truncated')) is not bool or data['truncated']!=(total>len(rows))):return None
            complete=not data['truncated'];source='#/status_filter'
        elif tool=='workout.get_progress' and data.get('aggregation')=='all_matching_sessions':
            scope=data['count_scope'];statuses=scope['statuses'];start=scope['start_date'];end=scope['end_date']
            as_of=scope['as_of'];timezone=scope['timezone'];total=data['total_sessions']
            rows=None;complete=False;source='#/count_scope/statuses'
        else:return None
        if (not isinstance(statuses,list) or not statuses or any(s not in STATUS_LABELS for s in statuses)
                or len(set(statuses))!=len(statuses) or type(total) is not int or total<0
                or timezone!='Asia/Shanghai' or _day(start)>min(_day(end),_day(as_of))):return None
        by_status=None
        if rows is not None:
            if any(not isinstance(r,dict) or r.get('status') not in statuses
                   or not _day(start)<=_day(r['trained_at'])<=min(_day(end),_day(as_of)) for r in rows):return None
            ids=[r['id'] for r in rows]
            if any(not isinstance(v,str) or not v for v in ids) or len(ids)!=len(set(ids)):return None
        if len(statuses)==1:
            by_status={statuses[0]:total}
        elif complete:
            by_status={s:sum(r['status']==s for r in rows) for s in statuses}
        return {'version':1,'start_date':start,'end_date':min(_day(end),_day(as_of)).isoformat(),
            'queried_statuses':copy.deepcopy(statuses),'status_source':source,
            'not_queried_statuses':[s for s in STATUS_LABELS if s not in statuses],
            'matching_record_count':total,'exact_status_counts':by_status,
            'detail_rows_complete':complete,'current_plan_link_required':False,
            'unit':'session_rows_not_unique_days','not_queried_status_presence':'unknown',
            'actual_load_not_proven_by_zero_sets':True}
    except (KeyError,TypeError,ValueError,OverflowError):return None
