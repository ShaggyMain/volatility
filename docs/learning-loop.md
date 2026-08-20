# Pętla ucząca

## Co tu znaczy „uczenie się"

Nie dopisywanie promptów i nie zapamiętywanie pojedynczych trafień. Uczenie to mierzalna
poprawa kalibracji i jakości decyzji, poparta próbą wystarczającą, by odróżnić umiejętność
od szczęścia.

```
predykcja ──► horyzont mija ──► rozliczenie ──► metryki ──► wnioski ──► następny run
    ▲                                              │
    └──────────── propozycja kalibracji ◄──────────┘
                  (nigdy automatyczna)
```

## Trzy pętle o różnym tempie

### 1. Pętla szybka — wnioski (`LESSONS.md`)

Po każdym rozliczeniu analityk szuka **wzorca**, nie pojedynczej wpadki. Potwierdzony
wzorzec trafia do `data/lessons.jsonl` (dopisywanie, bez edycji) i jest renderowany
do `LESSONS.md`. Każdy kolejny run czyta aktywne wnioski **przed** postawieniem predykcji
i zapisuje w manifeście, które wziął pod uwagę.

To jedyna pętla działająca z dnia na dzień. Nie zmienia żadnych wag — zmienia to,
na co analityk zwraca uwagę.

### 2. Pętla średnia — metryki (`vol metrics`)

Liczone na wszystkich rozliczonych predykcjach:

| Metryka | Na co odpowiada |
|---|---|
| Brier, log loss | czy prawdopodobieństwa są uczciwe |
| Brier skill score | czy model bije naiwną częstość bazową — poniżej 0 nie nauczył się niczego |
| Kubełki kalibracyjne | czy „65%" naprawdę znaczy 65% |
| Trafność kierunku | czy sygnały kierunkowe mają wartość |
| Udział LONG | czy nie ma systematycznego byczego biasu |
| Precyzja/czułość zmienności | czy silnik zmienności znajduje realne duże ruchy |
| Pokrycie oczekiwanego ruchu | czy przedział jest uczciwy, a nie po prostu szeroki |
| Podział per setup / horyzont / decyzja | gdzie koncentrują się błędy |

Pierwszy sensowny przegląd: **100 rozliczonych predykcji**. Wcześniej raport to szum
i mówi o tym wprost.

### 3. Pętla wolna — kalibracja (`vol calibrate`)

Generuje **propozycję**, nigdy zmianę. Propozycja zawiera obecną wartość, proponowaną,
powód i dowód z rozmiarem próby. Wdrożenie wymaga:

1. co najmniej 250 rozliczonych obserwacji,
2. porównania starego i nowego zestawu na tym samym oknie **oraz** na zbiorze holdout,
3. nowego pliku `config/calibration/vX.Y.yaml` — nigdy edycji istniejącego,
4. notatki migracyjnej w `CHANGELOG.md`,
5. akceptacji człowieka w pull requeście.

Powód takiej surowości: przy kilkudziesięciu obserwacjach każde „ulepszenie" wag jest
dopasowaniem do szumu, a system ma reprodukowalnie odtwarzać historyczne predykcje —
co wymaga, żeby każdy zestaw wag pozostał dostępny pod swoją wersją.

## Dlaczego wagi nie zmieniają się same

`AGENTS.md`, reguła 12: żadna zmiana modelu z pojedynczego wyniku. Reguła 13: każda zmiana
kalibracji reprodukowalna i wersjonowana.

System, który po każdej stracie przestawia wagi, nie uczy się — goni ostatni wynik.
Reprodukowalność jest tu ważniejsza niż tempo: predykcja z marca musi dać się przeliczyć
dokładnie tak, jak została policzona w marcu.

## Historia IV jako aktywo rosnące z czasem

Każdy run dopisuje jedną obserwację IV na spółkę do `data/iv_history/`. To jedyny sposób,
żeby ten system kiedykolwiek miał IV Rank — nie istnieje darmowe źródło historii IV.

Konsekwencja: **im dłużej system działa, tym mądrzejszy się staje niezależnie od kalibracji.**
Po 60 obserwacjach na spółkę odblokowuje się `iv_rank` i `iv_percentile`, czyli wejście
o największej wadze w wyniku zmienności (0,15). Do tego czasu ta waga rozkłada się na
IV/RV20, IV/RV252 i zmianę IV, a predykcje niosą flagę `IV_RANK_UNAVAILABLE`.

Dlatego regularność runów ma wartość samą w sobie, nawet gdy dany dzień nie przynosi
żadnej predykcji.
