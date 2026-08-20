"""Polish-language run report.

The report is the human-readable face of a run; ``manifest.json`` and the
prediction files remain the machine-readable record. Nothing is computed here --
this module only formats what the deterministic layer already decided.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

DECISION_LABELS = {
    "HIGH_CONVICTION_LONG": "LONG (wysoka konwikcja)",
    "LONG": "LONG",
    "LONG_BIAS_WATCH": "Obserwacja z przechyłem LONG",
    "HIGH_VOLATILITY_NO_DIRECTION": "Wysoka zmienność, brak kierunku",
    "NO_TRADE": "Brak setupu",
    "SHORT_BIAS_WATCH": "Obserwacja z przechyłem SHORT",
    "SHORT": "SHORT",
    "HIGH_CONVICTION_SHORT": "SHORT (wysoka konwikcja)",
}

SETUP_LABELS = {
    "EVENT_IV": "EVENT IV — zmienność wokół zdarzenia",
    "EVENT_IV_EARNINGS": "EVENT IV — wyniki kwartalne",
    "STRUCTURAL_IV": "STRUCTURAL IV — zmienność strukturalna",
    "PANIC_IV": "PANIC IV — zmienność paniczna",
    "SPECULATIVE_IV": "SPECULATIVE IV — zmienność spekulacyjna",
    "SQUEEZE_IV": "SQUEEZE IV — zmienność wymuszona pozycjonowaniem",
}

QUALITY_LABELS = {"HIGH": "wysoka", "MEDIUM": "średnia", "LOW": "niska"}


def _percent(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value * 100:.{digits}f}%"


def _number(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _thesis_source_label(source: str | None) -> str:
    return "analityk" if source == "analyst" else "wyliczenie deterministyczne"


def _label(mapping: Mapping[str, str], key: str | None) -> str:
    if not key:
        return "—"
    return mapping.get(key, key)


def render_prediction(record: Mapping[str, Any], index: int) -> str:
    scores = record["scores"]
    probabilities = record["probabilities"]
    expected_move = record["expected_move"]
    features = (record.get("features") or {}).get("values") or {}
    diagnostics = record.get("diagnostics") or {}

    lines = [
        f"### {index}. {record['ticker']} — {_label(DECISION_LABELS, record.get('decision'))}",
        "",
        f"*Typ setupu:* {_label(SETUP_LABELS, record.get('setup_type'))}  ",
        f"*Horyzont:* {record.get('horizon')}  ",
        f"*Jakość danych:* {QUALITY_LABELS.get(record.get('data_quality'), record.get('data_quality'))}  ",
        f"*ID predykcji:* `{record['prediction_id']}`",
        "",
        "| Wskaźnik | Wartość |",
        "|---|---|",
        f"| Zmienność | {_number(scores.get('volatility'), 1)} / 100 |",
        f"| Akceleracja zmienności | {_number(scores.get('volatility_acceleration'), 1)} / 100 |",
        f"| Katalizator | {_number(scores.get('catalyst'), 1)} / 100 |",
        f"| Byki / niedźwiedzie | {_number(scores.get('bull'), 1)} / {_number(scores.get('bear'), 1)} |",
        f"| Okazja | {_number(scores.get('opportunity'), 1)} / 100 |",
        f"| Pewność analizy | {_number(scores.get('confidence'), 2)} |",
        f"| Niepewność | {_number(scores.get('uncertainty'), 2)} |",
        (
            f"| P(wzrost) / P(bez ruchu) / P(spadek) |"
            f" {_percent(probabilities.get('up'), 1)} /"
            f" {_percent(probabilities.get('flat'), 1)} /"
            f" {_percent(probabilities.get('down'), 1)} |"
        ),
        (
            f"| Oczekiwany ruch w górę / w dół |"
            f" {_percent(expected_move.get('up'))} / {_percent(expected_move.get('down'))} |"
        ),
        f"| Ruch wyceniany przez rynek | {_percent(expected_move.get('market_implied'))} |",
        f"| Wartość oczekiwana | {_percent(record.get('expected_value'))} |",
        f"| Stosunek zysk/ryzyko | {_number(record.get('risk_reward'))} |",
        "",
        "**Dane rynkowe w momencie predykcji**",
        "",
        "| Cecha | Wartość |",
        "|---|---|",
        f"| IV30 | {_percent(features.get('iv30'), 1)} |",
        f"| Zmiana IV30 (1 sesja) | {_percent(features.get('iv30_change'), 2)} |",
        f"| IV Rank | {_number(features.get('iv_rank'), 1)} |",
        f"| IV / RV20 | {_number(features.get('iv_rv20'))} |",
        f"| RV20 | {_percent(features.get('rv20'), 1)} |",
        f"| Nachylenie struktury terminowej | {_percent(features.get('term_slope'), 2)} |",
        f"| Put/call (wolumen) | {_number(features.get('put_call_volume'))} |",
        f"| Wolumen opcji / OI | {_number(features.get('volume_oi_ratio'))} |",
        f"| Ruch 5 sesji | {_percent(features.get('return_5d'), 1)} |",
        f"| Siła względna 20 sesji vs benchmark | {_percent(features.get('relative_strength_20d'), 1)} |",
        "",
        f"**Teza** ({_thesis_source_label(record.get('thesis_source'))})",
        "",
        record.get("thesis", "—"),
        "",
    ]

    if record.get("key_catalyst"):
        lines += ["**Katalizator**", "", record["key_catalyst"], ""]

    lines += ["**Co jest już w cenie**", "", record.get("what_is_priced_in", "—"), ""]

    conditions = record.get("invalidation_conditions") or []
    if conditions:
        lines += ["**Warunki unieważnienia tezy**", ""]
        lines += [f"- {condition}" for condition in conditions]
        lines.append("")

    lines += [
        f"**Rozliczenie:** nie wcześniej niż {record.get('resolution_due', '—')}",
        "",
        (
            f"<sub>Pokrycie wag: zmienność {diagnostics.get('volatility_weight_coverage', '—')},"
            f" kierunek {diagnostics.get('direction_weight_coverage', '—')},"
            f" okazja {diagnostics.get('opportunity_weight_coverage', '—')}."
            f" Źródło oczekiwanego ruchu: {diagnostics.get('expected_move_source', '—')}.</sub>"
        ),
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def render(
    manifest: Mapping[str, Any],
    predictions: Sequence[Mapping[str, Any]],
    *,
    watchlist: Sequence[Mapping[str, Any]] = (),
    skipped: Sequence[Mapping[str, Any]] = (),
    lessons: Sequence[str] = (),
) -> str:
    universe = manifest.get("universe") or {}
    versions = manifest.get("versions") or {}
    started = str(manifest.get("started_at", ""))

    lines = [
        f"# Run {manifest.get('run_type', 'daily')} — {started[:10]}",
        "",
        f"**Run ID:** `{manifest['run_id']}`  ",
        f"**Start:** {started}  ",
        f"**Koniec:** {manifest.get('finished_at')}  ",
        f"**Data cutoff:** {manifest.get('data_cutoff')}  ",
        f"**Komenda:** {manifest.get('command') or '—'}",
        "",
        (
            f"**Wersje:** framework `{versions.get('framework')}`,"
            f" scoring `{versions.get('scoring')}`,"
            f" kalibracja `{versions.get('calibration')}`,"
            f" normalizacja `{versions.get('normalization')}`,"
            f" uniwersum `{versions.get('universe')}`"
        ),
        "",
        "## Zakres skanu",
        "",
        "| Etap | Liczba |",
        "|---|---|",
        f"| Pula kandydatów ({universe.get('mode', '—')}) | {universe.get('pool_size', 0)} |",
        f"| Po pre-screenie | {universe.get('prescreened', 0)} |",
        f"| Pełny skan łańcuchów opcji | {universe.get('deep_scanned', 0)} |",
        f"| Spółki z earnings w oknie | {universe.get('earnings_entries', 0)} |",
        f"| Zapisane predykcje | {len(predictions)} |",
        "",
    ]

    if lessons:
        lines += ["## Wnioski uwzględnione w tym runie", ""]
        lines += [f"- {lesson}" for lesson in lessons]
        lines.append("")

    if predictions:
        lines += ["## Predykcje", ""]
        for index, record in enumerate(predictions, start=1):
            lines.append(render_prediction(record, index))
    else:
        lines += [
            "## Predykcje",
            "",
            (
                "Ten run nie wygenerował żadnej predykcji. To wynik, nie brak wyniku: żadna"
                " spółka z przeskanowanego uniwersum nie przekroczyła progów z"
                " `config/scoring.yaml`, albo zabrakło danych krytycznych i system zatrzymał"
                " się zamiast zgadywać."
            ),
            "",
        ]

    if watchlist:
        lines += [
            "## Watchlista",
            "",
            "Spółki warte obserwacji, bez pełnej predykcji.",
            "",
            "| Ticker | Zmienność | Akceleracja | Okazja | IV30 | IV/RV20 | Struktura term. | Powód |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for entry in watchlist:
            lines.append(
                f"| {entry.get('ticker')} | {_number(entry.get('volatility'), 1)} |"
                f" {_number(entry.get('volatility_acceleration'), 1)} |"
                f" {_number(entry.get('opportunity'), 1)} |"
                f" {_percent(entry.get('iv30'), 1)} | {_number(entry.get('iv_rv20'))} |"
                f" {_percent(entry.get('term_slope'), 2)} | {entry.get('reason', '—')} |"
            )
        lines.append("")

    if skipped:
        lines += [
            "## Pominięte po pełnym skanie",
            "",
            "| Ticker | Powód |",
            "|---|---|",
        ]
        for entry in skipped:
            lines.append(f"| {entry.get('ticker')} | {entry.get('reason', '—')} |")
        lines.append("")

    sources = manifest.get("data_sources") or []
    if sources:
        lines += ["## Źródła danych", "", "| Źródło | Typ | Pobrano |", "|---|---|---|"]
        for source in sources:
            lines.append(
                f"| {source.get('source_name')} | {source.get('source_type', '—')} | "
                f"{source.get('retrieved_at', '—')} |"
            )
        lines.append("")

    errors = manifest.get("errors") or []
    if errors:
        lines += ["## Błędy i braki danych", "", "| Ticker | Błąd |", "|---|---|"]
        for error in errors:
            lines.append(f"| {error.get('ticker', '—')} | {error.get('error', '—')} |")
        lines.append("")

    lines += [
        "---",
        "",
        (
            "<sub>Run badawczy. Nie jest rekomendacją inwestycyjną ani zleceniem."
            " Predykcje są zapisane w `predictions/` i nie podlegają późniejszej edycji;"
            " wyniki trafią do `predictions/resolved/` po upływie horyzontu.</sub>"
        ),
        "",
    ]
    return "\n".join(lines)
