from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    DATABASE_URL: str
    REDIS_URL: str = "redis://redis:6379"
    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    ALGORITHM: str = "HS256"

    EMBEDDING_API_KEY: str = ""
    EMBEDDING_BASE_URL: str = "https://api.deepseek.com"
    EMBEDDING_MODEL: str = "deepseek-embedding"
    EMBEDDING_DIM: int = 2048


settings = Settings()
