"""Deep scan: full option chains and price history for the finalists."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from .features import FeatureSnapshot, build_snapshot
from .ivhistory import IVHistoryStore
from .providers.cboe import Bar, ProviderError, fetch_chain, fetch_history
from .scoring import ScoreResult, ScoringEngine
from .universe import Candidate


@dataclass
class ScanResult:
    candidate: Candidate
    snapshot: FeatureSnapshot | None = None
    score: ScoreResult | None = None
    error: str | None = None


def _scan_one(
    candidate: Candidate,
    *,
    as_of: date,
    benchmark_bars: Sequence[Bar] | None,
    store: IVHistoryStore,
    engine: ScoringEngine,
    horizon: str,
    record_iv: bool,
) -> ScanResult:
    try:
        chain = fetch_chain(candidate.ticker)
        bars = fetch_history(candidate.ticker)
    except ProviderError as error:
        return ScanResult(candidate=candidate, error=str(error))

    session_option_volume = sum(contract.volume for contract in chain.contracts) or None
    current_iv = chain.iv30

    stats = (
        store.stats(candidate.ticker, current_iv, session_option_volume)
        if current_iv
        else store.stats(candidate.ticker, 0.0, session_option_volume)
    )

    snapshot = build_snapshot(
        chain,
        bars,
        as_of=as_of,
        benchmark_bars=benchmark_bars,
        option_volume={"session": session_option_volume} if session_option_volume else None,
        option_volume_percentile=stats.relative_option_volume,
        iv_rank=stats.iv_rank,
        iv_percentile=stats.iv_percentile,
        iv_history_observations=stats.observations,
    )

    if record_iv and current_iv:
        # One observation per ticker per UTC day, so re-running a scan cannot
        # inflate the history that future IV Rank values depend on.
        day = chain.retrieved_at[:10]
        if not store.already_recorded_today(candidate.ticker, day):
            store.append(
                candidate.ticker,
                iv30=current_iv,
                spot=chain.spot,
                rv20=snapshot.values.get("rv20"),
                option_volume=session_option_volume,
            )

    result = engine.score(snapshot, horizon=horizon)
    return ScanResult(candidate=candidate, snapshot=snapshot, score=result)


def deep_scan(
    candidates: Sequence[Candidate],
    engine: ScoringEngine,
    *,
    as_of: date,
    benchmark_bars: Sequence[Bar] | None = None,
    store: IVHistoryStore | None = None,
    horizon: str = "3d",
    workers: int = 4,
    record_iv: bool = True,
) -> list[ScanResult]:
    """Score every finalist. Results come back ordered by opportunity score.

    Scoring here runs without analyst features: it is the quantitative pass that
    decides which names are worth an analyst's attention. Direction weights that
    depend on analyst input redistribute automatically, so the ranking reflects
    volatility and positioning only -- which is exactly what this stage should
    be ranking on.
    """
    store = store or IVHistoryStore()
    results: list[ScanResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _scan_one,
                candidate,
                as_of=as_of,
                benchmark_bars=benchmark_bars,
                store=store,
                engine=engine,
                horizon=horizon,
                record_iv=record_iv,
            ): candidate
            for candidate in candidates
        }
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(
        key=lambda result: (result.score.scores.opportunity if result.score else -1.0),
        reverse=True,
    )
    return results
