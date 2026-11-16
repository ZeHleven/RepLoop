"""CLI: import text file as knowledge chunks.

Usage:
    python scripts/import_knowledge.py --file knowledge/nutrition.txt --topic nutrition --source nutrition_guide
    python scripts/import_knowledge.py --file knowledge/training.md --topic training --source training_manual

Each non-empty paragraph (blank-line separated) becomes one chunk.
"""
import argparse
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.config import settings
from app.database_url import normalize_async_database_url
from app.services.knowledge import add_knowledge_chunk


def parse_chunks(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n\n") if p.strip()]


async def main(file_path: str, topic: str, source: str):
    with open(file_path, encoding="utf-8") as f:
        text = f.read()

    chunks = parse_chunks(text)
    if not chunks:
        print("No content found.")
        return

    engine = create_async_engine(
        normalize_async_database_url(settings.DATABASE_URL), echo=False
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        for i, content in enumerate(chunks):
            await add_knowledge_chunk(session, source=source, topic=topic, content=content)
            print(f"[{i + 1}/{len(chunks)}] imported: {content[:60]}...")

    await engine.dispose()
    print(f"Done. {len(chunks)} chunks imported from '{file_path}'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import text file as knowledge chunks")
    parser.add_argument("--file", required=True, help="Path to text or markdown file")
    parser.add_argument("--topic", required=True, help="Topic tag for all chunks")
    parser.add_argument("--source", required=True, help="Source identifier")
    args = parser.parse_args()

    asyncio.run(main(args.file, args.topic, args.source))
