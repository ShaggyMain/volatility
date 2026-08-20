"""Volatility & catalyst prediction research system.

Point-in-time-safe by construction: the LLM layer produces structured features,
the deterministic layer produces every score and probability, and historical
predictions are never rewritten.
"""

__version__ = "0.2.0"
