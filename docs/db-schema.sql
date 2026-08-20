-- SQLite schema for v0.1
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS predictions (
    prediction_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    data_cutoff_utc TEXT NOT NULL,
    horizon TEXT NOT NULL,
    event_id TEXT,
    framework_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    scoring_version TEXT NOT NULL,
    calibration_version TEXT NOT NULL,
    decision TEXT NOT NULL,
    setup_type TEXT,
    probability_up REAL NOT NULL,
    probability_flat REAL NOT NULL,
    probability_down REAL NOT NULL,
    expected_move_up REAL NOT NULL,
    expected_move_down REAL NOT NULL,
    market_implied_move REAL,
    expected_value REAL NOT NULL,
    risk_reward REAL,
    volatility_score REAL NOT NULL,
    volatility_acceleration REAL NOT NULL,
    catalyst_score REAL NOT NULL,
    bull_score REAL NOT NULL,
    bear_score REAL NOT NULL,
    opportunity_score REAL NOT NULL,
    confidence REAL NOT NULL,
    uncertainty REAL NOT NULL,
    data_quality TEXT NOT NULL,
    thesis TEXT NOT NULL,
    key_catalyst TEXT,
    what_is_priced_in TEXT NOT NULL,
    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS prediction_features (
    prediction_id TEXT PRIMARY KEY REFERENCES predictions(prediction_id),
    feature_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prediction_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id TEXT NOT NULL REFERENCES predictions(prediction_id),
    source_name TEXT NOT NULL,
    source_type TEXT,
    url TEXT,
    source_id TEXT,
    published_at_utc TEXT,
    retrieved_at_utc TEXT,
    source_hash TEXT
);

CREATE TABLE IF NOT EXISTS prediction_outcomes (
    prediction_id TEXT PRIMARY KEY REFERENCES predictions(prediction_id),
    resolved_at_utc TEXT NOT NULL,
    actual_return_1d REAL,
    actual_return_3d REAL,
    actual_return_5d REAL,
    actual_event_return REAL,
    max_favorable_excursion REAL,
    max_adverse_excursion REAL,
    realized_volatility REAL,
    move_within_expected_range INTEGER,
    direction_correct INTEGER,
    resolution_quality TEXT NOT NULL,
    outcome_source_json TEXT
);

CREATE TABLE IF NOT EXISTS model_versions (
    model_version TEXT PRIMARY KEY,
    framework_version TEXT NOT NULL,
    scoring_version TEXT NOT NULL,
    calibration_version TEXT NOT NULL,
    weights_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_predictions_ticker ON predictions(ticker);
CREATE INDEX IF NOT EXISTS idx_predictions_time ON predictions(timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_predictions_horizon ON predictions(horizon);
CREATE INDEX IF NOT EXISTS idx_outcomes_resolved ON prediction_outcomes(resolved_at_utc);
