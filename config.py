from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATA_API_KEY: str
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""
    AZURE_FOUNDRY_ENDPOINT: str
    AZURE_FOUNDRY_KEY: str
    MEMORY_CHAT_DEPLOYMENT: str = "gpt-6-luna"
    EMBEDDING_DEPLOYMENT: str = "embed-v-4-0"

    # Workspace-scoped agent tokens (REST bearer + MCP). Blank disables them.
    MEMORY_TOKEN_SECRET: str = ""
    MEMORY_TOKEN_ISSUER: str = "flwn-data-engine"
    MEMORY_TOKEN_MAX_TTL_SECONDS: int = 24 * 60 * 60
    # Comma-separated Host headers allowed on /mcp. Blank disables the check.
    MCP_ALLOWED_HOSTS: str = ""

    # Memory behavior.
    MEMORY_DEDUP_THRESHOLD: float = 0.97
    MEMORY_ORGANIZE_THRESHOLD: float = 0.88
    MEMORY_QUERY_REWRITE: bool = True
    RECENCY_HALF_LIFE_DAYS: float = 14.0
    SWEEP_SCAN_CAP: int = 5000
    SWEEP_BATCH_SIZE: int = 20
    ORGANIZE_SCAN_CAP: int = 1000

    @field_validator(
        "DATA_API_KEY", "QDRANT_URL", "AZURE_FOUNDRY_ENDPOINT", "AZURE_FOUNDRY_KEY"
    )
    @classmethod
    def not_blank(cls, value: str, info) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} is required and cannot be blank")
        return value

    @field_validator("MEMORY_TOKEN_SECRET")
    @classmethod
    def strong_secret(cls, value: str) -> str:
        if value and len(value) < 32:
            raise ValueError("MEMORY_TOKEN_SECRET must be at least 32 characters")
        return value


settings = Settings()
