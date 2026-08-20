# Prediction lifecycle

1. Create scan timestamp.
2. Define `data_cutoff`.
3. Capture raw inputs with source and published timestamp.
4. Normalize inputs.
5. Run LLM prompts.
6. Validate structured feature output.
7. Compute deterministic scores.
8. Write immutable prediction snapshot.
9. Wait until the horizon/event is complete.
10. Resolve outcome using only market data after prediction timestamp.
11. Append outcome to prediction record in the database and create a separate resolved artifact.
12. Update metrics.
13. Do not change the original thesis or probabilities.
