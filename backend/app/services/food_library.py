"""Manual library browse only. Does not change Agent's exact-match resolver."""
from typing import Literal
import sqlalchemy as sa
from app.models.food import Food, FoodAlias, CustomFood
from app.schemas.food import LibraryFood
from app.services.custom_foods import library_food
from app.services.food import normalize_food_reference, normalized_food_name
from app.services.food_catalog_v1 import catalog_entries

BrowseCategory=Literal['主食薯类','肉蛋类','鱼虾水产','豆类豆制品','奶类','蔬菜菌菇','水果','坚果油脂']


async def query_library(db,user_id,query=None,scope='all',category=None,limit=20,offset=0):
    normalized=normalize_food_reference(query or '')
    escaped=normalized.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
    pattern=f'%{escaped}%'
    private=sa.select(CustomFood).where(CustomFood.user_id==user_id,CustomFood.is_active.is_(True))
    if normalized:private=private.where(normalized_food_name(CustomFood.name).like(pattern,escape='\\'))
    result=[];private_count=0
    if not category or scope=='mine':
        rows=(await db.execute(private.order_by(CustomFood.name,CustomFood.id).offset(offset).limit(limit))).scalars().all()
        result=[library_food(row) for row in rows]
        if scope=='mine' or len(result)==limit:return result
        private_count=await db.scalar(sa.select(sa.func.count()).select_from(private.subquery()))
    legacy={e['name']:e for e in catalog_entries() if not e['new']}
    browse=sa.func.coalesce(Food.browse_category,sa.case({name:e['browse_category'] for name,e in legacy.items()},value=Food.name_zh))
    statement=sa.select(Food,browse.label('resolved_category')).where(Food.is_active.is_(True))
    if category:statement=statement.where(browse==category)
    if normalized:
        legacy_names=[name for name,e in legacy.items() if any(normalized in normalize_food_reference(alias) for alias in e['browse_aliases'])]
        # JSONB aliases are an array of strings: escaping prevents wildcard injection.
        statement=statement.where(sa.or_(normalized_food_name(Food.name_zh).like(pattern,escape='\\'),normalized_food_name(Food.name_en).like(pattern,escape='\\'),
            sa.cast(Food.browse_aliases,sa.Text).ilike(pattern,escape='\\'),Food.name_zh.in_(legacy_names),
            sa.exists(sa.select(FoodAlias.id).where(FoodAlias.food_id==Food.id,FoodAlias.normalized_alias.ilike(pattern,escape='\\')))))
    rows=(await db.execute(statement.order_by(Food.name_zh,Food.id).offset(max(0,offset-private_count)).limit(limit-len(result)))).all()
    for food,resolved_category in rows:
        item=LibraryFood.model_validate(food);item.browse_category=resolved_category;result.append(item)
    return result
