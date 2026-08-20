# Volatility — system predykcji zmienności i katalizatorów

Repozytorium, które zapisuje **każdy run** i uczy się z własnych predykcji.

Zbudowane na frameworku *Trading AI Prediction System v0.1*. Rdzeń jest niezmieniony:
**model językowy dostarcza ustrukturyzowane cechy, deterministyczny kod Pythona liczy
każdy wynik, prawdopodobieństwo i decyzję, a historyczne predykcje są niemodyfikowalne.**

## Jak się tego używa

W czacie z Claude Code piszesz komendę:

```
/dzienny-run
```

i system:

1. czyta, czego nauczył się z poprzednich runów (`LESSONS.md`, `results/metrics-latest.md`),
2. buduje kalendarz earnings na najbliższe 10 dni,
3. skanuje rynek pod kątem najwyższego potencjału zmienności (`vol scan`),
4. analizuje katalizatory dla najlepszych kandydatów i pisze cechy analityczne,
5. zapisuje niemodyfikowalne predykcje z prawdopodobieństwami i oczekiwanym ruchem (`vol predict`),
6. rozlicza predykcje, którym minął horyzont (`vol resolve`),
7. commit-uje wszystko do repo i pokazuje raport po polsku.

Pozostałe komendy: `/rozlicz` (rozliczenie + analiza błędów), `/metryki` (stan nauki systemu).

## Co dokładnie robi run

Uniwersum jest **dynamiczne** — budowane od zera przy każdym runie, nie z listy:

| Etap | Co się dzieje | Koszt |
|---|---|---|
| Pula | spółki z earnings w oknie 10 dni + liderzy wolumenu opcji z poprzedniej sesji | 1 plik CSV |
| Pre-screen | tani odczyt kwotowań (IV30, zmiana IV, ruch ceny) dla ~180 spółek | ~0,5 KB/spółkę |
| Pełny skan | łańcuchy opcji + historia cen dla ~26 finalistów | ~1 MB/spółkę |
| Predykcje | 5 pełnych predykcji + 10 pozycji na watchliście | — |

Silnik rozdziela trzy pytania, zgodnie z `prompts/volatility_catalyst_v2.0.md`:
**czy ruch będzie duży** (zmienność), **dlaczego teraz** (katalizator), **dlaczego w tę stronę**
(kierunek). Wysoka IV sama w sobie nigdy nie jest sygnałem kierunkowym.

## Skąd biorą się dane

Wszystko z publicznych źródeł, **bez kluczy API**:

| Dane | Źródło | Uwaga |
|---|---|---|
| Łańcuchy opcji: IV, OI, wolumen, greeki | Cboe delayed quotes | opóźnienie ~15 min |
| IV30 i jej zmiana dzienna | Cboe | liczone po stronie Cboe |
| Historia OHLCV od 2004 | Cboe historical | RV20/RV60/RV252, siła względna |
| Wolumen opcji per symbol | Cboe daily symbol data | tylko giełda Cboe, używane jako miara **względna** |
| Katalizatory, newsy, kalendarz earnings | wyszukiwanie w sieci przez Claude | każda pozycja z URL i datą publikacji |

**Czego nie ma i dlaczego to widać w raportach:** nie istnieje darmowe źródło historii IV,
więc `iv_rank` i `iv_percentile` są początkowo `null`. System buduje własną historię IV —
jedna obserwacja na spółkę na run — i zaczyna raportować IV Rank po 60 obserwacjach
(`config/thresholds.yaml`). Do tego czasu waga 0,15 przypisana do IV Rank rozkłada się na
pozostałe wejścia, a każda predykcja niesie flagę `IV_RANK_UNAVAILABLE`. Framework zabrania
podstawiania zmyślonej wartości w miejsce brakującej (`AGENTS.md`, reguła 8).

## Struktura repo

```
config/          wagi scoringu, kotwice normalizacji, parametry kalibracji, uniwersum
docs/            wiedza projektowa i decyzje architektoniczne
prompts/         wersjonowane instrukcje analityczne
schemas/         kontrakty danych (predykcja, cechy analityczne, run)
src/volatility_ai/
  providers/     adaptery Cboe
  features.py    cechy point-in-time
  scoring.py     deterministyczny silnik wyników
  universe.py    dynamiczne uniwersum i pre-screen
  prediction.py  zapis niemodyfikowalnych predykcji
  resolve.py     rozliczanie wyników
  metrics.py     Brier, log loss, kalibracja, wykrywanie zmienności
  calibration.py propozycje kalibracji (nigdy automatyczne zmiany)
  lessons.py     dziennik wniosków
runs/            manifesty runów + raporty po polsku
predictions/     predykcje (append-only) i rozliczone wyniki
results/         raporty metryk
models/          wersje kalibracji i propozycje zmian
data/            historia IV, kalendarz earnings, dziennik wniosków
```

## Instalacja

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
vol status
```

## Na czym polega „uczenie się"

Nie na dopisywaniu promptów. Zgodnie z `docs/calibration.md` uczenie to:

1. **kalibracja prawdopodobieństw** — czy „65% szans na wzrost" faktycznie oznacza 65%,
2. **ocena wag cech** — które sygnały niosą informację, a które są szumem,
3. **analiza per setup** — które typy zmienności działają, a które nie,
4. **analiza błędów** — zapisywana w `LESSONS.md` i czytana przed każdym kolejnym runem,
5. **walidacja out-of-sample** — zanim cokolwiek zmieni się w produkcji.

Twarde bezpieczniki: żadna zmiana wag z pojedynczego wyniku, minimum 100 rozliczonych
predykcji do pierwszego przeglądu, 250 do zmiany wag produkcyjnych, każda zmiana
wersjonowana i akceptowana przez człowieka.

## Zakres

Wersja badawcza. **System nie składa zleceń i nie jest doradztwem inwestycyjnym.**
`LONG` i `SHORT` to sygnały badawcze; `confidence` mierzy wiarygodność analizy,
a nie prawdopodobieństwo kierunku.
