# Sampled pprof profiler internals

*Part of the [mimalloc-pprof](../README.md) documentation.*

This is the implementation reference for the sampled heap profiler (`MI_PPROF`). Cost, the
environment variables, the seed guarantee, embedded stock mimallocs and the exact-stats
block are covered in the [profiler reference](profiler.md). This document covers what sits
under them: the hooks, how a sample is recorded and found again, the two dump formats, and
the locks and invariants that hold it together.

## 1. Purpose and provenance

The profiler samples allocations by byte volume, records the call stack of each sampled
block, and writes a gperftools-style `heap_v2` text profile or a pprof `profile.proto`
(epic #2). Its reason to exist is **usable Windows profiles**. Bun's fork of mimalloc has its
own profiler (its `prof.c`), but, as [fork divergence](fork-divergence.md) records, it writes
no module mappings on Win32, so its Windows profiles cannot be symbolized. This one
enumerates loaded modules on Windows, macOS and Linux (§4.7), and pprof resolves PCs
offline against the PDB or the ELF/Mach-O image.

Provenance, as stated in the source comments:

| Piece | Provenance |
|---|---|
| Thread-local sampling countdown, no lock until a sample fires | found by comparing with oven-sh/mimalloc's profiler (#78), `_mi_prof_on_alloc` |
| Zero-cost-when-stopped fast path (`prof_force_slow`, poisoned `pages_free_direct`) | strategy ported from oven-sh/mimalloc @ 942b8342, MIT (#267); the implementation is this fork's own |
| Hook reentrancy state on `mi_tld_t`, not `__thread` | #266: a macOS dylib's lazy TLS allocates through a dyld-interposed `calloc` |
| PRNG seeded from the thread ordinal | #91: the earlier address-based seed was randomised by ASLR |
| Capture that never allocates, no in-process symbolizer | #128 D1/D2, an audit of upstream's three allocation-site capture attempts |
| `backtrace()` on Apple | #35: arm64e pointer-authentication bits |
| Fork handlers, `prof_lock` innermost | #270, this fork's own decision |
| Hole-sweep interaction asserts | #272 |

## 2. Build and configuration surface

**Build switches.** CMake `MI_PPROF` (default `OFF`, #414) adds `MI_PPROF=1`, appends
`src/profile-stack.c` and `src/profile-maps.c` to the sources, and on non-Windows adds
`-fno-omit-frame-pointer`, which the Linux stack walk needs (§4.5).
`include/mimalloc/types.h` defaults `MI_PPROF` to 0. The Rust crate's `pprof` feature
(`default = []`) makes `rust/mimalloc-pprof/build.rs` define `MI_PPROF` to `1`, and to `0`
otherwise; the build script adds no frame-pointer flag. `src/static.c` always includes
`profile.c`, for its stubs, and includes the other two files only under `#if MI_PPROF`.

**Compile-time constants.** None of these is `#ifndef`-guarded, so no build can override
them. The T16 comment in `test/test-profile.c` works around the stack cap for that reason.

| Name | Value | Where | Meaning |
|---|---|---|---|
| `MI_PROF_CHUNK_SIZE` | 64 KiB | `src/profile.c` | profiler-arena chunk and dump-buffer chunk |
| `MI_PROF_BT_MAX_LIMIT` | 128 | `src/profile-stack.c` | captured-depth cap (repeated as a literal in `mi_prof_start_ex`) |
| `MI_PROF_STACK_CAP` | 65536 | `src/profile-stack.c` | maximum interned stacks |
| `PROF_PROTO_MAX_MODULES` / `PROF_PROTO_MAX_DEPTH` | 512 / 128 | `src/profile.c` (enums) | proto writer's module table and per-sample depth |
| initial `stack_capacity` | 1024 | `stack_init` | intern-table slots, doubled at 75% load |

**Runtime options** (`src/options.c`):

| Option | Environment | Default | Read |
|---|---|---|---|
| `mi_option_prof` | `MIMALLOC_PROF` | 0 | once, by `prof_auto_start` |
| `mi_option_prof_sample_rate` | `MIMALLOC_PROF_SAMPLE_RATE` | 524288 | latched at start |
| `mi_option_prof_bt_max` | `MIMALLOC_PROF_BT_MAX` | 32 | every sample (`_mi_prof_stack_intern`) |
| `mi_option_prof_accum` | `MIMALLOC_PROF_ACCUM` | 0 | every sample, and on stack release |
| `mi_option_prof_seed` | `MIMALLOC_PROF_SEED` | 0 | latched at start |
| `mi_option_prof_max_bytes` | `MIMALLOC_PROF_MAX_BYTES` | 0 | latched at start into `prof_max_bytes` |

Three variables have no option behind them and are read with `_mi_getenv`:
`MIMALLOC_PROF_SAMPLE_INTERVAL` (plain decimal, `prof_env_get_size`; takes precedence over
the `_RATE` alias), `MIMALLOC_PROF_DUMP_AT_EXIT`, and `MIMALLOC_PROF_DUMP_FORMAT`.

> **Known defect: `MIMALLOC_PROF_DUMP_FORMAT` is ignored.** Both of its readers
> (`prof_auto_start` and `mi_prof_start_ex`) pass a 32-byte `fmt_buf`, and `_mi_getenv`
> (`src/libc.c`) rejects any buffer under 64 bytes with `ENOENT`. So `=proto` still yields a
> text exit dump. Worse, when the variable is set, a FALLBACK-mode `mi_prof_start_ex` sees it
> through `prof_env_present` (a 64-byte buffer) and drops its own `dump_format` too. The only
> way to get a proto exit dump is `dump_format = MI_PROF_FORMAT_PROTO` in an OVERRIDE config,
> or in FALLBACK with the variable unset. No test sets the variable.

**What `MI_PPROF=0` removes.** Every hook call site (each inside `#if MI_PPROF`) except the
three in `src/fork.c`, which call the empty `_mi_prof_fork_*` stubs unconditionally. It also
removes the `mi_page_t` fields `metadata`/`has_metadata`, `mi_theap_t::prof_force_slow`,
`_mi_subproc_prof_sync_force_slow`, `_mi_theap_pages_free_direct_poison`, and the
`prof_force_slow` branches of `mi_theap_queue_first_update`. The public API survives as the
`#else` block at the end of `src/profile.c`, the fork's template for OFF-build stubs: every
`mi_prof_*` returns `false`, `NULL` or zeroes, and `_mi_prof_process_*`/`_mi_prof_fork_*` are
empty. (The `#else` stub of `_mi_prof_stack_capture` in `src/profile-stack.c` is dead:
`src/static.c`, the vendored amalgamation and CMake all include that file only under
`MI_PPROF`.) Some state stays unconditionally: `mi_tld_t::profiler` (a 32-byte
`mi_profiler_tld_t`), `mi_hooks_tld_t::prof_callback_depth`/`prof_lock_owner`, and the
`mi_option_prof*` entries.

## 3. Public API contracts

Declared in `include/mimalloc/profile.h`, all `mi_attr_noexcept`. The lifecycle, dump,
snapshot-creation and module entry points first call `prof_callback_depth_active()`: on a
thread currently inside a `mi_prof_visit` callback they fail fast (`false`, `NULL`, or a
no-op).

| Entry point | Contract | Lock | Rust |
|---|---|---|---|
| `mi_prof_start`, `mi_prof_start_seeded` | `false` if already running. A 0 rate resolves through the env/option chain (§4.3); bumps `prof_generation`; walks every theap (§4.2) | `prof_lock` for the flip, the walk outside it | `prof::start`, `prof::start_seeded`, `enable_heap_profiling` |
| `mi_prof_start_ex` | NULL means `mi_prof_start(0)`; `false` on a `size`/`version` mismatch. Applies `accum`/`max_stack_depth`/`max_profiler_bytes` (via `mi_option_set`, so they persist after stop) and the exit-dump path/format **before** calling `mi_prof_start_seeded`, so they take effect even when it returns `false` because the profiler is already running | as above | `enable_heap_profiling_with` |
| `mi_prof_stop` | Clears `prof_enabled`, writes `metadata`/`has_metadata` of each record's page (see §6 for the `mi_heap_destroy` defect), frees every arena chunk, zeroes counters, then clears `prof_force_slow` everywhere | `prof_lock`, then the walk | `prof::stop` |
| `mi_prof_reset` | Zeroes accum counters, sweeps refcount-0 stacks; live records untouched | `prof_lock` | `prof::reset` |
| `mi_prof_dump_writer`, `mi_prof_dump` | Text `heap_v2`. Fully buffered first; `write` runs only if everything succeeded, after `prof_lock` is released | `prof_lock` for the stack walk | `prof::dump_to_vec`, `prof::dump_file` |
| `mi_prof_dump_proto_writer`, `mi_prof_dump_proto` | Uncompressed `profile.proto`, built from a snapshot | only inside `mi_prof_snapshot_new` | `prof::dump_proto_to_vec`, `prof::dump_proto_file` |
| `mi_prof_visit` | `visitor` runs **with `prof_lock` held** and must not allocate (profile.h, #270) | held across callbacks | none |
| `mi_prof_snapshot_new` / `_visit` / `_free` | Deep copy under the lock, visited without it; survives `mi_prof_stop` | `_new` only | `prof::samples` |
| `mi_prof_modules_visit` | OS module list; `info->path` is valid only during the callback | none | `prof::modules` |
| `mi_prof_stats_get` | Accepts the v1, v2 and v3 `size`/`version` shapes; reads atomics unlocked; v3 also calls `mi_stats_get` | none | `prof::stats` |
| `mi_prof_is_enabled`, `mi_prof_debug_stats` | Relaxed flag read; the deprecated `mi_prof_debug_stats` requests the v1 shape so it skips the heap walk | none | `prof::is_enabled` |

Three guarantees hold throughout. **Profiler failures never fail the allocation**: an
arena NULL drops the sample (`prof_dropped_samples`). **Dumps are all-or-nothing**: a failed
buffer chunk or module enumeration returns `false` without calling `write`. **Callbacks never
run under `prof_lock`** except `mi_prof_visit`'s (`mi_prof_dump_writer` asserts `!lock_held`
in debug builds), which is why the Rust `samples()`, whose visitor pushes onto a `Vec`, uses a snapshot.

## 4. Architecture

### 4.1 Data structures and where state lives

File-static in `src/profile.c` / `src/profile-stack.c`:

- `prof_lock` and `prof_enabled`, an `_Atomic(size_t)` used as a bool. The source explains
  that MSVC's C atomics only implement word-width primitives, hence not a bool.
- `prof_chunks`: a LIFO of `mi_prof_chunk_t` behind the bump allocator
  `_mi_prof_arena_alloc`. Nothing is freed individually; the whole list goes in `mi_prof_stop`.
- `mi_prof_record_t` (6 words): `next` (page chain), `all_next` (global `prof_all` chain),
  `ptr` (block start), `page`, `size` (requested size), `stack`. Freed records are recycled
  through `prof_free`.
- `struct mi_prof_stack_s`: `hash`, `depth`, `refcount`, `pin`, `slot`, the counters
  `curobjs`/`curbytes`/`accumobjs`/`accumbytes`, and `pcs[]`. Entries live in `stack_table`,
  an open-addressing table with linear probing and FNV-1a over the PC bytes (`stack_hash`).
  Deleting an entry re-places the rest of its probe cluster.
- Counters readable without the lock: `prof_records`, `prof_bytes`, `prof_accum_records`,
  `prof_accum_bytes`, `prof_arena_committed`, `prof_dropped_samples`, `stack_count`,
  `stack_overflows`.

**Why per-thread state lives on `mi_tld_t`/`mi_theap_t`.** In v3 the `mi_heap_t` is shared
between threads, so owner-private counters cannot go on it. `mi_tld_t::profiler`
(`mi_profiler_tld_t`: `bytes_since_sample`, `next_threshold`, `random`, `generation`) is the
countdown of **one thread's** allocation stream. `_mi_prof_on_alloc` reaches it through
`theap->tld`, so a thread that allocates from several heaps has a single countdown. Only the
owner writes it; the debug build asserts that a scavenger sweep leaves it unchanged
(`_mi_thread_idle_work_ex`). `mi_theap_t::prof_force_slow` is per theap because the array it
poisons, `pages_free_direct`, is per theap. `mi_tld_t::hooks` holds `prof_callback_depth` and
`prof_lock_owner` (#266).

### 4.2 Hook sites and staying off the fast path

| File | Function | Guarded line |
|---|---|---|
| `src/page.c` | `_mi_malloc_generic` (small path), `mi_malloc_generic_fallback` | `_mi_prof_on_alloc(theap, page, p, size - MI_PADDING_SIZE)` |
| `src/page.c` | `_mi_malloc_generic`, `mi_find_page` | `mi_theap_queue_first_update(theap, pq)`: same-thread re-sync |
| `src/page.c` | `mi_page_thread_collect_to_local` | `if (page->has_metadata) _mi_prof_on_free_collect(page, head)` |
| `src/free.c` | `mi_free_block_local` | `if (page->has_metadata) _mi_prof_on_free(page, block)` |
| `src/alloc.c` | `mi_theap_malloc_guarded_hooked_inner` | suppress around the inner call, then one `_mi_prof_on_alloc` keyed by `_mi_page_ptr_unalign` |
| `src/alloc-aligned.c` | `mi_theap_malloc_guarded_aligned` | the same suppress-then-refire |
| `src/alloc.c` | `mi_expand`, `mi_theap_realloc_zero_ex` | `_mi_prof_on_realloc_in_place` |
| `src/subproc.c` / `src/theap.c` | `_mi_subproc_prof_sync_force_slow` / `_mi_theap_init` | cross-theap walk / new-theap flag read |
| `src/init.c` | `mi_process_init_once` / `mi_process_done_once` | `_mi_prof_process_init` / `_mi_prof_process_done` |
| `src/fork.c` | prepare / parent / child | `_mi_prof_fork_prepare` / `_parent` / `_child` |

The `has_metadata` test is at the call site, not inside the out-of-line hook, so a free on
an unsampled page costs one branch and no call (#267). The fast list pop, `alloc.c`'s
`mi_page_malloc_zero`, has no hook at all. The sampling countdown lives only in the slow
path. To route small allocations there while the profiler runs, `mi_prof_start_seeded`
releases `prof_lock` and then calls `_mi_subproc_prof_sync_force_slow`, which sets
`prof_force_slow` on every theap and overwrites `pages_free_direct` with the static empty
page (`_mi_page_empty_get`). Every fast pop then misses. The cross-thread write needs no
synchronization with the owner: the only value ever written is the immutable empty page, so
a stale read costs at most one unsampled allocation, which the source calls an accepted
start-time gap. While the flag is set, `mi_theap_queue_first_update` substitutes the empty
page for the real one and disables its "already set" short-circuit. An earlier version
returned early instead, which left a retired page's pointer in the array: a use-after-free.
`mi_prof_stop` only clears the flag and never rewrites another theap's array; each theap
restores its fast path the next time it passes through the generic path. The walker reads
`mi_prof_is_enabled()` fresh for each theap, so racing start and stop calls cannot leave a
stale final state. A theap created mid-walk reads the flag in `_mi_theap_init` under the same
`heap->theaps_lock`.

### 4.3 The sampling decision and seeding

`_mi_prof_on_alloc` runs these steps in order:

1. Return for a meta page (`_mi_meta_is_meta_page_safe`). This runs **before**
   `prof_auto_start()`, so thread and heap bootstrap cannot self-deadlock on
   `theap_meta_lock` or `heaps_lock` through the start walk (#267).
2. Run the one-shot `prof_auto_start()`, then a relaxed load of `prof_enabled`.
3. Peek the current thread's hooks with `_mi_hooks_tld_peek()`. Bail out on NULL (the thread
   is mid-init) or on `prof_callback_depth > 0` (inside a visitor, or a suppressed inner
   allocation).
4. If `tld->generation != prof_generation`, reset the countdown and the PRNG state. This is
   how a thread notices a restart without taking a lock.
5. Add `size` to `bytes_since_sample`; below the threshold, return. **No lock is taken on
   this common path.**

`prof_threshold` returns `prof_random(owner) % (rate * 2)`, clamped to at least 1: a
**uniform** draw on `[1, 2*rate)` with mean `rate`, not a geometric one. `prof_random` is
xorshift64 (12/25/27) with the output multiplied by `2685821657736338717`. A zero state is
seeded from `prof_seed ^ mi_tld_t::thread_seq ^ 0x9E3779B97F4A7C15`, so thread K always
gets the same stream for a given seed (#91). Every start zeroes `random` through the
generation check, so a restart with the same seed replays each thread's stream. A 0 rate
passed to start resolves to `MIMALLOC_PROF_SAMPLE_INTERVAL`, then to
`mi_option_prof_sample_rate`, then to 524288. `prof_rate` and `prof_generation` are plain
statics: written under `prof_lock`, read here without it. A stale generation delays a reset
by one allocation. The `if (prof_rate == 0)` guard never fires in practice, because
`mi_prof_start_seeded` never stores 0 and `mi_prof_stop` does not reset the rate.

### 4.4 Recording a sample

When the threshold is crossed, `_mi_prof_on_alloc` takes `prof_lock`, re-checks
`prof_enabled`, and takes a record from `prof_free` or from the arena. `_mi_prof_stack_intern`
then captures the stack **while `prof_lock` is held**, and finds or inserts the entry
(`refcount++`). `_mi_prof_stack_alloc` updates `curobjs`/`curbytes`, plus the accum counters
under `mi_option_prof_accum`. Finally the record is pushed onto `page->metadata` and
`prof_all`, and `page->has_metadata` is set.

A sample is dropped, and `prof_dropped_samples` incremented, when the record allocation
fails (the budget), when the capture returns depth 0, when arena or table growth fails, or
when a **new** stack would exceed `MI_PROF_STACK_CAP`. The lookup runs first, so at the cap an
already-interned stack still samples; the cap case also increments `stack_overflows`. When
interning fails, the record goes back to `prof_free`; before that fix every post-overflow
sample leaked arena memory. T16 does not detect a regression of that fix: its
`arena_committed` bounds follow from the budget alone. `_mi_prof_arena_alloc` checks
`prof_max_bytes` only when it adds a **new** chunk, so the budget has 64 KiB granularity.

### 4.5 Stack capture per platform (`src/profile-stack.c`)

| Platform | Mechanism | Notes |
|---|---|---|
| Windows (MSVC, clang-cl, win-gnu) | `RtlCaptureStackBackTrace(2, capacity, pcs, NULL)` | capacity clamped to 128 |
| Apple | `backtrace()` into a 130-slot stack buffer, frame 0 dropped | libSystem strips PAC bits (#35) and does not allocate |
| everything else | frame-pointer walk from `__builtin_frame_address(0)` | stops on a NULL return address, a frame that does not grow upward, a jump over 8 MiB, or a pointer not 8-byte aligned |

No branch allocates or symbolizes (#128). Upstream needed `mi_recurse_enter` and dbghelp
`Sym*` setup because its capture did both; this code needs neither, and the source asks that
it stay that way. `pcs[0]` is the innermost frame. Nothing beyond the fixed skip is trimmed,
so the innermost frames of every stack belong to the allocator's own call chain.

### 4.6 The free path

A sampled block is found through its page, never through a global lookup. On a local free,
`mi_free_block_local` tests `page->has_metadata`. `_mi_prof_on_free` then takes `prof_lock`,
unless this thread already holds it (`prof_lock_owner`, set only by `mi_prof_visit`).
`prof_free_record` does the rest:

- walks the page's `metadata` chain to the record with `ptr == p`, or returns if there is
  none (an unsampled block on a sampled page);
- unlinks it, and clears `has_metadata` once the chain is empty;
- unlinks it from `prof_all`, a linear search, so each sampled free is O(live records);
- decrements the counters, calls `_mi_prof_stack_free` and `_mi_prof_stack_release`, and
  pushes the record onto `prof_free`.

A cross-thread free (`mi_free_block_mt`) only queues the block on the page's
`xthread_free`. The record stays live until the owner collects:
`mi_page_thread_collect_to_local` calls `_mi_prof_on_free_collect`, which walks the collected
list while `has_metadata` holds. Live counts therefore lag remote frees until a collect,
which is why `test-profile` calls `mi_collect(true)` before it checks them. Records are keyed
by the **block start**: the guarded paths report `_mi_page_ptr_unalign(page, p)`, and
`prof_realloc_in_place` un-aligns its pointer before matching. Any other key would leave the
record unfindable forever (#266). `_mi_prof_stack_release` removes an entry at refcount 0
unless it is pinned or accum is on; in accum mode entries wait for `mi_prof_reset`.

### 4.7 Module mappings per platform (`src/profile-maps.c`)

`_mi_prof_maps_append` emits the text `MAPPED_LIBRARIES:` section. `_mi_prof_maps_visit`
yields `{path, base, size}` for `mi_prof_modules_visit` and for the proto `Mapping` table.
They are deliberately separate implementations, because T2 parses the text form verbatim.

| Platform | Source | Shape |
|---|---|---|
| Windows | `K32EnumProcessModulesEx(LIST_MODULES_ALL)` into `HMODULE[1024]`, `K32GetModuleInformation`, `GetModuleFileNameA` | `lpBaseOfDll` to `+SizeOfImage`; the text form is a synthesized `/proc/maps`-style line |
| macOS | `_dyld_image_count` / `_dyld_get_image_header`: the union of every `LC_SEGMENT`/`LC_SEGMENT_64`, plus the slide | synthesized line per image |
| Linux | `/proc/self/maps` via `open`/`read` | text: a verbatim copy. Visit: contiguous same-path regions merged, kept if any is executable |

All of this uses stack buffers and raw OS calls, runs after `prof_lock` is released, and
enumerates modules at dump time, not at sample time.

### 4.8 Dump formats

**Text.** `mi_prof_dump_writer` writes a `heap profile: ... @ heap_v2/<rate>` header, with
totals from `_mi_prof_stack_visit_info`, and one line per stack whose counters are not all
zero. The counts are **raw, unscaled** samples. After releasing the lock it appends the
`# mimalloc heap stats` block and `MAPPED_LIBRARIES:`. Everything is accumulated in
`prof_dump_chunk_t` buffers from `_mi_os_alloc`.

**`profile.proto`.** `mi_prof_dump_proto_writer` uses a hand-written encoder, so there is no
protobuf dependency (rule 5): `pb_varint`, `pb_field_varint`, `pb_field_bytes` and the
`pb_emit_*` helpers. Repeated scalars are packed, as in Go's `runtime/pprof`, and
submessages are built in bounded stack scratch buffers. The output is **not gzip-compressed**.

| Profile field | Content |
|---|---|
| 1 `sample_type` ×4 | `alloc_objects/count`, `alloc_space/bytes`, `inuse_objects/count`, `inuse_space/bytes` |
| 2 `sample` | packed `location_id` (innermost first) and 4 packed values, **pre-scaled** by `prof_scale_heap_sample`: Go's `scaleHeapSample`, `1/(1-exp(-avg/rate))`, using a local `prof_exp` so libm is not linked |
| 3 `mapping` | `id`, `memory_start`, `memory_limit`, `filename` |
| 4 `location` | `id`, `mapping_id` (when the PC is inside a module), `address` |
| 6 `string_table` | 8 fixed strings, the module paths, then `dropped_samples=N stack_table_overflows=M` |
| 13 `comment` | index of that last string (#33) |
| 11 / 12 / 14 | `period_type` space/bytes, `period` = `prof_rate`, `default_sample_type` = `inuse_space` |

No `Function` or `Line` messages are written; pprof symbolizes offline from the mappings.
`proto_pc_intern` de-duplicates PCs into `Location`s using a table sized to at least twice
the total PC count. With accum off, `alloc_*` comes out as 0.

**Triggers.** Besides the explicit calls, `prof_auto_start` runs once (`mi_atomic_do_once`),
from `_mi_prof_process_init` and also from the first `_mi_prof_on_alloc`. The second call is a
fallback for statically linked MinGW programs that lose the CRT/TLS startup callback. It
starts the profiler when `mi_option_prof` is set and caches `MIMALLOC_PROF_DUMP_AT_EXIT`. Its
read of `MIMALLOC_PROF_DUMP_FORMAT` always fails, which is the known defect in §2, so from the
environment alone the exit dump `_mi_prof_process_done` writes is always text. Under
`MI_NO_PROCESS_DETACH`, `_mi_auto_process_done` returns early, so no exit dump is written.
`mi_prof_start_ex` resolves each field against its variable (`prof_env_present`): FALLBACK
lets the environment win, `MI_PROF_CONFIG_OVERRIDE` lets non-zero fields win (see the
[profiler reference](profiler.md#if-your-process-contains-more-than-one-mimalloc)).

## 5. Invariants and concurrency

**Rule 4: allocation discipline.** Nothing in the profiler allocates on a hooked path.

| Memory | Comes from | Freed |
|---|---|---|
| records, stack entries, intern-table arrays | `_mi_prof_arena_alloc` over `_mi_os_alloc` chunks | whole, in `mi_prof_stop` |
| dump buffers, proto module and PC tables | `_mi_os_alloc`, per call | at the end of the call |
| snapshots | one `_mi_os_alloc` block | `mi_prof_snapshot_free` |
| capture buffers, map lines, proto submessages | the stack | n/a |

The debug build checks it where a violation would do harm.
`_mi_prof_debug_assert_no_records_in` runs before every hole discard
(`mi_page_holes_discard`, `MI_PPROF && MI_DEBUG`). For the records on **that page's own
`metadata` chain** (it returns 0 when `!page->has_metadata`), it asserts that neither the
block nor the record struct lies in the range. It is not a general check that every record
comes from `_mi_os_alloc`. It only try-acquires `prof_lock`, because the sweep holds `theaps_lock` while a
`mi_prof_visit` callback could be waiting on it. Separately, `mi_arenas_page_free_ex` asserts
`!page->has_metadata && page->metadata == NULL` before a page goes back to the arena.

**Locks and lock order.** `prof_lock` is the only lock, and it is **innermost**. The hooks
take it while arbitrary allocator locks may still be held further up the stack, and its own
critical sections take no allocator lock (`_mi_os_alloc` is the OS layer). The fork prepare
order therefore takes it twelfth, after every heap and arena lock
([fork safety](fork-safety.md)); a version that took it first deadlocked under
`MIMALLOC_PROF=1` (#270). `_mi_subproc_prof_sync_force_slow` runs **outside** `prof_lock`,
taking `mi_subprocs_lock`, then `subproc->heaps_lock`, then `heap->theaps_lock`.
`mi_stats_get` is always called before `prof_lock` is taken. `mi_prof_visit` is the known
inversion: `prof_lock` is outer while an allocating visitor would take allocator locks inner,
which is why the header forbids allocating visitors. Same-thread reentry does work, and T10
exercises it: the nested free sees `prof_lock_owner`, and `_mi_prof_stack_pin_all` keeps
table entries from moving while the table is being iterated.

**Atomics.** `prof_enabled` is stored with release semantics and loaded relaxed; the re-check
under the lock is what makes recording correct. The counters are relaxed atomics written
under `prof_lock` and read unlocked by `mi_prof_stats_get`, so its fields are not a
consistent snapshot while other threads run, and `test-profile-race` deliberately asserts
nothing across them. The known defect in §6 means `accum_bytes` is wrong even on a quiescent
heap. `page->has_metadata` is a plain `bool`: mutated only under `prof_lock`,
read unlocked as a filter. A stale `true` costs one locked search that finds nothing.

**Reentrancy.** `prof_callback_depth` and `prof_lock_owner` are reached through
`_mi_hooks_tld_peek()`, which never forces thread init. The one exception is `mi_prof_visit`,
which forces it with `mi_theap_get_default()` so that a nested free on the same thread sees
the real, shared `prof_lock_owner`. `_mi_prof_suppress_begin`/`_end` raise the depth around
an inner allocation whose size or pointer the caller then corrects.

**Fork.** Prepare acquires `prof_lock`, the parent releases it, and the child re-inits it.
The child policy is CONTINUE: records are plain copy-on-write process memory.

## 6. Known defects, edge cases and accepted limits

- **Known defect: use-after-free after `mi_heap_destroy` with live sampled blocks.**
  `_mi_heap_force_destroy` calls `_mi_dhat_forget_heap` but has no profiler counterpart.
  The destroyed pages go to the arena with their records still attached (the debug build
  trips `mi_arenas_page_free_ex`'s assertion). In a release build the orphaned records stay
  in `live_samples`/`live_bytes` and the stacks' `curobjs` until stop, and the next
  `mi_prof_stop` writes `rec->page->metadata`/`has_metadata` into the freed page. That is a
  use-after-free, and it faults if the memory was decommitted or released. No test covers it.
- **Known defect: `accum_bytes` omits in-place growth.** `prof_realloc_in_place` →
  `_mi_prof_stack_resize` adds the growth to the stack's `accumbytes`, but `prof_accum_bytes`
  is never adjusted. So `mi_prof_stats_t.accum_bytes` disagrees with the text header's
  totals, which are summed from the stacks, even when the heap is quiescent. `test-profile`
  only checks an in-place shrink, and only its live bytes.
- **Known defect: `MIMALLOC_PROF_DUMP_FORMAT` is ignored** (§2).
- **Frame pointers (non-Apple Unix).** The walk trusts frame pointers, with sanity bounds
  but no readability probe. CMake always compiles with `-fno-omit-frame-pointer`.
  `build.rs` adds nothing, and cc-rs adds the flag only when debuginfo is on (the default
  dev/test profiles) or `RUSTFLAGS` has `-C force-frame-pointers`. A default `release` cargo
  build has none, and neither do callers built without them; both truncate the walk.
- **Windows.** At most 1024 modules are enumerated and 512 kept in the proto; paths come from
  the ANSI `GetModuleFileNameA`, limited to `MAX_PATH`.
- **macOS Recovery** has no dyld shared cache, so `test_macos_stack_pcs_resolve_to_modules`
  fails there. `ci/recovery_expected_failures.py` requires exactly `test-profile`,
  `test-profile-accum` and `test-profile-auto` to fail.
- **Arena growth tracks cumulative stacks.** Only records are recycled. Unlinked stack
  entries and superseded table arrays keep their arena bytes until `mi_prof_stop`, so a
  stream of short-lived distinct stacks grows the arena up to `max_profiler_bytes`.
- **Per-sample cost.** Capture runs under the global lock, so sampled allocations
  serialize, and the `prof_all` unlink is linear in the number of live samples.
- **Latent deadlock** (documented in `prof_auto_start`, not fixed): a
  `mi_subproc_visit_heaps` visitor that makes the process's first allocation under
  `MIMALLOC_PROF=1` reaches the start walk while holding `heaps_lock`.
- **Teardown.** Meta allocations are never sampled: on an MSVC DLL they can be freed after
  their thread's tld is gone. A free after a thread's teardown peeks NULL hooks and takes the
  locked path.
- **Sticky options.** Accum, depth and budget are set through `mi_option_set` and outlive
  the session; the #267 test restores accum for that reason.

## 7. Testing

| CTest target | Source | Asserts |
|---|---|---|
| `test-profile` | `test/test-profile.c` | Sample-count bounds at a fixed seed, the `@ heap_v2/<rate>` header, zero records after free, in-place realloc accounting. Then: T10 visitor reentrancy (a dump inside a visitor fails; a snapshot survives stop); T12 proto parse (scaled `inuse` between 1× and 2× raw, a mapping naming the test binary, the comment string); T14/T15 module visit and the macOS PAC check; T15a–e `mi_prof_start_ex` precedence and budget; T16 drops and stats v1/v2/v3; T17 aligned; T18 empty profile; start/stop cycles (generation); page reuse after stop; rate 1; cross-thread free; meta pages never sampled; #267 start/stop/restart/reset under allocating workers |
| `test-profile-accum` | same binary, `MIMALLOC_PROF_ACCUM=1` | the accum branches: stacks kept until reset |
| `test-profile-auto` | same binary, `MIMALLOC_PROF=1` | auto-start on the first allocation |
| `test-profile-race`, `test-profile-race-scavenger` | `test/test-profile-race.c` | bootstrap race, cross-thread free, snapshot under mutation, visit against the scavenger, hole sweep against visits (with a negative control for `_mi_prof_debug_records_in`). The second variant adds `MIMALLOC_PURGE_DELAY=1;MIMALLOC_SCAVENGER=1`. Both are `RUN_SERIAL` |
| `test-prof-seed-determinism` | `test/test-prof-seed-determinism.c` | two child processes with the same seed report equal, non-zero sample counts (#91) |
| `test-fork-locks-prof-env`, `test-fork-locks-spawn-prof-env` | `test/test-fork-locks.c` with `MIMALLOC_PROF=1` | the #270 lock-order reproducer |

The first five targets are registered only with `MI_PPROF=ON`. The two fork-locks variants
sit under `if(NOT WIN32)` alone: they are never registered on Windows, and they are also
registered in OFF builds, where `MIMALLOC_PROF=1` does nothing. To run them, use
`uv run ci/dev_linux.py c-test` (which configures `-DMI_PPROF=ON`) or `ctest -R prof` in a
tree configured that way; `-R test-prof` misses the fork-locks pair. The Rust tests in
`rust/mimalloc-pprof/tests` need `--features pprof` (or `full`).

The CI gates ([CI gates](ci-gates.md)):

- `c-unit.yml`'s `release` bundle, with the profiler on, runs the tests above on Linux, and
  its `pprof-off` row proves the minimal build.
- `build-windows-native`/`ctest (windows-latest)` builds with `cl` and `MI_PPROF=ON`.
- `windows-bundles.yml` covers clang-cl and win-gnu. It and `.github/workflows/cross.yml`
  run google/pprof `-raw` on the dumps from `t3_stats` and `t12_proto`.
- `ci/check_fastpath_identity.py` pins `MI_PPROF=ON`; `ci/check_rust_surface.py` and
  `t19_layout.rs` keep the Rust bindings in step with `profile.h`.

## 8. Where to look

| File / symbol | What |
|---|---|
| `src/profile.c` `_mi_prof_on_alloc`, `prof_random`, `prof_threshold`, `_mi_prof_arena_alloc` | sampling decision, recording, arena |
| `src/profile.c` `prof_free_record`, `_mi_prof_on_free`, `_mi_prof_on_free_collect` | free path |
| `src/profile.c` `mi_prof_start_seeded`, `mi_prof_start_ex`, `mi_prof_stop`, `mi_prof_dump_writer`, `mi_prof_dump_proto_writer` | lifecycle and the two formats |
| `src/profile.c` `_mi_prof_debug_records_in`; the `#else` block | debug check; OFF-build stub template |
| `src/profile-stack.c` `_mi_prof_stack_capture`, `_mi_prof_stack_intern` | capture and intern table |
| `src/profile-maps.c` `_mi_prof_maps_append`, `_mi_prof_maps_visit` | module mappings |
| `src/subproc.c` `_mi_subproc_prof_sync_force_slow`; `src/page-queue.c` `mi_theap_queue_first_update` | fast-path poisoning |
| `include/mimalloc/types.h` `mi_profiler_tld_t`, `mi_hooks_tld_t` | per-thread state |
| `include/mimalloc/hooks-tld.h` `_mi_hooks_tld_peek` | non-initializing TLD access |
| `rust/mimalloc-pprof/src/lib.rs` `mod prof` | safe Rust wrappers |

Related: [page holes](page-holes.md), [scavenger and idle handoff](scavenger-and-idle-handoff.md), [memory-events internals](memory-events-internals.md), [DHAT internals](dhat-internals.md).
