"""Run manifests.

Every scan writes one manifest, whether or not it produced predictions. The
manifest is what makes a run reproducible and auditable: it records the universe
that was searched, what was rejected and why, which config versions were in
force, and which predictions came out. A run that finds nothing is still a run
worth recording -- an empty day is evidence about the market, not a non-event.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

RUNS_ROOT = "runs"
SCHEMA_PATH = "schemas/run.schema.json"


def validate(manifest: Mapping[str, Any], schema_path: str | Path = SCHEMA_PATH) -> None:
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(manifest), key=str)
    if errors:
        raise ValueError("; ".join(f"{list(e.path)}: {e.message}" for e in errors))


def run_directory(manifest: Mapping[str, Any], root: str | Path = RUNS_ROOT) -> Path:
    """``runs/YYYY/MM-DD/<run_id>/``."""
    started = str(manifest["started_at"])
    return Path(root) / started[0:4] / f"{started[5:7]}-{started[8:10]}" / str(manifest["run_id"])


def write(
    manifest: Mapping[str, Any],
    report_markdown: str | None = None,
    root: str | Path = RUNS_ROOT,
) -> Path:
    """Write ``manifest.json`` and, when supplied, the human-readable report."""
    validate(manifest)
    directory = run_directory(manifest, root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if report_markdown is not None:
        (directory / "raport.md").write_text(report_markdown, encoding="utf-8")
    return directory


def load_all(root: str | Path = RUNS_ROOT) -> list[dict[str, Any]]:
    base = Path(root)
    if not base.exists():
        return []
    manifests: list[dict[str, Any]] = []
    for path in sorted(base.rglob("manifest.json")):
        try:
            manifests.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    manifests.sort(key=lambda manifest: str(manifest.get("started_at", "")))
    return manifests
