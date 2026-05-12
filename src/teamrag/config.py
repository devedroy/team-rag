from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Vector store
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "teamrag"

    # Text Embeddings Inference
    TEI_URL: str = "http://localhost:8080"

    # Relational database (async driver)
    DATABASE_URL: str = "postgresql+asyncpg://teamrag:teamrag@localhost:5432/teamrag"

    # Confluence connector
    CONFLUENCE_URL: str = "https://your-org.atlassian.net"
    CONFLUENCE_USERNAME: str = ""
    CONFLUENCE_API_TOKEN: str = ""
    CONFLUENCE_SPACE_KEYS: str = ""          # comma-separated, e.g. "ENG,ARCH"
    CONFLUENCE_MAX_PAGES: int = 500

    # GitHub connector
    GITHUB_TOKEN: str = ""
    GITHUB_REPOS: str = ""            # comma-separated, e.g. "org/repo1,org/repo2"
    GITHUB_MAX_PRS: int = 200

    # LLM backend (proxied by /v1/chat/completions)
    LLM_BASE_URL: str = ""            # e.g. "https://api.openai.com/v1" or Ollama base
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "gpt-4o-mini"


settings = Settings()
