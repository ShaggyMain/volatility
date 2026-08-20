# Protokół runu

Dokument opisuje, co dokładnie dzieje się między komendą „dzienny run" a zapisaną predykcją,
i dlaczego akurat w takiej kolejności.

## Dlaczego run jest podzielony na dwa etapy

`vol scan` i `vol predict` to osobne komendy, choć technicznie mogłyby być jedną.
Rozdzielenie wymusza dwie rzeczy:

1. **Separację warstw.** Etap ilościowy nie widzi żadnej narracji — rankuje wyłącznie na
   danych rynkowych. Etap analityczny nie może zmienić liczby, może tylko dostarczyć cechę,
   z której liczba zostanie policzona.
2. **Stabilny `data_cutoff`.** Analiza katalizatorów trwa. Gdyby predykcja pobierała dane
   w momencie zapisu, każda minuta researchu przesuwałaby punkt odniesienia i cicho wprowadzała
   informacje nowsze niż moment, do którego predykcja się deklaruje. `vol predict` liczy
   na snapshocie zapisanym przez `vol scan` i nigdy nie pobiera świeższych danych.

## Etap 1 — ilościowy (`vol scan`)

```
wolumen opcji z poprzedniej sesji  ─┐
kalendarz earnings (10 dni)        ─┼─► pula kandydatów
opcjonalna watchlista              ─┘
                                        │
                     pre-screen (kwotowania, ~0,5 KB/spółkę)
                                        │
                     pełny skan (łańcuch opcji + historia OHLCV)
                                        │
                     cechy point-in-time ─► deterministyczny scoring
                                        │
                     manifest.json + scan.json
```

Na tym etapie scoring działa **bez cech analitycznych**. Wagi kierunkowe zależne od analityka
redystrybuują się automatycznie, więc ranking odzwierciedla wyłącznie zmienność, akcelerację
i pozycjonowanie w opcjach — czyli dokładnie to, na czym ten etap ma rankować.

## Etap 2 — analityczny (`vol predict`)

Analityk (model językowy) dostarcza dla wybranych spółek:

- ocenę katalizatora i tego, co jest już w cenie,
- sentyment i jego momentum,
- cechy kierunkowe, wyłącznie takie, które da się poprzeć źródłem z datą,
- tezę, katalizator, warunki unieważnienia,
- referencje źródeł z `published_at` i `retrieved_at`.

Kod waliduje to schematem `schemas/llm_features.schema.json`, przelicza wszystkie wyniki
i prawdopodobieństwa, podejmuje decyzję według progów z `config/scoring.yaml` i zapisuje
niemodyfikowalny rekord.

## Jak powstaje każda liczba w predykcji

| Liczba | Skąd się bierze |
|---|---|
| `scores.volatility` | średnia ważona 11 wejść z `config/scoring.yaml`, wagi brakujących redystrybuowane |
| `scores.volatility_acceleration` | kompozyt: RV5/RV20, zmiana IV30, nachylenie struktury, wolumen/OI, wolumen akcji |
| `scores.bull` / `scores.bear` | **niezależne** miary siły dowodów w każdą stronę; sprzeczne dowody dają wysokie oba |
| `probabilities` | `flat` z poziomu zmienności, przechył z `tanh(gain × edge × confidence)` |
| `expected_move` | straddle ATM przeskalowany pierwiastkiem czasu na horyzont, skorygowany o własny pogląd na zmienność |
| `expected_value` | `P(wzrost) × ruch_w_górę + P(spadek) × ruch_w_dół`, dla proponowanej strony |
| `confidence` | pokrycie cech, płynność, świeżość danych, zgodność sygnałów, minus kary za flagi jakości |
| `decision` | progi z `config/scoring.yaml`, w ustalonej kolejności |

Parametry przekładające dowody na prawdopodobieństwa (`direction_gain`, `flat_base`,
`volatility_scale_*`) siedzą w `config/calibration/v0.1.yaml` i to **jedyne** pokrętła,
które pętla ucząca może stroić.

## Świadoma ostrożność wersji 0.1

Priory kalibracyjne v0.1 są celowo zachowawcze. Przy maksymalnym dowodzie kierunkowym
i pełnej pewności `P(wzrost)` ląduje w okolicach 0,68, a nie 0,95. Powód: nadmierna pewność
jest trudniejsza do wykrycia i kosztowniejsza niż zbyt ostrożne prawdopodobieństwa, a próba
kalibracyjna dopiero powstaje. Te wartości są ustawione ręcznie i **nie są dopasowane
do żadnych danych** — zmienią się dopiero po 250 rozliczonych obserwacjach.

## Bezpieczeństwo point-in-time

- Kanał Cboe ma ~15 minut opóźnienia, a `data_cutoff` to moment pobrania. Wszystko w snapshocie
  było publiczne **przed** deklarowanym cutoffem — margines działa na naszą niekorzyść, czyli
  bezpiecznie.
- Rozliczenie czyta wyłącznie sesje zamknięte **po** dacie predykcji. Sesja trwająca w momencie
  predykcji jest pomijana w całości, bo jej część działa się wcześniej.
- Rozliczenie nigdy nie zapisuje niczego do pliku predykcji.
