"""Seed 20 foods into the database. Safe to re-run (skips existing by name_zh)."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.config import settings
from app.database_url import normalize_async_database_url
from app.models.food import Food

FOODS = [
    dict(name_zh="鸡胸肉", name_en="Chicken Breast", category="蛋白质", calories_per_100g=165, protein_g=31.0, carbs_g=0.0, fat_g=3.6, common_portion_g=150, diet_tags=["high-protein", "low-fat", "gluten-free"]),
    dict(name_zh="鸡蛋", name_en="Egg", category="蛋白质", calories_per_100g=155, protein_g=13.0, carbs_g=1.1, fat_g=11.0, common_portion_g=60, diet_tags=["high-protein", "gluten-free", "keto"]),
    dict(name_zh="三文鱼", name_en="Salmon", category="蛋白质", calories_per_100g=208, protein_g=20.0, carbs_g=0.0, fat_g=13.0, common_portion_g=150, diet_tags=["high-protein", "omega-3", "gluten-free"]),
    dict(name_zh="牛肉", name_en="Beef (Lean)", category="蛋白质", calories_per_100g=215, protein_g=26.0, carbs_g=0.0, fat_g=12.0, common_portion_g=150, diet_tags=["high-protein", "keto"]),
    dict(name_zh="豆腐", name_en="Tofu", category="蛋白质", calories_per_100g=76, protein_g=8.0, carbs_g=2.0, fat_g=4.0, common_portion_g=100, diet_tags=["vegan", "high-protein", "gluten-free"]),
    dict(name_zh="糙米", name_en="Brown Rice", category="碳水", calories_per_100g=216, protein_g=5.0, carbs_g=45.0, fat_g=1.8, common_portion_g=150, diet_tags=["gluten-free", "whole-grain"]),
    dict(name_zh="燕麦", name_en="Oatmeal", category="碳水", calories_per_100g=389, protein_g=17.0, carbs_g=66.0, fat_g=7.0, common_portion_g=80, diet_tags=["high-fiber", "vegan", "whole-grain"]),
    dict(name_zh="红薯", name_en="Sweet Potato", category="碳水", calories_per_100g=86, protein_g=1.6, carbs_g=20.0, fat_g=0.1, common_portion_g=150, diet_tags=["vegan", "gluten-free", "high-fiber"]),
    dict(name_zh="全麦面包", name_en="Whole Wheat Bread", category="碳水", calories_per_100g=247, protein_g=13.0, carbs_g=41.0, fat_g=4.2, common_portion_g=60, diet_tags=["high-fiber", "whole-grain"]),
    dict(name_zh="香蕉", name_en="Banana", category="水果", calories_per_100g=89, protein_g=1.1, carbs_g=23.0, fat_g=0.3, fiber_g=2.6, common_portion_g=120, diet_tags=["vegan", "gluten-free"]),
    dict(name_zh="西蓝花", name_en="Broccoli", category="蔬菜", calories_per_100g=34, protein_g=2.8, carbs_g=7.0, fat_g=0.4, fiber_g=2.6, common_portion_g=150, diet_tags=["vegan", "gluten-free", "low-calorie"]),
    dict(name_zh="菠菜", name_en="Spinach", category="蔬菜", calories_per_100g=23, protein_g=2.9, carbs_g=3.6, fat_g=0.4, fiber_g=2.2, common_portion_g=100, diet_tags=["vegan", "gluten-free", "low-calorie"]),
    dict(name_zh="牛奶", name_en="Whole Milk", category="乳制品", calories_per_100g=61, protein_g=3.2, carbs_g=4.8, fat_g=3.3, common_portion_g=250, diet_tags=["gluten-free"]),
    dict(name_zh="希腊酸奶", name_en="Greek Yogurt", category="乳制品", calories_per_100g=97, protein_g=9.0, carbs_g=3.6, fat_g=5.0, common_portion_g=150, diet_tags=["high-protein", "gluten-free"]),
    dict(name_zh="杏仁", name_en="Almonds", category="坚果", calories_per_100g=579, protein_g=21.0, carbs_g=22.0, fat_g=50.0, fiber_g=12.5, common_portion_g=30, diet_tags=["vegan", "keto", "gluten-free"]),
    dict(name_zh="花生酱", name_en="Peanut Butter", category="坚果", calories_per_100g=588, protein_g=25.0, carbs_g=20.0, fat_g=50.0, common_portion_g=32, diet_tags=["vegan", "high-protein"]),
    dict(name_zh="橄榄油", name_en="Olive Oil", category="油脂", calories_per_100g=884, protein_g=0.0, carbs_g=0.0, fat_g=100.0, common_portion_g=14, diet_tags=["vegan", "keto", "gluten-free"]),
    dict(name_zh="鳕鱼", name_en="Cod", category="蛋白质", calories_per_100g=82, protein_g=18.0, carbs_g=0.0, fat_g=0.7, common_portion_g=150, diet_tags=["high-protein", "low-fat", "gluten-free"]),
    dict(name_zh="藜麦", name_en="Quinoa", category="碳水", calories_per_100g=368, protein_g=14.0, carbs_g=64.0, fat_g=6.0, fiber_g=7.0, common_portion_g=150, diet_tags=["vegan", "gluten-free", "high-protein"]),
    dict(name_zh="蓝莓", name_en="Blueberries", category="水果", calories_per_100g=57, protein_g=0.7, carbs_g=14.0, fat_g=0.3, fiber_g=2.4, common_portion_g=100, diet_tags=["vegan", "gluten-free", "antioxidant"]),
]


async def main():
    engine = create_async_engine(
        normalize_async_database_url(settings.DATABASE_URL), echo=False
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        inserted = 0
        for data in FOODS:
            existing = await session.scalar(
                select(Food).where(Food.name_zh == data["name_zh"])
            )
            if existing:
                continue
            session.add(Food(**data))
            inserted += 1
        await session.commit()
        # Migrations create the catalogue ledger. This also classifies legacy seeds
        # on fresh databases without changing their nutrient values or identities.
        from app.services.food_catalog_v1 import sync_catalog
        connection = await session.connection()
        await connection.run_sync(sync_catalog)
        await session.commit()
        print(f"Inserted {inserted} foods ({len(FOODS) - inserted} already existed).")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
