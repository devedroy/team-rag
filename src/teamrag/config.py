from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Vector store
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "teamrag"

    # Text Embeddings Inference
    TEI_URL: str = "http://localhost:8080"

    # Relational database (async driver)
    DATABASE_URL: str  # required — set in .env (no default to avoid committing credentials)


settings = Settings()
