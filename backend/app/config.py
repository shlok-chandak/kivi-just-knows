import uuid

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Two tiers: the small model does the high-volume work (extraction, parsing,
# verification); the large one is reserved for final synthesis. Pinned to
# explicit versions rather than "-latest" aliases so eval runs stay
# reproducible when the provider ships a new model.
DEFAULT_SMALL_MODEL = "gemini-3.5-flash-lite"
DEFAULT_LARGE_MODEL = "gemini-3.8-flash"
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

_MODEL_DEFAULTS = {
    "llm_small_model": DEFAULT_SMALL_MODEL,
    "llm_large_model": DEFAULT_LARGE_MODEL,
    "embedding_model": DEFAULT_EMBEDDING_MODEL,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    # Single-user build: every row is owned by this id. Fixed rather than
    # generated so the API, importer and worker always agree.
    default_user_id: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")

    # Zone that wall-clock expressions ("around 5 PM") resolve in.
    user_timezone: str = "Asia/Kolkata"

    store_sensitive_content: bool = False

    llm_api_key: str = ""
    llm_small_model: str = DEFAULT_SMALL_MODEL
    llm_large_model: str = DEFAULT_LARGE_MODEL
    # Free-tier keys allow a small number of requests per minute. Spacing
    # calls to stay under it costs a wait; discovering it costs a round trip,
    # a retry, and eventually a parked job. Set 0 to disable.
    llm_requests_per_minute: int = 12
    llm_timeout_seconds: float = 60.0

    # Embeddings run locally: no key, no quota, free to recompute.
    embedding_model: str = DEFAULT_EMBEDDING_MODEL

    app_env: str = "development"
    log_level: str = "info"

    @model_validator(mode="after")
    def blank_model_means_default(self) -> "Settings":
        """Treat an empty env var as absent.

        A blank line in .env otherwise overrides the default with "", which
        fails deep inside the provider SDK with an unhelpful error.
        """
        for field, default in _MODEL_DEFAULTS.items():
            if not (getattr(self, field) or "").strip():
                setattr(self, field, default)
        return self


settings = Settings()
