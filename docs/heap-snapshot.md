# Heap snapshots (binary arena/page map) and mi-heapview

*Part of the [mimalloc-pprof](../README.md) documentation.*

`mi_heap_snapshot` writes a compact, fixed-width binary description of every arena and
every page to a file descriptor. With a flag it also writes a free map for each page the
calling thread owns, recording which blocks are free. The point is to answer "why is this
process using so much memory?" offline, after the process has moved on or exited. Three
things read the file: the standalone C viewer `tools/mi-heapview.c`, the Python reference
reader `examples/heap-snapshot/mi_snapshot.py`, and an independent reader in
`test/test-snapshot-reader.h`, which `test-snapshot-exit` and `test-snapshot-walk` use. This
page covers the writer (`src/heap-snapshot.c`), format version 1 field by field, the
viewers, and the tests that keep all of them in agreement.

The live-heap JSON dump (`mi_heap_dump_json`) is behind the same build switch. It is a
separate subsystem with its own ownership protocol, covered in
[heap-dump-and-diagnostics.md](heap-dump-and-diagnostics.md).

## 1. Purpose and provenance

A snapshot shows where the committed memory is (by block size, thread and heap), which
pages waste the most, and the three slice bitmaps of every arena (committed, free,
purge-scheduled). Waste is `committed - used * block_size`: memory the OS backs for a page
that no live block covers. With free maps it also gives a page's live block addresses as
of the writer's collect (`mi-heapview blocks`), which `mi-heapview peek` uses to read block contents out of
a core file. The snapshot is point-in-time and best-effort. It does not stop other threads
(section 7).

`src/heap-snapshot.c` and `tools/mi-heapview.c` were imported verbatim from
oven-sh/mimalloc @ `b20b60d9` (MIT) in issue #338, for Bun parity. Bun itself never calls
the API. The gap analysis rates it a nice-to-have debug tool, not a shipped feature
([bun-gap-analysis-2026-09-01.md](bun-gap-analysis-2026-09-01.md), row B16). Issue #414
then made it opt-in, like every other observability subsystem (CLAUDE.md rule 6).

## 2. Build and configuration surface

| Surface | Name | Default | Effect |
|---|---|---|---|
| CMake option | `MI_DIAGNOSTICS` | `OFF` | `ON` appends `MI_DIAGNOSTICS=1` to `mi_defines`, `OFF` appends `MI_DIAGNOSTICS=0`. The same switch controls the JSON dump. |
| C define | `MI_DIAGNOSTICS` | `0` (`include/mimalloc/types.h`, `#ifndef`-guarded) | A direct `src/static.c` compile has to define it to get the writer. |
| cargo feature | `diagnostics` | off (`default = []`). Part of `full`. | `rust/mimalloc-pprof/build.rs` maps `CARGO_FEATURE_DIAGNOSTICS` to `MI_DIAGNOSTICS`. |
| API flag | `MI_SNAPSHOT_BLOCKS` (`0x01`) | — | Adds free maps for pages owned by the calling thread. |
| runtime option | `mi_option_snapshot_on_exit` / `MIMALLOC_SNAPSHOT_ON_EXIT` | `0` | `1` writes pages only at process exit. `2` or more also writes free maps. |
| environment | `MIMALLOC_SNAPSHOT_PATH` | unset | Output path for the exit snapshot. Falls back to `mimalloc-snapshot.<pid>.bin` in the working directory. |

`mi_option_snapshot_on_exit` is declared in `src/options.c` as `MI_OPTION(snapshot_on_exit)`
with `MI_OPTION_UNINIT`, so it is read from the environment the first time it is used. Its
enumerator value is 60 (`rust/mimalloc-pprof/src/sys.rs`), while Bun's is slot 47: option
slots from 47 on diverged long ago. Bun parity covers the environment variable name and the
file format, not the enum value, so always refer to the option by name.

The writer's constants are plain `#define`s: `MI_SNAPSHOT_MAGIC`, `MI_SNAPSHOT_VERSION`,
the section tags, and the 16 KiB output buffer `MI_SNAP_BUFSIZE`. Magic, version and tags
are the format itself. `MI_SNAP_BUFSIZE`, the 512-byte free-map window and the 512-byte
exit-path buffer are literals from the verbatim import. They are not `#ifndef` tuning knobs
of the kind rule 11 asks new code to use. The lower-case platform macros (`mi_snap_write`,
`mi_snap_open`, `mi_snap_close`, `mi_snap_getpid`) are grandfathered in
`ci/macro_case_baseline.txt` (rule 10).

**Compiled out.** With `MI_DIAGNOSTICS=0` the whole writer is replaced by the `#else` block
at the end of `src/heap-snapshot.c`, which follows the stub template of `src/profile.c`.
`mi_heap_snapshot` and `mi_heap_snapshot_to_file` return `-1` without touching their
arguments, and `_mi_heap_snapshot_on_exit` is empty. `mi_option_snapshot_on_exit` still
exists and can be set, but nothing reads it. So no downstream code needs an `#ifdef`
([c-integration.md](c-integration.md)). The viewer is built either way (section 9).

## 3. Public API

The C declarations are in `include/mimalloc.h`.

| Entry point | Contract |
|---|---|
| `int mi_heap_snapshot(int fd, unsigned flags)` | Writes the whole snapshot to `fd`, starting at the descriptor's current position, and does not close it. Returns `0` on success. Returns `-1` if `fd < 0`, or if any `write` returned `<= 0`, in which case a partial file is left behind. Only the `MI_SNAPSHOT_BLOCKS` bit of `flags` means anything, but the raw value is stored in the header. |
| `int mi_heap_snapshot_to_file(const char* path, unsigned flags)` | Returns `-1` if `path` is NULL or the open fails. Otherwise creates or truncates the file with mode `0644` (`O_WRONLY\|O_CREAT\|O_TRUNC`, plus `_O_BINARY` on Windows), calls `mi_heap_snapshot`, closes the file and returns its result. |

Both functions walk the arenas and heaps of every sub-process while other threads keep
running. Call them from any thread that does not already hold `mi_subprocs_lock`,
`sp->heaps_lock` or a heap's `os_abandoned_pages_lock` (section 7). In an ungated build, do
not call them between `mi_on_thread_idle_start` and `mi_on_thread_idle_end` (section 8).
Without `MI_SNAPSHOT_BLOCKS` they change no allocator state; with it they collect the free
lists of the calling thread's own pages.

```c
#include <mimalloc.h>

// 0 = written; -1 = open/write error, or a build without MI_DIAGNOSTICS
int rc = mi_heap_snapshot_to_file("snap.bin", MI_SNAPSHOT_BLOCKS);
mi_option_set(mi_option_snapshot_on_exit, 2);   // and write another one at process exit
(void)rc;
```

**At exit.** `_mi_heap_snapshot_on_exit` (declared in `include/mimalloc/internal.h`) runs
from `mi_process_done_once` in `src/init.c`. It runs after `_mi_scavenger_stop()` and
before everything else, including the `_mi_process_is_initialized` check. It returns if
`mi_option_snapshot_on_exit` is `<= 0`. Otherwise it resolves the path into a 512-byte
stack buffer with `_mi_getenv("MIMALLOC_SNAPSHOT_PATH", ...)`, falling back to
`mimalloc-snapshot.<pid>.bin` when that returns non-zero, and calls
`mi_heap_snapshot_to_file`. Success is reported through `_mi_verbose_message` (visible with
`MIMALLOC_VERBOSE`), failure through `_mi_warning_message`.

**Rust** (`rust/mimalloc-pprof/src`):

| Item | Notes |
|---|---|
| `mimalloc_pprof::heap_snapshot_to_file(path, blocks) -> std::io::Result<()>` | Builds a `CString` from `as_encoded_bytes()`. An interior NUL gives `InvalidInput`, a non-zero return gives `io::Error::other`. Always returns `Err` without the `diagnostics` feature. |
| `sys::mi_heap_snapshot(fd, flags)` | sys-only. `fd` is a CRT descriptor, not a `HANDLE`. |
| `sys::mi_heap_snapshot_to_file(path, flags)`, `sys::MI_SNAPSHOT_BLOCKS` | Raw bindings. |
| `options::Opt::SNAPSHOT_ON_EXIT` with `options::set` | The exit option. |

The C writer allocates nothing, but the Rust wrapper allocates a `CString`, plus a
`String` on error. It is not itself safe to call from inside a `GlobalAlloc`
implementation.

## 4. Format version 1

`examples/heap-snapshot/mi_snapshot.py` is the executable spec. The tables below follow
that file and the writer's emit functions.

**Conventions.**

- A packed byte stream with no alignment padding: readers `memcpy` or `struct.unpack`.
- Every field is written by `mi_snap_u8`/`mi_snap_u32`/`mi_snap_u64`, a `memcpy` of a
  native integer, so the bytes are in **host order**. The writer's comment supports only
  same-endianness reading. Every CI target (x86_64, aarch64) is little-endian, and
  `mi_snapshot.py` always decodes little-endian.
- Addresses are `u64` whatever the `ptr_size`. Signed values (`numa_node`, `arena_idx`)
  are two's-complement `u32`, so `0xFFFFFFFF` means `-1`.
- A page list is a `u32 'PAGE'` tag, page records, then a `u64 0` where the next record's
  `page_start` would be. Readers peek one `u64` at a time.

```mermaid
graph TD
  H["header, 44 bytes"] --> A["ARNA record + 3 bitmaps"]
  A --> AP["PAGE list: pages starting in this arena"]
  AP -->|"once per non-NULL arena slot, every sub-process"| A
  AP --> OP["PAGE list: writer thread's non-arena pages"]
  OP --> HP["HEAP record"]
  HP --> HL["PAGE list: heap's OS-backed abandoned pages"]
  HL -->|"once per heap, every sub-process"| HP
  HL --> E["END footer, 12 bytes"]
```

**Header** (44 bytes):

| Off | Type | Field | Written from |
|---|---|---|---|
| 0 | u32 | magic | `MI_SNAPSHOT_MAGIC` = `0x5348494D` (the bytes `MIHS`) |
| 4 | u32 | version | `MI_SNAPSHOT_VERSION` = 1 |
| 8 | u32 | ptr_size | `MI_INTPTR_SIZE` |
| 12 | u32 | slice_size | `MI_ARENA_SLICE_SIZE`: 64 KiB on 64-bit, 32 KiB on 32-bit, 128 KiB under `MI_SECURE>=5` on Apple arm64 or 16 KiB-page systems |
| 16 | u32 | flags | the caller's `flags`, unmasked |
| 20 | u32 | reserved | 0 |
| 24 | u64 | clock_ms | `_mi_clock_now()`: a monotonic clock (`CLOCK_MONOTONIC` / `QueryPerformanceCounter`), not wall time |
| 32 | u64 | writer_tid | `_mi_prim_thread_id()`: the thread pointer, not an OS thread id |
| 40 | u32 | arena_count | the ARNA records that follow: the non-NULL arena slots of every sub-process, counted before the arena pass (`mi_snap_count_arenas`) |

**Arena record** (40 bytes, then 3 bitmaps, then its page list):

| Off | Type | Field | Source |
|---|---|---|---|
| 0 | u32 | tag | `MI_SNAP_SEC_ARENA` = `0x414E5241` (`ARNA`) |
| 4 | u32 | idx | slot index in its sub-process's `arenas[]`. Not globally unique: every sub-process has a slot 0. |
| 8 | u64 | base | the `mi_arena_t*` itself, i.e. the start of the arena |
| 16 | u64 | size | `mi_size_of_slices(slice_count)` |
| 24 | u32 | slice_count | `arena->slice_count` |
| 28 | u32 | info_slices | metadata slices at the arena start. The page walk begins after them. |
| 32 | u32 | numa_node | `arena->numa_node` (i32, `-1` = any) |
| 36 | u8, u8, u8[2] | pinned, exclusive, pad | `memid.is_pinned`, `is_exclusive` |
| 40 | bitmap ×3 | committed, free, purge | `slices_committed`, `slices_free` (a `mi_bbitmap_t`), `slices_purge`: the long-window queue only; since #506 the short-window `slices_purge_short` and both `_aged` bitmaps are not written, so "purge" undercounts slices queued from small and medium pages |

A **bitmap** is `u32 chunk_count | u32 chunk_bytes | chunk_count × chunk_bytes bytes`.
`chunk_bytes` is `MI_BCHUNK_SIZE` in bytes, which is 64 (512 bits) on 64-bit. A
`chunk_count` of 0 encodes a NULL bitmap. The payload is the live `bfields` words, each
read with `mi_atomic_load_relaxed`. Bit *i* is slice *i*. Viewers count only the first
`slice_count` bits (`mi-heapview` rounds that up to a whole byte).

**Page record** (80 bytes, then an optional free map):

| Off | Type | Field | Source |
|---|---|---|---|
| 0 | u64 | page_start | `mi_page_start`, the first block. The value `0` is the sentinel. |
| 8 | u64 | slice_start | `mi_page_slice_start` |
| 16 | u64 | block_size | `mi_page_block_size` |
| 24 | u32 ×3 | reserved, capacity, used | `page->reserved` (blocks reserved), `page->capacity` (blocks initialised), `page->used` (in use, including pending thread frees) |
| 36 | u64 | committed | `mi_page_committed`, in bytes |
| 44 | u64 | tid | `mi_page_thread_id`, flag bits masked. `0` is `MI_THREADID_ABANDONED`; `4` is `MI_THREADID_ABANDONED_MAPPED`. |
| 52 | u64 | heap_seq | `page->heap->heap_seq`, or 0 |
| 60 | u32 | arena_idx | arena slot for pages found by the arena walk (the `idx` of the ARNA record the list follows). `0xFFFFFFFF` for pages from the other two lists. |
| 64 | u32 ×2 | slice_index, slice_count | `memid.mem.arena.*` for `MI_MEM_ARENA`, else 0 |
| 72 | u8 | memkind | raw `mi_memkind_t`: 0 NONE, 1 EXTERNAL, 2 STATIC, 3 OS, 4 OS_HUGE, 5 OS_REMAP, 6 ARENA, 7 MALLOC |
| 73 | u8 | page_kind | `mi_snap_page_kind`: 0 small, 1 medium, 2 large, 3 singleton |
| 74 | u8 ×3 | abandoned, full, has_freemap | `mi_page_is_abandoned`, `mi_page_is_full` (`reserved == used`), whether a free map follows |
| 77 | u8[3] | pad | 0 |
| 80 | u32 + bytes | freemap | only when `has_freemap`: `nbytes = ceil(capacity / 8)`, then the map |

`page_kind` is derived, not stored: `mi_snap_page_kind` returns singleton when
`mi_page_is_singleton` (`reserved == 1`). Otherwise it compares `block_size` against
`MI_SMALL_MAX_OBJ_SIZE` and `MI_MEDIUM_MAX_OBJ_SIZE`. `memkind` is an enum value, so
format parity also depends on `mi_memkind_t` keeping its order.

**Free map.** Bit *j* of byte `j >> 3` (least significant bit first) is `1` when block *j*
is **free**. The writer first calls `_mi_page_free_collect_no_unpurge(page, true)`, which
folds the thread-free and local-free lists into `page->free`. A block then counts as free
if it is on `page->free`, or if `mi_page_block_index_is_purged` says it is a purged hole.
Hole purging keeps purged blocks off the free list ([page-holes.md](page-holes.md)), and
inspection must not fault them back in. Blocks in `[capacity, reserved)` are not
described. `mi_snap_emit_page` samples `used`, `full` and `abandoned` *before* this collect,
so on a page with a free map `used` (and so `full`) can overcount by the pending thread
frees the collect then folds. The free map is the accurate one.

When the map fits the 512-byte window (capacity up to 4096) the writer walks the free list
once. It computes each block index with a copy of the fast-divisor arithmetic. Larger pages
take a windowed slow path that rescans the free list for each 512-byte window using plain
division.

**Heap record** (24 bytes, then its page list): `u32 'HEAP'` (`0x50414548`),
`u64 heap_seq`, `u32 numa_node` (i32), `u64 exclusive_arena`. The last field is the
*pointer value* of `heap->exclusive_arena` (0 if none), so it matches an arena's `base`,
not its `idx`.

**Footer** (12 bytes): `u32 ' END'` (`0x444E4520`), then `u64 page_count`, the number of
page records written.

**Reader strictness differs.** `parse` in `mi_snapshot.py` expects exactly `arena_count`
ARNA records, each followed by a PAGE list, then one more PAGE list, then HEAP records
until END. It checks the footer count and raises `FormatError` on anything else, including
truncation. The reader in `test/test-snapshot-exit.c` is just as strict. `hv_parse` in
`tools/mi-heapview.c` accepts PAGE and HEAP sections in any order after the arenas, and
ignores the footer count. All three reject every version but 1. None of them keys arenas
by `idx` or heaps by `heap_seq`, so all three accept the records of several sub-processes
(section 8). The docstring of `ci/tests/test_heap_snapshot_example.py` records the rule: a
change to the writer's format must bump the version.

## 5. How the writer walks the heap

1. **`mi_heap_snapshot`** returns `-1` for a negative `fd`, then takes `mi_subprocs_lock`
   and holds it for the whole snapshot, *before* the owner gate (section 7 says why).
   `mi_heap_snapshot_gated` is the owner-gate site (#366). In a `MI_OWNER_GATE` build it
   brackets the work with `MI_GATE_ENTER`/`MI_GATE_LEAVE`, unless the calling theap is
   uninitialised. The free-map path writes owner-private state in the caller's pages:
   without the gate a parked caller could be swept meanwhile, and the gated check in
   `_mi_page_free_collect_no_unpurge` would skip the collect
   ([purge-all-implementation.md](purge-all-implementation.md) §5).
2. **`mi_heap_snapshot_inner`** zeroes a stack `mi_snap_out_t` and writes the header up to
   `arena_count`. Steps 3 to 5 walk the sub-process registry (`_mi_subprocs_head`, newest
   first, so the main sub-process comes last). It never consults `_mi_subproc()`, which at
   process exit can see an empty theap.
3. **Arena pass.** `mi_snap_count_arenas` counts the non-NULL slots below each
   sub-process's `arena_count`, and that count completes the header. Then, for each
   non-NULL slot (`mi_atomic_load_ptr_acquire`), until that many records are written,
   `mi_snap_emit_arena_header` writes the record and bitmaps. `mi_snap_walk_arena_pages`
   then steps from `info_slices` to `slice_count`, finding each slice's page with
   `mi_arena_slice_start` and `_mi_safe_ptr_page` (a page-map lookup). It emits a page only
   at the page's first slice (`start == mi_page_slice_start(page)`) and then skips the
   page's `memid` slice count; otherwise it moves one slice.
4. **Own non-arena pages.** `mi_snap_walk_own_theaps` visits every theap of the caller's
   tld (`tld->theaps`, `tnext`) and every bin below `MI_BIN_COUNT`, emitting pages whose
   `memid.memkind` is not `MI_MEM_ARENA`. Per the source, this catches OS-direct pages
   created while preloading, before any arena existed (common with macOS dynamic override).
5. **Heap pass.** For each sub-process, `mi_snap_walk_subproc_heaps` takes
   `sp->heaps_lock` and writes a HEAP record per heap. `mi_snap_walk_heap_os_pages` then
   walks `heap->os_abandoned_pages` under `heap->os_abandoned_pages_lock`.
6. **Footer and flush.** The function returns `-1` if any write failed.

**Output path.** `mi_snap_put` copies into the 16 KiB buffer and flushes when it is full.
`mi_snap_flush` loops over short writes. A `write` returning `<= 0` sets `err`, and every
later put becomes a no-op. The walk still runs to the end, so the locks are taken and
released normally.

**Touch points in upstream-owned files** (rule 6):

| File | What |
|---|---|
| `src/init.c` | One unconditional call to `_mi_heap_snapshot_on_exit()` in `mi_process_done_once`. The stub makes an `#if` unnecessary. |
| `src/options.c`, `include/mimalloc.h` | the option entry and enumerator; `MI_SNAPSHOT_BLOCKS` and the two entry points |
| `include/mimalloc/internal.h`, `include/mimalloc/types.h` | the `_mi_heap_snapshot_on_exit` declaration; the `MI_DIAGNOSTICS` default |
| `src/static.c` | `#include "heap-snapshot.c"`, needed by the Rust amalgamation |
| `src/page.c` | `_mi_page_free_collect_no_unpurge`, the #272 hook this writer shares with the block visitors |

## 6. The parity contract and the local deviations

Format version 1 is a parity contract with oven-sh/mimalloc @ `b20b60d9`: a snapshot from
either allocator must open in either viewer. For that reason `src/heap-snapshot.c` carries
no fork extensions *to the format*, and any change to the byte layout breaks the contract.
The file's header comment lists the local deviations, all outside the format. The two
`src/arena.c` functions it calls, `mi_arenas_get_count` and `mi_arena_slice_start`, are
declared in the file because this tree's `internal.h` does not export them
(`src/arena-reclaim.c` copies the pattern). The exit message goes through
`_mi_verbose_message`, because this tree has no ungated `_mi_message`. And two writer fixes
change which records are written, never their layout: the walk covers every sub-process,
and the header declares only the arenas the arena pass writes (section 5). With one
sub-process and no NULL arena slot the output is what Bun's writer produces.

Three more fork adaptations are not in that list, and none changes a byte of output: the #414 `#if MI_DIAGNOSTICS` guard and its stubs, the #366
owner-gate wrapper around `mi_heap_snapshot`, and the `_mi_getenv` test in
`_mi_heap_snapshot_on_exit`. This tree's `_mi_getenv` returns an errno-style code (`0` =
found) where Bun's returns a bool, so the test is inverted to keep the same meaning.

## 7. Invariants and concurrency

**Allocation discipline.** The writer allocates nothing. All its memory, a little over
16 KiB, is on the stack: `mi_snap_out_t` with its 16 KiB buffer, the 512-byte `map`, one
chunk of `fields[MI_BCHUNK_FIELDS]`, and at exit the 512-byte `path`. There is no
`mi_malloc` and no `_mi_os_alloc`. That is stricter than rule 4, which allows raw-OS
memory, and it is what makes the writer safe to run from `mi_process_done` and from inside
hooked allocation paths. Keep it that way. The snapshot bytes go through `write`/`_write`.
The exit hook's single status line uses mimalloc's normal message output: by default
`_mi_prim_out_stderr` (`fputs` to stderr on POSIX), unless an output handler is registered.

**Locks.** The writer holds `mi_subprocs_lock` (`src/fork.c` step 1) for the whole
snapshot. Inside it, per sub-process, it takes `sp->heaps_lock` (step 2) and then
`heap->os_abandoned_pages_lock` (step 10, a leaf). The registry lock keeps each listed
sub-process alive (`mi_subproc_destroy` unlinks under it before freeing anything) and
excludes `MI_PURGE_RECLAIM`, which holds it for its whole pass. It is taken *before* the
caller's owner gate. `mi_purge_all_ex` holds the registry while it waits for RUNNING owners
to park, and relies on no owner taking the registry inside an allocator call
(`src/purge-all.c`). A caller that entered its gate first would be an owner the purge waits
for while it waits for the purge, until the purge's deadline. A probe with one thread
snapshotting in a loop against 200 `mi_purge_all_ex(0, 3000, ..)` calls measured the
difference: gate first, 115 owners reported pending and a worst call of 3.4 s; registry
first, none pending and 60 ms. Registry-then-gate cannot deadlock, because nothing that can
hold a SWEEPING claim on the caller's tld waits for the registry: the scavenger and the dump
capture never take it, and the purge and the reclaim claim only while holding it. A
snapshot nested inside a gated allocator operation (a callback) already holds its gate, so
a concurrent purge reports it pending, as it would any owner that stays inside the
allocator. The registry lock is held across the file writes, so a slow descriptor such as
a full pipe also delays `mi_subproc_new`, `mi_subproc_destroy`, `fork()`, `mi_prof_start`'s
theap sync and `mi_purge_all_ex`. The locks are not recursive, so calling the writer from
code that holds any of them deadlocks. An example is a callback reached during heap
teardown, which frees under `heaps_lock` (`src/fork.c`).

**Reads.** Arena slots are loaded with acquire, bitmap words relaxed, and the page owner
through the atomic `xthread_id` (`mi_page_thread_id`). For pages of *other* threads,
`used`, `capacity` and `reserved` are plain field reads of memory that the owner is
writing, which is why their counts can be slightly stale. The header comment's claim that
every shared read goes through an atomic or a const field does not hold for these fields.

**Writes.** The writer changes allocator state only when `MI_SNAPSHOT_BLOCKS` is set, and
only for pages the caller owns. The test is `tid == self_tid`, with
`tid > MI_THREADID_ABANDONED_MAPPED`. `_mi_page_free_collect_no_unpurge` refuses a
non-abandoned page unless the caller is its owner (inside its gate, in a gated build),
holds the tld's sweep claim, or the theap is detached from its heap (#366). It folds
abandoned pages for anyone; the writer never passes one (the `tid` test). No unpurge.

## 8. Edge cases and accepted limits

- **NULL arena slots.** A slot can be NULL below a sub-process's `arena_count`: after
  `MI_PURGE_RECLAIM` releases an arena that was not the last slot
  ([arena-reclaim.md](arena-reclaim.md)), until `mi_arenas_add` reuses it, and for a moment
  while `mi_arenas_add` has raised `arena_count` but not yet stored the pointer. The header
  counts only non-NULL slots, so such a slot is simply skipped. (Before the fix the header
  summed `mi_arenas_get_count`, the file declared more ARNA records than it held, and all
  three readers rejected it; `test-snapshot-walk` row W1 pins this.)
- **Arenas added during the walk.** With `mi_subprocs_lock` held a slot can go from NULL to
  an arena (`mi_arenas_add` takes no registry lock) but not back, because the reclaim and
  sub-process teardown both need that lock. An arena added after the count can therefore
  take the place of a counted arena in a later slot, but the count always matches the
  records. The one other path that clears a slot, `mi_arena_unload`, has no public
  declaration and requires that no thread use the arena.
- **Parked caller, ungated build.** A caller parked by `mi_on_thread_idle_start` is not
  protected by `MI_GATE_ENTER`, which does nothing there. With `MI_SNAPSHOT_BLOCKS`, its
  collect races the scavenger's sweep of its own theaps. A concurrent `MI_PURGE_RECLAIM`,
  which this caveat used to include, is excluded by the registry lock in every build.
- **Sub-processes.** Every sub-process is written, in registry order. (Before the fix the
  walk started at `_mi_subproc_main()` and followed `next`, which is always NULL for it:
  `mi_subproc_init` pushes each new sub-process at the head and main registers first. Row
  W2 pins this.) Format version 1 has no sub-process field, so the records of all
  sub-processes form one flat list and a reader cannot tell which sub-process a record
  belongs to. `idx` and a page's `arena_idx` repeat across sub-processes, and every
  sub-process's main heap has `heap_seq` 0. Attribute a page to its arena by position (the
  PAGE list right after an ARNA record) or by address (`slice_start` within
  `[base, base + size)`), and a heap's `exclusive_arena` by `base`.
- **Not recorded:** non-arena pages of other live threads (the own-theap pass covers only
  the caller), and anything of another mimalloc instance in the same process.
- **Free maps** exist only for the calling thread's pages (for the exit snapshot, the thread
  running `mi_process_done`), never for abandoned pages. `mi-heapview blocks` says why.
- **Windows.** `fd` must be a descriptor of the C runtime mimalloc uses, not a `HANDLE`,
  opened in binary mode (`_O_BINARY`) as `mi_snap_open` and `test/test-snapshot.c` do.
  `_open` takes a narrow path while the Rust wrapper passes `as_encoded_bytes()`, so keep
  snapshot paths ASCII there.
- **Exit path fallbacks.** The path falls back silently to the default name when
  `MIMALLOC_SNAPSHOT_PATH` is unset, too long for the 512-byte buffer, or unreadable;
  `MI_NO_GETENV` builds always use the default. Option values above 2 behave like 2. The
  exit snapshot never runs if `_mi_auto_process_done` returns early (`MI_NO_PROCESS_DETACH`,
  or `mi_option_destroy_on_exit` at 2 or more) unless the embedder calls `mi_process_done`
  itself, and `mi_process_done` runs its body at most once.
- **Locks at exit.** On Windows the exit snapshot runs inside `ExitProcess`, after every
  other thread was terminated. If one of them died holding `mi_subprocs_lock` (in
  `mi_purge_all_ex`, `mi_subproc_new`/`mi_subproc_destroy`, or its own snapshot) or a
  `heaps_lock`, the exit snapshot waits forever. The registry is held longer than the
  `heaps_lock` the imported writer needed, so the window is wider. A snapshot taken after
  `mi_process_done` (from a later atexit handler or static destructor) locks the registry
  after `_mi_subproc_main_done` destroyed it, as `mi_purge_all_ex` would.
- **Exit ordering.** The exit snapshot runs after the scavenger has stopped, and before the
  theap cache reset, `_mi_prim_thread_done_auto_done` and the final collect, so the
  caller's pages and theaps are still live. `test-snapshot-exit` pins this ordering.
- **Errors.** A `write` of `-1`, including `EINTR`, is not retried; the partial file stays.
- **Fork.** Nothing in `src/fork.c` is snapshot-specific; the three locks the writer takes
  are among those `_mi_process_fork_prepare` quiesces ([fork-safety.md](fork-safety.md)).

## 9. mi-heapview and the Python reader

**Building the viewer.** CMake target `mi-heapview` builds `tools/mi-heapview.c`. The tool
includes no mimalloc header and never links the allocator. It is built whenever
`MI_BUILD_TESTS=ON` (the default), whatever the value of `MI_DIAGNOSTICS`, so every gate
proves it compiles. It is not installed, and no CTest runs it. On its own:
`cc -O2 tools/mi-heapview.c -o mi-heapview`.

| Command | Output |
|---|---|
| `summary` | arena reserved/committed/purgeable, heap and page counts, page memory, internal fragmentation |
| `sizes [--top N] [--by-tid\|--by-heap]` | per-block-size histogram, sorted by committed bytes |
| `frag [--top N] [--min-waste B]` | pages sorted by waste (default top 30) |
| `arenas` | per arena: committed, free and purgeable bytes (bit count × `slice_size`), NUMA node, pinned/excl |
| `pages [--top N] [--size B] [--sort waste\|addr\|used] [--min-waste B]` | page table |
| `blocks --addr 0xADDR` | live blocks in the page containing ADDR (needs a free map) |
| `diff <snapshot2> [--top N] [--by-tid\|--by-heap]` | per (size, group) deltas `snapshot2 - snapshot`, sorted by \|Δcommitted\| |
| `peek --core FILE --size B [--tid T] [--sample N] [--bytes N]` | hexdumps of sampled live blocks from a core dump |
| `json` | full dump as JSON |

`--human` prints sizes as KiB/MiB/GiB. If `<snapshot>.meta.json` exists with
`{"threads":{"0xTID":"name"}}`, thread names are shown. Nothing in this tree writes that
file, and its keys must be the writer's thread ids (thread pointers), not OS thread ids.
Threads with `tid <= 4` are labelled `abandoned`.

**peek** (`hv_sample_blocks`) samples pages with `used > 0` that either have a free map or
have `used == capacity` (every initialised block live). It reads ELF cores
when built on Linux or FreeBSD and 64-bit Mach-O cores when built on macOS. If at least
half of the samples share their first 8 bytes, it reports that value as a likely vtable.

**Python.** `examples/heap-snapshot/mi_snapshot.py` is stdlib-only and importable
(`load`, `parse`, `Snapshot`, `Arena`, `Heap`, `Page`, `Bitmap`, `FormatError`).
`heapview.py` mirrors `summary`, `sizes`, `frag`, `pages`, `blocks`, `arenas` and `diff`
with the same columns and ordering, leaving out `peek`, `json`, `--human` and the meta
file. `demo.c` is a two-thread workload that writes a snapshot (usage in its `README.md`).

## 10. Testing

| Test | CTest name / target | When | What it asserts |
|---|---|---|---|
| `test/test-snapshot.c` | `test-snapshot` / `mimalloc-test-snapshot` | `MI_BUILD_TESTS` and `MI_DIAGNOSTICS` | Allocates 12 size classes × 200, keeps a third, adds one 8 MiB block. `mi_heap_snapshot(fd, MI_SNAPSHOT_BLOCKS)` must return 0, and the file is deleted afterwards. Optional arguments produce the fixtures by hand: a second snapshot after 5000 × 300-byte allocations stamped `0xC0DEFACE00112233`, `--pause` to wait for a core dump (POSIX), `--keep` to keep the files. |
| `test/test-snapshot-exit.c` | `test-snapshot-exit` / `mimalloc-test-snapshot-exit` | same | Re-runs itself as a child (`fork`+`execl`, or `_spawnv`) with `MIMALLOC_SNAPSHOT_ON_EXIT=2` and `MIMALLOC_SNAPSHOT_PATH` set. The child allocates on two threads and exits normally. The parent parses the file with `test/test-snapshot-reader.h`, which checks magic, version 1, exactly `arena_count` ARNA records, the PAGE/HEAP/END structure, a footer count equal to the records read, and no bytes after the footer. The test adds `MI_SNAPSHOT_BLOCKS` in the flags, more than 0 pages, and at least one free map. The free map proves the exit ordering. |
| `test/test-snapshot-walk.c` | `test-snapshot-walk` / `mimalloc-test-snapshot-walk` | same, plus `MI_BUILD_STATIC` and not `MI_DEBUG_TSAN`; `MIMALLOC_SCAVENGER=0` | White-box: it reads `subproc->arenas[]`. W2: a thread of a `mi_subproc_new` sub-process allocates and exits; the file must hold that sub-process's arenas, with pages, and both main heaps (`heap_seq` 0). W1: with `arena_reserve` at 32 MiB, three 32 MiB objects get an arena each; freeing the middle one and `MI_PURGE_RECLAIM` leave a NULL slot below `arena_count`. In both rows the strict reader must accept the file, the header's `arena_count` must equal the records, and the records must be exactly the live arenas of every sub-process. `--keep` leaves `<path>.W1`/`.W2` for the viewers. |
| `ci/tests/test_heap_snapshot_example.py` | pytest | always | Parses `ci/tests/fixtures/heap-snapshot/test-snapshot.bin` (Linux x86_64): version 1, `ptr_size` 8, one arena, more than 50 pages, footer matches, some free map. Checks that `heapview.py` output equals the committed `mi-heapview-*.txt` output for seven commands, and that bad input exits 1 with `bad magic`. |
| `rust/mimalloc-pprof/tests/t18_heap_snapshot.rs` | cargo `t18_heap_snapshot` | `required-features = ["diagnostics"]` | For both flag settings: magic and version, flags at offset 16, the END footer with a page count above 0. An unwritable path gives `Err`. |
| `rust/mimalloc-pprof/tests/feature_contract.rs`; `lib.rs` test `heap_dump_and_snapshot_are_inert_when_compiled_out` | cargo | every configuration | A `diagnostics` build writes a non-empty file; without the feature the call returns `Err`. |
| `ci/check_crate_package.py` | release packaging | publish | `pub fn heap_snapshot_to_file` is still in the published archive. |

None of the three CTest tests has LABELS or `RUN_SERIAL`. The fixtures tie the C viewer to
the Python reader; per the test's docstring, regenerate them when the *viewer* changes on
purpose. A *writer* format change is caught by `test-snapshot-exit` and must bump the
version.

**Running locally.** `uv run ci/dev_linux.py c-test` runs the whole suite and configures
with `-DMI_DIAGNOSTICS=ON`, among others, because otherwise these tests are not registered.
For just these tests, configure with `-DMI_DIAGNOSTICS=ON`, build, and run
`ctest --test-dir build -R test-snapshot`, which matches all three. The Python side is
`python3 -m pytest ci/tests/test_heap_snapshot_example.py -q`; the Rust side is
`cargo test -p mimalloc-pprof --features diagnostics --test t18_heap_snapshot`, run from
`rust/`.

**CI gates.** These rows pass `-DMI_DIAGNOSTICS=ON` and therefore run the CTest tests
(`test-snapshot-walk` only where the static library is built, outside TSAN): in
`.github/workflows/c-unit.yml` the `release`, `debug-full`, `gated` and `musl` rows plus
`build-windows-native` (the hard `ctest (windows-latest)` gate), and the bundles of
`.github/workflows/windows-bundles.yml` (win-gnu and MSVC-ABI),
`.github/workflows/macos-bundles.yml` and `.github/workflows/asan.yml`. The minimal
`pprof-off` row compiles the stubs instead. `python-lint.yml` runs
`python3 -m pytest ci/tests -q`. `src/heap-snapshot.c` is on the `MI_STRICT_WARNINGS`
fork-source list ([ci-gates.md](ci-gates.md)).

## 11. Where to look

| File | Function / symbol | What it is |
|---|---|---|
| `src/heap-snapshot.c` | `mi_heap_snapshot`, `mi_heap_snapshot_gated`, `mi_heap_snapshot_inner` | registry lock, then the owner gate; header, arena, own-theap and heap passes, footer |
| `src/heap-snapshot.c` | `mi_snap_count_arenas`, `mi_snap_walk_subproc_heaps` | the header's `arena_count`; one sub-process's heaps |
| `src/heap-snapshot.c` | `mi_snap_emit_arena_header`, `mi_snap_emit_bitmap` | ARNA record and the three slice bitmaps |
| `src/heap-snapshot.c` | `mi_snap_walk_arena_pages`, `mi_snap_emit_page` | page-map walk of an arena; the 80-byte page record |
| `src/heap-snapshot.c` | `mi_snap_emit_page_freemap` | collect own page, then fast or windowed free-map build |
| `src/heap-snapshot.c` | `mi_snap_walk_own_theaps`, `mi_snap_walk_heap_os_pages` | the caller's non-arena pages; a heap's OS-backed abandoned pages |
| `src/heap-snapshot.c` | `mi_snap_put`, `mi_snap_flush` | 16 KiB stack buffer, short-write loop, sticky `err` |
| `src/heap-snapshot.c` | `_mi_heap_snapshot_on_exit`, `#else` stubs | exit-time path resolution; the compiled-out API |
| `src/init.c` | `mi_process_done_once` | calls the exit hook after `_mi_scavenger_stop()` |
| `src/page.c` | `_mi_page_free_collect_no_unpurge` | owner-only collect that leaves holes purged |
| `include/mimalloc.h` | `MI_SNAPSHOT_BLOCKS`, `mi_option_snapshot_on_exit` | public surface |
| `tools/mi-heapview.c` | `hv_parse`, `cmd_*`, `hv_core_open` | the C viewer; `peek`'s ELF/Mach-O segment map |
| `examples/heap-snapshot/mi_snapshot.py` | `parse`, `load` | executable format spec |
| `examples/heap-snapshot/heapview.py` | `main` | Python mirror of the viewer |
| `test/test-snapshot-reader.h` | `snap_parse`, `snap_read_file` | third, independent v1 reader |
| `test/test-snapshot-walk.c` | `run_subproc_row`, `run_hole_row` | several sub-processes; a NULL arena slot |
| `rust/mimalloc-pprof/src/lib.rs` | `heap_snapshot_to_file` | safe Rust wrapper |
