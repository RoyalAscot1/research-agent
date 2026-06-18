from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    gemini_api_key: str
    tavily_api_key: str
    youtube_api_key: str
    frontend_url: str = "http://localhost:3000"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://us.cloud.langfuse.com"
    clerk_issuer: str
    clerk_authorized_parties_raw: str = Field(
        "http://localhost:3000", validation_alias="CLERK_AUTHORIZED_PARTIES"
    )
    # Per-user API rate limiting. Defaults on; tests set RATE_LIMIT_ENABLED=false so the
    # suite isn't throttled (the one 429 test re-enables it locally).
    rate_limit_enabled: bool = True

    @property
    def clerk_authorized_parties(self) -> list[str]:
        return [p.strip() for p in self.clerk_authorized_parties_raw.split(",")]


settings = Settings()
