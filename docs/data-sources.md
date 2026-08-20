# Źródła danych

Wszystkie źródła są publiczne i nie wymagają klucza API. Każde ma udokumentowane
ograniczenie — braki są raportowane, nigdy uzupełniane zgadywaniem (`AGENTS.md`, reguła 8).

## Cboe — kwotowania opóźnione

`https://cdn.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json`

Pełny łańcuch opcji: IV per kontrakt, open interest, wolumen, greeki, bid/ask, oraz
kwotowanie instrumentu bazowego wraz z **IV30 i jej dzienną zmianą** liczonymi przez Cboe.

- Opóźnienie: ~15 minut. Działa na naszą korzyść: wszystko w snapshocie było publiczne
  przed `data_cutoff`.
- Rozmiar: ~0,6–1,6 MB na spółkę. Dlatego pełny skan obejmuje kilkadziesiąt, a nie kilkaset spółek.
- Indeksy gotówkowe mają prefiks `_` (np. `_SPX`) — obsługuje to `normalize_symbol`.

## Cboe — historia dzienna

`https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/{SYMBOL}.json`

OHLCV od 2004 roku. Podstawa dla RV20/RV60/RV252, ATR, siły względnej, pozycji względem
52-tygodniowych ekstremów.

- Plik potrafi być opóźniony o sesję względem łańcucha opcji. Snapshot zapisuje datę ostatniej
  świecy w `meta.last_bar`, a zwrot jednodniowy liczy z **żywego kwotowania**, nie ze świecy.
  Przy opóźnieniu powyżej 5 sesji pojawia się flaga `STALE_PRICE_HISTORY`.

## Cboe — dzienny wolumen opcji per symbol

`https://www.cboe.com/us/options/market_statistics/symbol_data/csv/?mkt=cone`

Wolumen per kontrakt z poprzedniej sesji, agregowany do jednego wiersza na instrument bazowy
wraz z podziałem call/put. Zasila dynamiczne uniwersum i ranking pre-screenu.

- **Obejmuje wyłącznie giełdę Cboe (C1), nie skonsolidowany OPRA.** Dlatego jest używany jako
  miara **względna** — kto handluje najżywiej — a nigdy jako bezwzględna wielkość rynku.

## Cboe — kwotowanie instrumentu bazowego

`https://cdn.cboe.com/api/global/delayed_quotes/quotes/{SYMBOL}.json`

~0,5 KB. To dzięki niemu pre-screen może objąć ~180 spółek zamiast kilkunastu.

- Pole `price_change_percent` bywa niespójne z `current_price` vs `prev_day_close`.
  Kod liczy zmianę samodzielnie z tych dwóch pól i ignoruje gotowe pole.

## Wyszukiwanie w sieci — katalizatory i kalendarz earnings

Realizowane przez Claude (WebSearch/WebFetch) na etapie analitycznym.

- Każdy wpis w `data/earnings_calendar.json` musi mieć `source_url`.
- Każda cecha analityczna musi być poparta źródłem z datą publikacji; pola bez pokrycia
  są pomijane, a nie wypełniane wartością neutralną.

## Czego nie ma

| Brakujące dane | Dlaczego | Jak system to obsługuje |
|---|---|---|
| Historia IV (IV Rank, IV Percentile) | brak darmowego źródła | buduje własną historię, 1 obserwacja/run; `null` + flaga do 60 obserwacji |
| Market Chameleon | blokada sieciowa w tym środowisku | zastąpiony danymi Cboe; IV Rank czeka na własną historię |
| Skonsolidowany wolumen OPRA | płatny | wolumen Cboe używany względnie |
| Konsensus i rewizje analityków | płatne API | wyłącznie jakościowo, przez wyszukiwanie, z URL |
| Short interest | publikowany dwa razy w miesiącu | pole opcjonalne, pomijane gdy niepotwierdzone |

## Dodanie płatnego źródła

Adapter trafia do `src/volatility_ai/providers/`, normalizuje dane do wewnętrznego schematu
przed scoringiem i zachowuje `published_at` oraz `retrieved_at`. Klucze wyłącznie ze zmiennych
środowiskowych — w repo tylko `.env.example` (`AGENTS.md`, reguła 15).
