from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenRouter
    OPENROUTER_API_KEY: str
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    # Tavily
    TAVILY_API_KEY: str

    # Neo4j
    NEO4J_URI: str = "bolt://neo4j:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "research_agent_dev"

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # LangSmith
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "argus"
    LANGCHAIN_TRACING_V2: bool = True

    # Agent config
    MAX_SEARCH_DEPTH: int = 5
    MAX_RESULTS_PER_QUERY: int = 10
    RATE_LIMIT_SEARCHES_PER_MIN: int = 20
    CONFIDENCE_THRESHOLD: float = 0.6
    MAX_SCRAPE_CONCURRENT: int = 5

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = Field(default="json", description="'json' for production, 'console' for dev")


def get_settings() -> Settings:
    return Settings()
