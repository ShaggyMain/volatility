"""Compatibility shim for the v0.1 module path ``resolve_prediction``."""

from volatility_ai.resolve import Outcome, write_outcome

__all__ = ["Outcome", "write_outcome"]
