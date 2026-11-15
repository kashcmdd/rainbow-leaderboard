from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://rainbow:rainbow@localhost:5432/rainbow"
    starting_elo: int = 500
    starting_rd: float = 350.0
    starting_volatility: float = 0.06
    glicko_tau: float = 0.5
    provisional_rd: float = 200.0
    max_score_per_side: int = 20
    rating_floor: int = 0
    margin_weight: float = 0.3
    site_name: str = "Vantage"
    cookie_secure: bool = False
    public_base_url: str = ""
    dev_login_discord_id: str = ""
    dev_login_username: str = "DevAdmin"
    dev_login_admin: bool = False
    discord_webhook_url: str = ""
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_redirect_uri: str = ""
    admin_discord_ids: list[str] = []
    secret_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

if not settings.secret_key:
    import warnings
    warnings.warn("SECRET_KEY is empty! Set a SECRET_KEY in .env", stacklevel=2)
if not settings.discord_redirect_uri:
    import warnings
    warnings.warn("DISCORD_REDIRECT_URI is empty! Set it in .env", stacklevel=2)
