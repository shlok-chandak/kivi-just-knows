import uuid

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    # Single-user build: every row is owned by this id. Fixed rather than
    # generated so the API, importer and worker always agree.
    default_user_id: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")

    user_timezone: str = "Asia/Kolkata"
    llm_api_key: str = ""
    llm_small_model: str = ""
    llm_large_model: str = ""
    embedding_model: str = ""
    app_env: str = "development"
    log_level: str = "info"


settings = Settings()
