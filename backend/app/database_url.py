from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalize_async_database_url(database_url: str) -> str:
    """Convert a standard Postgres URL into a SQLAlchemy asyncpg URL.

    Managed Postgres providers such as Neon expose libpq-style URLs.  The
    asyncpg SQLAlchemy dialect expects its own driver scheme and ``ssl``
    keyword, while Neon also includes the libpq-only ``channel_binding``
    option.  Normalizing in one place lets operators paste the provider URL
    unchanged into DATABASE_URL.
    """

    value = database_url.strip()
    parsed = urlsplit(value)

    if parsed.scheme in {"postgres", "postgresql"}:
        scheme = "postgresql+asyncpg"
    elif parsed.scheme == "postgresql+asyncpg":
        scheme = parsed.scheme
    else:
        raise ValueError("DATABASE_URL must use a PostgreSQL URL scheme")

    query_items: list[tuple[str, str]] = []
    has_ssl = False
    sslmode: str | None = None

    for key, item_value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = key.lower()
        if normalized_key == "channel_binding":
            continue
        if normalized_key == "sslmode":
            sslmode = item_value
            continue
        if normalized_key == "ssl":
            has_ssl = True
        query_items.append((key, item_value))

    if sslmode is not None and not has_ssl:
        query_items.append(("ssl", sslmode))

    return urlunsplit(
        (scheme, parsed.netloc, parsed.path, urlencode(query_items), parsed.fragment)
    )
