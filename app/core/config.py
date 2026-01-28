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

    cors_allow_origins: str = "*"

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
    hf_token: str | None = None
    replicate_api_token: str | None = None


settings = Settings()

if settings.supabase_db_url:
    settings.database_url = settings.supabase_db_url

if not settings.database_url:
    settings.database_url = (
        "postgresql+psycopg://"
        f"{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}"
        f"/{settings.postgres_db}"
    )
