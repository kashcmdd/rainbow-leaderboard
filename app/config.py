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
    discord_redirect_uri: str = "http://100.114.30.40:8000/auth/callback"
    admin_discord_ids: list[str] = ["1415420243836407878"]
    secret_key: str = "change-me-to-a-random-string"

    class Config:
        env_file = ".env"


settings = Settings()
