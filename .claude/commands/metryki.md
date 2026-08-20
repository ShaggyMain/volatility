---
description: Pokaż aktualny stan nauki systemu — metryki, kalibrację i wnioski
---

# Metryki

Argumenty: `$ARGUMENTS`.

```bash
vol status
vol metrics
vol lessons list --limit 20
```

Zreferuj w czacie:

1. **Czy system w ogóle bije bazę?** — `brier_skill` powyżej 0 oznacza, że przewidywania
   niosą informację ponad częstość bazową. Poniżej 0 — nie niosą, i trzeba to powiedzieć wprost.
2. **Czy prawdopodobieństwa są uczciwe?** — tabela kalibracji: przewidywane vs zrealizowane.
3. **Czy silnik zmienności działa?** — precyzja i czułość wykrywania wysokiej zmienności.
4. **Gdzie koncentrują się błędy?** — podział na typ setupu, horyzont i decyzję.
5. **Ile jeszcze do progu zmiany wag?** — 100 obserwacji do przeglądu, 250 do zmiany.

Nie interpretuj metryk na próbie mniejszej niż 100 rozliczonych predykcji jako dowodu
czegokolwiek. Powiedz, ile jeszcze brakuje.
