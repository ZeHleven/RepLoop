"""Per-observation food contributions, never nutrient estimates from names.

Values stay attached to a date and exact food identity/version. The ledger is
evidence for generation and repair, not a certification of the final answer.
"""
from decimal import Decimal
import math

METRICS = ('calories', 'protein_g', 'carbs_g', 'fat_g')


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def day_contributions(day, path, selected_meals):
    if not isinstance(day, dict) or not isinstance(day.get('date'), str):
        return None
    meals = day.get('meals')
    if not isinstance(meals, list):
        return None
    groups = {}; all_items = []; meal_ids = set(); item_ids = set()
    for mi, meal in enumerate(meals):
        if (not isinstance(meal, dict) or not isinstance(meal.get('items'), list)
                or meal.get('meal_type') is not None and not isinstance(meal['meal_type'], str)):
            return None
        if meal.get('id') is not None:
            if str(meal['id']) in meal_ids:
                return None  # Duplicate observations must not be summed twice.
            meal_ids.add(str(meal['id']))
        for ii, item in enumerate(meal['items']):
            if not isinstance(item, dict):
                return None
            if item.get('id') is not None:
                if str(item['id']) in item_ids:
                    return None
                item_ids.add(str(item['id']))
            identity = {k:item.get(k) for k in ('food_id', 'custom_food_id', 'custom_food_version', 'food_name')}
            # Missing identities may be grouped only by their exact display
            # name; different versions/IDs are never merged by spelling.
            key = tuple(str(identity[k]) if identity[k] is not None else None for k in identity)
            group = groups.setdefault(key, {'identity':identity, 'rows':[]})
            item_path = f'{path}/meals/{mi}/items/{ii}'
            row = {'item':item, 'path':item_path, 'meal_type':meal.get('meal_type')}
            group['rows'].append(row); all_items.append(row)
    foods = []
    for group in groups.values():
        values = {}; sources = {}; missing = []
        for metric in (*METRICS, 'amount_g'):
            rows = group['rows']
            if all(_number(row['item'].get(metric)) for row in rows):
                values[metric] = float(sum((Decimal(str(row['item'][metric])) for row in rows), Decimal(0)))
                sources[metric] = [row['path']+'/'+metric for row in rows]
            else:
                missing.append(metric)
        foods.append({'identity':group['identity'], 'values':values, 'sources':sources,
                      'missing_metrics':missing, 'item_count':len(group['rows']),
                      'meal_types':list(dict.fromkeys(row['meal_type'] for row in group['rows'])),
                      'identity_sources':[row['path'] for row in group['rows']]})
    reconciliation = {}
    for metric in METRICS:
        observed = day.get('total_'+metric)
        complete = bool(all_items) and all(_number(row['item'].get(metric)) for row in all_items)
        summed = float(sum((Decimal(str(row['item'][metric])) for row in all_items), Decimal(0))) if complete else None
        reconciliation[metric] = {'item_sum':summed, 'reported_total':observed if _number(observed) else None,
            'matches':math.isclose(summed, observed, abs_tol=1e-6, rel_tol=1e-9) if complete and _number(observed) else None,
            'reported_source':path+'/total_'+metric}
    return {'date':day['date'], 'date_source':path+'/date', 'foods':foods,
            'basis':'selected_meal_records' if selected_meals else 'returned_meal_records',
            'selected_meal_types':list(selected_meals), 'reconciliation':reconciliation,
            'complete_intake_certified':False, 'cross_observation_addition_allowed':False,
            'exclusive_nutrient_source_requires_all_other_contributions_zero':True}
