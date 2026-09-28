# The background scavenger and the idle-handoff (park) protocol

*Part of the [mimalloc-pprof](../README.md) documentation.*

`src/scavenger.c` holds two things that only make sense together. The **scavenger** is one
background thread per process that returns freed arena memory to the OS on a timer instead of
waiting for the next allocation to run a purge. The **idle handoff** (the *park protocol*) lets a
thread that is about to block hand its heaps to that thread, which does the idle work (folding
pending frees, discarding holes, draining the arena purge queue) while the owner sits in the
kernel. User-facing options: [C integration](c-integration.md#scavenger-and-hole-purging). What a
sweep discards *inside* pages: [page holes](page-holes.md). The process-wide purge that reuses the
protocol from any thread: [purge-all](purge-all.md) and its [plan](purge-all-implementation.md).

## 1. Why it exists

Upstream mimalloc purges freed arena memory only from `_mi_arenas_try_purge`, on a thread that is
allocating or collecting. A process that frees a lot and then goes idle keeps it all resident:
nothing allocates again to notice the deadline passed (oven-sh/bun#39844, cited by
`test/test-thread-idle-rss.cpp`).

The code is imported from oven-sh/mimalloc @ `942b8342` (MIT) as **#272, Bun parity P7a**
(scavenger + handoff); P7b added the hole sweep; **#366** the owner gate, the foreign sweep door
and other claimants; **#457** fixed lost purge deadlines; **#483/#493** made the scavenger release
emptied large pages too. With a scavenger, the default `mi_option_purge_delay` drops from 1000 to
100 ms (oven-sh/bun#34217, `src/options.c`): a short delay now costs a wait, not a hot-path purge.

Deliberate differences from Bun, each marked `DEVIATION` in the source:

| Bun | here | why |
|---|---|---|
| idle handoff and sweep drivers in `src/theap.c` | `src/scavenger.c` | CLAUDE.md rule 6: `src/theap.c` stays upstream plus a few guarded lines |
| hole engine in `src/page.c` | `_mi_purge_holes_of` in `src/page-holes.c` | rule 6 |
| `#include <linux/futex.h>` | `MI_FUTEX_WAIT_PRIVATE` / `MI_FUTEX_WAKE_PRIVATE` defined here | rule 5: the uapi header is not in Alpine's `build-base` |
| `scavenger_wake` and the tld registry mid-struct | at the tail of `mi_subproc_t` | shifting `stats` measured ~1.5-2 ns per alloc+free (`include/mimalloc/types.h`) |
| lazy start from any second thread | only from a thread of the **main** sub-process | `pthread_create` takes the stack from the caller's sub-process, which can be destroyed before the join (`_mi_thread_init_with_heap`) |

## 2. Build and configuration surface

There is **no CMake option and no cargo feature**: `src/scavenger.c` is in the CMake source list
and `#include`d unconditionally by `src/static.c`. A memory-return feature, not an opt-in
observability subsystem, it is **on by default at run time**. `MI_PPROF` does not change it;
`MI_OWNER_GATE` (CMake `OFF`, cargo `owner-gate`) inverts the protocol's default (§6). A stub block
for `__wasi__` and Emscripten without pthreads is meant to make `_mi_scavenger_*` no-ops (purging
allocation-driven), but does not build as written (§9, D1); no CI row builds those targets.

| option | env | default | effect here |
|---|---|---|---|
| `mi_option_scavenger` | `MIMALLOC_SCAVENGER` | 1 | 0: the thread is never started, `mi_on_thread_idle_start` returns false |
| `mi_option_purge_delay` | `MIMALLOC_PURGE_DELAY` | 100 ms | `<= 0` also prevents the start |
| `mi_option_arena_purge_mult` | `MIMALLOC_ARENA_PURGE_MULT` | `MI_ARENA_PURGE_MULT_DEFAULT` (4) | arena deadline = delay x mult (`mi_arena_purge_delay`) |
| `mi_option_purge_holes_min_interval` | `MIMALLOC_PURGE_HOLES_MIN_INTERVAL` | 100 ms | at most one sweep of a thread per interval (clamped to 0..3600000) |
| `mi_option_purge_holes` | `MIMALLOC_PURGE_HOLES` | 1 | the hole phase of a sweep ([page holes](page-holes.md)) |

The start is **one-shot**: `_mi_scavenger_start_lazy` sets a static `started` flag on its first
call, the only time `_mi_scavenger_start` reads the two options; a fork child gets one more (§4.5).
Overridable (`#ifndef`): `MI_SCAVENGER_MAX_WAIT_MS` (30000: longest sleep with nothing scheduled,
period of the safety-net pass, #457); in `types.h`, `MI_RETIRED_PAGE_SLOTS` (16),
`MI_RETIRED_RELEASE_MULT` and `MI_PAGE_RESERVE_RELEASE_MULT` (10 purge delays before a retired or
reserved large page is released), `MI_RELEASE_SLACK_MS` (300). Inline, not overridable: 256 spins
in `mi_park_leave_loop`, the 2000 ms Windows join, the interval clamp, a forced purge's 1000 ms
guard wait (`_mi_arenas_try_purge`).

## 3. Public API

Declared in `include/mimalloc.h`, exported in every configuration, part of Bun's linked surface
(`ci/check_bun_surface.py`) and pinned to their Rust wrappers by `ci/check_rust_surface.py`.

| C | Rust (`rust/mimalloc-pprof/src/lib.rs`) | contract |
|---|---|---|
| `void mi_on_thread_idle(void)` | `on_thread_idle()` | the idle work, inline on the calling thread |
| `bool mi_on_thread_idle_start(void)` | `park_while_idle() -> Option<IdlePark>` | hand this thread's heaps to the scavenger |
| `void mi_on_thread_idle_end(void)` | `Drop for IdlePark` | take them back |
| `void mi_scavenger_stop(void)` | `scavenger_stop()` | stop and join the scavenger |

**`mi_on_thread_idle`** returns at once on a thread that never allocated or whose tld is not its
own; otherwise it runs `_mi_thread_idle_work` on its tld: collect, hole sweep, then
`_mi_arenas_purge_now`, which brings every queued arena purge forward to "now" and wakes the
scavenger for it, or purges inline when none runs. It costs a few discard syscalls, so it belongs
at idle points. Gated, the whole body is one `MI_GATE_ENTER`/`MI_GATE_LEAVE`. A *parked* thread
must not call it: it is an allocator call and races the sweep (`src/arena-reclaim.c`).

**`mi_on_thread_idle_start`** is a promise: *this thread will not allocate or free until
`mi_on_thread_idle_end`*. It returns **false**, and `_end` must then not be called, when the
thread never allocated, is not the tld's owner, belongs to a sub-process other than
`_mi_subproc_main()` (the only one swept), no scavenger runs after the lazy start, the thread is
already parked, or the build is gated. False is deliberately **not** an inline sweep: a caller
parks far more often than it is idle, and only it knows when `mi_on_thread_idle()` is affordable.
Unbalanced calls are tolerated and leave the thread usable (`test_unbalanced`).
**`mi_on_thread_idle_end`** calls `_mi_park_leave`: normally one uncontended CAS, else a short
spin until the sweep stops (§5.4). A no-op when gated.

**`mi_scavenger_stop`** sets `_mi_scavenger_shutdown`, wakes the thread and joins it (see D2 for a
start/stop window). It is **permanent for the process image**: `_mi_scavenger_start` refuses once
`shutdown` is set, and only `_mi_scavenger_forked_child` clears it (the header's and the Rust
docs' "restarts on demand" is wrong). Afterwards `mi_on_thread_idle_start` returns false and
`mi_on_thread_idle` still runs on the caller; `test_scavenger_stop` checks the former and only that
the latter does not crash. A second stop is a no-op.

The Rust guard is `!Send`, `#[must_use]`; options `Opt::SCAVENGER`, `Opt::PURGE_DELAY`, etc. In C:

```c
#include <mimalloc.h>
#include <stdbool.h>

bool parked = mi_on_thread_idle_start();   // about to block in the kernel
/* ... block here: no allocation and no free until the park ends ... */
if (parked) { mi_on_thread_idle_end(); }
mi_on_thread_idle();   // or: do the idle work on this thread, when it can afford it
```

## 4. The scavenger thread

### 4.1 Start

`_mi_scavenger_start_lazy` runs when a **second thread of the main sub-process** initialises
(`_mi_thread_init_with_heap`) and on every `mi_on_thread_idle_start`, never at process init (the
macOS Objective-C runtime aborts if a thread exists before it initialises; a single-threaded
process should not pay for a thread). `_mi_scavenger_start` claims `running` with an exchange
**first** and re-checks `shutdown` after, so a start and a stop always see each other; but the
join handle is published only after the thread is created (D2). On POSIX the thread starts with
every signal blocked except `SIGSEGV`, `SIGBUS`, `SIGILL`, `SIGFPE`, `SIGTRAP`, `SIGABRT` and
`SIGSYS` (no stolen process-directed signals; its own faults reach the host's crash handler).

### 4.2 The loop (`mi_scavenger_run`)

It works on `_mi_subproc_main()` directly (it never allocates, so it must not create a tld
through the TLS path) and repeats while `_mi_scavenger_running` is set:

1. **Clear the wake word with an RMW** (`mi_atomic_exchange_acq_rel`). A plain store could pass a
   parker's `parked_count` increment and exchange in opposite directions (store buffering), and
   that park would wait for the safety timeout.
2. **Sweep parked threads** (`_mi_theap_sweep_parked`, §5.3); it returns when a skipped park is due.
3. **Arena purge**: if `subproc->purge_expire` has expired, or is 0 and `MI_SCAVENGER_MAX_WAIT_MS`
   passed since the last full pass, run `_mi_arenas_try_purge(false, true, subproc, 0)` and start
   over. A full pass settles `purge_expire` to the earliest pending arena deadline (a short retry
   if the purge guard is held) and never clears it blindly (#457).
4. **Retired pages**: `_mi_pages_release_retired` releases retired and reserved large pages
   ([page holes §4.6](page-holes.md#46-who-drives-the-sweep)); while some are too young, the next
   wait is at most one `purge_delay`.
5. **Wait** on `scavenger_wake` for the least of: time to `purge_expire`, the rest of
   `MI_SCAVENGER_MAX_WAIT_MS`, the park due time, the retired-page tick.

### 4.3 Wake-ups and wait primitives

`_mi_scavenger_wake` does nothing unless the thread runs, and makes the OS call only on the
`0 -> 1` edge of `scavenger_wake`, so a burst of frees costs one syscall. Its callers:
`mi_arena_schedule_purge` (`subproc->purge_expire` went `0 -> set`, or is re-armed after a settle
raced it, #457), `_mi_arenas_purge_now` (the last phase of every idle pass),
`mi_on_thread_idle_start`, and `_mi_pages_release_schedule`. `_mi_scavenger_stop` does not use it:
it release-stores `scavenger_wake = 1` and calls `mi_scav_wake_one` directly, uncoalesced. Every
wait re-reads the word, tolerates spurious wake-ups and retries `EINTR`. `mi_scav_word_t` is
`uint32_t`, pointer-width on Windows (§5.1).

| platform | wait / wake | notes |
|---|---|---|
| Linux | raw `SYS_futex`, private | 32-bit word, no kernel header |
| macOS | `__ulock_wait` / `__ulock_wake` (`MI_UL_COMPARE_AND_WAIT`, `MI_ULF_NO_ERRNO`) | private, stable since 10.12; what libc++ and Rust std park on |
| Windows 8+ | `WaitOnAddress` / `WakeByAddressSingle` via `GetProcAddress` | resolved in `mi_scav_init` before `running` is published |
| Windows 7 | `CRITICAL_SECTION` + `CONDITION_VARIABLE` | fallback when the lookup fails |
| other POSIX | `pthread_mutex_t` + `pthread_cond_t`, absolute `CLOCK_REALTIME` deadline | initialised explicitly by `mi_scav_init` (FreeBSD allocates statically initialised ones lazily), but only *after* `running` is published (D3) |

### 4.4 Stop and teardown

`_mi_scavenger_stop` stores `shutdown = 1` **before** exchanging `running` to 0 (no restart past
it), wakes the thread, and on POSIX joins it if `_mi_scavenger_joinable`. It runs first in
`mi_process_done_once` (`src/init.c`), before `_mi_heap_snapshot_on_exit`; from
`mi_scavenger_stop`; and on Windows from `mi_scavenger_crt_stop`, which `mi_crt_init` registers
with `atexit` **first** so it runs **last**, while the thread can still be joined. Process detach
runs inside `ExitProcess` after every other thread was terminated, so that join is bounded
(`WaitForSingleObject(..., 2000)`); a signalled handle without `_mi_scavenger_exited` means the OS
killed the thread where it stood, perhaps holding `mi_arenas_purge_guard`, on which the forced
purge `mi_process_done_once` runs next would spin: `_mi_arenas_purge_guard_reset` frees it.

### 4.5 Fork

The thread does not survive `fork()`; its flags do. `_mi_process_fork_child` (`src/fork.c`) calls
`_mi_scavenger_forked_child` (clear `_mi_scavenger_tld`, `shutdown`, `joinable`, `running`; reset
the generic-POSIX mutex; set `_mi_scavenger_needs_restart` for the next lazy start); without it
the child signals nobody and never purges (Case C of `test/test-fork-user-heap.c`). It also zeroes
`scavenger_wake` and `parked_count`, resets every tld's park fields to `RUNNING`, and clears the
purge guard (`_mi_arenas_fork_child`). Before the fork, `_mi_process_fork_prepare` calls
`_mi_park_leave` on the forking thread's tld ahead of any lock
([fork safety §4](fork-safety.md#4-before-any-lock-leave-the-park); gated builds: D4).

## 5. The idle-handoff protocol

### 5.1 States and fields

`mi_tld_t::park_state` is the whole protocol: a three-state ownership lock over the thread's
theaps.

| state | meaning | entered by |
|---|---|---|
| `MI_PARK_RUNNING` (0) | only the owner may touch its theaps (default) | a CAS from `PARKED` in `mi_park_leave_loop`: by the owner, **or by a thread deleting a heap** that holds the tld's `park_theap0` (`mi_heap_detach_theaps`), which ends another thread's park; plain stores in `mi_tld_init` and the fork-child reset |
| `MI_PARK_PARKED` (1) | the owner promised not to allocate or free (gated: it is outside the allocator) | the owner's CAS in `mi_on_thread_idle_start`; gated, a release-store in `_mi_gate_leave` or `_mi_thread_init_with_heap`; a claimant's hand-back |
| `MI_PARK_SWEEPING` (2) | a claimant holds the theaps | a claimant's CAS from `PARKED`, under `tlds_lock` (scavenger, purge-all walk, arena reclaim) or under `heap->theaps_lock` (diagnostic walk, `_mi_heap_visit_capture`) |

| field | written by | purpose |
|---|---|---|
| `mi_tld_t::park_reclaim` | whoever leaves the park (owner, or a heap deleter) | "give the theaps back": the sweep stops at its next page or phase |
| `mi_tld_t::park_theap0` (plain) | owner, before leaving `RUNNING` | its default theap; the scavenger has no TLS of the owner's |
| `mi_tld_t::park_swept` | scavenger; cleared by `_start` | this park is done (gated: a per-call round stamp) |
| `mi_tld_t::sweeper` | claimant | thread id holding the `SWEEPING` claim (#366) |
| `mi_tld_t::subproc_next`; `mi_subproc_t::tlds`, `tlds_lock` | `mi_tld_register` / `mi_tld_unregister` | the registry |
| `mi_subproc_t::parked_count` | `_start`; `_mi_park_leave` (any leaver) | lets the scavenger skip the walk (read ungated only) |

All sit at the **tail** of their structs (`mi_process_tld_main` and `mi_tld_detached` in
`src/init.c` are initialised positionally; zero is each field's initial value). Every field reached
through `mi_atomic_*` is **pointer-width**, enforced by `mi_scav_atomic_widths_assert_t`: the MSVC
**C** atomics wrapper (`cl` without C11 atomics, as `rust/mimalloc-pprof/build.rs` builds for
`x86_64-pc-windows-msvc`) accesses them only at `uintptr_t` width, and so writes every CAS
out-parameter, hence the `mi_park_state_t` locals.
`mi_tld_init` registers every non-detached tld (`mi_tld_register`, idempotent, under
`tlds_lock`); `mi_tld_free` unregisters it, asserting it is not `SWEEPING`.

### 5.2 Park

`mi_on_thread_idle_start` (ungated), after the §3 checks: return false unless `RUNNING` (a second
`_start`: the scavenger may be reading the fields); store `park_theap0`; release-store
`park_reclaim = 0` and `park_swept = 0`; CAS `RUNNING -> PARKED` (acq_rel: publishes those and
every earlier free-list write); relaxed `parked_count++`; `_mi_scavenger_wake`.

### 5.3 Claim and sweep (`_mi_theap_sweep_parked`, scavenger only)

Ungated, it returns at once when `parked_count` is 0. Otherwise it claims one tld per trip
through `tlds_lock`, skipping a tld that is already `park_swept`, then:

- read `park_state` with **acquire**, skip anything not `PARKED`, then read the plain
  `holes_sweep_last`. The source comment calls that read race-free because a parked owner cannot
  write again until it leaves; but leaving takes no lock, so in between the owner can go
  `RUNNING`, call `mi_on_thread_idle` and write the field (`_mi_purge_holes_of`). A formal data
  race, benign because the claim CAS then fails (a 64-bit read can tear on a 32-bit target);
- skip a tld swept less than `purge_holes_min_interval` ago, remembering when it becomes due;
- CAS `PARKED -> SWEEPING`, release-store `sweeper` = own thread id, leave the lock.

With the claim and **no lock** held, `mi_tld_sweep_theap0` picks the theap to collect first
(`park_theap0`, else the tld's theap of the main heap, else the list head, under
`tld->theaps_lock`) and `_mi_thread_idle_work` runs. Then, in order: `park_swept = swept_mark`
(before the release, or it could land on the *next* park), `sweeper = 0`, and a release-store of
**`PARKED`**, never `RUNNING`: the owner is still blocked and owns the way out.
`holes_sweep_last` is stamped by `_mi_purge_holes_of`, the path both callers share.

The body has two doors (plan §6). `_mi_thread_idle_work_ex(tld, theap0, force)` checks
`park_reclaim` before each phase (unless `MI_GATE_FLAG_RECLAIM_IGNORED`), collects `theap0` with
`_mi_theap_collect_foreign` for a foreign tld or `mi_theap_collect` for its own, then calls
`_mi_purge_holes_of`. It does no arena purge (`mi_purge_all` purges arenas once).
`_mi_thread_idle_work` then runs `_mi_arenas_purge_now` (skipped once `park_reclaim` is set)
**still under the claim**, and bumps `mi_idle_work_count`. For a claimed tld, `mi_theap_collect_ex`
differs in three ways:

- `_mi_deferred_free` returns unless called by the owner: the embedder's handler must run on the
  allocating thread, and `heartbeat`/`recurse` are owner-private plain fields;
- `mi_theap_page_collect` stops as soon as `park_reclaim` is set: the owner waits one page at most;
- `_mi_arenas_collect` is skipped while `park_state == MI_PARK_SWEEPING`: the arena purge never reads
  `park_reclaim`, and the owner's bounded wait would become a sub-process-wide `madvise` pass.

### 5.4 Leave (`_mi_park_leave`, `mi_park_leave_loop`)

CAS `PARKED -> RUNNING`. A tld already `RUNNING` has nothing to take back (no `parked_count`
decrement). If `SWEEPING`: release-store `park_reclaim = 1` and spin while it stays so, 256
`mi_atomic_pause`s and then `_mi_prim_thread_yield` (a real `sched_yield` on POSIX) for a
descheduled sweeper; then **re-race the CAS** rather than store `RUNNING`, because the sweeper
hands back `PARKED` and may claim again at once. On success, `park_reclaim = 0` and
`parked_count--`. Every exit from a park goes through that loop, which is how `SWEEPING` keeps a tld alive:

| path | where | why |
|---|---|---|
| `mi_on_thread_idle_end` | `src/scavenger.c` | the normal end |
| `_mi_thread_done` | `src/init.c`, caller's own tld only | a thread can exit parked (`epoll_wait` is a cancellation point); freeing its tld under a sweep is a use-after-free |
| `_mi_process_fork_prepare` | `src/fork.c` | nothing of the forking thread may be claimed across `fork()` |
| `mi_heap_detach_theaps` | `src/heap.c`, under `tlds_lock`, **another thread's tld** | `mi_heap_delete` of a heap holding a parked thread's `park_theap0`; that owner's later `_end` finds `RUNNING` and returns |
| `_mi_park_leave_if_parked` | `_mi_malloc_generic`, `mi_free_generic_local` (ungated) | a TLS destructor allocating or freeing on a parked thread |

The last covers only the slow paths. The thread-local free fast path (`mi_free_ex` into
`mi_free_block_local`) and the free-list pop bypass it; `mi_free_block_local` keeps a permanent
debug assertion that its theap's tld is not `SWEEPING` as a detector for that residual.

## 6. The owner gate, as far as the park protocol goes

With `MI_OWNER_GATE=1` the default is inverted: a thread is `PARKED` whenever it is outside the
allocator (one exception, D4). `_mi_gate_enter` (`include/mimalloc/owner-gate.h`) acquires on the
outermost `gate_depth` `0 -> 1` through `_mi_park_leave_gate`, which is `mi_park_leave_loop`
without the `parked_count` decrement (a gated park is never counted); `_mi_gate_leave`
release-stores `PARKED` at depth 0, and `_mi_thread_init_with_heap` publishes `PARKED` once a new
thread is set up. For this file:

- `mi_on_thread_idle_start` still lazily starts and wakes the scavenger but returns false;
  `mi_on_thread_idle_end` is a no-op; `mi_on_thread_idle` works (it is an allocator call);
- `_mi_theap_sweep_parked` ignores `parked_count` and uses `park_swept` as a per-call round stamp
  (`mi_sweep_round`): each call sweeps a tld at most once and gets back to its wait even with the
  interval at 0. It becomes a **paced timed sweep of busy threads**, bounded per visit by
  `park_reclaim` (the owner's next allocator call);
- `park_theap0` stays NULL, hence the fallback in `mi_tld_sweep_theap0`;
- `_mi_park_leave_if_parked` is compiled out, and `_mi_thread_done` enters the gate and never leaves.

The other claimants (the `mi_purge_all` walk in `src/purge-all.c`, the arena reclaim in
`src/arena-reclaim.c`, the diagnostic walk in `src/diagnostic-walk.c`) use the same CAS and hand
back `PARKED`. The claim names its holder in `sweeper`, which `_mi_thread_idle_work_ex` asserts for
a foreign tld and `_mi_gate_held` checks. The purge and reclaim walks skip `_mi_scavenger_tld_ptr()`
by pointer (a fork child's first thread can reuse a dead thread's id). It is non-NULL only if the
scavenger had an initialised theap at start. The source comment says a Windows DLL build gives it
one via `mi_win_main(DLL_THREAD_ATTACH)`; that is out of date: `mi_win_main` ignores
`DLL_THREAD_ATTACH`, and a scavenger with a theap would trip its own debug assertions (§7.5). The
skip is defensive. Details: [plan §5](purge-all-implementation.md#5-the-gate-includemimallocowner-gateh-new).

## 7. Invariants and concurrency

1. **Transitions.** Only the owner takes its tld out of `RUNNING`; only a claimant takes
   `PARKED -> SWEEPING` and back. `RUNNING` is reached by a CAS from `PARKED` (the owner, or a heap
   deleter on its behalf) or by the plain resets in `mi_tld_init` and the fork child.
2. **Finding a tld.** A claimant reaches a tld only under a lock that keeps it registered:
   `tlds_lock`, or `heap->theaps_lock` for the diagnostic walk; once claimed, `SWEEPING` keeps it
   alive. The scavenger and purge walk release `tlds_lock` before the sweep body. It is **not a
   leaf**: it is step 4 of 15 in `src/fork.c`'s order, the arena reclaim holds
   `heaps_lock -> tlds_lock -> theap_meta_lock` across its whole claimed pass (fork order 2, 4, 7),
   and `_mi_pages_release_retired` discards pages under it. `mi_heap_detach_theaps` may wait in
   `_mi_park_leave` while holding it because no sweep body takes it.
3. **Cross-tld order** (`src/fork.c`): self `RUNNING` before other `SWEEPING`; a sweeper never
   waits for the owner it holds. The one stack with many claims is `mi_arena_reclaim_claim_all`,
   which holds every tld of a sub-process `SWEEPING` at once (under `tlds_lock`).
4. **Bounded owner wait.** `park_reclaim` is checked between pages (`mi_theap_page_collect`,
   `mi_arena_page_purge_holes_at`, `mi_theap_page_purge_holes` via `mi_tld_reclaim_requested`) and
   between phases. The arena phase of `_mi_thread_idle_work` also runs under the claim: normally a
   wake, but once `_mi_scavenger_stop` has cleared `running`, `_mi_arenas_purge_now` purges inline,
   so a stop landing mid-sweep makes the owner wait for a sub-process-wide purge pass.
   `MI_GATE_FLAG_RECLAIM_IGNORED` (`MI_PURGE_FORCE`) makes only the phase checks and each theap's
   hole walk ignore the reclaim; the collect and the abandoned-page pass still stop at it. The
   arena reclaim holds its owners for its whole pass and never reads `park_reclaim`.
5. **The scavenger has no theap and never touches `mi_tld_t::profiler`** (#272 profiler invariant
   3): `mi_scavenger_run` and `_mi_theap_sweep_parked` assert `_mi_theap_default()` is
   uninitialised; `_mi_thread_idle_work_ex` asserts (debug) the swept tld's sampling countdown is
   unchanged. Hook accessors peek (`_mi_hooks_tld_peek`) instead of creating state.
6. **Purge guard.** `mi_arenas_purge_guard` serialises arena purges and is non-blocking for the
   scavenger (a held guard means "retry shortly", #457). Forced purges (`mi_collect(true)`,
   `mi_purge_all` with `MI_PURGE_FORCE`) wait up to 1000 ms; the scavenger never forces.
7. **Rule 4.** Nothing here allocates: atomics and OS wait objects only; profiler memory stays in
   the raw-OS arena (§8).

| operation | order | pairs with |
|---|---|---|
| owner CAS `RUNNING -> PARKED`; gated `_mi_gate_leave` store | acq_rel; release | a claimant's CAS, and the scavenger's acquire load |
| claimant CAS `PARKED -> SWEEPING`; `sweeper` store | acq_rel; release | `_mi_gate_held`'s acquire loads |
| hand-back: (`park_swept`, scavenger only), `sweeper = 0`, `PARKED` | release, in that order | the leaver's CAS |
| leave CAS `PARKED -> RUNNING`; spin on `park_state` | acq_rel; acquire | the hand-back |
| `park_reclaim` set / clear | release | the sweep's relaxed checks (a stop hint) |
| `parked_count` | relaxed | a hint to skip the walk |
| wake word: `_mi_scavenger_wake`, the scavenger's clear | acq_rel exchange | each other (§4.2); waits load acquire |
| wake word: `_mi_scavenger_stop` / fork child | release store / relaxed store | the waits' acquire load / none (single-threaded child) |
| park resets in `mi_tld_init` / the fork child | relaxed | `tlds_lock` in `mi_tld_register` / none (single-threaded child) |

## 8. The "background purge thread: not adopted" row

[What this fork carries](fork-divergence.md) still lists, under *Deliberately not adopted*, the
background purge thread ("purge can decommit a page a live sample record still points into",
use-after-decommit at dump time) and hole purging. The rows date from `092e0a24` (2026-08-01) and
moved to fork-divergence.md with the README split (`71311517`, 2026-09-01), a day before #272
landed the scavenger (`8c1d1d05`); P7b then landed hole purging. **Both rows are stale**: both
features are adopted and on by default, and the concern is answered by construction, debug
assertions and tests:

- **Records never live in purgeable memory**: they come from `_mi_prof_arena_alloc` (rule 4).
- **No record points into a page handed back to the arena**: a page gets there only when every
  block is free, each free unlinks its record under `prof_lock` (`_mi_prof_on_free`,
  `_mi_prof_on_free_collect`), and `mi_arenas_page_free_ex` asserts
  `!page->has_metadata && page->metadata == NULL` under `MI_PPROF` exactly where the slices become
  purgeable.
- **Hole discards cover free blocks only**: `_mi_prof_debug_assert_no_records_in` checks every
  discard in debug builds; `test/test-profile-race.c` scenario 5 checks that predicate with a
  negative control. **Inspection never faults a hole in**: `_mi_theap_area_visit_blocks` counts a
  purged block as free, and inspection collects through `_mi_page_free_collect_no_unpurge`.
- `test-profile-race-scavenger` runs scenario 4 with `MIMALLOC_PURGE_DELAY=1`, so slices are
  decommitted continuously under `mi_prof_visit` / `mi_prof_snapshot_new` while threads park.

## 9. Edge cases, accepted limits and possible defects

- **Only the main sub-process is swept and purged.** For another one, `_mi_arenas_purge_now` and
  `mi_arena_schedule_purge` wake the scavenger on a word it never waits on (the condition-variable
  fallbacks wake it and it sleeps again); that sub-process's arena purges stay allocation-driven.
- **The ungated park is a promise, not a proof**: allocating while parked races the sweep; slow
  paths repair it (§5.4), the fast-path residual has only a debug detector.
- **Start is one-shot and stop is final** (§2, §3); a fork child starts fresh.
- **Windows**: exit kills the thread inside `ExitProcess`, hence the `atexit` stop, bounded join
  and guard reset (§4.4); Windows 7 uses the fallback; FLS shutdown can run `_mi_thread_done` for
  another thread's theaps, so it leaves a park only for the caller's own tld. For win-gnu the
  `GetProcAddress` function-pointer cast here is one reason `-Wpedantic` is not in
  `MI_STRICT_WARNINGS`. macOS depends on the private `__ulock_*` calls; on generic POSIX a
  `CLOCK_REALTIME` jump stretches or cuts one wait.
- **Several mimalloc copies in one process** have separate file statics, so each runs its own
  scavenger and registry, uncoordinated. With `purge_delay <= 0` there is no scavenger at all.

Possible code defects found by static trace (unconfirmed by a test; not intended behaviour):

- **D1, stub does not build.** The WASI/Emscripten `_mi_scavenger_forked_child` stores to
  `_mi_scavenger_tld`, declared only in the non-stub branch, and `_mi_scavenger_tld_ptr`, called
  unconditionally from `src/purge-all.c` and `src/arena-reclaim.c`, is defined only there.
- **D2, stop can miss the join.** POSIX sets `_mi_scavenger_joinable` only after `pthread_create`
  returns; Windows assigns `_mi_scavenger_thread` only after `CreateThread` (its comment "a stop
  that sees running == 1 sees this" does not hold). A stop landing after the start's `shutdown`
  re-check but before that store returns without joining; the new thread sees `running == 0` and
  exits unjoined.
- **D3, generic-POSIX mutex used before init.** `_mi_scavenger_start` publishes `running` before
  `mi_scav_init`, so a concurrent `_mi_scavenger_wake` (relaxed `running` check) or stop can reach
  `mi_scav_wake_one` and lock the not-yet-initialised mutex, which `pthread_mutex_init` may then
  re-initialise while held. Windows initialises first; Linux and macOS need no init.
- **D4, fork in a gated build.** `_mi_process_fork_prepare` calls `_mi_park_leave`, not
  `_mi_park_leave_gate`. A thread forking outside the allocator (depth 0, `PARKED`) goes `RUNNING`
  and decrements `parked_count`, which a gated build never increments, so it wraps (unread there;
  reset in the child). In the parent the thread then stays `RUNNING` outside the allocator until
  its next allocator call: unsweepable, and pending to `mi_purge_all`.

## 10. Testing

| CTest target | source | configuration | asserts |
|---|---|---|---|
| `test-park-handoff` | `test/test-park-handoff.c` | POSIX (`if(NOT WIN32)`), TIMEOUT 600 | lazy start and signal mask; handoff sweeps; survivors byte-intact; unbalanced calls; third-thread frees during a sweep; pacing; fork while parked; exit/cancel while parked or swept; no handoff after `mi_scavenger_stop`; finally, hole memory really discarded |
| `test-park-handoff-no-scavenger`, `-eager` | same | `MIMALLOC_SCAVENGER=0`; `MIMALLOC_PURGE_HOLES_MIN_INTERVAL=0` | every case takes its "nothing handed off" branch; every park is due at once |
| `test-thread-idle-rss` | `test/test-thread-idle-rss.cpp` | C++, static, not TSAN | hand-written `extern "C" ... noexcept` declarations link; RSS drops by half of a freed 256 MiB within 1 s, nobody allocating |
| `test-arena-purge-rearm` | `test/test-arena-purge-rearm.c` | static, `MIMALLOC_PURGE_DELAY=20` | idle workers' memory returns through the deadline alone (#457); RSS unasserted on Darwin/ASan/DHAT |
| `test-profile-race-scavenger` | `test/test-profile-race.c` | `MI_PPROF`; `MIMALLOC_PURGE_DELAY=1;MIMALLOC_SCAVENGER=1` | §8 |
| `test-heap-release-mt{,-no-scavenger}` | `test/test-heap-release-mt.c` | | heap delete/destroy while the owner is parked or swept |
| `test-heap-teardown` (`parked`) | `test/test-heap-teardown.c` | | `mi_heap_delete` past a sweep of the heap's theap |
| `test-fork-user-heap` (Case C) | `test/test-fork-user-heap.c` | POSIX | a fork child still purges |
| `test-purge-all{,-no-scavenger}` | `test/test-purge-all.cpp` | both gate settings | G8: gated, the timed sweep reaches busy threads repeatedly; otherwise the count stays flat |

`_mi_test_idle_work_count()` (exported, not in `mimalloc.h`) counts completed idle passes in every
build type. Tests needing deterministic purging turn the scavenger off (`test-diagnostic-walks`,
`test-arena-purge-aged`, `test-arena-retention`, `test-resident-first`). The RSS and wall-clock
tests above are `RUN_SERIAL` ([the serial group](ci-gates.md#the-serial-group-and-why-it-is-in-cmakeliststxt)).
Locally: `uv run ci/dev_linux.py c-test`, or in a build directory
`ctest -R 'park-handoff|thread-idle|arena-purge-rearm|purge-all' --output-on-failure`. The `c-unit`
rows run whichever of these they register, the `gated` row (`-DMI_OWNER_GATE=ON`, `GATED`
branches) included. Exceptions: the `shared` row (`-DMI_BUILD_STATIC=OFF`) registers neither
`test-thread-idle-rss` nor `test-arena-purge-rearm`; no Windows lane (native `ctest (windows-latest)`
or the bundles) registers `test-park-handoff*`; `test-profile-race-scavenger` needs `MI_PPROF=ON`.
[`bun-surface`](ci-gates.md#bun-surface-is-a-hard-gate) is a hard gate on `mi_on_thread_idle`. None
carries the `macos` label, so the selective Recovery lane does not run them.

## 11. Where to look

| file | functions / symbols | role |
|---|---|---|
| `src/scavenger.c` | `mi_scavenger_run`; `_mi_scavenger_start`, `_mi_scavenger_start_lazy`, `_mi_scavenger_stop`, `_mi_scavenger_forked_child` | the loop; lifecycle per platform |
| `src/scavenger.c` | `_mi_scavenger_wake`, `mi_scav_wait`, `mi_scav_wake_one`; the four public functions | coalesced wake, OS wait; §3 |
| `src/scavenger.c` | `_mi_theap_sweep_parked`, `mi_tld_sweep_theap0`, `_mi_thread_idle_work`, `_mi_thread_idle_work_ex`; `_mi_park_leave`, `_mi_park_leave_gate`, `mi_park_leave_loop` | claim and sweep; taking a tld back |
| `include/mimalloc/types.h`, `include/mimalloc/internal.h` | `MI_PARK_*`, `mi_tld_t` park fields, `mi_scav_word_t`; `_mi_park_leave_if_parked`, `_mi_theap_can_touch` | state and layout; slow-path leave, debug ownership check |
| `include/mimalloc/owner-gate.h` | `_mi_gate_enter`, `_mi_gate_leave`, `_mi_gate_held` | the gated default |
| `src/arena.c`, `src/theap.c` | `mi_arena_schedule_purge`, `_mi_arenas_purge_now`, `_mi_arenas_try_purge`; `mi_theap_collect_ex`, `mi_theap_page_collect`, `_mi_theap_collect_foreign` | deadlines, wakes, purge pass; reclaim checks, no arena purge while `SWEEPING` |
| `src/init.c`, `src/heap.c`, `src/fork.c` | `mi_tld_register`, `_mi_thread_done`, `mi_process_done_once`; `mi_heap_detach_theaps`; `_mi_process_fork_prepare`, `_mi_process_fork_child` | registry, exit while parked, teardown; leave before delete; fork |
| `src/purge-all.c`, `src/arena-reclaim.c`, `src/diagnostic-walk.c` | `mi_purge_walk_claim`, `mi_arena_reclaim_claim_all`, `mi_diag_try_tld` | the other claimants |
| `src/page-holes.c`, `src/prim/windows/prim.c` | `_mi_purge_holes_of`, `_mi_pages_release_retired`; `mi_scavenger_crt_stop` | hole phase, retired pages; the `atexit` stop |
