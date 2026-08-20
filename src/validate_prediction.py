"""Compatibility shim for the v0.1 module path ``validate_prediction``."""

from __future__ import annotations

from volatility_ai.prediction import validate as _validate


def validate_prediction(path: str) -> None:
    import json
    from pathlib import Path

    _validate(json.loads(Path(path).read_text(encoding="utf-8")))


if __name__ == "__main__":
    import sys

    validate_prediction(sys.argv[1])
    print("VALID")
