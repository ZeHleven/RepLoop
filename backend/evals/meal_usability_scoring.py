"""Independent benchmark rule; never imports application/solver code."""
import json
import math
from pathlib import Path

POLICY = json.loads(Path(__file__).with_name('policy.json').read_text(encoding='utf-8'))

def quality_errors(meals, reference, foods):
    errors = []
    if len(meals) != len(reference): return ['reference_topology']
    energies = []
    mains = [i for i,m in enumerate(meals) if m['meal_type'] in ('早餐','午餐','晚餐')]
    for n,(meal,original) in enumerate(zip(meals,reference)):
        if meal['meal_type'] != original['meal_type'] or len(meal['items']) != len(original['items']):
            return ['reference_topology']
        energy=0.; substantial=0
        for item,source in zip(meal['items'],original['items']):
            if item['food_id'] != source['food_id']: return ['reference_topology']
            food=foods[item['food_id']]
            oil=food['category'] in ('oil','fats','油脂') or '油' in food.get('name','')
            amount=item['amount_g']; base=source['amount_g']
            if isinstance(amount,bool) or not isinstance(amount,(float,int)) or not math.isfinite(amount):
                errors.append('invalid_amount'); continue
            lo,hi=POLICY['oil_grams' if oil else 'non_oil_grams']
            if not lo<=amount<=hi: errors.append('portion_bounds')
            if not .5*base-1e-6<=amount<=1.5*base+1e-6: errors.append('portion_drift')
            if abs(amount/(1 if oil else 5)-round(amount/(1 if oil else 5)))>1e-6: errors.append('portion_grid')
            if not oil and amount>=25: substantial+=1
            energy+=food['calories']*amount/100
        if n in mains and not substantial: errors.append('main_without_substantial_food')
        energies.append(energy)
    total=sum(energies)
    if total<=0: return sorted(set(errors+['zero_energy']))
    if len(mains) in (2,3):
        lo,hi=POLICY['main_energy_share_by_count'][str(len(mains))]
        for n in mains:
            if not lo-1e-6<=energies[n]/total<=hi+1e-6: errors.append('meal_energy_share')
    if mains and sum(e for i,e in enumerate(energies) if i not in mains)/total>.15+1e-6: errors.append('snack_energy_share')
    return sorted(set(errors))
