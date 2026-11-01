"""ConfigPort — the dependency-inversion boundary between core and env config.

Core services call ``get_config_port()`` to read settings. In tests, pass a
fake implementation or monkeypatch ``get_config_port`` to avoid touching the
real environment.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ConfigPort(Protocol):
    """The config surface that core actually needs."""

    @property
    def llm_timeout_seconds(self) -> int: ...

    @property
    def max_custom_message_len(self) -> int: ...

    @property
    def diagram_renderer(self) -> str: ...


def get_config_port() -> ConfigPort:
    """Return the default EnvConfigAdapter (lazy import — keeps adapters out of core import path)."""
    from app.adapters.outbound.config.env_config import get_config  # noqa: PLC0415
    return get_config()  # type: ignore[return-value]
