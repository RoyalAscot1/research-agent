from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    gemini_api_key: str
    tavily_api_key: str
    youtube_api_key: str
    frontend_url: str = "http://localhost:3000"


settings = Settings()
