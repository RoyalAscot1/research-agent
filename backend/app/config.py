from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    gemini_api_key: str
    tavily_api_key: str
    reddit_client_id: str
    reddit_client_secret: str
    reddit_user_agent: str = "lens/1.0"
    frontend_url: str = "http://localhost:3000"


settings = Settings()
