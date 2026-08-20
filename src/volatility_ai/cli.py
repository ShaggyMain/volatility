"""Command-line interface.

The daily workflow is deliberately two commands, not one:

    vol scan       quantitative pass -- builds the universe, scores volatility,
                   and writes a scan file. No prediction is written here.
    vol predict    analyst pass -- takes catalyst features and theses, re-scores
                   against the *stored* snapshot, and writes immutable predictions.

Splitting them enforces the framework's central rule: the LLM supplies features
and narrative, the code supplies every score, probability and decision. It also
keeps the prediction anchored to the scan's ``data_cutoff`` instead of drifting
onto newer data while the analyst works.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from . import calibration as calibration_module
from . import lessons as lessons_module
from . import metrics as metrics_module
from . import prediction as prediction_module
from . import report as report_module
from . import runlog
from .features import FeatureSnapshot
from .ids import iso, run_id, utc_now
from .ivhistory import IVHistoryStore
from .providers.cboe import ProviderError, fetch_history, fetch_symbol_option_volume
from .scan import deep_scan
from .scoring import load_engine
from .universe import build_pool, load_earnings_calendar, prescreen

RESULTS_ROOT = "results"


def _load_yaml(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def _print(message: str = "") -> None:
    print(message, flush=True)


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def command_scan(args: argparse.Namespace) -> int:
    started = utc_now()
    universe_config = _load_yaml(args.universe_config)
    thresholds = _load_yaml("config/thresholds.yaml")
    engine = load_engine(args.config_dir, args.calibration)
    sizing = universe_config.get("sizing") or {}

    identifier = run_id(args.run_type, started)
    _print(f"Run {identifier}")

    _print("1/4  Pobieram wolumen opcji z poprzedniej sesji (Cboe)...")
    try:
        option_volume = fetch_symbol_option_volume()
    except ProviderError as error:
        _print(f"BŁĄD: nie udało się pobrać wolumenu opcji: {error}")
        return 2

    earnings = load_earnings_calendar(args.earnings)
    if earnings:
        _print(f"     Kalendarz earnings: {len(earnings)} pozycji ({args.earnings})")
    else:
        _print(
            f"     Kalendarz earnings: brak pliku {args.earnings}. "
            "Run rankuje wyłącznie na ruchu i aktywności opcyjnej."
        )

    pool = build_pool(universe_config, option_volume, earnings, as_of=started.date())
    _print(f"2/4  Pula kandydatów: {len(pool)}")

    prescreen_limit = args.prescreen or int(sizing.get("prescreen_maximum", 180))
    survivors = prescreen(pool, universe_config, workers=args.workers, limit=prescreen_limit)
    rejected = [c.to_json() for c in pool if c.rejected]
    _print(f"3/4  Po pre-screenie: {len(survivors)} (odrzucone: {len(rejected)})")

    deep_count = args.deep or int(sizing.get("deep_scan", 26))
    finalists = survivors[:deep_count]
    _print(f"4/4  Pełny skan łańcuchów opcji: {len(finalists)} spółek...")

    benchmark = universe_config.get("benchmark") or "SPY"
    try:
        benchmark_bars = fetch_history(benchmark)
    except ProviderError as error:
        _print(f"     Uwaga: brak historii benchmarku {benchmark} ({error}); siła względna niedostępna.")
        benchmark_bars = None

    store = IVHistoryStore(
        minimum_observations=int(thresholds.get("minimum_iv_history_observations", 60))
    )
    results = deep_scan(
        finalists,
        engine,
        as_of=started.date(),
        benchmark_bars=benchmark_bars,
        store=store,
        horizon=args.horizon,
        workers=args.workers,
        record_iv=not args.no_iv_history,
    )

    scanned = [r for r in results if r.score is not None and r.snapshot is not None]
    errors = [
        {"ticker": r.candidate.ticker, "error": r.error} for r in results if r.score is None
    ]

    finished = utc_now()
    cutoff = min((r.snapshot.retrieved_at for r in scanned), default=iso(finished))

    manifest = {
        "run_id": identifier,
        "run_type": args.run_type,
        "command": args.command_text,
        "started_at": iso(started),
        "finished_at": iso(finished),
        "data_cutoff": cutoff,
        "versions": {
            "framework": prediction_module.FRAMEWORK_VERSION,
            "scoring": str(engine.scoring.get("version", "0.1")),
            "calibration": str(engine.calibration.get("version", args.calibration)),
            "normalization": engine.normalizer.version,
            "universe": str(universe_config.get("version", "0.2")),
        },
        "universe": {
            "mode": str(universe_config.get("mode", "dynamic")),
            "pool_size": len(pool),
            "prescreened": len(survivors),
            "deep_scanned": len(scanned),
            "earnings_entries": len(earnings),
            "candidates": [c.to_json() for c in survivors[:deep_count]],
            "rejected": rejected[:60],
        },
        "predictions": [],
        "data_sources": [
            {
                "source_name": "Cboe delayed quotes",
                "source_type": "options_market_data",
                "retrieved_at": cutoff,
                "url": "https://cdn.cboe.com/api/global/delayed_quotes/",
            },
            {
                "source_name": "Cboe daily symbol option volume",
                "source_type": "options_volume",
                "retrieved_at": iso(started),
                "url": "https://www.cboe.com/us/options/market_statistics/symbol_data/",
            },
        ],
        "errors": errors,
        "notes": "Etap ilościowy. Predykcje powstają dopiero w `vol predict`.",
    }

    directory = runlog.write(manifest)
    scan_payload = {
        "run_id": identifier,
        "data_cutoff": cutoff,
        "horizon": args.horizon,
        "results": [
            {
                "ticker": r.candidate.ticker,
                "candidate": r.candidate.to_json(),
                "snapshot": r.snapshot.to_json(),
                "quant_score": {
                    "scores": r.score.scores.to_json(),
                    "probabilities": [round(p, 4) for p in r.score.probabilities],
                    "expected_move_up": r.score.expected_move_up,
                    "expected_move_down": r.score.expected_move_down,
                    "market_implied_move": r.score.market_implied_move,
                    "decision": r.score.decision,
                    "setup_type": r.score.setup_type,
                    "diagnostics": r.score.diagnostics,
                },
            }
            for r in scanned
        ],
    }
    (directory / "scan.json").write_text(
        json.dumps(scan_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    _print("")
    _print(f"{'Ticker':<8}{'Zmien.':>8}{'Akcel.':>8}{'Okazja':>8}{'IV30':>9}{'IV/RV20':>9}{'Term':>9}  Setup")
    _print("-" * 88)
    for result in scanned[: args.show]:
        values = result.snapshot.values
        scores = result.score.scores
        _print(
            f"{result.candidate.ticker:<8}"
            f"{scores.volatility:>8.1f}"
            f"{scores.volatility_acceleration:>8.1f}"
            f"{scores.opportunity:>8.1f}"
            f"{(values.get('iv30') or 0) * 100:>8.1f}%"
            f"{(values.get('iv_rv20') or 0):>9.2f}"
            f"{(values.get('term_slope') or 0) * 100:>8.1f}%"
            f"  {result.score.setup_type}"
        )
    _print("")
    _print(f"Zapisano: {directory}")
    _print(f"Następny krok: uzupełnij cechy analityczne i uruchom `vol predict --run {identifier}`.")
    return 0


# ---------------------------------------------------------------------------
# predict
# ---------------------------------------------------------------------------


def _find_run_directory(identifier: str, root: str = runlog.RUNS_ROOT) -> Path:
    matches = [path.parent for path in Path(root).rglob("manifest.json") if identifier in str(path.parent)]
    if not matches:
        raise FileNotFoundError(f"Nie znaleziono runu {identifier} w {root}/")
    return matches[0]


def command_predict(args: argparse.Namespace) -> int:
    directory = _find_run_directory(args.run)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    scan_payload = json.loads((directory / "scan.json").read_text(encoding="utf-8"))
    analyst = json.loads(Path(args.analyst).read_text(encoding="utf-8"))

    engine = load_engine(args.config_dir, args.calibration)
    llm_schema = json.loads(Path("schemas/llm_features.schema.json").read_text(encoding="utf-8"))
    llm_validator = Draft202012Validator(llm_schema, format_checker=FormatChecker())

    snapshots = {
        entry["ticker"]: FeatureSnapshot.from_json(entry["snapshot"]) for entry in scan_payload["results"]
    }

    written: list[Mapping[str, Any]] = []
    written_ids: list[str] = []
    skipped: list[dict[str, str]] = list(analyst.get("skipped") or [])

    for item in analyst.get("predictions") or []:
        ticker = str(item.get("ticker", "")).strip().upper()
        snapshot = snapshots.get(ticker)
        if snapshot is None:
            skipped.append({"ticker": ticker, "reason": "brak w skanie tego runu"})
            continue

        features = item.get("llm_features")
        if features:
            errors = sorted(llm_validator.iter_errors(features), key=str)
            if errors:
                skipped.append(
                    {
                        "ticker": ticker,
                        "reason": "cechy analityczne niezgodne ze schematem: "
                        + "; ".join(e.message for e in errors[:3]),
                    }
                )
                continue

        horizon = str(item.get("horizon") or scan_payload.get("horizon") or "3d")
        result = engine.score(snapshot, horizon=horizon, llm_features=features)

        try:
            record = prediction_module.build_record(
                snapshot,
                result,
                horizon=horizon,
                run_identifier=manifest["run_id"],
                thesis=item.get("thesis"),
                key_catalyst=item.get("key_catalyst"),
                what_is_priced_in=item.get("what_is_priced_in"),
                invalidation_conditions=item.get("invalidation_conditions"),
                llm_features=features,
                source_refs=item.get("source_refs"),
                calibration_version=str(engine.calibration.get("version", args.calibration)),
                scoring_version=str(engine.scoring.get("version", "0.1")),
                event_id=item.get("event_id"),
            )
            path = prediction_module.write(record)
        except (ValueError, prediction_module.ImmutabilityError) as error:
            skipped.append({"ticker": ticker, "reason": str(error)})
            continue

        written.append(record)
        written_ids.append(str(record["prediction_id"]))
        _print(f"  zapisano {record['prediction_id']}  {record['decision']:<32} {path}")

    manifest["predictions"] = written_ids
    manifest["watchlist"] = list(analyst.get("watchlist") or [])
    manifest["skipped"] = skipped
    manifest["lessons_applied"] = list(analyst.get("lessons_applied") or [])
    manifest["finished_at"] = iso(utc_now())
    manifest["notes"] = analyst.get("notes") or "Etap analityczny zakończony."

    watchlist = _build_watchlist(scan_payload, analyst, written_ids)
    markdown = report_module.render(
        manifest,
        written,
        watchlist=watchlist,
        skipped=skipped,
        lessons=manifest["lessons_applied"],
    )
    runlog.write(manifest, markdown)

    _print("")
    _print(f"Predykcje: {len(written)}   pominięte: {len(skipped)}   watchlista: {len(watchlist)}")
    _print(f"Raport: {directory / 'raport.md'}")
    return 0


def _build_watchlist(
    scan_payload: Mapping[str, Any],
    analyst: Mapping[str, Any],
    predicted_ids: Sequence[str],
) -> list[dict[str, Any]]:
    reasons = {
        str(entry.get("ticker", "")).upper(): entry.get("reason", "")
        for entry in analyst.get("watchlist") or []
    }
    predicted_tickers = {
        str(item.get("ticker", "")).upper() for item in analyst.get("predictions") or []
    }
    rows: list[dict[str, Any]] = []
    for entry in scan_payload.get("results") or []:
        ticker = str(entry["ticker"]).upper()
        if ticker in predicted_tickers or (reasons and ticker not in reasons):
            continue
        values = entry["snapshot"]["values"]
        scores = entry["quant_score"]["scores"]
        rows.append(
            {
                "ticker": ticker,
                "volatility": scores.get("volatility"),
                "volatility_acceleration": scores.get("volatility_acceleration"),
                "opportunity": scores.get("opportunity"),
                "iv30": values.get("iv30"),
                "iv_rv20": values.get("iv_rv20"),
                "term_slope": values.get("term_slope"),
                "reason": reasons.get(ticker, "wysoka zmienność bez potwierdzonego katalizatora"),
            }
        )
    rows.sort(key=lambda row: row.get("volatility") or 0, reverse=True)
    return rows


# ---------------------------------------------------------------------------
# resolve / metrics / calibrate / lessons / status
# ---------------------------------------------------------------------------


def command_resolve(args: argparse.Namespace) -> int:
    from . import resolve as resolve_module

    records = prediction_module.load_all()
    if not records:
        _print("Brak predykcji do rozliczenia.")
        return 0

    resolutions = resolve_module.resolve_due(records)
    counts: dict[str, int] = {}
    for resolution in resolutions:
        counts[resolution.status] = counts.get(resolution.status, 0) + 1
        if resolution.status == "resolved":
            _print(f"  rozliczono {resolution.ticker:<8} {resolution.prediction_id}")
        elif args.verbose:
            _print(f"  {resolution.status:<12} {resolution.ticker:<8} {resolution.detail or ''}")

    _print("")
    _print("Podsumowanie: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "brak zmian"))
    return 0


def command_metrics(args: argparse.Namespace) -> int:
    from . import resolve as resolve_module

    records = prediction_module.load_all()
    outcomes = resolve_module.load_outcomes()
    calibration = _load_yaml(Path(args.config_dir) / "calibration" / f"v{args.calibration}.yaml")
    thresholds = _load_yaml("config/thresholds.yaml")

    report = metrics_module.compute(records, outcomes, calibration, generated_at=iso(utc_now()))
    markdown = metrics_module.render_markdown(
        report, minimum_sample=int(thresholds.get("minimum_calibration_sample", 100))
    )

    root = Path(RESULTS_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    stamp = iso(utc_now()).replace(":", "").replace("-", "")[:15]
    (root / f"metrics-{stamp}.json").write_text(
        json.dumps(report.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (root / "metrics-latest.md").write_text(markdown, encoding="utf-8")
    _print(markdown)
    _print(f"Zapisano: {root}/metrics-latest.md")
    return 0


def command_calibrate(args: argparse.Namespace) -> int:
    from . import resolve as resolve_module

    records = prediction_module.load_all()
    outcomes = resolve_module.load_outcomes()
    calibration = _load_yaml(Path(args.config_dir) / "calibration" / f"v{args.calibration}.yaml")
    thresholds = _load_yaml("config/thresholds.yaml")

    report = metrics_module.compute(records, outcomes, calibration, generated_at=iso(utc_now()))
    proposal = calibration_module.build(
        report,
        calibration,
        minimum_sample=int(thresholds.get("minimum_recalibration_sample", 250)),
        minimum_review_sample=int(thresholds.get("minimum_calibration_sample", 100)),
    )
    path = calibration_module.write(proposal)
    markdown = calibration_module.render_markdown(proposal)
    path.with_suffix(".md").write_text(markdown, encoding="utf-8")
    _print(markdown)
    _print(f"Zapisano: {path}")
    return 0


def command_lessons(args: argparse.Namespace) -> int:
    if args.action == "add":
        lesson = lessons_module.record(
            args.category,
            args.title,
            args.observation,
            action=args.action_text,
            prediction_ids=args.prediction or [],
            run_id=args.run,
        )
        lessons_module.rebuild_markdown()
        _print(f"Dodano wniosek: {lesson.title}")
        return 0

    if args.action == "render":
        path = lessons_module.rebuild_markdown()
        _print(f"Odświeżono {path}")
        return 0

    for lesson in lessons_module.active(limit=args.limit):
        _print(f"[{lesson.recorded_at[:10]}] {lesson.category:<18} {lesson.title}")
    return 0


def command_status(args: argparse.Namespace) -> int:
    from . import resolve as resolve_module

    records = prediction_module.load_all()
    outcomes = resolve_module.load_outcomes()
    runs = runlog.load_all()
    now = utc_now()

    pending = [r for r in records if str(r.get("prediction_id")) not in outcomes]
    due = [
        r
        for r in pending
        if r.get("resolution_due")
        and datetime.fromisoformat(str(r["resolution_due"]).replace("Z", "+00:00")) <= now
    ]

    _print(f"Runy:                {len(runs)}")
    _print(f"Predykcje:           {len(records)}")
    _print(f"Rozliczone:          {len(outcomes)}")
    _print(f"Oczekujące:          {len(pending)}")
    _print(f"Gotowe do rozliczenia: {len(due)}")
    if runs:
        _print(f"Ostatni run:         {runs[-1]['run_id']} ({runs[-1]['started_at']})")
    if due:
        _print("")
        _print("Do rozliczenia teraz:")
        for record in due[:20]:
            _print(f"  {record['ticker']:<8} {record['horizon']:<6} {record['prediction_id']}")

    store = IVHistoryStore()
    history_files = list(Path(store.directory).glob("*.csv")) if Path(store.directory).exists() else []
    if history_files:
        counts = [(path.stem, sum(1 for _ in path.open()) - 1) for path in history_files]
        counts.sort(key=lambda item: -item[1])
        best = counts[0]
        _print("")
        _print(
            f"Historia IV:         {len(history_files)} spółek, najdłuższa seria {best[0]} = {best[1]} obs. "
            f"(IV Rank od {store.minimum_observations} obs.)"
        )
    return 0


def command_validate(args: argparse.Namespace) -> int:
    record = json.loads(Path(args.path).read_text(encoding="utf-8"))
    try:
        prediction_module.validate(record)
        prediction_module.check_probability_sum(record)
    except ValueError as error:
        _print(f"NIEPOPRAWNY: {error}")
        return 1
    _print("POPRAWNY")
    return 0


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vol", description="Volatility prediction research system")
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--calibration", default="0.1", help="Calibration version to score with")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Etap ilościowy: uniwersum, pre-screen, pełny skan")
    scan.add_argument("--run-type", default="daily", choices=["daily", "intraday", "event", "manual", "backfill"])
    scan.add_argument("--universe-config", default="config/universe.yaml")
    scan.add_argument("--earnings", default="data/earnings_calendar.json")
    scan.add_argument("--horizon", default="3d", choices=["1d", "3d", "5d", "event"])
    scan.add_argument("--prescreen", type=int, default=None)
    scan.add_argument("--deep", type=int, default=None)
    scan.add_argument("--workers", type=int, default=5)
    scan.add_argument("--show", type=int, default=20)
    scan.add_argument("--command-text", default=None, help="Instrukcja, która wywołała run")
    scan.add_argument("--no-iv-history", action="store_true", help="Nie dopisuj obserwacji IV")
    scan.set_defaults(func=command_scan)

    predict = subparsers.add_parser("predict", help="Etap analityczny: zapis predykcji i raportu")
    predict.add_argument("--run", required=True)
    predict.add_argument("--analyst", required=True, help="Plik JSON z cechami analitycznymi i tezami")
    predict.set_defaults(func=command_predict)

    resolve = subparsers.add_parser("resolve", help="Rozlicz predykcje po upływie horyzontu")
    resolve.add_argument("--verbose", action="store_true")
    resolve.set_defaults(func=command_resolve)

    metrics = subparsers.add_parser("metrics", help="Policz metryki na rozliczonych predykcjach")
    metrics.set_defaults(func=command_metrics)

    calibrate = subparsers.add_parser("calibrate", help="Wygeneruj propozycję kalibracji")
    calibrate.set_defaults(func=command_calibrate)

    lessons = subparsers.add_parser("lessons", help="Wnioski z rozliczonych predykcji")
    lessons.add_argument("action", nargs="?", default="list", choices=["list", "add", "render"])
    lessons.add_argument("--category", default="process", choices=list(lessons_module.CATEGORIES))
    lessons.add_argument("--title", default="")
    lessons.add_argument("--observation", default="")
    lessons.add_argument("--action-text", default=None)
    lessons.add_argument("--prediction", action="append")
    lessons.add_argument("--run", default=None)
    lessons.add_argument("--limit", type=int, default=20)
    lessons.set_defaults(func=command_lessons)

    status = subparsers.add_parser("status", help="Stan systemu")
    status.set_defaults(func=command_status)

    validate = subparsers.add_parser("validate", help="Zwaliduj plik predykcji schematem")
    validate.add_argument("path")
    validate.set_defaults(func=command_validate)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
