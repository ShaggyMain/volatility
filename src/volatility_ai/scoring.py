"""Deterministic scoring engine.

The LLM layer never produces a score, a probability or a decision. It produces
structured features; everything below turns those features plus market data into
numbers, using only versioned configuration. Given the same snapshot and the
same config versions, this module returns the same result forever.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .features import FeatureSnapshot

Anchors = Sequence[Sequence[float]]


# ---------------------------------------------------------------------------
# v0.1 public helpers (kept for contract compatibility)
# ---------------------------------------------------------------------------


def load_scoring_config(path: str = "config/scoring.yaml") -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def weighted_score(features: Mapping[str, float], weights: Mapping[str, float]) -> float:
    """Weighted average over the features that are actually present.

    Missing inputs are dropped and their weight is redistributed across the rest
    (AGENTS.md rule 8: never invent a value to fill a hole). A score built from
    few inputs is therefore still on a 0-100 scale, but its reliability is lower
    -- that is what ``confidence`` exists to record.
    """
    available = [
        (key, float(features[key]), float(weight))
        for key, weight in weights.items()
        if features.get(key) is not None
    ]
    if not available:
        return 0.0
    total_weight = sum(weight for _, _, weight in available)
    return sum(value * weight for _, value, weight in available) / total_weight


def normalize_probabilities(up: float, flat: float, down: float) -> tuple[float, float, float]:
    total = up + flat + down
    if total <= 0:
        raise ValueError("Probabilities must have positive total")
    return (up / total, flat / total, down / total)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def interpolate(anchors: Anchors, raw: float) -> float:
    """Piecewise-linear map from a raw feature value onto 0-100, clamped."""
    points = [(float(x), float(y)) for x, y in anchors]
    if raw <= points[0][0]:
        return points[0][1]
    if raw >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= raw <= x1:
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (raw - x0) / (x1 - x0)
    return points[-1][1]


def coverage_weighted(
    values: Mapping[str, float | None], weights: Mapping[str, float]
) -> tuple[float | None, float]:
    """Return ``(score, weight_coverage)`` for a weighted composite.

    ``weight_coverage`` is the share of the configured weight that had data, and
    is the honest measure of how complete a score is.
    """
    present = {key: value for key, value in values.items() if value is not None and key in weights}
    if not present:
        return None, 0.0
    total_weight = sum(float(weights[key]) for key in present)
    all_weight = sum(float(weight) for weight in weights.values())
    score = sum(float(present[key]) * float(weights[key]) for key in present) / total_weight
    return score, (total_weight / all_weight if all_weight else 0.0)


class Normalizer:
    """Applies ``config/normalization.yaml`` to raw features."""

    def __init__(self, config: Mapping[str, Any]):
        self.version = str(config.get("version", "unknown"))
        self.anchors: dict[str, Anchors] = dict(config.get("anchors") or {})
        self.composites: dict[str, dict[str, float]] = dict(config.get("composites") or {})

    def scale(self, anchor_name: str, raw: float | None) -> float | None:
        if raw is None:
            return None
        anchors = self.anchors.get(anchor_name)
        if not anchors:
            raise KeyError(f"No normalization anchors for {anchor_name!r}")
        return interpolate(anchors, float(raw))

    def composite(self, name: str, parts: Mapping[str, float | None]) -> float | None:
        weights = self.composites.get(name)
        if not weights:
            raise KeyError(f"No composite definition for {name!r}")
        score, _ = coverage_weighted(parts, weights)
        return score


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scores:
    volatility: float
    volatility_acceleration: float
    catalyst: float
    bull: float
    bear: float
    opportunity: float
    confidence: float
    uncertainty: float

    def to_json(self) -> dict[str, float]:
        return {
            "volatility": round(self.volatility, 2),
            "volatility_acceleration": round(self.volatility_acceleration, 2),
            "catalyst": round(self.catalyst, 2),
            "bull": round(self.bull, 2),
            "bear": round(self.bear, 2),
            "opportunity": round(self.opportunity, 2),
            "confidence": round(self.confidence, 4),
            "uncertainty": round(self.uncertainty, 4),
        }


@dataclass(frozen=True)
class ScoreResult:
    ticker: str
    horizon: str
    scores: Scores
    probabilities: tuple[float, float, float]
    expected_move_up: float | None
    expected_move_down: float | None
    market_implied_move: float | None
    expected_value: float | None
    risk_reward: float | None
    decision: str
    setup_type: str
    normalized_inputs: dict[str, float | None] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# LLM feature adapter
# ---------------------------------------------------------------------------


def _signed_to_scale(value: float | None) -> float | None:
    """Map a signed -100..100 LLM feature onto the 0..100 scale (50 = neutral)."""
    if value is None:
        return None
    return max(0.0, min(100.0, (float(value) + 100.0) / 2.0))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _threshold(value: float | None, default: float) -> float:
    """Read a threshold that may be written as 0-1 or as 0-100."""
    if value is None:
        return default
    number = float(value)
    return number / 100.0 if number > 1.0 else number


def extract_llm_inputs(llm: Mapping[str, Any] | None) -> dict[str, float | None]:
    """Pull the scoring-relevant numbers out of a validated LLM feature object.

    Any section the analyst could not support with evidence should simply be
    absent; absent means ``None`` means the weight is redistributed.
    """
    if not llm:
        return {}
    catalyst = llm.get("catalyst") or {}
    sentiment = llm.get("sentiment") or {}
    direction = llm.get("direction") or {}
    market = llm.get("market_context") or {}
    news = llm.get("news") or {}

    return {
        "catalyst_quality": catalyst.get("score"),
        "priced_in": catalyst.get("priced_in_score"),
        "catalyst_novelty": catalyst.get("novelty"),
        "catalyst_surprise": catalyst.get("surprise"),
        "sentiment": _signed_to_scale(sentiment.get("score")),
        "sentiment_magnitude": abs(float(sentiment["score"])) if sentiment.get("score") is not None else None,
        "sentiment_momentum": sentiment.get("momentum"),
        "news_velocity": news.get("velocity"),
        "catalyst_direction": _signed_to_scale(direction.get("catalyst_direction")),
        "analyst_revisions": _signed_to_scale(direction.get("analyst_revisions")),
        "valuation_expectations": _signed_to_scale(direction.get("valuation_expectations")),
        "squeeze": direction.get("squeeze"),
        "sector": _signed_to_scale(market.get("sector_score")),
        "market_regime": _signed_to_scale(market.get("regime_score")),
        "contradiction_count": float(len(llm.get("contradictions") or [])),
        "risk_count": float(len(llm.get("risks") or [])),
    }


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


class ScoringEngine:
    def __init__(
        self,
        scoring_config: Mapping[str, Any],
        normalization_config: Mapping[str, Any],
        calibration_config: Mapping[str, Any],
    ):
        self.scoring = scoring_config
        self.normalizer = Normalizer(normalization_config)
        self.calibration = calibration_config
        self.thresholds = dict(scoring_config.get("thresholds") or {})

    # -- component scores ---------------------------------------------------

    def _acceleration(self, values: Mapping[str, float | None]) -> float | None:
        parts = {
            "rv_acceleration": self.normalizer.scale("rv_acceleration", values.get("rv_acceleration")),
            "iv30_change": self.normalizer.scale("iv30_change", values.get("iv30_change")),
            "term_slope_inverted": self.normalizer.scale("term_slope_inverted", values.get("term_slope")),
            "volume_oi_ratio": self.normalizer.scale("volume_oi_ratio", values.get("volume_oi_ratio")),
            "stock_volume_ratio": self.normalizer.scale(
                "stock_volume_ratio", values.get("stock_volume_ratio")
            ),
        }
        return self.normalizer.composite("volatility_acceleration", parts)

    def _liquidity(self, values: Mapping[str, float | None]) -> float | None:
        parts = {
            "spread_liquidity": self.normalizer.scale(
                "spread_liquidity", values.get("median_spread_pct")
            ),
            "option_volume_level": self.normalizer.scale(
                "option_volume_level", values.get("option_volume")
            ),
        }
        return self.normalizer.composite("liquidity", parts)

    def _options_positioning(self, values: Mapping[str, float | None]) -> float | None:
        parts = {
            "put_call_volume_inverted": self.normalizer.scale(
                "put_call_volume_inverted", values.get("put_call_volume")
            ),
            "skew_inverted": self.normalizer.scale("skew_inverted", values.get("skew_25d")),
        }
        return self.normalizer.composite("options_positioning", parts)

    def _price_action(self, values: Mapping[str, float | None]) -> float | None:
        parts = {
            "return_20d": self.normalizer.scale("return_20d", values.get("return_20d")),
            "pct_from_52w_high": self.normalizer.scale(
                "pct_from_52w_high", values.get("pct_from_52w_high")
            ),
        }
        return self.normalizer.composite("price_action", parts)

    def _relative_strength(self, values: Mapping[str, float | None]) -> float | None:
        parts = {
            "relative_strength_20d": self.normalizer.scale(
                "relative_strength_20d", values.get("relative_strength_20d")
            ),
            "relative_strength_5d": self.normalizer.scale(
                "relative_strength_5d", values.get("relative_strength_5d")
            ),
        }
        return self.normalizer.composite("relative_strength", parts)

    # -- confidence ---------------------------------------------------------

    def _confidence(
        self,
        snapshot: FeatureSnapshot,
        liquidity: float | None,
        bull: float,
        bear: float,
        llm_inputs: Mapping[str, float | None],
    ) -> float:
        settings = self.calibration.get("confidence") or {}
        weights = settings.get("weights") or {}

        freshness = 1.0
        for flag in snapshot.quality_flags:
            if flag.startswith("STALE_PRICE_HISTORY"):
                freshness = 0.4

        evidence_total = bull + bear
        agreement = abs(bull - bear) / evidence_total if evidence_total > 0 else 0.0

        parts: dict[str, float | None] = {
            "feature_coverage": snapshot.coverage,
            "liquidity": (liquidity / 100.0) if liquidity is not None else None,
            "data_freshness": freshness,
            "signal_agreement": agreement,
        }
        base, _ = coverage_weighted(parts, weights)
        confidence = base if base is not None else 0.3

        penalty = float(settings.get("quality_flag_penalty", 0.06))
        confidence -= penalty * len(snapshot.quality_flags)
        contradictions = llm_inputs.get("contradiction_count") or 0.0
        confidence -= penalty * float(contradictions)

        floor = float(settings.get("floor", 0.10))
        ceiling = float(settings.get("ceiling", 0.90))
        return max(floor, min(ceiling, confidence))

    # -- probabilities and expected move ------------------------------------

    def _probabilities(self, volatility: float, edge: float, confidence: float) -> tuple[float, float, float]:
        settings = self.calibration.get("probabilities") or {}
        flat_base = float(settings.get("flat_base", 0.34))
        flat_min = float(settings.get("flat_min", 0.10))
        flat_max = float(settings.get("flat_max", 0.45))
        gain = float(settings.get("direction_gain", 1.10))

        flat = flat_base * (1.0 - (volatility - 50.0) / 100.0)
        flat = max(flat_min, min(flat_max, flat))

        tilt = math.tanh(gain * edge * confidence)
        directional = 1.0 - flat
        return normalize_probabilities(
            directional * (1.0 + tilt) / 2.0,
            flat,
            directional * (1.0 - tilt) / 2.0,
        )

    def _expected_move(
        self,
        values: Mapping[str, float | None],
        meta: Mapping[str, Any],
        horizon: str,
        volatility: float,
        tilt: float,
    ) -> tuple[float | None, float | None, float | None, str]:
        settings = self.calibration.get("expected_move") or {}
        horizon_days = (settings.get("horizon_trading_days") or {}).get(horizon)
        if horizon_days is None:
            horizon_days = 3
        horizon_days = float(horizon_days)

        source = "none"
        market_implied: float | None = None

        straddle = values.get("expected_move_front")
        front_dte = meta.get("front_dte")
        if straddle and front_dte:
            if horizon == "event":
                market_implied = float(straddle)
                source = "front_straddle"
            else:
                # Straddles price the move to their own expiry; rescale by sqrt(time).
                front_trading_days = max(1.0, float(front_dte) * 252.0 / 365.0)
                market_implied = float(straddle) * math.sqrt(horizon_days / front_trading_days)
                source = "front_straddle_scaled"
        elif values.get("iv30"):
            market_implied = float(values["iv30"]) * math.sqrt(horizon_days / 252.0)
            source = "iv30"

        if market_implied is None:
            return None, None, None, source

        scale_min = float(settings.get("volatility_scale_min", 0.85))
        scale_max = float(settings.get("volatility_scale_max", 1.25))
        stretch = float(settings.get("directional_stretch", 0.35))

        scale = scale_min + (scale_max - scale_min) * (volatility / 100.0)
        base = market_implied * scale
        up = base * (1.0 + stretch * max(tilt, 0.0))
        down = -base * (1.0 + stretch * max(-tilt, 0.0))
        return up, down, market_implied, source

    # -- decision -----------------------------------------------------------

    def _decide(
        self,
        *,
        volatility: float,
        opportunity: float,
        catalyst: float,
        confidence: float,
        probabilities: tuple[float, float, float],
        risk_reward: float | None,
        has_direction_evidence: bool,
    ) -> str:
        probability_up, _, probability_down = probabilities
        directional = max(probability_up, probability_down)
        side_long = probability_up >= probability_down

        high_volatility = _threshold(self.thresholds.get("high_volatility"), 0.80) * 100
        high_opportunity = _threshold(self.thresholds.get("high_conviction_opportunity"), 0.80) * 100
        high_probability = _threshold(self.thresholds.get("high_conviction_probability"), 0.70)
        high_confidence = _threshold(self.thresholds.get("high_conviction_confidence"), 0.75)
        minimum_catalyst = _threshold(self.thresholds.get("minimum_catalyst_score"), 0.70) * 100
        minimum_risk_reward = float(self.thresholds.get("minimum_risk_reward", 1.5))
        watch_probability = _threshold(self.thresholds.get("watch_direction_confidence"), 0.60)

        if not has_direction_evidence:
            # No directional evidence at all: the only honest read is volatility.
            return "HIGH_VOLATILITY_NO_DIRECTION" if volatility >= high_volatility else "NO_TRADE"

        meets_risk_reward = risk_reward is None or risk_reward >= minimum_risk_reward

        if (
            opportunity >= high_opportunity
            and directional >= high_probability
            and confidence >= high_confidence
            and catalyst >= minimum_catalyst
            and meets_risk_reward
        ):
            return "HIGH_CONVICTION_LONG" if side_long else "HIGH_CONVICTION_SHORT"

        if directional >= high_probability and catalyst >= minimum_catalyst and meets_risk_reward:
            return "LONG" if side_long else "SHORT"

        if directional >= watch_probability:
            return "LONG_BIAS_WATCH" if side_long else "SHORT_BIAS_WATCH"

        if volatility >= high_volatility:
            return "HIGH_VOLATILITY_NO_DIRECTION"

        return "NO_TRADE"

    def _setup_type(self, values: Mapping[str, float | None], llm: Mapping[str, Any] | None) -> str:
        """Classify the volatility regime, following prompts/volatility_catalyst_v2.0.md."""
        if llm:
            declared = ((llm.get("catalyst") or {}).get("type") or "").strip().upper()
            if declared in {"EARNINGS", "EARNINGS_EVENT"}:
                return "EVENT_IV_EARNINGS"
            if declared in {"FDA", "TRIAL", "REGULATORY", "MERGER", "LEGAL"}:
                return f"EVENT_IV_{declared}"

        term_slope = values.get("term_slope")
        iv_rv20 = values.get("iv_rv20")
        squeeze = values.get("relative_option_volume")

        if term_slope is not None and term_slope < -0.03:
            return "EVENT_IV"
        if iv_rv20 is not None and iv_rv20 < 0.9:
            return "PANIC_IV" if (values.get("return_5d") or 0) < -0.08 else "STRUCTURAL_IV"
        if squeeze is not None and squeeze > 90:
            return "SPECULATIVE_IV"
        return "STRUCTURAL_IV"

    # -- entry point --------------------------------------------------------

    def score(
        self,
        snapshot: FeatureSnapshot,
        *,
        horizon: str = "3d",
        llm_features: Mapping[str, Any] | None = None,
    ) -> ScoreResult:
        values = snapshot.values
        llm = extract_llm_inputs(llm_features)

        acceleration = self._acceleration(values)
        liquidity = self._liquidity(values)
        positioning = self._options_positioning(values)
        price_action = self._price_action(values)
        relative_strength = self._relative_strength(values)

        # --- volatility score, weights from config/scoring.yaml -------------
        volatility_inputs: dict[str, float | None] = {
            "iv_rank_percentile": values.get("iv_rank"),
            "iv_vs_rv20": self.normalizer.scale("iv_rv20", values.get("iv_rv20")),
            "iv_vs_rv252": self.normalizer.scale("iv_rv252", values.get("iv_rv252")),
            "iv_change": self.normalizer.scale("iv30_change", values.get("iv30_change")),
            "volatility_acceleration": acceleration,
            "options_activity": self.normalizer.scale("volume_oi_ratio", values.get("volume_oi_ratio")),
            "relative_options_volume": values.get("relative_option_volume"),
            "historical_volatility": self.normalizer.scale("rv20_level", values.get("rv20")),
            "expected_move": self.normalizer.scale("expected_move", values.get("expected_move_front")),
            "news_velocity": llm.get("news_velocity"),
            "catalyst_quality": llm.get("catalyst_quality"),
        }
        volatility, volatility_coverage = coverage_weighted(
            volatility_inputs, self.scoring["volatility_score"]
        )
        volatility = volatility if volatility is not None else 0.0

        # --- directional evidence -------------------------------------------
        direction_inputs: dict[str, float | None] = {
            "catalyst_direction": llm.get("catalyst_direction"),
            "sentiment": llm.get("sentiment"),
            "sentiment_momentum": self._sentiment_momentum(llm),
            "analyst_revisions": llm.get("analyst_revisions"),
            "relative_strength": relative_strength,
            "options_positioning": positioning,
            "sector": llm.get("sector"),
            "market_regime": llm.get("market_regime"),
            "valuation_expectations": llm.get("valuation_expectations"),
            "price_action": price_action,
            "squeeze": llm.get("squeeze"),
        }
        direction_weights = self.scoring["direction_score"]
        bull, bear, direction_coverage = self._bull_bear(direction_inputs, direction_weights)

        edge = (bull - bear) / 100.0
        priced_in = llm.get("priced_in")
        if priced_in is not None:
            # A catalyst the market already understands carries less directional edge.
            edge *= 1.0 - 0.5 * (float(priced_in) / 100.0)

        catalyst = llm.get("catalyst_quality")
        catalyst_score = float(catalyst) if catalyst is not None else 0.0

        confidence = self._confidence(snapshot, liquidity, bull, bear, llm)
        uncertainty = _clamp01(1.0 - confidence)

        probabilities = self._probabilities(volatility, edge, confidence)
        tilt = math.tanh(
            float((self.calibration.get("probabilities") or {}).get("direction_gain", 1.10))
            * edge
            * confidence
        )
        move_up, move_down, market_implied, move_source = self._expected_move(
            values, snapshot.meta, horizon, volatility, tilt
        )

        expected_value: float | None = None
        risk_reward: float | None = None
        if move_up is not None and move_down is not None:
            probability_up, _, probability_down = probabilities
            expected_value = probability_up * move_up + probability_down * move_down
            if move_down != 0:
                risk_reward = abs(move_up / move_down)

        # --- opportunity score ----------------------------------------------
        opportunity_inputs: dict[str, float | None] = {
            "catalyst_quality": catalyst,
            "direction_confidence": abs(bull - bear),
            "volatility": volatility,
            "volatility_acceleration": acceleration,
            "sentiment": llm.get("sentiment_magnitude"),
            "options_positioning": abs(positioning - 50.0) * 2.0 if positioning is not None else None,
            "expected_value": self._expected_value_score(expected_value, market_implied),
            "liquidity": liquidity,
        }
        opportunity, opportunity_coverage = coverage_weighted(
            opportunity_inputs, self.scoring["opportunity_score"]
        )
        opportunity = opportunity if opportunity is not None else 0.0

        has_direction_evidence = direction_coverage > 0.0 and (bull + bear) > 5.0
        decision = self._decide(
            volatility=volatility,
            opportunity=opportunity,
            catalyst=catalyst_score,
            confidence=confidence,
            probabilities=probabilities,
            risk_reward=risk_reward,
            has_direction_evidence=has_direction_evidence,
        )
        if expected_value is not None and decision in {"SHORT", "HIGH_CONVICTION_SHORT", "SHORT_BIAS_WATCH"}:
            # Report expected value for the side actually being proposed.
            expected_value = -expected_value
            if risk_reward is not None and risk_reward != 0:
                risk_reward = 1.0 / risk_reward

        scores = Scores(
            volatility=volatility,
            volatility_acceleration=acceleration if acceleration is not None else 0.0,
            catalyst=catalyst_score,
            bull=bull,
            bear=bear,
            opportunity=opportunity,
            confidence=confidence,
            uncertainty=uncertainty,
        )

        return ScoreResult(
            ticker=snapshot.ticker,
            horizon=horizon,
            scores=scores,
            probabilities=probabilities,
            expected_move_up=move_up,
            expected_move_down=move_down,
            market_implied_move=market_implied,
            expected_value=expected_value,
            risk_reward=risk_reward,
            decision=decision,
            setup_type=self._setup_type(values, llm_features),
            normalized_inputs={
                "volatility": volatility_inputs,
                "direction": direction_inputs,
                "opportunity": opportunity_inputs,
            },
            diagnostics={
                "volatility_weight_coverage": round(volatility_coverage, 4),
                "direction_weight_coverage": round(direction_coverage, 4),
                "opportunity_weight_coverage": round(opportunity_coverage, 4),
                "feature_coverage": round(snapshot.coverage, 4),
                "edge": round(edge, 4),
                "tilt": round(tilt, 4),
                "expected_move_source": move_source,
                "normalization_version": self.normalizer.version,
                "quality_flags": list(snapshot.quality_flags),
            },
        )

    @staticmethod
    def _sentiment_momentum(llm: Mapping[str, float | None]) -> float | None:
        """Momentum is a magnitude; it takes its sign from sentiment itself."""
        momentum = llm.get("sentiment_momentum")
        sentiment = llm.get("sentiment")
        if momentum is None or sentiment is None:
            return None
        direction = 1.0 if float(sentiment) >= 50.0 else -1.0
        return 50.0 + direction * float(momentum) / 2.0

    @staticmethod
    def _bull_bear(
        inputs: Mapping[str, float | None], weights: Mapping[str, float]
    ) -> tuple[float, float, float]:
        """Split directional inputs into independent bull and bear evidence.

        Each input sits on 0-100 with 50 neutral. Distance above 50 is bullish
        evidence, distance below is bearish. Keeping the two sides separate means
        a stock with strong but conflicting evidence scores high on both, which
        is information the single net number would destroy.
        """
        bull_parts: dict[str, float | None] = {}
        bear_parts: dict[str, float | None] = {}
        for key, value in inputs.items():
            if value is None:
                bull_parts[key] = None
                bear_parts[key] = None
                continue
            deviation = (float(value) - 50.0) * 2.0
            bull_parts[key] = max(0.0, deviation)
            bear_parts[key] = max(0.0, -deviation)
        bull, coverage = coverage_weighted(bull_parts, weights)
        bear, _ = coverage_weighted(bear_parts, weights)
        return (bull or 0.0), (bear or 0.0), coverage

    @staticmethod
    def _expected_value_score(expected_value: float | None, market_implied: float | None) -> float | None:
        """Score expected value relative to the move the market is charging for."""
        if expected_value is None or not market_implied:
            return None
        ratio = expected_value / market_implied
        return interpolate([[-0.5, 0], [-0.1, 25], [0.0, 45], [0.15, 70], [0.35, 90], [0.60, 100]], ratio)


def load_engine(config_dir: str = "config", calibration_version: str = "0.1") -> ScoringEngine:
    base = Path(config_dir)
    scoring = yaml.safe_load((base / "scoring.yaml").read_text(encoding="utf-8"))
    normalization = yaml.safe_load((base / "normalization.yaml").read_text(encoding="utf-8"))
    calibration_path = base / "calibration" / f"v{calibration_version}.yaml"
    calibration = yaml.safe_load(calibration_path.read_text(encoding="utf-8"))
    return ScoringEngine(scoring, normalization, calibration)
