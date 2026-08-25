from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_JWT_SECRET = "change-this-local-development-secret"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "personal-ai-secretary"
    app_env: Literal["development", "test", "production"] = "development"
    api_v1_prefix: str = "/api/v1"
    ai_provider: Literal["deterministic", "local", "remote"] = "deterministic"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2"
    database_url: str = "sqlite+aiosqlite:///./personal_ai_secretary.db"
    persistent_stores: bool = False
    jwt_secret: str = Field(default="change-this-local-development-secret", min_length=16)
    jwt_issuer: str = "personal-ai-secretary"
    jwt_audience: str = "personal-ai-secretary-api"
    jwt_required: bool = True
    jwt_access_token_minutes: int = 60
    jwt_leeway_seconds: int = 10
    stale_running_seconds: int = 300
    log_level: str = "INFO"
    metrics_flush_seconds: int = 10
    metrics_retention_seconds: int = 60
    # OpenTelemetry tracing (FASE 12A): spans are always captured in-process.
    # When OTEL_EXPORTER_ENDPOINT is set (e.g. a local OTLP/HTTP collector),
    # spans are exported via the OTLP HTTP exporter; otherwise they are retained
    # in an in-memory buffer. See docs/PHASE-2-9-IMPLEMENTATION.md (FASE 12A).
    otel_enabled: bool = True
    otel_exporter_endpoint: str | None = None
    # FASE 12D OTLP export hardening. The exporter timeout is in seconds;
    # OTEL_EXPORT_HEADERS accepts a JSON object of static headers (e.g.
    # {"Authorization": "..."}) used for external/cloud collectors and is
    # default-empty. Compression is "gzip" or "none". Batch knobs bound the
    # export cost. None of these change the default no-endpoint in-memory path.
    otel_export_timeout_seconds: float = 10.0
    otel_export_headers: str | None = None
    otel_export_compression: Literal["gzip", "none"] = "gzip"
    otel_export_batch_schedule_seconds: float = 5.0
    otel_export_batch_max_queue_size: int = 2048
    otel_export_batch_max_export_batch_size: int = 512
    # FASE 12E tracing configuration governance. Sampling ratio is parent-based
    # (default 1.0 = current behavior, i.e. trace every span); 0.0 disables
    # sampling. Resource attributes standardize span identity across workers:
    # deployment.environment (from APP_ENV), service.version, and
    # service.instance.id (worker id; auto-generated per process when unset).
    otel_sampling_ratio: float = 1.0
    otel_service_version: str = "1.0.0"
    otel_worker_id: str | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1/chat/completions"
    nvidia_model: str = "meta/llama-3.1-8b-instruct"
    nvidia_api_key: str | None = None
    provider_timeout_seconds: float = 30.0
    # FASE T: Gemini cloud provider
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    # FASE T: OpenCode provider
    opencode_base_url: str = "http://127.0.0.1:4096"
    opencode_model: str = "default"
    # FASE T: Model manager auto-verify on startup
    auto_verify_models: bool = False
    # FASE 13A compliance governance. COMPLIANCE_ENABLED toggles the policy
    # stage; COMPLIANCE_RULES is a comma-separated list of enabled rule ids
    # (prohibited_commands, credential_leakage) resolved against
    # compliance.policy.DEFAULT_RULES.
    compliance_enabled: bool = True
    compliance_rules: str = "prohibited_commands,credential_leakage"
    # FASE 13B persistent RAG. EVIDENCE_TTL_SECONDS optionally expires ingested
    # evidence sources; when unset sources never expire.
    evidence_ttl_seconds: int | None = None
    # FASE 14A retention configuration.
    # audit_retention_seconds: window after which audit rows are pruned
    #   (default 30 days; set to 0 to disable the sweep entirely).
    # cleanup_interval_seconds: how often the background sweep runs
    #   (default 1 hour).
    audit_retention_seconds: int = 2592000
    cleanup_interval_seconds: int = 3600
    # FASE 14B operational hardening.
    # db_pool_size: size of the SQLAlchemy connection pool (passed to
    # create_async_engine pool_size parameter). Tune for production workloads.
    # db_max_overflow: maximum number of connections to overflow beyond
    # pool_size. Set to 0 to disable overflow.
    db_pool_size: int = 5
    db_max_overflow: int = 10

    @model_validator(mode="after")
    def _guard_production_secrets(self) -> Self:
        if self.app_env == "production" and self.jwt_secret == _DEV_JWT_SECRET:
            raise ValueError(
                "JWT_SECRET must be overridden with a strong value when APP_ENV=production"
            )
        if self.app_env == "production" and "sqlite" in self.database_url:
            raise ValueError(
                "DATABASE_URL must point to a production database (e.g. PostgreSQL) "
                "when APP_ENV=production; SQLite is not allowed"
            )
        if (
            self.app_env == "production"
            and self.ai_provider == "remote"
            and not self.nvidia_api_key
        ):
            raise ValueError(
                "NVIDIA_API_KEY must be set when AI_PROVIDER=remote in production"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
