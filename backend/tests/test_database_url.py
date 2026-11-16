import pytest

from app.database_url import normalize_async_database_url


def test_normalizes_neon_url_for_sqlalchemy_asyncpg():
    source = (
        "postgresql://owner:secret@example.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )

    result = normalize_async_database_url(source)

    assert result == (
        "postgresql+asyncpg://owner:secret@example.neon.tech/neondb?ssl=require"
    )


def test_preserves_existing_asyncpg_ssl_setting():
    source = "postgresql+asyncpg://user:pass@localhost/app?ssl=require"

    assert normalize_async_database_url(source) == source


def test_rejects_non_postgres_urls():
    with pytest.raises(ValueError, match="PostgreSQL"):
        normalize_async_database_url("sqlite:///app.db")
