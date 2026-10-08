from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    DATABASE_URL: str
    REDIS_URL: str = "redis://redis:6379"
    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    ALGORITHM: str = "HS256"

    WECHAT_APP_ID: str = ""
    WECHAT_APP_SECRET: str = ""
    WECHAT_LOGIN_TIMEOUT_SECONDS: float = 5.0

    RAG_ENABLED: bool = False
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_BASE_URL: str = ""
    EMBEDDING_MODEL: str = ""
    EMBEDDING_DIM: int = 2048

    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_CHAT_MODEL: str = "deepseek-v4-flash"
    DEEPSEEK_REASONING_MODEL: str = "deepseek-v4-pro"

    AGENT_ENABLED: bool = True
    AGENT_MODEL: str = "deepseek-v4-flash"
    AGENT_TIMEOUT_SECONDS: float = 60.0
    AGENT_INTENT_MODEL_ENABLED: bool = True
    AGENT_INTENT_MODEL: str = "deepseek-v4-flash"
    # Deprecated compatibility setting. Ordinary semantic routing never uses
    # deterministic rules as an execution authority.
    AGENT_RULES_FIRST_ENABLED: bool = False
    AGENT_INTENT_TIMEOUT_SECONDS: float = 10.0
    AGENT_INTENT_TOTAL_TIMEOUT_SECONDS: float = 24.0
    AGENT_INTENT_RETRY_MIN_REMAINING_SECONDS: float = 2.0
    AGENT_INTENT_MAX_TOKENS: int = 1100
    # The compact domain/action router has its own budget. Keeping this
    # separate means existing deployments with older extraction timeouts gain
    # the reliability fix without needing an immediate environment change.
    AGENT_INTENT_ROUTE_TIMEOUT_SECONDS: float = Field(
        default=14.0, ge=1.0, le=30.0
    )
    AGENT_INTENT_ROUTE_MAX_TOKENS: int = Field(default=1000, ge=128, le=1600)
    DAILY_MEAL_OPTIMIZER_TIMEOUT_SECONDS: float = Field(
        default=3.0, ge=0.1, le=10.0
    )
    AGENT_MAX_HISTORY_MESSAGES: int = 20
    AGENT_RECURSION_LIMIT: int = 8
    AGENT_PLANNED_EXECUTION_ENABLED: bool = True
    AGENT_PLANNER_TIMEOUT_SECONDS: float = 15.0
    AGENT_REPLANNER_TIMEOUT_SECONDS: float = 30.0
    AGENT_PLANNING_MAX_TOKENS: int = 1200
    AGENT_EXECUTOR_TIMEOUT_SECONDS: float = 20.0
    AGENT_MAX_PLAN_STEPS: int = 3
    AGENT_MAX_TOOL_CALLS: int = 4
    AGENT_MAX_REPLANS: int = 1
    AGENT_MAX_MODEL_CALLS: int = 12
    AGENT_MAX_STEP_DECISIONS: int = 4
    AGENT_DIRECT_STEP_MAX_TOOL_CALLS: int = 1
    AGENT_REACT_STEP_MAX_TOOL_CALLS: int = 2
    AGENT_ASYNC_WORKER_ENABLED: bool = True
    AGENT_WORKER_POLL_SECONDS: float = 0.5
    AGENT_RUN_LEASE_SECONDS: int = 180
    AGENT_RUN_MAX_ATTEMPTS: int = 3
    AGENT_TOOL_REGISTRY_SHADOW_ENABLED: bool = False
    AGENT_TOOL_REGISTRY_SHADOW_SAMPLE_RATE: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
    )
    AGENT_TOOL_REGISTRY_SHADOW_PERSIST_TRACE: bool = False
    AGENT_TOOL_REGISTRY_SHADOW_EMIT_METRICS: bool = False
    # Separately gated read-only Registry authority transition.
    AGENT_TOOL_REGISTRY_ENFORCE_READS_ENABLED: bool = False
    # Independent write-Proposal rollout gates. All remain disabled by default.
    AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED: bool = False
    MANUAL_PLAN_PROPOSALS_ENABLED: bool = False
    AGENT_PLAN_MANAGEMENT_PROPOSALS_ENABLED: bool = False
    AGENT_PROFILE_PROPOSALS_ENABLED: bool = False
    AGENT_WEIGHT_PROPOSALS_ENABLED: bool = False
    AGENT_NUTRITION_PROPOSALS_ENABLED: bool = False


settings = Settings()
