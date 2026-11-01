"""Configuration adapter — reads from environment + .env file."""
from app.adapters.outbound.config.env_config import EnvConfigAdapter

__all__ = ["EnvConfigAdapter"]
