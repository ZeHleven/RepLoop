from uuid import uuid4
import pytest
from sqlalchemy import delete
from app.models.food import Food
from app.services.food_catalog_v1 import catalog_entries
from test_nutrition_library_energy import auth, custom


def test_frozen_catalogue_scope_and_raw_cooked_sources():
    entries = catalog_entries()
    new = [e for e in entries if e['new']]
    assert len(new) == 59 and len(entries) == 80
    by_key = {e['key']: e for e in new}
    assert not {'N07','N28','N29','N30','N35','N36','N63'} & by_key.keys()
    for raw, cooked in [('N65','N09'),('N66','N10')]:
        assert by_key[raw]['fields']['name_zh'] != by_key[cooked]['fields']['name_zh']
        assert by_key[raw]['fields']['source_reference'] != by_key[cooked]['fields']['source_reference']
    assert by_key['N15']['fields']['source_info']['license_url']


@pytest.mark.asyncio
async def test_library_category_alias_pagination_and_private_isolation(client, db_session):
    headers, other = await auth(client), await auth(client)
    marker = str(uuid4())
    foods = [Food(id=str(uuid4()), name_zh=f'{marker}土豆{i}', category='碳水',
                  browse_category='主食薯类', browse_aliases=[f'{marker}马铃薯'],
                  calories_per_100g=80, protein_g=2, carbs_g=18, fat_g=0,
                  is_active=True) for i in range(15)]
    db_session.add_all(foods)
    await db_session.commit()
    body=custom(); body['name']=marker+'私人食物'
    private=(await client.post('/api/v1/foods/custom',headers=headers,json=body)).json()
    pages=[]
    for offset in (0,7,14):
        response=await client.get('/api/v1/foods/library',headers=headers,params={'q':marker,'offset':offset,'limit':7})
        assert response.status_code==200,response.text
        pages+=response.json()
    assert len(pages)==16 and len({r['id'] for r in pages})==16
    assert pages[0]['id']==private['id']
    filtered=(await client.get('/api/v1/foods/library',headers=headers,params={'q':marker+'马铃薯','browse_category':'主食薯类','limit':50})).json()
    assert len(filtered)==15 and all(r['source']=='standard' for r in filtered)
    other_rows=(await client.get('/api/v1/foods/library',headers=other,params={'q':marker,'limit':50})).json()
    assert private['id'] not in {r['id'] for r in other_rows}
    assert (await client.get('/api/v1/foods/library',headers=headers,params={'q':marker,'scope':'mine'})).json()[0]['id']==private['id']
    assert (await client.get('/api/v1/foods/library',headers=headers,params={'browse_category':'伪造分类'})).status_code==422
    # Browse aliases are deliberately NOT added to the Agent's unique alias table.
    assert (await client.get('/api/v1/foods',params={'q':marker+'马铃薯','limit':50})).json()==[]
    # Literal wildcard input must not turn into an all-food search.
    assert (await client.get('/api/v1/foods/library',headers=headers,params={'q':marker+'%'})).json()==[]
    await db_session.execute(delete(Food).where(Food.id.in_([f.id for f in foods])))
    await db_session.commit()


@pytest.mark.asyncio
async def test_per100_private_basis_server_recomputes_portion(client):
    headers=await auth(client)
    body=custom();body.update(amount_g=100,calories=280/4.184,protein_g=4,carbs_g=8,fat_g=2)
    food=(await client.post('/api/v1/foods/custom',headers=headers,json=body)).json()
    assert food['basis']['amount_g']==100
    from app.services.training_lifecycle import training_today
    meal=await client.post('/api/v1/meals',headers=headers,json={'logged_at':str(training_today()),'meal_type':'早餐','items':[
        {'custom_food_id':food['id'],'custom_food_version':1,'food_name':'tampered','amount_g':150,'calories':0,'protein_g':0,'carbs_g':0,'fat_g':0}]})
    assert meal.status_code==201,meal.text
    item=meal.json()['items'][0]
    assert item['calories']==round(280/4.184*1.5,1) and item['protein_g']==6


@pytest.mark.asyncio
async def test_creation_readback_is_owned_and_rejects_changed_food(client):
    owner,other=await auth(client),await auth(client)
    body=custom();food=(await client.post('/api/v1/foods/custom',headers=owner,json=body)).json()
    path='/api/v1/foods/custom/requests/'+body['client_request_id']
    assert (await client.get(path,headers=owner)).json()['id']==food['id']
    assert (await client.get(path,headers=other)).status_code==404
    assert (await client.get(path)).status_code==403
    await client.delete('/api/v1/foods/custom/'+food['id'],headers=owner,params={'version':1})
    assert (await client.get(path,headers=owner)).status_code==409
