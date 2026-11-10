from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    sql_lab_readonly_password: str | None = None

    supabase_url: str | None = None
    supabase_function_secret: str | None = None
    supabase_service_role_key: str | None = None

    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-20b"
    groq_max_completion_tokens: int = Field(default=1400, gt=0, le=1400)

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()