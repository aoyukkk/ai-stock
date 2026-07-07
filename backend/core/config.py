from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_name: str = "AI Trader Assistant"
    version: str = "1.2"

    app_env: str = "development"
    log_level: str = "INFO"
    timezone: str = "Asia/Shanghai"
    database_url: str = ""
    redis_url: str = ""
    deepseek_api_key: str = Field(default="", repr=False)
    openai_api_key: str = Field(default="", repr=False)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
