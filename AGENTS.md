# AGENTS.md — mimalloc-pprof

Fork of microsoft/mimalloc adding pprof-compatible sampled heap profiling (Windows-first)
plus Rust crates in `rust/`. The repo root IS mimalloc (tracking the v3/`dev3` line).

## Read this first

`CLAUDE.md` is the canonical hard-rules document. Read it in full before any change to
`src/`, `include/`, `test/`, `CMakeLists.txt`, `rust/`, or CI. The rules below are the
load-bearing subset; where this file and `CLAUDE.md` differ, `CLAUDE.md` wins.

## Layout

```
src/                 C core (mimalloc + profiler/memory-events/dhat). New logic goes in
                     new files: src/profile*.c, src/memory-events.c, src/dhat*.c.
include/mimalloc/    Public + internal headers. Profiler API in profile.h,
                     memory-events.h, dhat.h. internal.h holds the _mi_memevt_on_* hooks.
test/                C test suite (ctest). macOS-gated tests carry `LABELS macos`.
rust/                Cargo workspace: mimalloc-pprof (sys crate), bench-harness,
                     benchmark-suite, dashboard, stress-harness, xtask. rust-toolchain 1.94.1.
ci/                  Python gating scripts + release tooling. Linted like the code they gate.
docs/                Generated Doxygen + long-form docs (dev-loop.md, ci-gates.md,
                     release-process.md, fork-divergence.md, arena-reclaim.md).
CMakeLists.txt       Single CMake project (libmimalloc C). Observability is opt-in options.
```

`src/static.c` is the single-TU amalgamation the Rust sys crate compiles. Every new C
file must be included there (guarded by `MI_PPROF` where appropriate) or Rust builds
silently miss it.

## Build & test

All commands run from the repo root. The project has no Python package; `uv run` only
drives the CI/dev scripts in `ci/`.

```bash
# Linux: fast parallel mirror of the Linux-runnable CI subset (the main local loop)
uv run ci/verify_local.py                 # 11 concurrent configs, fast tier
uv run ci/verify_local.py --only release,lint
uv run ci/verify_local.py --slow          # include long-tail ctest (test-profile-race, etc.)
uv run ci/verify_local.py --list          # print config + bundle table
uv run ci/verify_local.py --bundle macos-arm64-release   # cross-build a CI bundle

# Windows: C/Rust loops run inside a Docker bind mount via
uv run ci/dev_linux.py doctor | c-test | rust-test | bench

# Plain CMake (what the CI flags mirror)
cmake -S . -B build -G Ninja -DMI_PPROF=ON -DMI_DHAT=ON -DMI_MEMEVT=ON -DMI_DIAGNOSTICS=ON
cmake --build build && ctest --test-dir build --output-on-failure

# Rust
cargo test --manifest-path rust/Cargo.toml
```

`bench` (dev_linux) / the benchmark-suite is the speed acceptance test; paste its output
on issue #10 when touched.

## Observability flags (all opt-in, all OFF by default)

CMake: `MI_PPROF` / `MI_MEMEVT` / `MI_DIAGNOSTICS` / `MI_DHAT` / `MI_OWNER_GATE`.
Cargo: `pprof` / `memory-events` / `diagnostics` / `dhat` (implies `memory-events`) /
`owner-gate`, with `full` = all five and `default = []`.

The public C API and every Rust wrapper stay present as stubs in EVERY configuration
(template: the `#else` block at the end of `src/profile.c`). Never add a CI row or script
that relies on a default; name the flag.

## Non-negotiable invariants (full text in CLAUDE.md)

1. Never commit directly to `main`. Feature branch from the sub-issue name -> one PR per
   phase -> merge. Conventional commits (`feat:`, `fix:`, `ci:`, `docs:`, `test:`).
2. Never mix C-core paths and `rust/` paths in one commit (keeps upstream cherry-picks clean).
3. Profiler-internal memory comes ONLY from the raw-OS arena (`_mi_os_alloc`), never from
   hooked allocation paths. Debug builds assert this.
4. No new required C dependencies (no mandatory libunwind/protobuf/zlib).
5. Hook sites in src/alloc.c, src/free.c, src/alloc-aligned.c call the `static inline`
   `_mi_memevt_on_*` wrappers, which expand to nothing unless `MI_MEMEVT || MI_DHAT`.
   Never put a call, TLS read, or atomic RMW in front of the `_mi_observers_armed` flag
   test. `ci/check_fastpath_identity.py` enforces byte-identical fast path vs upstream.
6. Edits to upstream files stay to a few guarded lines (`#if MI_PPROF` / one line each for
   the observer hooks). New logic goes in new files.
7. Never suppress `-Wunused-function` file-wide. Mark the individual `static` function
   `MI_DECL_MAYBE_UNUSED` with a why-comment. `ci/check_no_diagnostic_suppression.py`.
8. Macros are UPPER_CASE. `ci/check_macro_case.py` + `ci/macro_case_baseline.txt`.
9. No magic numbers: tuning constants are named `#define` guarded by `#ifndef` (overridable
   via `-D...`), or an `mi_option` when settable at run time.
10. Escalate, don't improvise: when reality diverges from a sub-issue, comment on that
    issue with evidence and stop.

## Merge gates (every PR)

- `c-unit` green on ubuntu and windows-MSVC with `MI_PPROF=ON`, and the `pprof-off`
  (MINIMAL) configuration green.
- `pprof-off` is the MINIMAL build: every observability subsystem compiled out.
- win-gnu green in `windows-bundles.yml`; `rust-native` green.
- MSVC native `cl` build (`c-unit.yml` `ctest (windows-latest)`) is a hard gate —
  clang-cl is not a substitute.
- macOS: `macos-bundles.yml`; routine path uses no Apple hardware (cross-built on Linux).
  `run-macos-x64-selective` runs the `macos`-labelled tests (~10 min) when a Darwin path
  is touched; `run-macos-x64-recovery` (full bundle) is manual-only.
- Internal PR/main CI is the minimal lane. Add literal `ci-test` for the full C DAG,
  `ci-full` for the release platform matrix. External PRs get the full matrix automatically.

## Linting (ci/ and examples/heap-snapshot)

```bash
uv run ruff check ci examples/heap-snapshot
uv run ruff format --check ci examples/heap-snapshot
uv run pyright ci examples/heap-snapshot   # strict, py39 target
pytest ci/tests                            # includes test_verify_local.py (workflow drift)
```

Ruff config and Pyright strict settings live in `pyproject.toml`. Line length 100.

## Release & CI

Releases are issue-driven through `ci/release.py`. Read `docs/release-process.md` and
`docs/ci-gates.md` before changing release workflows or tagging/publishing. A release
needs the full matrix on the exact merged SHA, the issue freeze, matching packaged crate,
and shipped-asset smoke gates. The issue body must say `- State: **ready-to-publish**.`;
a dry run never sets that state.

## Upstream sync

`main` (v3) and `upstream/dev3` have unrelated histories: never merge directly, never use
`--allow-unrelated-histories` or `commit-tree` parent rewriting. A v3 sync is an
issue-scoped, reviewed selective C-engine overlay that reapplies the fork hooks and
verifies protected path/Rust allowlists. `readme-upstream.md` carries upstream README
edits; do not replace it with the fork README.
