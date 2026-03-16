from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
    )

    project_name: str = "CURFD AI Backend"
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"

    database_url: str | None = None
    supabase_db_url: str | None = None
    supabase_url: str | None = None
    supabase_anon_key: str | None = None
    supabase_service_role_key: str | None = None
    supabase_bucket_name: str | None = None
    postgres_user: str = "curfd"
    postgres_password: str = "curfdai"
    postgres_db: str = "curfd"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    cors_allow_origins: str = "http://localhost:3000,http://localhost:5173,https://nooriat.com,https://nooriat.org,https://www.nooriat.com,https://www.nooriat.org"
    trusted_hosts: str = "localhost,127.0.0.1,[::1],clownfish-app-ipxaa.ondigitalocean.app,*.ondigitalocean.app"

    secret_key: str | None = None
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 2880

    frontend_url: str | None = None
    backend_url: str | None = None

    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str | None = None

    facebook_client_id: str | None = None
    facebook_client_secret: str | None = None

    linkedin_client_id: str | None = None
    linkedin_client_secret: str | None = None
    linkedin_redirect_uri: str | None = None

    stripe_api_key: str | None = None
    stripe_secret_key: str | None = None
    react_app_stripe_publishable_key: str | None = None

    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    hf_token: str | None = None
    replicate_api_token: str | None = None

    # LLM provider configuration
    llm_provider: str = "anthropic"  # "gemini" | "anthropic"
    llm_model: str | None = None  # Override model name, or use provider default
    llm_temperature: float = 0.3
    anthropic_api_key: str | None = None

    # Agent configuration
    agent_max_iterations: int = 5

    # Code generation LLM (hardcoded, not env-configurable)
    code_gen_model: str | None = None  # uses llm_model by default
    code_gen_temperature: float = 0.1
    code_gen_max_tokens: int = 32000

    runpod_base_url: str = "https://api.runpod.ai/v2/k91olo35clkkky"
    runpod_api_token: str | None = None
    runpod_status_poll_interval_seconds: float = 2.5
    runpod_status_timeout_seconds: int = 7200


settings = Settings()

if settings.supabase_db_url:
    settings.database_url = settings.supabase_db_url

if not settings.database_url:
    settings.database_url = (
        "postgresql+asyncpg://"
        f"{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}"
        f"/{settings.postgres_db}"
    )
