from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    llm_api_key: str = ""
    llm_small_model: str = ""
    llm_large_model: str = ""
    embedding_model: str = ""
    app_env: str = "development"
    log_level: str = "info"

    class Config:
        env_file = ".env"


settings = Settings()
