"""Configure HTTPS certificate verification for all outbound API calls.

On Windows (especially Python 3.14+), the default CA bundle is often missing or
too strict for corporate proxies. truststore uses the OS certificate store instead.

Thread-safe via ``threading.once`` semantics — idempotent on repeated calls.
"""

from __future__ import annotations

import os
import threading
import warnings

_ssl_lock = threading.Lock()
_ssl_ready: bool = False
ssl_verify_enabled: bool = True


def configure_ssl() -> bool:
    """Return False when verification is explicitly disabled.

    Safe to call from any thread; only the first invocation takes effect.
    """
    global _ssl_ready, ssl_verify_enabled
    if _ssl_ready:
        return ssl_verify_enabled

    with _ssl_lock:
        if _ssl_ready:
            return ssl_verify_enabled

        if os.getenv("SSL_VERIFY", "true").strip().lower() in ("0", "false", "no"):
            ssl_verify_enabled = False
            warnings.warn(
                "SSL_VERIFY is disabled — HTTPS certificate checks are off. "
                "Use only for local debugging.",
                stacklevel=2,
            )
            _ssl_ready = True
            return False

        try:
            import truststore

            truststore.inject_into_ssl()
        except ImportError:
            import certifi

            bundle = certifi.where()
            os.environ.setdefault("SSL_CERT_FILE", bundle)
            os.environ.setdefault("REQUESTS_CA_BUNDLE", bundle)

        _ssl_ready = True
        return True


# Initialize on import — same behavior as before, but now thread-safe.
configure_ssl()
