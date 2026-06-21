"""Configure HTTPS certificate verification for all outbound API calls.

On Windows (especially Python 3.14+), the default CA bundle is often missing or
too strict for corporate proxies. truststore uses the OS certificate store instead.
"""

from __future__ import annotations

import os
import warnings

_configured = False
SSL_VERIFY_ENABLED = True


def configure_ssl() -> bool:
    """Return False when verification is explicitly disabled."""
    global _configured, SSL_VERIFY_ENABLED
    if _configured:
        return SSL_VERIFY_ENABLED
    _configured = True

    if os.getenv("SSL_VERIFY", "true").strip().lower() in ("0", "false", "no"):
        SSL_VERIFY_ENABLED = False
        warnings.warn(
            "SSL_VERIFY is disabled — HTTPS certificate checks are off. "
            "Use only for local debugging.",
            stacklevel=2,
        )
        return False

    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        import certifi

        bundle = certifi.where()
        os.environ.setdefault("SSL_CERT_FILE", bundle)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", bundle)

    return True


configure_ssl()
