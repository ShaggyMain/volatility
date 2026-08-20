"""End-to-end test of the analyst stage, with no network access.

This exercises the real path a daily run takes: a stored scan plus an analyst
file in, immutable predictions and a Polish report out.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from volatility_ai import prediction as prediction_module
from volatility_ai.cli import main
from volatility_ai.features import build_snapshot
from volatility_ai.ids import iso, run_id, utc_now

AS_OF = date(2026, 8, 20)
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def workspace(tmp_path, monkeypatch, engine, chain, bars):
    """A throwaway repo containing one completed scan."""
    for directory in ("config", "schemas"):
        shutil.copytree(REPO / directory, tmp_path / directory)
    monkeypatch.chdir(tmp_path)

    identifier = run_id("daily", utc_now())
    snapshot = build_snapshot(chain, bars, as_of=AS_OF)
    score = engine.score(snapshot, horizon="3d")

    manifest = {
        "run_id": identifier,
        "run_type": "daily",
        "command": "dzienny run",
        "started_at": iso(utc_now()),
        "finished_at": iso(utc_now()),
        "data_cutoff": snapshot.retrieved_at,
        "versions": {
            "framework": prediction_module.FRAMEWORK_VERSION,
            "scoring": "0.1",
            "calibration": "0.1",
            "normalization": "0.2",
            "universe": "0.2",
        },
        "universe": {"mode": "dynamic", "pool_size": 140, "prescreened": 24, "deep_scanned": 1, "earnings_entries": 1},
        "predictions": [],
    }
    directory = tmp_path / "runs" / "2026" / "08-20" / identifier
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "scan.json").write_text(
        json.dumps(
            {
                "run_id": identifier,
                "data_cutoff": snapshot.retrieved_at,
                "horizon": "3d",
                "results": [
                    {
                        "ticker": "TEST",
                        "candidate": {"ticker": "TEST", "sources": ["earnings"]},
                        "snapshot": snapshot.to_json(),
                        "quant_score": {
                            "scores": score.scores.to_json(),
                            "decision": score.decision,
                            "setup_type": score.setup_type,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return tmp_path, identifier, directory


def _analyst_file(directory: Path, **overrides) -> Path:
    payload = {
        "predictions": [
            {
                "ticker": "TEST",
                "horizon": "3d",
                "thesis": "Wyniki kwartalne za dwa dni przy strukturze terminowej w backwardation.",
                "key_catalyst": "Raport kwartalny",
                "what_is_priced_in": "Rynek wycenia duży ruch, ale nie jego kierunek.",
                "invalidation_conditions": ["Publikacja zostaje przesunięta."],
                "llm_features": {
                    "catalyst": {"score": 82, "type": "EARNINGS", "why_now": "raport za 2 dni", "priced_in_score": 45},
                    "sentiment": {"score": 30, "momentum": 60},
                    "direction": {"catalyst_direction": 40},
                    "market_context": {"sector_score": 20, "regime_score": 10},
                    "news": {"velocity": 70},
                    "risks": ["Wąskie spready wygasającej serii."],
                    "contradictions": [],
                },
                "source_refs": [
                    {"source_name": "Example", "source_type": "news", "url": "https://example.test",
                     "published_at": "2026-08-19T12:00:00Z", "retrieved_at": "2026-08-20T09:00:00Z"}
                ],
            }
        ],
        "watchlist": [],
        "skipped": [],
        "lessons_applied": ["Nie stawiać kierunku na samej wysokiej IV."],
    }
    payload.update(overrides)
    path = directory / "analyst.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_predict_writes_a_valid_immutable_prediction_and_report(workspace):
    root, identifier, directory = workspace
    analyst = _analyst_file(directory)

    assert main(["predict", "--run", identifier, "--analyst", str(analyst)]) == 0

    written = list((root / "predictions").rglob("*.json"))
    assert len(written) == 1
    record = json.loads(written[0].read_text(encoding="utf-8"))
    prediction_module.validate(record)
    prediction_module.check_probability_sum(record)

    assert record["run_id"] == identifier
    assert record["thesis_source"] == "analyst"
    assert record["llm_features"]["catalyst"]["score"] == 82
    # The prediction is anchored to the scan, not to when the analyst finished.
    assert record["data_cutoff"] == "2026-08-20T09:05:00Z"

    report = (directory / "raport.md").read_text(encoding="utf-8")
    assert "## Predykcje" in report
    assert "TEST" in report
    assert "Warunki unieważnienia tezy" in report

    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["predictions"] == [record["prediction_id"]]
    assert manifest["lessons_applied"] == ["Nie stawiać kierunku na samej wysokiej IV."]


def test_analyst_features_that_break_the_schema_are_skipped(workspace):
    """Bad features must not silently become a prediction."""
    root, identifier, directory = workspace
    analyst = _analyst_file(
        directory,
        predictions=[
            {
                "ticker": "TEST",
                "horizon": "3d",
                "thesis": "Teza wystarczająco długa, aby przejść walidację schematu.",
                "llm_features": {
                    # sentiment.score is out of the -100..100 range.
                    "catalyst": {"score": 50, "type": "EARNINGS", "why_now": "x", "priced_in_score": 10},
                    "sentiment": {"score": 900, "momentum": 50},
                    "market_context": {},
                    "risks": [],
                },
            }
        ],
    )

    assert main(["predict", "--run", identifier, "--analyst", str(analyst)]) == 0
    assert list((root / "predictions").rglob("*.json")) == []

    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["predictions"] == []
    assert "schemat" in manifest["skipped"][0]["reason"]


def test_unknown_ticker_is_skipped_with_a_reason(workspace):
    root, identifier, directory = workspace
    analyst = _analyst_file(
        directory,
        predictions=[{"ticker": "NOTINSCAN", "horizon": "3d", "thesis": "A" * 40}],
    )
    assert main(["predict", "--run", identifier, "--analyst", str(analyst)]) == 0
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["skipped"][0]["ticker"] == "NOTINSCAN"
    assert "brak w skanie" in manifest["skipped"][0]["reason"]
