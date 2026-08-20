"""Calibration proposals.

Proposals are artifacts, never edits. This module reads resolved outcomes,
finds where the current parameters are demonstrably miscalibrated, and writes a
dated proposal with the evidence behind each suggested change. Applying one is a
separate, human act: copy it into a new ``config/calibration/vX.Y.yaml``, bump
the version, and merge it deliberately (AGENTS.md rules 12, 13).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .ids import iso, utc_now
from .metrics import Report

PROPOSALS_ROOT = "models/proposals"

# Roughly the share of moves that should land inside a one-standard-deviation
# range. A coverage far above this means the predicted range is too wide to say
# anything; far below means it is too narrow to be trusted.
TARGET_COVERAGE = 0.68


def _suggestion(
    parameter: str,
    current: float,
    proposed: float,
    reason: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "parameter": parameter,
        "current": round(float(current), 4),
        "proposed": round(float(proposed), 4),
        "change_pct": round((proposed - current) / current * 100, 2) if current else None,
        "reason": reason,
        "evidence": dict(evidence),
    }


def build(
    report: Report,
    calibration: Mapping[str, Any],
    *,
    minimum_sample: int = 250,
    minimum_review_sample: int = 100,
) -> dict[str, Any]:
    """Produce a proposal document. Always safe to run; never mutates config."""
    metrics = report.metrics
    probabilities = dict(calibration.get("probabilities") or {})
    expected_move = dict(calibration.get("expected_move") or {})
    suggestions: list[dict[str, Any]] = []

    # --- directional over/under-confidence ---------------------------------
    buckets = [row for row in (metrics.get("calibration") or []) if row["count"] >= 10]
    if buckets:
        weighted_gap = sum(row["gap"] * row["count"] for row in buckets) / sum(
            row["count"] for row in buckets
        )
        gain = float(probabilities.get("direction_gain", 1.10))
        if abs(weighted_gap) >= 0.05:
            # Positive gap: predicted P(up) exceeded the realized up rate.
            factor = 0.85 if weighted_gap > 0 else 1.15
            suggestions.append(
                _suggestion(
                    "probabilities.direction_gain",
                    gain,
                    gain * factor,
                    (
                        "Przewidywane P(wzrost) systematycznie przewyższa zrealizowaną częstość"
                        if weighted_gap > 0
                        else "Przewidywane P(wzrost) systematycznie zaniża zrealizowaną częstość"
                    ),
                    {
                        "weighted_gap": round(weighted_gap, 4),
                        "buckets_considered": len(buckets),
                        "observations": sum(row["count"] for row in buckets),
                    },
                )
            )

    # --- width of the expected-move range ------------------------------------
    coverage = metrics.get("expected_move_coverage") or {}
    if coverage.get("coverage") is not None and coverage.get("count", 0) >= 30:
        actual_coverage = float(coverage["coverage"])
        if abs(actual_coverage - TARGET_COVERAGE) >= 0.10:
            scale_max = float(expected_move.get("volatility_scale_max", 1.25))
            factor = 0.9 if actual_coverage > TARGET_COVERAGE else 1.1
            suggestions.append(
                _suggestion(
                    "expected_move.volatility_scale_max",
                    scale_max,
                    scale_max * factor,
                    (
                        "Przedział oczekiwanego ruchu jest za szeroki — prawie wszystko się w nim mieści"
                        if actual_coverage > TARGET_COVERAGE
                        else "Przedział oczekiwanego ruchu jest za wąski — rynek regularnie z niego wychodzi"
                    ),
                    {
                        "coverage": actual_coverage,
                        "target": TARGET_COVERAGE,
                        "observations": coverage.get("count"),
                    },
                )
            )

    # --- systematic long bias ----------------------------------------------
    direction = metrics.get("direction") or {}
    if direction.get("count", 0) >= 40 and direction.get("long_bias") is not None:
        long_bias = float(direction["long_bias"])
        if long_bias >= 0.85 or long_bias <= 0.15:
            suggestions.append(
                {
                    "parameter": "direction_score weights",
                    "current": None,
                    "proposed": None,
                    "reason": (
                        "Niemal wszystkie predykcje kierunkowe idą w jedną stronę. To wskazuje na "
                        "bias w cechach kierunkowych, a nie na rynek. Wymaga analizy przed zmianą wag."
                    ),
                    "evidence": {
                        "long_bias": long_bias,
                        "long_count": direction.get("long_count"),
                        "short_count": direction.get("short_count"),
                    },
                }
            )

    # --- setups that do not work -------------------------------------------
    for row in metrics.get("by_setup_type") or []:
        if row["count"] >= 30 and row.get("accuracy") is not None and float(row["accuracy"]) < 0.40:
            suggestions.append(
                {
                    "parameter": f"setup_type::{row['setup_type']}",
                    "current": None,
                    "proposed": None,
                    "reason": "Ten typ setupu trafia poniżej losowego wyboru w wystarczająco dużej próbie.",
                    "evidence": row,
                }
            )

    blocked_reason = None
    if report.sample_size < minimum_review_sample:
        blocked_reason = (
            f"Próba {report.sample_size} < {minimum_review_sample} wymaganych do przeglądu kalibracyjnego."
        )
    elif report.sample_size < minimum_sample:
        blocked_reason = (
            f"Próba {report.sample_size} < {minimum_sample} wymaganych do zmiany wag produkcyjnych. "
            "Propozycję można analizować, ale nie wdrażać."
        )

    return {
        "generated_at": iso(utc_now()),
        "based_on_calibration_version": calibration.get("version"),
        "sample_size": report.sample_size,
        "minimum_sample_for_change": minimum_sample,
        "blocked": blocked_reason is not None,
        "blocked_reason": blocked_reason,
        "suggestions": suggestions,
        "metrics_snapshot": {
            "brier": metrics.get("brier"),
            "brier_baseline": metrics.get("brier_baseline"),
            "brier_skill": metrics.get("brier_skill"),
            "log_loss": metrics.get("log_loss"),
            "direction": metrics.get("direction"),
            "expected_move_coverage": metrics.get("expected_move_coverage"),
            "volatility_detection": metrics.get("volatility_detection"),
        },
        "how_to_apply": [
            "Nie edytuj istniejącego pliku kalibracji — utwórz nowy config/calibration/vX.Y.yaml.",
            "Porównaj stary i nowy zestaw na tym samym oknie ewaluacyjnym oraz na zbiorze holdout.",
            "Zmianę wdraża człowiek przez pull request, z notatką migracyjną w CHANGELOG.md.",
        ],
    }


def write(proposal: Mapping[str, Any], root: str | Path = PROPOSALS_ROOT) -> Path:
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = str(proposal["generated_at"]).replace(":", "").replace("-", "")[:15]
    path = directory / f"proposal-{stamp}.json"
    path.write_text(json.dumps(proposal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def render_markdown(proposal: Mapping[str, Any]) -> str:
    lines = [
        "# Propozycja kalibracji",
        "",
        f"**Wygenerowano:** {proposal.get('generated_at')}  ",
        f"**Bazuje na kalibracji:** `v{proposal.get('based_on_calibration_version')}`  ",
        f"**Rozliczonych obserwacji:** {proposal.get('sample_size')}",
        "",
    ]
    if proposal.get("blocked"):
        lines += [f"> **Wdrożenie zablokowane.** {proposal.get('blocked_reason')}", ""]

    suggestions = proposal.get("suggestions") or []
    if not suggestions:
        lines += ["Nie wykryto systematycznego błędu kalibracji przy obecnej próbie.", ""]
    else:
        lines += ["## Sugerowane zmiany", ""]
        for index, suggestion in enumerate(suggestions, start=1):
            lines += [
                f"### {index}. `{suggestion.get('parameter')}`",
                "",
                f"- Obecnie: `{suggestion.get('current')}`",
                f"- Propozycja: `{suggestion.get('proposed')}`",
                f"- Powód: {suggestion.get('reason')}",
                "",
                "```json",
                json.dumps(suggestion.get("evidence") or {}, indent=2, ensure_ascii=False),
                "```",
                "",
            ]

    lines += ["## Jak wdrożyć", ""]
    lines += [f"{index}. {step}" for index, step in enumerate(proposal.get("how_to_apply") or [], start=1)]
    lines.append("")
    return "\n".join(lines)
