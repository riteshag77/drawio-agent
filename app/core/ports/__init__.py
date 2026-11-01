"""Core ports — dependency-inversion boundaries.

Core services import from here, never from adapters directly (except the
default factory functions which lazy-import the real adapter).

MCP / XML tools are still imported directly in xml_pipeline for this pass;
diagram ports are left as a follow-up.
"""

from app.core.ports.llm import LlmPort, get_llm
from app.core.ports.config import ConfigPort, get_config_port

__all__ = ["LlmPort", "get_llm", "ConfigPort", "get_config_port"]
