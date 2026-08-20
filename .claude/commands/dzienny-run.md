---
description: Wykonaj pełny dzienny run frameworku — skan zmienności, analiza katalizatorów, zapis predykcji
---

# Dzienny run

Wykonaj pełny cykl predykcyjny zgodnie z `AGENTS.md` i `docs/run-protocol.md`.
Argumenty użytkownika (jeśli są): `$ARGUMENTS`.

## Zasada nadrzędna

Ty dostarczasz **cechy i tezy**. Kod dostarcza **każdy wynik, prawdopodobieństwo i decyzję**.
Nigdy nie wpisuj ręcznie wyniku, prawdopodobieństwa ani decyzji do pliku predykcji — te
liczby wylicza `vol predict`. Jeśli uważasz, że silnik się myli, zapisz to jako wniosek
przez `vol lessons add`, a nie przez nadpisanie liczby.

## Krok 1 — Przeczytaj, czego system się już nauczył

```bash
vol status
vol lessons list --limit 10
cat results/metrics-latest.md 2>/dev/null | head -40
```

Aktywne wnioski **muszą** wpłynąć na ten run. Trafiają do `lessons_applied` w pliku analityka
i do raportu. Jeśli wniosek mówi „setup X zawodzi na horyzoncie 1d", nie stawiaj takiej
predykcji bez wyraźnego uzasadnienia, dlaczego tym razem jest inaczej.

## Krok 2 — Zbuduj kalendarz earnings

Wyszukaj spółki raportujące wyniki w ciągu najbliższych 10 dni (WebSearch/WebFetch).
Zapisz `data/earnings_calendar.json`:

```json
{
  "retrieved_at": "2026-08-20T11:00:00Z",
  "entries": [
    {"ticker": "NVDA", "date": "2026-08-26", "session": "AMC",
     "source_url": "https://...", "retrieved_at": "2026-08-20T11:00:00Z"}
  ]
}
```

Każda pozycja **musi** mieć `source_url`. Nie wpisuj daty, której nie potwierdziłeś w źródle —
błędna data earnings zatruwa cały run. Jeśli nie znajdziesz kalendarza, pomiń ten krok:
run policzy się na samym ruchu i aktywności opcyjnej, a raport to odnotuje.

## Krok 3 — Etap ilościowy

```bash
vol scan --run-type daily --command-text "dzienny run" --horizon 3d
```

Zapisuje `runs/<rok>/<mm-dd>/<RUN_ID>/manifest.json` oraz `scan.json`.
Zanotuj `RUN_ID` — potrzebujesz go w kroku 5.

## Krok 4 — Etap analityczny

Weź 10–12 spółek z najwyższym `opportunity` i `volatility` ze `scan.json`. Dla każdej:

1. **Dlaczego teraz?** Wyszukaj konkretny katalizator (WebSearch). Zanotuj URL i datę publikacji.
2. **Co jest w cenie?** Porównaj to, co wiadomo, z tym, co wycenia rynek (`market_implied_move`,
   nachylenie struktury terminowej, put/call).
3. **Co byłoby prawdziwym zaskoczeniem?**
4. **Co unieważnia tezę?**
5. **Przegląd adwersaryjny** — zastosuj `prompts/adversarial_review_v1.0.md` do własnej tezy,
   zanim ją zapiszesz.

Zapisz `analyst.json` w katalogu runu:

```json
{
  "run_id": "RUN-...",
  "lessons_applied": ["skrócony opis wniosku, który wpłynął na ten run"],
  "predictions": [
    {
      "ticker": "MRNA",
      "horizon": "3d",
      "thesis": "min. 20 znaków, konkretnie: co, dlaczego teraz, dlaczego rynek tego nie wycenia",
      "key_catalyst": "...",
      "what_is_priced_in": "...",
      "invalidation_conditions": ["...", "..."],
      "llm_features": {
        "catalyst": {"score": 0, "type": "...", "why_now": "...", "priced_in_score": 0},
        "sentiment": {"score": 0, "momentum": 0},
        "direction": {"catalyst_direction": 0, "analyst_revisions": 0},
        "market_context": {"sector_score": 0, "regime_score": 0},
        "news": {"velocity": 0},
        "risks": ["..."],
        "contradictions": ["..."]
      },
      "source_refs": [
        {"source_name": "...", "source_type": "news", "url": "...",
         "published_at": "...", "retrieved_at": "..."}
      ]
    }
  ],
  "watchlist": [{"ticker": "XYZ", "reason": "..."}],
  "skipped": [{"ticker": "ABC", "reason": "..."}]
}
```

### Twarde reguły dla cech analitycznych

- **Pomiń pole, którego nie potrafisz poprzeć źródłem z datą.** Brak pola oznacza
  redystrybucję jego wagi. Wpisanie „neutralnego" 0 to zmyślenie danych, nie ostrożność.
- Nie używaj informacji opublikowanej po `data_cutoff` z `manifest.json`.
- Wysoka IV sama w sobie nie jest sygnałem kierunkowym (`AGENTS.md`, reguła 9).
- Sentyment z mediów społecznościowych nie jest potwierdzeniem faktu (reguła 10).
- Docelowo 5 predykcji i 10 pozycji na watchliście, ale **mniej jest w porządku**.
  Jeśli tylko dwie spółki mają realny katalizator, zapisz dwie i powiedz to wprost.
  Dopychanie raportu do pięciu pozycji zatruwa próbę kalibracyjną.

## Krok 5 — Zapisz predykcje

```bash
vol predict --run <RUN_ID> --analyst runs/<rok>/<mm-dd>/<RUN_ID>/analyst.json
```

Waliduje cechy schematem, przelicza wszystko na **zapisanym snapshocie** (bez pobierania
świeższych danych), zapisuje niemodyfikowalne predykcje i generuje `raport.md` po polsku.

## Krok 6 — Rozlicz to, co dojrzało

```bash
vol resolve
vol metrics
```

Jeśli metryki pokazują powtarzalny błąd, zapisz wniosek:

```bash
vol lessons add --category overconfidence --title "..." --observation "..." --action-text "..."
```

Kategorie: `overconfidence`, `underconfidence`, `setup_failure`, `data_quality`,
`directional_bias`, `horizon`, `process`.

## Krok 7 — Zapisz do repo

```bash
git add -A && git commit -m "run: dzienny run <data> (<N> predykcji)" && git push -u origin <branch>
```

## Krok 8 — Pokaż wynik w czacie

Streść: ile spółek przeskanowano, top predykcje z prawdopodobieństwami i oczekiwanym ruchem,
co trafiło na watchlistę, czego zabrakło w danych. Podaj ścieżkę do `raport.md`.

Jeśli run nie znalazł nic sensownego — powiedz to wprost. Pusty run to wynik, nie porażka.
