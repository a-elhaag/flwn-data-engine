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

    @field_validator(
        "DATA_API_KEY", "QDRANT_URL", "AZURE_FOUNDRY_ENDPOINT", "AZURE_FOUNDRY_KEY"
    )
    @classmethod
    def not_blank(cls, value: str, info) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} is required and cannot be blank")
        return value


settings = Settings()
