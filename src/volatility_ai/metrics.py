"""Evaluation metrics over resolved predictions.

This is where "learning" actually happens in v0.1: not by rewriting prompts, but
by measuring whether the probabilities were honest, whether the volatility calls
found real movement, and where the errors concentrate (docs/calibration.md).

Nothing here changes any weight. It produces evidence; a human merges changes.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .resolve import horizon_return

DIRECTIONAL_DECISIONS = {
    "HIGH_CONVICTION_LONG",
    "LONG",
    "LONG_BIAS_WATCH",
    "SHORT_BIAS_WATCH",
    "SHORT",
    "HIGH_CONVICTION_SHORT",
}
EPSILON = 1e-12


@dataclass
class Pair:
    """One prediction joined to its resolved outcome."""

    prediction: Mapping[str, Any]
    outcome: Mapping[str, Any]
    realized: float
    bucket: str


def join(
    predictions: Iterable[Mapping[str, Any]],
    outcomes: Mapping[str, Mapping[str, Any]],
    calibration: Mapping[str, Any] | None = None,
) -> list[Pair]:
    settings = ((calibration or {}).get("outcome")) or {}
    band_ratio = float(settings.get("flat_band_vs_implied", 0.5))
    band_absolute = float(settings.get("flat_band_absolute", 0.01))

    pairs: list[Pair] = []
    for record in predictions:
        outcome = outcomes.get(str(record.get("prediction_id")))
        if not outcome:
            continue
        realized = outcome.get("horizon_return")
        if realized is None:
            realized = horizon_return(record, outcome.get("outcome") or {})
        if realized is None:
            continue

        implied = (record.get("expected_move") or {}).get("market_implied")
        band = abs(float(implied)) * band_ratio if implied else band_absolute
        if abs(realized) < band:
            bucket = "flat"
        else:
            bucket = "up" if realized > 0 else "down"
        pairs.append(Pair(record, outcome, float(realized), bucket))
    return pairs


def brier_score(pairs: Sequence[Pair]) -> float | None:
    """Multiclass Brier score over (up, flat, down). Lower is better."""
    if not pairs:
        return None
    total = 0.0
    for pair in pairs:
        probabilities = pair.prediction.get("probabilities") or {}
        for outcome_name in ("up", "flat", "down"):
            predicted = float(probabilities.get(outcome_name, 0.0))
            actual = 1.0 if pair.bucket == outcome_name else 0.0
            total += (predicted - actual) ** 2
    return total / len(pairs)


def log_loss(pairs: Sequence[Pair]) -> float | None:
    if not pairs:
        return None
    total = 0.0
    for pair in pairs:
        probabilities = pair.prediction.get("probabilities") or {}
        predicted = max(EPSILON, float(probabilities.get(pair.bucket, 0.0)))
        total -= math.log(predicted)
    return total / len(pairs)


def reference_brier(pairs: Sequence[Pair]) -> float | None:
    """Brier score of the naive forecast that always predicts the base rate.

    A model that cannot beat this has learned nothing, however good its absolute
    score looks.
    """
    if not pairs:
        return None
    counts = defaultdict(int)
    for pair in pairs:
        counts[pair.bucket] += 1
    base = {name: counts[name] / len(pairs) for name in ("up", "flat", "down")}
    total = 0.0
    for pair in pairs:
        for name in ("up", "flat", "down"):
            actual = 1.0 if pair.bucket == name else 0.0
            total += (base[name] - actual) ** 2
    return total / len(pairs)


def calibration_buckets(pairs: Sequence[Pair], width: float = 0.1) -> list[dict[str, Any]]:
    """Predicted P(up) versus how often "up" actually happened."""
    buckets: dict[int, list[Pair]] = defaultdict(list)
    for pair in pairs:
        probability = float((pair.prediction.get("probabilities") or {}).get("up", 0.0))
        buckets[min(int(probability / width), int(1 / width) - 1)].append(pair)

    rows: list[dict[str, Any]] = []
    for index in sorted(buckets):
        members = buckets[index]
        predicted = statistics.mean(
            float((pair.prediction.get("probabilities") or {}).get("up", 0.0)) for pair in members
        )
        realized = sum(1 for pair in members if pair.bucket == "up") / len(members)
        rows.append(
            {
                "bucket": f"{index * width:.1f}-{(index + 1) * width:.1f}",
                "count": len(members),
                "mean_predicted_up": round(predicted, 4),
                "realized_up_rate": round(realized, 4),
                "gap": round(predicted - realized, 4),
            }
        )
    return rows


def directional_accuracy(pairs: Sequence[Pair]) -> dict[str, Any]:
    """Accuracy over predictions that actually claimed a direction."""
    directional = [
        pair for pair in pairs if str(pair.prediction.get("decision")) in DIRECTIONAL_DECISIONS
    ]
    if not directional:
        return {"count": 0, "accuracy": None, "long_count": 0, "short_count": 0, "long_bias": None}

    correct = 0
    longs = shorts = 0
    for pair in directional:
        decision = str(pair.prediction.get("decision"))
        is_long = "LONG" in decision
        longs += int(is_long)
        shorts += int(not is_long)
        if pair.bucket == "flat":
            continue
        if (is_long and pair.bucket == "up") or (not is_long and pair.bucket == "down"):
            correct += 1

    non_flat = [pair for pair in directional if pair.bucket != "flat"]
    return {
        "count": len(directional),
        "non_flat_count": len(non_flat),
        "accuracy": round(correct / len(non_flat), 4) if non_flat else None,
        "long_count": longs,
        "short_count": shorts,
        "long_bias": round(longs / len(directional), 4),
    }


def volatility_detection(
    pairs: Sequence[Pair], calibration: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Precision and recall of the high-volatility call.

    A "positive" is a volatility score at or above the threshold; a "true event"
    is a realized move at or above the market-implied move.
    """
    settings = ((calibration or {}).get("outcome")) or {}
    score_threshold = float(settings.get("high_volatility_score", 80))
    realized_multiple = float(settings.get("high_volatility_realized_multiple", 1.0))

    true_positive = false_positive = false_negative = true_negative = 0
    for pair in pairs:
        implied = (pair.prediction.get("expected_move") or {}).get("market_implied")
        if not implied:
            continue
        predicted_high = float((pair.prediction.get("scores") or {}).get("volatility", 0)) >= score_threshold
        realized_high = abs(pair.realized) >= abs(float(implied)) * realized_multiple
        if predicted_high and realized_high:
            true_positive += 1
        elif predicted_high:
            false_positive += 1
        elif realized_high:
            false_negative += 1
        else:
            true_negative += 1

    positives = true_positive + false_positive
    actual = true_positive + false_negative
    return {
        "threshold": score_threshold,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "precision": round(true_positive / positives, 4) if positives else None,
        "recall": round(true_positive / actual, 4) if actual else None,
    }


def expected_move_coverage(pairs: Sequence[Pair]) -> dict[str, Any]:
    """How often the realized move landed inside the predicted range.

    A well-calibrated range is not one that always contains the move -- that
    means the range is too wide to be useful.
    """
    considered = [
        pair
        for pair in pairs
        if (pair.prediction.get("expected_move") or {}).get("up") is not None
    ]
    if not considered:
        return {"count": 0, "coverage": None, "mean_abs_realized": None, "mean_predicted_range": None}

    inside = 0
    realized_moves: list[float] = []
    ranges: list[float] = []
    for pair in considered:
        expected = pair.prediction["expected_move"]
        if float(expected["down"]) <= pair.realized <= float(expected["up"]):
            inside += 1
        realized_moves.append(abs(pair.realized))
        ranges.append(abs(float(expected["up"])) + abs(float(expected["down"])))
    return {
        "count": len(considered),
        "coverage": round(inside / len(considered), 4),
        "mean_abs_realized": round(statistics.mean(realized_moves), 5),
        "mean_predicted_range": round(statistics.mean(ranges), 5),
    }


def by_score_bucket(
    pairs: Sequence[Pair], score: str = "opportunity", width: int = 20
) -> list[dict[str, Any]]:
    """Realized movement grouped by score bucket -- does a higher score pay?"""
    buckets: dict[int, list[Pair]] = defaultdict(list)
    for pair in pairs:
        value = float((pair.prediction.get("scores") or {}).get(score, 0))
        buckets[min(int(value / width), int(100 / width) - 1)].append(pair)

    rows: list[dict[str, Any]] = []
    for index in sorted(buckets):
        members = buckets[index]
        moves = [abs(pair.realized) for pair in members]
        rows.append(
            {
                "bucket": f"{index * width}-{(index + 1) * width}",
                "count": len(members),
                "mean_abs_move": round(statistics.mean(moves), 5),
                "median_abs_move": round(statistics.median(moves), 5),
                "up_rate": round(sum(1 for p in members if p.bucket == "up") / len(members), 4),
            }
        )
    return rows


def grouped(pairs: Sequence[Pair], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[Pair]] = defaultdict(list)
    for pair in pairs:
        groups[str(pair.prediction.get(key, "UNKNOWN"))].append(pair)

    rows: list[dict[str, Any]] = []
    for name, members in sorted(groups.items(), key=lambda item: -len(item[1])):
        rows.append(
            {
                key: name,
                "count": len(members),
                "brier": round(brier_score(members) or 0.0, 4),
                "mean_abs_move": round(statistics.mean(abs(p.realized) for p in members), 5),
                "accuracy": directional_accuracy(members)["accuracy"],
            }
        )
    return rows


@dataclass
class Report:
    sample_size: int
    generated_at: str
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "sample_size": self.sample_size,
            "generated_at": self.generated_at,
            "metrics": self.metrics,
        }


def compute(
    predictions: Iterable[Mapping[str, Any]],
    outcomes: Mapping[str, Mapping[str, Any]],
    calibration: Mapping[str, Any] | None = None,
    *,
    generated_at: str = "",
) -> Report:
    pairs = join(predictions, outcomes, calibration)
    brier = brier_score(pairs)
    baseline = reference_brier(pairs)
    return Report(
        sample_size=len(pairs),
        generated_at=generated_at,
        metrics={
            "brier": round(brier, 5) if brier is not None else None,
            "brier_baseline": round(baseline, 5) if baseline is not None else None,
            "brier_skill": (
                round(1 - brier / baseline, 4) if brier is not None and baseline else None
            ),
            "log_loss": round(log_loss(pairs), 5) if pairs else None,
            "calibration": calibration_buckets(pairs),
            "direction": directional_accuracy(pairs),
            "volatility_detection": volatility_detection(pairs, calibration),
            "expected_move_coverage": expected_move_coverage(pairs),
            "by_opportunity": by_score_bucket(pairs, "opportunity"),
            "by_volatility": by_score_bucket(pairs, "volatility"),
            "by_setup_type": grouped(pairs, "setup_type"),
            "by_horizon": grouped(pairs, "horizon"),
            "by_decision": grouped(pairs, "decision"),
        },
    )


def render_markdown(report: Report, minimum_sample: int = 100) -> str:
    """Polish-language metrics report."""
    metrics = report.metrics
    lines = [
        "# Metryki predykcji",
        "",
        f"**Wygenerowano:** {report.generated_at}  ",
        f"**Rozliczonych predykcji:** {report.sample_size}",
        "",
    ]

    if report.sample_size < minimum_sample:
        lines += [
            (
                f"> Próba liczy {report.sample_size} obserwacji, a `config/thresholds.yaml`"
                f" wymaga {minimum_sample} do pierwszego przeglądu kalibracyjnego i 250 do"
                " zmiany wag. Poniższe liczby są orientacyjne i nie uzasadniają jeszcze"
                " żadnej zmiany modelu."
            ),
            "",
        ]

    if report.sample_size == 0:
        lines += ["Brak rozliczonych predykcji — nie ma czego mierzyć.", ""]
        return "\n".join(lines)

    lines += [
        "## Jakość prawdopodobieństw",
        "",
        "| Metryka | Wartość | Interpretacja |",
        "|---|---|---|",
        f"| Brier | {metrics.get('brier')} | im niżej, tym lepiej |",
        f"| Brier — model bazowy | {metrics.get('brier_baseline')} | zawsze przewiduje częstość bazową |",
        f"| Brier skill score | {metrics.get('brier_skill')} | powyżej 0 = model bije bazę |",
        f"| Log loss | {metrics.get('log_loss')} | karze pewne pomyłki |",
        "",
        "## Kalibracja P(wzrost)",
        "",
        "| Kubełek | Liczba | Śr. przewidywane | Zrealizowane | Odchylenie |",
        "|---|---|---|---|---|",
    ]
    for row in metrics.get("calibration") or []:
        lines.append(
            f"| {row['bucket']} | {row['count']} | {row['mean_predicted_up']} | "
            f"{row['realized_up_rate']} | {row['gap']:+.4f} |"
        )

    direction = metrics.get("direction") or {}
    detection = metrics.get("volatility_detection") or {}
    coverage = metrics.get("expected_move_coverage") or {}
    lines += [
        "",
        "## Kierunek",
        "",
        (
            f"- Predykcji kierunkowych: **{direction.get('count')}**"
            f" (bez ruchów płaskich: {direction.get('non_flat_count')})"
        ),
        f"- Trafność kierunku: **{direction.get('accuracy')}**",
        (
            f"- Udział LONG: **{direction.get('long_bias')}** — wartość trwale bliska 1,0"
            " oznacza systematyczny byczy bias"
        ),
        "",
        "## Wykrywanie wysokiej zmienności",
        "",
        f"- Próg wyniku zmienności: {detection.get('threshold')}",
        (
            f"- Precyzja: **{detection.get('precision')}** — ile sygnałów wysokiej"
            " zmienności faktycznie się zmaterializowało"
        ),
        f"- Czułość: **{detection.get('recall')}** — ile realnych dużych ruchów system wyłapał",
        (
            f"- TP {detection.get('true_positive')} / FP {detection.get('false_positive')}"
            f" / FN {detection.get('false_negative')} / TN {detection.get('true_negative')}"
        ),
        "",
        "## Pokrycie oczekiwanego ruchu",
        "",
        f"- Ruch zmieścił się w przedziale: **{coverage.get('coverage')}**",
        f"- Średni ruch bezwzględny: {coverage.get('mean_abs_realized')}",
        f"- Średnia szerokość przedziału: {coverage.get('mean_predicted_range')}",
        "",
        "## Ruch według kubełka wyniku okazji",
        "",
        "| Kubełek | Liczba | Śr. ruch | Mediana ruchu | Udział wzrostów |",
        "|---|---|---|---|---|",
    ]
    for row in metrics.get("by_opportunity") or []:
        lines.append(
            f"| {row['bucket']} | {row['count']} | {row['mean_abs_move']} | "
            f"{row['median_abs_move']} | {row['up_rate']} |"
        )

    for title, key, column in (
        ("Według typu setupu", "by_setup_type", "setup_type"),
        ("Według horyzontu", "by_horizon", "horizon"),
        ("Według decyzji", "by_decision", "decision"),
    ):
        rows = metrics.get(key) or []
        if not rows:
            continue
        lines += [
            "",
            f"## {title}",
            "",
            f"| {column} | Liczba | Brier | Śr. ruch | Trafność |",
            "|---|---|---|---|---|",
        ]
        for row in rows:
            lines.append(
                f"| {row[column]} | {row['count']} | {row['brier']} | "
                f"{row['mean_abs_move']} | {row['accuracy']} |"
            )

    lines += [
        "",
        "---",
        "",
        (
            "<sub>Raport nie zmienia żadnych wag. Zmiana scoringu wymaga procedury z"
            " `docs/calibration.md`: minimum 250 rozliczonych obserwacji, porównanie na"
            " zbiorze holdout i akceptacji człowieka.</sub>"
        ),
        "",
    ]
    return "\n".join(lines)
