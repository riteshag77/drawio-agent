"""A7: Dependency injection container — single point of initialization for all roots.

All roots (FastAPI app, MCP server, CLI tools) use this container instead of
bootstrapping independently, ensuring consistent setup and reducing duplication.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.adapters.outbound.config.config import Config, get_config
from app.adapters.outbound.diagram.style_cache import clear_style_cache
from app.adapters.outbound.llm import providers as providers_module
from app.adapters.outbound.llm.async_client import AsyncLlmClient
from app.adapters.outbound.repositories.unified_repository import UnifiedRepository
from app.core.logging import StepLogger

logger = logging.getLogger("drawio-agent")


class AppContainer:
    """Dependency injection container for the entire application.
    
    Handles initialization of config, LLM client, repositories, and other
    shared services. Ensures all roots (FastAPI, MCP, CLI) share the same
    configuration and component instances.
    """

    def __init__(self):
        """Initialize all application components."""
        self.config: Config = get_config()
        self._validate_config()
        
        # Initialize LLM infrastructure
        self.providers = self._load_providers()
        self.llm_client: AsyncLlmClient = self._create_llm_client()
        
        # Initialize data layer
        self.repository: UnifiedRepository = UnifiedRepository(
            db_path=self.config.database_path,
            enable_migrations=True
        )
        
        # Initialize optional features
        if self.config.drawio_style_cache_reset:
            clear_style_cache()
    
    def _validate_config(self) -> None:
        """Validate that required config is present and warn about missing keys."""
        default_key = providers_module.default_provider_key()
        spec = providers_module.get_provider(default_key)
        
        if spec.api_key_env and not self.config.get(spec.api_key_env):
            logger.warning(
                f"Default provider '{default_key}' requires {spec.api_key_env} "
                f"to be set in environment or admin settings. "
                f"Requests without an explicit provider will fail."
            )
    
    def _load_providers(self) -> providers_module:
        """Load and validate LLM providers from configuration."""
        return providers_module
    
    def _create_llm_client(self) -> AsyncLlmClient:
        """Create async LLM client with provider fallback chain."""
        return AsyncLlmClient(
            providers=self.providers,
            config=self.config
        )
    
    def create_step_logger(self, request_id: Optional[str] = None) -> StepLogger:
        """Factory for creating request-scoped loggers."""
        return StepLogger(request_id=request_id)


# Global container instance (lazy-initialized)
_container: Optional[AppContainer] = None


def get_container() -> AppContainer:
    """Get or create the global application container.
    
    Safe to call multiple times; container is initialized only once.
    """
    global _container
    if _container is None:
        _container = AppContainer()
    return _container


def reset_container() -> None:
    """Reset the global container (useful for testing).
    
    WARNING: Only call this in test setup/teardown.
    """
    global _container
    _container = None
