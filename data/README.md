# data/

Stan, który system gromadzi o sobie samym. Wszystko tutaj jest **dopisywane, nigdy edytowane**.

| Ścieżka | Co to jest |
|---|---|
| `iv_history/{TICKER}.csv` | własna historia IV — jedna obserwacja na spółkę na run. Jedyne źródło, z którego kiedykolwiek powstanie IV Rank, bo darmowa historia IV nie istnieje. Odblokowuje się po 60 obserwacjach. |
| `lessons.jsonl` | dziennik wniosków z rozliczeń. `LESSONS.md` jest renderowanym widokiem tego pliku. |
| `earnings_calendar.json` | kalendarz earnings zbudowany na etapie analitycznym runu. Każda pozycja musi mieć `source_url`. |
| `schema.sql` | wskaźnik zgodności z v0.1; kanoniczny schemat bazy leży w `docs/db-schema.sql`. |

Ten katalog jest wersjonowany celowo. Skasowanie `iv_history/` cofa zegar IV Rank do zera.
