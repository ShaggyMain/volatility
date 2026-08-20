# Changelog

## v0.2.1 — przypięty kontrakt lintu

### Naprawione

- **Zestaw reguł lintu jest teraz zdefiniowany w repo**, a nie dziedziczony z domyślnych
  ustawień zainstalowanej wersji narzędzia. Poprzednio `pyproject.toml` dopuszczał
  `ruff>=0.6,<1` bez sekcji `[tool.ruff.lint] select`, więc ten sam commit przechodził
  lint lokalnie (ruff 0.15.8) i przewracał CI (ruff 0.16.3) — 41 znalezisk, których
  lokalnie w ogóle nie było. Zakres wersji zawężony do `>=0.16,<0.17`, reguły wypisane
  jawnie: `E, W, F, I, UP, B, C4, DTZ, ISC, RUF`.

### Zmiany w kodzie wynikające z włączonych reguł

- **Daty kalendarzowe parsowane jako daty, nie jako naiwne znaczniki czasu** (DTZ007).
  Wygasanie opcji i świece dzienne to daty bez godziny i strefy: symbole OCC składane są
  teraz bezpośrednio przez `date(...)`, a daty ISO przez `date.fromisoformat`.
- **`datetime.utcnow()` zastąpione przez `datetime.now(UTC)`** (DTZ003) — stara forma jest
  wycofywana i zwraca naiwny obiekt, co w systemie trzymającym wszystko w UTC jest pułapką.
- **Niejawne sklejanie łańcuchów w listach opakowane w nawiasy** (ISC004). W generatorach
  raportów brakujący przecinek między dwoma sąsiednimi łańcuchami po cichu je skleja
  zamiast dać dwa elementy — nawiasy czynią zamiar jawnym.
- `typing.Mapping/Sequence/Iterable` → `collections.abc`, `timezone.utc` → `UTC`,
  `zip(points, points[1:])` → `itertools.pairwise`, uporządkowane importy, skrócone linie.

Bez zmian w zachowaniu silnika, kontraktach danych i wynikach scoringu.

## v0.2.0 — warstwa runów i pętla ucząca

Nadbudowa nad frameworkiem v0.1. **Żadna reguła v0.1 nie została zmieniona ani osłabiona.**

### Dodane

- **Adaptery danych Cboe** (`src/volatility_ai/providers/cboe.py`) — łańcuchy opcji z IV,
  OI, wolumenem i greekami; historia OHLCV od 2004; dzienny wolumen opcji per symbol;
  tanie kwotowania na potrzeby pre-screenu. Bez kluczy API.
- **Cechy point-in-time** (`features.py`) — IV30, expected move ze straddle'a ATM,
  nachylenie struktury terminowej, skew 25-delta, put/call, wolumen/OI, RV20/60/252,
  akceleracja zmienności, ATR, siła względna, pozycja względem ekstremów 52-tygodniowych.
- **Deterministyczny silnik scoringu** (`scoring.py`) — implementacja wag z `config/scoring.yaml`
  z redystrybucją przy brakujących wejściach, kotwice normalizacji w `config/normalization.yaml`,
  niezależne wyniki bull/bear, prawdopodobieństwa, expected value, decyzja według progów.
- **Dynamiczne uniwersum** (`universe.py`) — pula z earnings i liderów wolumenu opcji,
  dwuetapowy skan (tani pre-screen, potem pełne łańcuchy).
- **Warstwa runów** — manifest per run (`schemas/run.schema.json`), raport po polsku,
  plik skanu do etapu analitycznego.
- **Rozliczanie wyników** (`resolve.py`) — T+1/T+3/T+5, MFE/MAE, zrealizowana zmienność,
  w osobnych artefaktach.
- **Metryki** (`metrics.py`) — Brier, log loss, Brier skill score, kubełki kalibracyjne,
  trafność kierunku, bias LONG, precyzja/czułość wykrywania zmienności, pokrycie
  oczekiwanego ruchu, podziały per setup / horyzont / decyzja.
- **Propozycje kalibracji** (`calibration.py`) — artefakty z dowodami, nigdy automatyczne
  zmiany wag.
- **Dziennik wniosków** (`lessons.py`, `LESSONS.md`) — append-only, czytany przed każdym runem.
- **Własna historia IV** (`ivhistory.py`) — jedna obserwacja na spółkę na run; odblokowuje
  IV Rank po 60 obserwacjach.
- **CLI `vol`** i komendy `/dzienny-run`, `/rozlicz`, `/metryki`.
- Dokumentacja: `docs/run-protocol.md`, `docs/learning-loop.md`, `docs/data-sources.md`,
  `RUNBOOK.md`, polski `README.md`.

### Zmiany kontraktów danych

Wszystkie **addytywne i opcjonalne** — każdy rekord v0.1 nadal przechodzi walidację.

- `schemas/prediction.schema.json`: dodane opcjonalne `run_id`, `thesis_source`,
  `llm_features`, `scoring_inputs`, `diagnostics`, `resolution_due`, `resolved_at`
  oraz `versions.normalization`.
- `schemas/llm_features.schema.json`: dodane opcjonalne sekcje `direction`, `news`,
  `earnings`; doprecyzowane właściwości `market_context`. Lista `required` bez zmian.
- `schemas/run.schema.json`: nowy kontrakt.

### Notatki migracyjne

- **Wersja Pythona:** `requires-python` obniżone z `>=3.12` na `>=3.11`, żeby pipeline
  uruchamiał się w bieżącym środowisku. Kod nie używa składni wyłącznej dla 3.12.
- **Ścieżki modułów:** implementacja przeniesiona do pakietu `volatility_ai`. Stare ścieżki
  (`scoring`, `generate_id`, `validate_prediction`, `resolve_prediction`) działają jako shimy.
- **Niespójność jednostek w v0.1:** `config/scoring.yaml` podaje `high_volatility: 80`
  i `high_uncertainty: 80`, podczas gdy schemat trzyma `confidence` i `uncertainty` w skali
  0–1. Silnik interpretuje progi powyżej 1 jako wartości procentowe i dzieli je przez 100.
  Plik konfiguracyjny został zachowany bez zmian, żeby nie łamać kontraktu v0.1.
- **Struktura terminowa:** nachylenie i skew liczone są od pierwszej serii oddalonej
  o co najmniej 7 dni, nie od najbliższej. IV opcji jedno- i dwudniowych jest zdominowana
  przez mechanikę wygasania, co dawało fałszywe rozpoznania EVENT_IV na spokojnych spółkach.
  Expected move nadal czyta najbliższą serię, bo to ona wycenia najbliższe zdarzenie.
- **`config/normalization.yaml` w wersji 0.2** — zmiana kotwic zmienia wyniki, więc każda
  przyszła korekta wymaga podbicia wersji i notatki migracyjnej.
- **`config/calibration/v0.1.yaml`** zawiera priory ustawione ręcznie, **niedopasowane
  do żadnych danych**. Pierwszy przegląd po 100 rozliczonych obserwacjach, zmiana wag po 250.
