"""Append-only error log that feeds the next run.

Metrics say *how much* the system is wrong. Lessons say *what* was wrong, in
language the analyst pass can act on before it writes the next prediction. The
JSONL file is the record; ``LESSONS.md`` is a rendered view of it, so the two can
never disagree.

A lesson is an observation with evidence, not an instruction to change weights.
Weight changes go through docs/calibration.md.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .ids import iso, utc_now

LESSONS_JSONL = "data/lessons.jsonl"
LESSONS_MARKDOWN = "LESSONS.md"

CATEGORIES = (
    "overconfidence",
    "underconfidence",
    "setup_failure",
    "data_quality",
    "directional_bias",
    "horizon",
    "process",
)


@dataclass
class Lesson:
    recorded_at: str
    category: str
    title: str
    observation: str
    evidence: dict[str, Any] = field(default_factory=dict)
    action: str | None = None
    prediction_ids: list[str] = field(default_factory=list)
    run_id: str | None = None
    status: str = "active"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def append(lesson: Lesson, path: str | Path = LESSONS_JSONL) -> Path:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(lesson.to_json(), ensure_ascii=False) + "\n")
    return file_path


def record(
    category: str,
    title: str,
    observation: str,
    *,
    evidence: dict[str, Any] | None = None,
    action: str | None = None,
    prediction_ids: Iterable[str] = (),
    run_id: str | None = None,
    path: str | Path = LESSONS_JSONL,
) -> Lesson:
    if category not in CATEGORIES:
        raise ValueError(f"Unknown lesson category {category!r}; expected one of {CATEGORIES}")
    lesson = Lesson(
        recorded_at=iso(utc_now()),
        category=category,
        title=title,
        observation=observation,
        evidence=evidence or {},
        action=action,
        prediction_ids=list(prediction_ids),
        run_id=run_id,
    )
    append(lesson, path)
    return lesson


def load(path: str | Path = LESSONS_JSONL) -> list[Lesson]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    lessons: list[Lesson] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        lessons.append(Lesson(**payload))
    return lessons


def active(path: str | Path = LESSONS_JSONL, limit: int | None = None) -> list[Lesson]:
    """Lessons the next run should take into account, newest first."""
    items = [lesson for lesson in load(path) if lesson.status == "active"]
    items.reverse()
    return items[:limit] if limit else items


CATEGORY_LABELS = {
    "overconfidence": "Nadmierna pewność",
    "underconfidence": "Zbyt niska pewność",
    "setup_failure": "Zawodzący typ setupu",
    "data_quality": "Jakość danych",
    "directional_bias": "Bias kierunkowy",
    "horizon": "Horyzont",
    "process": "Proces",
}


def render_markdown(lessons: list[Lesson]) -> str:
    lines = [
        "# Wnioski z rozliczonych predykcji",
        "",
        "Plik generowany z `data/lessons.jsonl` (dopisywanie, bez edycji historii).",
        "Każdy run czyta aktywne wnioski **przed** postawieniem nowych predykcji.",
        "",
        "Wniosek to obserwacja z dowodem, a nie zgoda na zmianę wag — te przechodzą",
        "procedurą z `docs/calibration.md`.",
        "",
    ]
    if not lessons:
        lines += [
            "Brak wniosków. Pojawią się po pierwszych rozliczeniach (`vol resolve`).",
            "",
        ]
        return "\n".join(lines)

    for lesson in lessons:
        label = CATEGORY_LABELS.get(lesson.category, lesson.category)
        lines += [
            f"## {lesson.recorded_at[:10]} — {lesson.title}",
            "",
            f"**Kategoria:** {label}  ",
            f"**Status:** {lesson.status}",
            "",
            lesson.observation,
            "",
        ]
        if lesson.evidence:
            evidence = json.dumps(lesson.evidence, indent=2, ensure_ascii=False)
            lines += ["**Dowód:**", "", "```json", evidence, "```", ""]
        if lesson.action:
            lines += [f"**Co z tym robić:** {lesson.action}", ""]
        if lesson.prediction_ids:
            preview = ", ".join(f"`{pid}`" for pid in lesson.prediction_ids[:8])
            more = "" if len(lesson.prediction_ids) <= 8 else f" (+{len(lesson.prediction_ids) - 8})"
            lines += [f"**Predykcje:** {preview}{more}", ""]
        lines += ["---", ""]
    return "\n".join(lines)


def rebuild_markdown(
    jsonl_path: str | Path = LESSONS_JSONL, markdown_path: str | Path = LESSONS_MARKDOWN
) -> Path:
    lessons = load(jsonl_path)
    lessons.reverse()
    output = Path(markdown_path)
    output.write_text(render_markdown(lessons), encoding="utf-8")
    return output
