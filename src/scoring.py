"""Compatibility shim for the v0.1 module path ``scoring``.

The implementation moved into the ``volatility_ai`` package in v0.2. This module
keeps the original import path working so v0.1 tests and scripts do not break.
"""

from volatility_ai.scoring import load_scoring_config, normalize_probabilities, weighted_score

__all__ = ["load_scoring_config", "normalize_probabilities", "weighted_score"]
