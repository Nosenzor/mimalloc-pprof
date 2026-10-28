# Issue 610 evidence archive notes

The final archive must include every original measurement and rejected candidate,
not only the accepted source. Acceptance is recorded separately from numeric
PASS / INCONCLUSIVE / FAIL verdicts. A successful workflow is not a numeric PASS.

V3 completed all thirteen original comparisons: **62 PASS, 16 INCONCLUSIVE,
zero FAIL** across 78 metric checks. Every release bound held. The canonical
and zero-credit checks have the same verdicts. These are numeric gate results,
not an aggregate score or a merge decision. See [PR #611](https://github.com/zackees/mimalloc-pprof/pull/611)
and [issue #610](https://github.com/zackees/mimalloc-pprof/issues/610) for disposition.

## Ledgers

- [V1 full original ledger](ledger-v1.md): rejected.
- [V2 full original ledger](ledger-v2.md): rejected, including the incomplete sanity failure.
- [V3 full original ledger](ledger-v3.md): numeric results; acceptance is recorded separately in the PR and issue.

## Provenance

- Frozen baseline: `2c7fed4edf91cf412c8969cf3cafeb690f0ed3e3`.
- V1: `42d06c341b7d8d636acd0864b260bac5fdb63942`, inline full bounds;
  rejected for plain-ephemeral p99. Full original ledger: 65 PASS,
  12 INCONCLUSIVE, one FAIL.
- V2: `a718c4e7d3e7be00eb65e4f8895d03bd35d3ffa5`, inline prefix only;
  rejected for chart-ephemeral p99 and an incomplete random-large/8 RSS sanity
  failure. Twelve complete rows: 60 PASS, 11 INCONCLUSIVE, one FAIL.
  The incomplete row has one sample per arm and no fabricated six-cell ledger.
- V3: `b2978df6ae0266e8ebd47f116874fba72a38e340`, separate noinline selector
  with full bounds. Its own measurements and platform gates determine acceptance.

Each dispatch manifest records exact run IDs, baseline/head SHAs, input options
and sample limits. Each completed run retains original sample bytes, metadata,
logs and the four actual throughput/latency executables, verified against the
original binary manifest. Ledgers are recomputed from those samples. A stricter
zero-memory-credit shadow check is retained alongside the canonical ledger;
it does not modify the raw data, theoretical floor, noise or canonical verdicts.
No new RSS saving is claimed for this CPU search change.

The baseline-only RSS diagnostic uses the baseline for all three arms and verifies
identical executable hashes. It is attribution only, not candidate acceptance or
a replacement for v2's failure. Its EPYC 7763 result does not establish the cause
of the earlier EPYC 9V45 failure. No incompatible hosts are pooled.

Local sampled profiles, instruction/cache counters, work counts, selection
comparison probes, compile errors and focused test results are diagnostic evidence.
Local replay timing output is retained but is not timing acceptance. The selector
comparison probe checks both selectors against the same queue state, not the
entire concurrent execution history. It does not prove a historical p99 cause.
The original override-on v3 code-shape prototype is retained separately; only
the corrected override-off prototype matches the performance harness flags.

## Archive format and reconstruction

From the repository root, run:

```sh
python -B .github/assets/perf-610/verify.py .github/assets/perf-610
```

This checks bytes, original run coverage and recomputed ledgers. It does not
decide merge acceptance. `verification.json` records the final verification.

`manifest.json` records the archive hash and every input's logical path, byte count
and SHA-256. Original file modes are recorded when available. Identical executable
or static-library payloads are stored once under `binaries/<sha256>` and referenced
by all original logical paths. Other inputs preserve their relative paths.
`source/<sha>.tar` contains tracked C sources, headers, tests, CMake and CI harness
sources for the fixed revisions; the Git commit IDs also identify the full repo,
including separately committed Rust regeneration.

To reconstruct inputs into a new scratch directory, verify the archive digest,
then read each manifest entry's `member`, verify its hash/length, write it to its
relative `path` and restore `mode` (default `0o644`). Do not merely unzip the
binary payload directory and assume those hash names are the original paths.
Adjust the archived scripts' original `/tmp/mimalloc-610` paths before rerunning.
To inspect sampled profiles, restore the corresponding exact ELF or configure
perf's symbol root/build-ID cache. Old pipeline versions are not assumed to
accept the current command-line flags.

Do not reclassify historical failed/incomplete data using later samples. Do not
copy a passing row from one source into another source's ledger without direct
identity proof of all four actual linked executables. The v3 staged plan permits
adding the remaining ten workloads once; it does not permit repeating its first
three to change their labels.
