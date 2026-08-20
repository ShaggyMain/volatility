---
description: Rozlicz dojrzałe predykcje, policz metryki i zapisz wnioski
---

# Rozliczenie

Argumenty: `$ARGUMENTS`.

## Krok 1 — Rozlicz

```bash
vol resolve --verbose
```

Rozlicza wyłącznie predykcje, którym minął horyzont i istnieją zamknięte sesje po dacie
predykcji. Zapisuje osobne artefakty w `predictions/resolved/`. **Nigdy** nie modyfikuje
pliku predykcji (`AGENTS.md`, reguły 1 i 7).

## Krok 2 — Policz metryki

```bash
vol metrics
```

## Krok 3 — Analiza błędów

Przeczytaj `results/metrics-latest.md` i zastosuj `prompts/calibration_review_v1.0.md`.
Szukaj:

- **Nadmiernej pewności** — kolumna „Odchylenie" w kalibracji trwale dodatnia.
- **Biasu kierunkowego** — „Udział LONG" bliski 1,0 lub 0,0.
- **Zawodzących setupów** — typ setupu z trafnością poniżej 0,40 przy próbie ≥ 30.
- **Złego kalibrowania przedziału** — pokrycie oczekiwanego ruchu daleko od ~0,68.
- **Braków danych** — czy `data_quality: LOW` koreluje z gorszymi wynikami.

Zapisz każdy potwierdzony wzorzec:

```bash
vol lessons add --category <kategoria> --title "..." --observation "..." \
  --action-text "co konkretnie zrobić inaczej w kolejnym runie"
```

Wniosek to obserwacja z dowodem. Jedna nietrafiona predykcja **nie jest** wnioskiem
(reguła 12 — żadna zmiana modelu z pojedynczego wyniku).

## Krok 4 — Propozycja kalibracji (dopiero przy dużej próbie)

```bash
vol calibrate
```

Generuje propozycję w `models/proposals/`. **Nie wdrażaj jej automatycznie.**
Zmiana wag wymaga: ≥ 250 rozliczonych obserwacji, porównania na zbiorze holdout,
nowego pliku `config/calibration/vX.Y.yaml` i akceptacji człowieka.

## Krok 5 — Commit

```bash
git add -A && git commit -m "resolve: <N> rozliczeń, metryki na <M> obserwacjach" && git push -u origin <branch>
```
