# Runbook

Skrócona ściąga operacyjna. Pełna procedura: `.claude/commands/dzienny-run.md`
i `docs/run-protocol.md`.

## Komendy w czacie

| Komenda | Co robi |
|---|---|
| `/dzienny-run` | pełny cykl: skan → analiza → predykcje → rozliczenie → commit |
| `/rozlicz` | rozliczenie dojrzałych predykcji + analiza błędów + wnioski |
| `/metryki` | stan nauki systemu: kalibracja, trafność, ile do progu zmiany wag |

## Komendy CLI

```bash
vol status                       # ile runów, predykcji, co czeka na rozliczenie
vol scan --run-type daily        # etap ilościowy
vol predict --run <ID> --analyst <plik.json>   # etap analityczny
vol resolve [--verbose]          # rozliczenie po horyzoncie
vol metrics                      # Brier, log loss, kalibracja, wykrywanie zmienności
vol calibrate                    # propozycja zmiany parametrów (nie wdraża jej)
vol lessons list|add|render      # dziennik wniosków
vol validate <plik.json>         # walidacja predykcji schematem
```

Przydatne przełączniki `vol scan`: `--horizon 1d|3d|5d|event`, `--deep N` (ile pełnych
skanów), `--prescreen N`, `--workers N`, `--no-iv-history` (nie dopisuj obserwacji IV —
używaj przy testach, żeby nie zatruć historii).

## Kolejność, która ma znaczenie

1. **Najpierw przeczytaj wnioski**, potem stawiaj predykcje. Odwrotna kolejność sprawia,
   że system nie uczy się niczego.
2. **Skan przed analizą.** Analizujesz to, co silnik wskazał, a nie to, co pamiętasz z newsów.
3. **`vol predict` liczy na zapisanym snapshocie.** Nie pobiera świeższych danych, więc
   predykcja pozostaje związana z `data_cutoff` ze skanu, choćby analiza trwała godzinę.
4. **Rozliczenie zawsze osobno.** Nigdy nie dopisuj wyniku do pliku predykcji.

## Czego nigdy nie robić

- Nie nadpisuj pliku w `predictions/` — kod to blokuje, ale zasada dotyczy też człowieka.
- Nie zmieniaj `timestamp` ani `data_cutoff` istniejącej predykcji.
- Nie wpisuj cechy analitycznej, której nie potrafisz poprzeć źródłem z datą. Pomiń pole.
- Nie zmieniaj wag w `config/scoring.yaml` na podstawie kilku wyników.
- Nie uruchamiaj skanu dwa razy tego samego dnia z zapisem historii IV — jest zabezpieczenie
  (jedna obserwacja na spółkę na dobę UTC), ale nie polegaj na nim bez potrzeby.

## Gdy coś nie działa

| Objaw | Przyczyna | Co zrobić |
|---|---|---|
| `vol scan` kończy się błędem pobierania | Cboe niedostępne lub blokada proxy | powtórz; adapter sam ponawia 3 razy z narastającym odstępem |
| Wszystkie predykcje mają `data_quality: LOW` | brak historii IV (normalne na starcie) lub szerokie spready | sprawdź `quality_flags` w raporcie |
| `vol predict` pomija spółkę | cechy analityczne niezgodne ze schematem albo brak kwotowanego straddle'a | powód jest w `skipped` w manifeście i w raporcie |
| `vol resolve` nic nie rozlicza | horyzont jeszcze nie minął albo historia cen jest opóźniona o sesję | `vol status` pokaże, co jest gotowe |
