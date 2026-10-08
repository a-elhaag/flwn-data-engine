from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATA_API_KEY: str
    AZURE_FOUNDRY_ENDPOINT: str
    AZURE_FOUNDRY_KEY: str
    MEMORY_CHAT_DEPLOYMENT: str = "gpt-6-luna"
    EMBEDDING_DEPLOYMENT: str = "embed-v-4-0"
    RERANK_DEPLOYMENT: str = "Cohere-rerank-v4.0-pro"
    PARSE_DEPLOYMENT: str = "Cohere-parse-v5"  # reads images and scanned pages (image input only)
    PARSE_INTERVAL_SECONDS: float = 10.0  # the deployment's quota is tiny: about one page per 10s

    # Workspace-scoped agent tokens (REST bearer + MCP). Blank disables them.
    MEMORY_TOKEN_SECRET: str = ""
    MEMORY_TOKEN_ISSUER: str = "flwn-data-engine"
    MEMORY_TOKEN_MAX_TTL_SECONDS: int = 24 * 60 * 60
    # Comma-separated Host headers allowed on /mcp. Blank disables the check.
    MCP_ALLOWED_HOSTS: str = ""

    # Azure Blob Storage for files, chat media, recordings and agent reports.
    # Blank disables the file routes. Access uses Entra ID (managed identity), never account keys.
    AZURE_STORAGE_ACCOUNT_URL: str = ""
    UPLOAD_URL_TTL_SECONDS: int = 900
    DOWNLOAD_URL_TTL_SECONDS: int = 300

    # File indexing: extract text from uploaded files, chunk, embed, make them searchable.
    FILE_INDEXING: bool = True  # run the background worker (needs AZURE_STORAGE_ACCOUNT_URL)
    INDEX_MAX_BYTES: int = 50 * 1024 * 1024  # larger files are skipped, not indexed
    INDEX_MAX_PAGES: int = 500
    INDEX_MAX_CHUNKS: int = 2000
    INDEX_MAX_SCANNED_PAGES: int = 10  # pages of one scanned PDF sent to the (slow) Parse model
    INDEX_POLL_SECONDS: int = 5
    INDEX_STUCK_MINUTES: int = 15  # an `indexing` claim older than this is taken over

    # Memory behavior.
    MEMORY_DEDUP_THRESHOLD: float = 0.97
    MEMORY_ORGANIZE_THRESHOLD: float = 0.88
    MEMORY_QUERY_REWRITE: bool = True
    MEMORY_RERANK: bool = True  # rerank recall candidates with the Cohere model
    RECALL_CANDIDATES: int = 30  # most candidates sent to the reranker (cost and latency)
    RECENCY_HALF_LIFE_DAYS: float = 14.0
    SWEEP_SCAN_CAP: int = 5000
    SWEEP_BATCH_SIZE: int = 20
    ORGANIZE_SCAN_CAP: int = 1000

    @field_validator("DATA_API_KEY", "AZURE_FOUNDRY_ENDPOINT", "AZURE_FOUNDRY_KEY")
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
