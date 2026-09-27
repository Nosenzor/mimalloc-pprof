# Exact DHAT profiling and the memory-events API

*Part of the [mimalloc-pprof](../README.md) documentation.*

## Exact DHAT profiling

`<mimalloc/dhat.h>` provides an **exact**, high-overhead heap/lifetime observer that
writes [DHAT file-version 2](https://valgrind.org/docs/manual/dh-manual.html) JSON for
`dh_view.html`. Unlike the production-oriented sampled pprof profiler, it keeps one
raw-OS-backed record for every allocation it observes. A freed allocation's record is
unlinked but its memory is not reused, so collector memory grows with the number of
allocations in the session, not with the live set: about 49 bytes each in a measured run,
so the default 64 MiB budget covers roughly 1.3 million allocations. Nothing is returned
until the next `mi_dhat_start`. Use it for short tests and focused investigations, not a
continuously running production workload.

**DHAT is opt-in at build time** and compiled out by default: configure CMake with
`-DMI_DHAT=ON` (default `OFF`), enable the Rust crate's `dhat` feature
(`features = ["dhat"]`), or define `MI_DHAT=1` when compiling `src/static.c` directly.
Without it the per-allocation hook sites vanish from the allocator and the `mi_dhat_*`
API remains only as stubs: `mi_dhat_start` returns `false` and `mi_dhat_dump` fails.

```c
#include <mimalloc.h>
#include <mimalloc/dhat.h>

int main(void) {
  if (!mi_dhat_start()) return 1;
  void* p = mi_malloc(4096);
  mi_free(p);
  mi_dhat_stop();                 /* stop observing; retained report can still dump */
  return mi_dhat_dump("heap.dhat.json") ? 0 : 2;
}
```

DHAT is built independently of `MI_PPROF` and coexists with an application-installed
`mi_memory_set_callbacks` table. It observes pointer identity before the application
callback, suppresses callback-internal allocations just as memory-events does, and
commits its ledger update after the callback returns. It records requested bytes,
not allocator slack, and emits heap/lifetime metrics only (`bklt: true`, `bkacc: false`):
it does **not** claim reads, writes, copy traffic, access histograms, or instruction
counts.

`MIMALLOC_DHAT=1` is meant to start DHAT at process initialization, but that is a
**known issue** today: the variable is read into a buffer smaller than `_mi_getenv`'s
64-byte minimum, so it is never seen and DHAT stays off. Until that is fixed, call
`mi_dhat_start()` (Rust: `dhat::start()`) yourself.
`MIMALLOC_DHAT_DUMP_AT_EXIT=heap.dhat.json` writes an exit report and is unaffected. The timestamps
are monotonic wall-clock milliseconds (`tu: "ms"`), not Valgrind instruction counts.
`MIMALLOC_DHAT_MAX_BYTES` bounds persistent raw-OS collector state (default 64 MiB).
When the budget is exhausted the application allocation still succeeds; the collector
marks the report partial (`mi_dhat_incomplete`) and exposes the drop count through
`mi_dhat_stats_t`.

## Memory-events API

[`include/mimalloc/memory-events.h`](../include/mimalloc/memory-events.h) exposes
opt-in allocation-change counters, callbacks, a best-effort live-allocation
visitor, and raw-OS-layer `mi_unwrapped_*` functions for instrumentation that must
avoid allocator recursion. It is **independent of `MI_PPROF`** and remains
available in an `MI_PPROF=OFF` build.

Since #414 it is also **opt-in at compile time**: build with `-DMI_MEMEVT=ON` (cargo
feature `memory-events`, which `dhat` implies), or the counters, the callback table and
the per-allocation hook sites are compiled out and every function here is a stub that
returns `false`/`NULL`. Compiled in but disabled it costs 9-13 instructions per
malloc/free pair, which is why it is no longer on by default. `mi_unwrapped_malloc` /
`_free` / `_realloc` are NOT part of that: they are raw-OS helpers and are real in every
build.

Enable tracking before the first allocation when exact lifetime totals matter:

```c
#include <mimalloc/memory-events.h>

mi_memory_tracking_set_enabled(true);

mi_memory_snapshot_t_decl(snapshot);
if (mi_memory_snapshot(&snapshot)) {
  /* snapshot.live_bytes and snapshot.accum_bytes are now available */
}
```

Alternatively set `MIMALLOC_MEMORY_EVENTS=1` before launch. Enabling tracking
later does not reconstruct allocations made while it was disabled. Callback
reentrancy, pointer lifetime, and live-visitor restrictions are documented in the
header.
