# What this fork carries, and where each piece came from

*Part of the [mimalloc-pprof](../README.md) documentation.*

Every divergence from upstream mimalloc, with its origin and current upstream status. The
point is that you should not have to read git history to answer *"is this theirs, ours, or
someone else's?"* — which matters most when deciding whether to depend on a behaviour.

**Total C-core divergence from the pinned base: about 13,900 added lines** (`git diff
--shortstat 6def7be9 HEAD -- src include`), of which about 8,800 are 21 new files. The
patches into upstream's own files now come to about +5,100/−350 lines across 32 files. The
bulk of that is the Bun imports (the scavenger and idle handoff, hole purging, fork safety,
the heap-teardown protocol; see the README's [Bun features](../README.md#bun-features)
table), this fork's own `mi_purge_all` and arena reclaim, and the comments that explain
them. New
logic still goes into new files, because every line patched into an upstream file has to
be re-reasoned on each upstream sync.

## Features (new files — none of this exists upstream)

| Feature | Source | Upstream status | Where |
|---|---|---|---|
| pprof-compatible sampled heap profiler | this fork | not upstream — but see the note below | `src/profile.c` (1151), `src/profile-stack.c` (210), `src/profile-maps.c` (172), `include/mimalloc/profile.h` |
| Memory-events accounting + callbacks | this fork | not upstream | `src/memory-events.c` (628), `include/mimalloc/memory-events.h` |
| DHAT v2 exact heap profiler (`mi_dhat_*`, opt-in `MI_DHAT`) | this fork | not upstream | `src/dhat.c` (640), `src/dhat-stack.c` (47), `include/mimalloc/dhat.h` |
| `mi_unwrapped_*` non-recursive scratch allocator | this fork | not upstream | `src/memory-events.c` |
| Rust crate + `GlobalAlloc`, incl. the zeroing-realloc family | this fork | n/a | `rust/mimalloc-pprof/` |

> **Not the only pprof profiler for mimalloc.** [Bun's fork](https://github.com/oven-sh/mimalloc)
> built one independently (`src/prof.c`), and upstream branch `pr-1266` carries a third from
> Datadog — using the same filenames as ours. What distinguishes this one is **usable
> Windows profiles**: Bun captures Win32 stacks (`RtlCaptureStackBackTrace`) but its
> `mi_prof_collect_mappings` has an empty `#else`, so it emits no module mappings there and
> a Win32 profile cannot be symbolized. See [`MIMALLOC_FORKS.md`](../MIMALLOC_FORKS.md).

## Bug fixes in upstream code

The first three are described in depth in [upstream bugs](upstream-bugs.md).

| Fix | Source | Upstream status |
|---|---|---|
| MinGW thread-exit cleanup never runs (unbounded leak) | **this fork** | **adopted upstream** in [`60c4f031`](https://github.com/microsoft/mimalloc/commit/60c4f031c9d878da05ffa6066777accd51458b98), crediting [#56](https://github.com/zackees/mimalloc-pprof/issues/56) |
| …but upstream's copy guards on `__GCC__`, which no compiler defines, so it never compiles | **this fork** | **not upstream** — one-token fix we carry; reported as [microsoft/mimalloc#1349](https://github.com/microsoft/mimalloc/pull/1349) with a 44× thread-churn measurement |
| `mi_heap_new` / `mi_subproc_new` do not bootstrap the library | this fork | not upstream; same class as upstream [#1341](https://github.com/microsoft/mimalloc/issues/1341) |
| `test-stress.c` dereferences unchecked allocations | this fork | not upstream |
| Seeded sampling was not reproducible (ASLR in the PRNG seed) | this fork | n/a — our code |

## Adopted from other forks

| Change | Source | Status |
|---|---|---|
| Zero-tracking — `zalloc` skips its `memset` after a zeroing purge | idea from [Bun](https://github.com/oven-sh/mimalloc); implementation ours | on `main` since the v4 line was dissolved (#129), **off by default** (`mi_option_purge_zeroes`). −10.8% on the anti-workload on Windows; **no effect on Linux**; macOS unmeasured |
| Zero the new TLS slots after the slot array grows | code from [`oven-sh/mimalloc@d078ad06`](https://github.com/oven-sh/mimalloc/commit/d078ad06), MIT | landed in #148. `rezalloc` preserves the uninitialized slack between the requested size and the bin size, and `_mi_thread_local_get` validates a slot only by its version lane — so garbage could be returned as a `mi_theap_t*`. Bun fixed the zeroing; we had separately fixed the array's provenance (#128 B3), which **they still lack**. Each fork had one half |
| Background scavenger thread + `mi_on_thread_idle*` | [Bun](https://github.com/oven-sh/mimalloc) @ `942b8342`, MIT | imported by #272 (landed as #299), **on by default** (`mi_option_scavenger`). The objection this page used to record — a purge decommitting a page that a live sample record still points into — is met by construction: records live in the profiler's raw-OS arena, every free unlinks its record before a page can go back to the arena, and `mi_arenas_page_free_ex` asserts that in `MI_PPROF` debug builds |
| Page hole purging (`purge_holes*`) | Bun @ `942b8342`, MIT | imported by #272 (landed as #302), **on by default** (`mi_option_purge_holes`). The engine lives in the new `src/page-holes.c` rather than in `src/page.c`, which carries only single-line calls. It does not change what `mi_usable_size` or the memory-events counters report |

## Deliberately not adopted

| Change | Source | Why not |
|---|---|---|
| `mi_theap_merge_stats` NULL guard | Bun | already fixed differently upstream (`b6dc592b`); **Bun dropped it too** |
| Arena/page-map rollback helper | Bun | already fixed differently upstream (`66fd7a99`); **Bun dropped it too** |
| MetaSafe liveness bits, `MI_MUSL_BUILTIN`, Arma 3 defaults | various | see [`MIMALLOC_FORKS.md`](../MIMALLOC_FORKS.md) |

Reasoning and per-change ratings for the whole fork survey are in
[`MIMALLOC_FORKS.md`](../MIMALLOC_FORKS.md). The complete list of what was imported from Bun,
with the PR that landed each piece, is the README's [Bun features](../README.md#bun-features)
table.

## Engine

Tracks `upstream/dev3`, pinned at **`6def7be9`** (Bun's merge-base), bumped from `bcee5a88`
by [#266](https://github.com/zackees/mimalloc-pprof/issues/266). The pin is bumped
deliberately rather than continuously — see
[#80](https://github.com/zackees/mimalloc-pprof/issues/80) for the method.

## What the hardening pass changed

A hardening pass over v3 ([#61](https://github.com/zackees/mimalloc-pprof/issues/61))
landed the following. Each row says where it came from, because "is this ours, upstream's,
or someone else's?" should not require reading git history.

| Change | Source | Why |
|---|---|---|
| Memory + counter regression gate, per-platform baselines | this fork | The two leaks in [upstream bugs](upstream-bugs.md) passed every existing test. Nothing asked whether memory stayed bounded. |
| ISA baseline check (x64 + arm64) | prompted by Debian #1094881 / Fedora #2342055 | Above-baseline instructions SIGILL on older CPUs. Debian and Fedora independently found `MI_OPT_ARCH=OFF` is a no-op on arm64 — confirmed here. |
| Shared-library CI (ubuntu + win-gnu) | prompted by conda-forge's `extern inline` patch | Our single-TU amalgamation hides link errors that only appear in a real DLL/so. Found 4 test targets that broke shared-only builds. |
| `MI_PROF_CONFIG_OVERRIDE` tested on every env-backed field | this fork | `MIMALLOC_*` is process-global and other libraries embed mimalloc. This is the documented way to be immune to the ambient environment. |
| `ruff` + `pyright --strict` over `ci/` | this fork | The gating layer was itself unchecked; two silent gate failures were Python bugs. |
| `-Wmaybe-uninitialized` fix in `src/profile.c` | this fork | Noise in every build log is where a real warning hides. |

## How v3 was validated

v3 was held to the same bar as v2 before it became the mainline — identical
workloads, same machine, Windows/MinGW, 32 threads:

| | v3 | v2 |
|---|---|---|
| `ctest`, Debug and Release, 3 runs each | **13/13** | 9/9 |
| `MI_PPROF=OFF` | 9/9 | 6/6 |
| Rust workspace suite | green | green |
| `test-stress` peak RSS @ 50 iterations | **0.24 GB** | 11.98 GB |
| `test-stress` peak RSS @ 100 iterations | **flat** | 23.50 GB |
| `test-stress-heaps` @ 25/50/100/200 iterations | **flat ~0.82 GB** | test not present |

v3's test set is a strict superset of v2's, adding `test-stress-heaps`,
`test-stress-subprocs`, `test-profile-race`, and `test-degenerate`.
