# Codex operating instructions

Use this repository as the source of truth.

Before changing code:
1. Read `AGENTS.md`.
2. Read the relevant files under `docs/`.
3. Inspect the current tests.
4. Define a small, testable change.

For each task:
- list files to change;
- implement the smallest correct patch;
- add/update tests;
- run tests;
- report data-contract or migration impacts.

For prediction/scoring changes:
- never rewrite historical predictions;
- bump the appropriate version;
- add a migration note;
- keep old and new scoring available for comparison;
- add an offline regression test if possible.

For data integrations:
- keep provider-specific code under `src/` providers or adapters;
- normalize into the internal schema before scoring;
- preserve source timestamps and retrieval timestamps;
- never expose API keys in logs or committed files.

For calibration:
- first produce a report/proposal;
- do not silently change production weights;
- compare old vs proposed model on a holdout set;
- only then create a PR with the new weight version.
