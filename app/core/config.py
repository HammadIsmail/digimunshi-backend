from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/digimunshi"
    JWT_SECRET_KEY: str = "dev-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    UPLIFTAI_API_KEY: str = ""
    UPLIFTAI_API_URL: str = "https://api.upliftai.org/v1"
    UPLIFTAI_ASSISTANT_ID: str = "83cf7ec1-ab59-4b40-bd79-e1dce4fed393"
    UPLIFTAI_VOICE_ID: str = "prime-time-anchor"
    GROQ_API_KEY: str = ""
    CORS_ORIGINS: list[str] = ["http://localhost:8081"]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
