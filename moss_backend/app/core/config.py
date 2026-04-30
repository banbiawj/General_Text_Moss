import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_CORS_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "null",
]

CONFIG_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CONFIG_DIR.parents[1]
WORKSPACE_DIR = CONFIG_DIR.parents[2]


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=(
            CONFIG_DIR / ".env",
            BACKEND_DIR / ".env",
            WORKSPACE_DIR / ".env",
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Moss 智能文档助手"
    api_v1_prefix: str = "/api/v1"

    cors_origins: str = Field(
        default=",".join(DEFAULT_CORS_ORIGINS),
        alias="CORS_ORIGINS",
    )

    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")
    llm_base_url: str = Field(default="https://api.deepseek.com", alias="LLM_BASE_URL")
    llm_model: str = Field(default="deepseek-chat", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.2, alias="LLM_TEMPERATURE")
    enable_mock_llm: bool = Field(default=True, alias="ENABLE_MOCK_LLM")
    enable_llm_logging: bool = Field(default=True, alias="ENABLE_LLM_LOGGING")
    llm_log_file: Path = Field(default=Path("logs") / "llm_messages.jsonl", alias="LLM_LOG_FILE")

    storage_dir: Path = Field(default=Path("storage"), alias="STORAGE_DIR")

    @property
    def allowed_cors_origins(self) -> list[str]:
        value = self.cors_origins.strip()
        if not value:
            return DEFAULT_CORS_ORIGINS
        if value.startswith("["):
            parsed = json.loads(value)
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
