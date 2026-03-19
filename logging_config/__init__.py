"""Shared structured JSON logging configuration for all DanClaw components.

Re-exports the public API so callers can do::

    from logging_config import setup_logging
"""

from logging_config.setup import setup_logging

__all__ = ["setup_logging"]
