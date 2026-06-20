from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://rainbow:rainbow@localhost:5432/rainbow"
    base_k: int = 32
    new_player_k: int = 64
    provisional_matches: int = 10
    rating_floor: int = 0
    decay_days: int = 30
    decay_per_day: int = 10
    max_decay: int = 200
    margin_weight: float = 0.3
    format_k_multipliers: dict[str, float] = {
        "1v1": 1.0,
        "2v2": 0.8,
        "3v3": 0.7,
        "4v4": 0.6,
        "5v5": 0.5,
    }
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_redirect_uri: str = ""
    admin_discord_ids: list[str] = []
    secret_key: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()

if not settings.secret_key:
    import warnings
    warnings.warn("SECRET_KEY is empty! Set a SECRET_KEY in .env")
if not settings.discord_redirect_uri:
    import warnings
    warnings.warn("DISCORD_REDIRECT_URI is empty! Set it in .env")
