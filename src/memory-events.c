/* Opt-in global-heap allocation-change accounting and callbacks (issue #20).

   Independent of MI_PPROF. #414: opt-in at COMPILE time too -- `MI_MEMEVT` (CMake
   `-DMI_MEMEVT=ON`, cargo feature `memory-events`) must be 1 or the accounting, the
   callback table and the per-allocation hook sites all compile away and the public
   `mi_memory_*` API is the stub block near the end of this file. The hook *entry points*
   survive whenever `MI_MEMEVT || MI_DHAT`, because DHAT dispatches through them.
   Once compiled in, it is still gated by the runtime `memevt_state` flag below (env var
   MIMALLOC_MEMORY_EVENTS, or the mi_memory_tracking_set_enabled API).
   The `mi_unwrapped_*` family at the end is NOT part of any of this: it is a raw-OS
   helper for instrumentation callers and is real in every configuration.
   Structurally this module mirrors src/profile.c's
   patterns (mi_atomic_do_once-style lazy env read, snapshot-then-release callback
   dispatch, a thread-local reentrancy depth counter) but is a separate, independent
   feature: see include/mimalloc/memory-events.h for the full API contract.

   Like the profiler, this module's own bookkeeping never uses mi_malloc: the callback
   table and counters below are static storage (no dynamic allocation at all), so there
   is nothing here that could recursively enter the hooked allocation paths. The public
   mi_unwrapped_* family (backed directly by _mi_os_alloc/_mi_os_free) is provided as a
   stable API for *callers* (e.g. a memory-change callback) that need non-recursive
   scratch storage; this module does not need to consume its own API for that purpose. */
#include "mimalloc.h"
#include "mimalloc/internal.h"
#include "mimalloc/prim-tls.h"   // _mi_theap_default
#include "mimalloc/hooks-tld.h"  // _mi_hooks_tld_peek/_peek_or_local (#266)
#include <string.h>
#include <stdint.h>

// ---------------------------------------------------------------------------------------
// Activation state.
//
// State machine (single _Atomic(size_t), not a bool -- see profile.c's prof_enabled
// comment on MSVC's plain-C atomic wrapper only implementing uintptr_t/int64_t widths):
//   MEMEVT_UNINIT   (0): never resolved; `_mi_observers_armed` (below) still carries this
//                        module's UNRESOLVED bit, so the first allocation hook falls into
//                        the slow path and resolves it there.
//   MEMEVT_DISABLED (1): resolved off, by env or by explicit API call. Steady-state
//                        common case: once no compiled-in observer is on, the hot-path
//                        check (`_mi_observers_idle` in internal.h) is one relaxed load of
//                        `_mi_observers_armed` + compare, no lock, no callback-table touch.
//   MEMEVT_ENABLED  (2): resolved on, by env or by explicit API call.
//
// A shared mi_atomic_once_t (memevt_once) synchronizes the two ways this can first
// resolve -- the lazy env read at the first allocation hook, and an explicit
// mi_memory_tracking_set_enabled call, which may happen before any allocation at all.
// Whichever runs first "wins" the once (so the other is a no-op for the *env read*);
// but mi_memory_tracking_set_enabled always writes memevt_state directly afterwards too,
// so an explicit call remains authoritative even if it runs after the once already
// resolved via the lazy env path -- matching "tracking may also be enabled/disabled by
// API" as an override, not just a fallback.
// ---------------------------------------------------------------------------------------
#if MI_MEMEVT
#define MEMEVT_UNINIT    0
#define MEMEVT_DISABLED  1
#define MEMEVT_ENABLED   2

static _Atomic(size_t) memevt_state;
#endif

#if MI_MEMEVT || MI_DHAT
// #371 tier 2: the word the alloc/free fast path reads (see internal.h). Starts non-zero so
// the first hook still resolves the environment lazily, exactly as documented; each observer
// publishes its own bits into it and cannot clear the other's.
//
// Cache-line aligned on purpose. Every allocation and every free reads this word, so it must
// not share a line with anything written in steady state -- the accounting counters below are
// written on every event once tracking is on, and false sharing there would hand the enabled
// path the very cache-line ping-pong this issue is about.
mi_decl_cache_align _Atomic(size_t) _mi_observers_armed = MI_OBSERVERS_INITIAL;
#endif // MI_MEMEVT || MI_DHAT

#if MI_MEMEVT
// Publish this module's two bits. Order matters when arming: ON is set before UNRESOLVED is
// cleared, so the word is never transiently zero while tracking is on.
static void memevt_publish_armed(size_t state) {
  if (state == MEMEVT_ENABLED) {
    mi_atomic_or_acq_rel(&_mi_observers_armed, MI_OBSERVERS_MEMEVT_ON);
  }
  else {
    mi_atomic_and_acq_rel(&_mi_observers_armed, ~(size_t)MI_OBSERVERS_MEMEVT_ON);
  }
  if (state != MEMEVT_UNINIT) {
    mi_atomic_and_acq_rel(&_mi_observers_armed, ~(size_t)MI_OBSERVERS_MEMEVT_UNRESOLVED);
  }
}
static mi_atomic_once_t memevt_once = { MI_ATOMIC_VAR_INIT(0), MI_LOCK_INITIALIZER };

// Counters (see mi_memory_snapshot_t). Maintained only while MEMEVT_ENABLED; never
// reset on disable/re-enable (the running totals just stop advancing while disabled --
// this is the documented "partial accounting" caveat in memory-events.h).
static _Atomic(size_t) memevt_live_bytes;
static _Atomic(size_t) memevt_accum_bytes;
static _Atomic(size_t) memevt_live_count;
static _Atomic(size_t) memevt_accum_count;

// Callback table + its lock. The lock only ever guards a snapshot-copy of the table
// (mi_memory_set_callbacks writing it, or memevt_dispatch reading one entry out of it);
// it is never held while a user handler runs.
static mi_lock_t memevt_cb_lock = MI_LOCK_INITIALIZER;
static mi_memory_change_fun* memevt_handlers[MI_MEMORY_CHANGE_COUNT];
static void*                 memevt_args[MI_MEMORY_CHANGE_COUNT];
#endif // MI_MEMEVT

// Reentrancy / internal-op suppression (mirrors profile.c's prof_callback_depth).
// >0 means: skip accounting and skip dispatch entirely. Incremented by:
//   (a) memevt_dispatch, around invoking the user's handler -- so if the handler itself
//       calls mi_malloc/mi_free, that nested allocation is not itself accounted for or
//       dispatched (bounds recursion depth; see memory-events.h's callback contract).
//   (b) the moving-realloc paths -- mi_theap_realloc_zero_ex (alloc.c) and
//       mi_theap_realloc_zero_aligned_at (alloc-aligned.c) -- around their internal
//       allocate+mi_free pair, so those two calls don't leak an ALLOCATE/FREE pair to
//       consumers; the caller then explicitly calls _mi_memevt_on_resize once, after
//       suppression is lifted, to emit the single synthesized RESIZE.
//   (c) the guarded and over-aligned allocation paths (alloc.c, alloc-aligned.c), around
//       their inner over-allocation, before re-emitting one event for the caller's request;
//       and mi_dhat_dump (dhat.c), around its stdio.
// #266: this used to be `static mi_decl_thread int memevt_suppress_depth`; it now lives
// on `mi_tld_t::hooks` (see hooks-tld.h's file comment for why). The allocation-path
// callers run on an already-initialized thread (paired around inner mi_realloc/
// mi_malloc_aligned/mi_theap_malloc_guarded calls, never inside `_mi_meta_zalloc`'s own call
// chain), so a NULL peek is not expected there; mi_dhat_dump may run with no tld at all
// (it uses a peek-or-local hooks struct), and a NULL peek then leaves the depth untouched.
// Either way this must only ever PEEK, never
// force: forcing (mi_theap_get_default() -> mi_thread_init()) is unsafe not only mid-init
// but also mid *teardown* (mi_thread_theaps_done resets the default theap to the empty
// sentinel before freeing this thread's theaps specifically so nothing re-initializes it
// in that window; see its comment in init.c). No-op on NULL rather than crash either way.
// #414: these two stay REAL in every configuration -- they are a per-thread depth counter
// on state that exists unconditionally, they are off the mi_malloc/mi_free fast path
// (guarded-page, aligned and moving-realloc paths only), and keeping them real leaves the
// unconditional call sites in alloc.c/alloc-aligned.c/dhat.c untouched.
void _mi_memevt_suppress_begin(void) { mi_hooks_tld_t* const h = _mi_hooks_tld_peek(); if (h != NULL) h->memevt_suppress_depth++; }
void _mi_memevt_suppress_end(void)   { mi_hooks_tld_t* const h = _mi_hooks_tld_peek(); if (h != NULL) h->memevt_suppress_depth--; }

#if MI_MEMEVT
// #270: fork-safety. Child-side policy: CONTINUE. `memevt_cb_lock` only ever guards a
// snapshot-copy of the callback table (see the comment above its declaration) and is
// never held while a user handler runs. But `memevt_dispatch` takes it from inside the
// alloc/free hooks, which can run with a heap/arena lock held further up the stack, so
// like `prof_lock`/`dhat_lock` it must come after every allocator lock: fork.c's
// lock-order block puts it innermost, just before `out_buf_lock`.
// The registered handlers themselves are the embedder's own responsibility across
// fork (same as any other pthread_atfork-registered library) -- mimalloc does not know
// how to make an arbitrary user callback fork-safe. The lock and the env-var lazy-init
// guard (`memevt_once`, in case a thread was mid-resolve at fork time) are reset.
void _mi_memevt_fork_prepare(void) { mi_lock_acquire(&memevt_cb_lock); }
void _mi_memevt_fork_parent(void)  { mi_lock_release(&memevt_cb_lock); }
void _mi_memevt_fork_child(void)   { mi_lock_init(&memevt_cb_lock); _mi_atomic_once_fork_child_reset(&memevt_once); }

// ---------------------------------------------------------------------------------------
// Lazy activation.
// ---------------------------------------------------------------------------------------

static void memevt_resolve_env(void) {
  if (_mi_atomic_once_enter(&memevt_once)) {
    const bool enabled = mi_option_is_enabled(mi_option_memory_events);
    const size_t resolved = (size_t)(enabled ? MEMEVT_ENABLED : MEMEVT_DISABLED);
    mi_atomic_store_release(&memevt_state, resolved);
    memevt_publish_armed(resolved);
    _mi_atomic_once_release(&memevt_once);
  }
  // else: either a concurrent thread is mid-resolution (we blocked until it finished, in
  // which case memevt_state now holds its result) or an explicit API call already won
  // the once before any allocation occurred (memevt_state already holds that value).
}

bool mi_memory_tracking_set_enabled(bool enabled) mi_attr_noexcept {
  const size_t new_state = (size_t)(enabled ? MEMEVT_ENABLED : MEMEVT_DISABLED);
  if (_mi_atomic_once_enter(&memevt_once)) {
    // First-ever activation path, and it is this explicit call: resolve the once
    // without ever reading the environment, so a later first-allocation lazy read is
    // permanently skipped (memevt_resolve_env's `else` branch above).
    mi_atomic_store_release(&memevt_state, new_state);
    memevt_publish_armed(new_state);
    _mi_atomic_once_release(&memevt_once);
  }
  else {
    // Once already resolved (by a prior lazy env read or a prior API call): an explicit
    // call always overrides the cached flag, matching "tracking may also be enabled or
    // disabled by API" as an authoritative override, not merely a fallback default.
    mi_atomic_store_release(&memevt_state, new_state);
    memevt_publish_armed(new_state);
  }
  return true;
}

bool mi_memory_tracking_is_enabled(void) mi_attr_noexcept {
  return (mi_atomic_load_relaxed(&memevt_state) == MEMEVT_ENABLED);
}

// ---------------------------------------------------------------------------------------
// Callback table.
// ---------------------------------------------------------------------------------------

bool mi_memory_set_callbacks(const mi_memory_callbacks_t* callbacks) mi_attr_noexcept {
  mi_lock_acquire(&memevt_cb_lock);
  for (int i = 0; i < MI_MEMORY_CHANGE_COUNT; i++) {
    memevt_handlers[i] = (callbacks != NULL ? callbacks->handlers[i] : NULL);
    memevt_args[i]     = (callbacks != NULL ? callbacks->args[i]     : NULL);
  }
  mi_lock_release(&memevt_cb_lock);
  return true;
}

bool mi_memory_snapshot(mi_memory_snapshot_t* out) mi_attr_noexcept {
  if (out == NULL) return false;
  if (out->size != sizeof(mi_memory_snapshot_t) || out->version != MI_MEMORY_SNAPSHOT_VERSION) return false;
  out->live_bytes  = (uint64_t)mi_atomic_load_relaxed(&memevt_live_bytes);
  out->accum_bytes = (uint64_t)mi_atomic_load_relaxed(&memevt_accum_bytes);
  out->live_count  = (uint64_t)mi_atomic_load_relaxed(&memevt_live_count);
  out->accum_count = (uint64_t)mi_atomic_load_relaxed(&memevt_accum_count);
  return true;
}

// ---------------------------------------------------------------------------------------
// Dispatch. Called only once tracking is confirmed MEMEVT_ENABLED. Updates counters
// (total_bytes-affecting update happens before the callback, per spec), then snapshots
// the relevant handler/arg pair under memevt_cb_lock, releases the lock, and only then
// invokes the handler -- so the handler never runs under memevt_cb_lock. The hook sites
// take no allocator lock of their own (alloc.c runs after the block is popped and
// zeroed; both free hooks in free.c run BEFORE the block is pushed on `local_free` /
// `xthread_free`, deliberately, so the address cannot be reused while DHAT still holds
// its record). A caller further up the stack can still hold one for internal events --
// see the callback contract in memory-events.h.
// ---------------------------------------------------------------------------------------

// `hooks` is the caller's already-peeked, known-non-NULL `mi_hooks_tld_t*` (every call
// site below obtained it before calling here; see the hook entry points).
static void memevt_dispatch(mi_hooks_tld_t* hooks, mi_memory_change_kind_t kind, int64_t delta_bytes, uint64_t request_size) {
  if (hooks->memevt_suppress_depth > 0) return;

  size_t live_bytes_after;
  if (delta_bytes >= 0) {
    live_bytes_after = mi_atomic_add_relaxed(&memevt_live_bytes, (size_t)delta_bytes) + (size_t)delta_bytes;
  }
  else {
    const size_t magnitude = (size_t)(-delta_bytes);
    live_bytes_after = mi_atomic_sub_relaxed(&memevt_live_bytes, magnitude) - magnitude;
  }

  switch (kind) {
    case MI_MEMORY_ALLOCATE:
      mi_atomic_increment_relaxed(&memevt_live_count);
      mi_atomic_increment_relaxed(&memevt_accum_count);
      if (delta_bytes > 0) mi_atomic_add_relaxed(&memevt_accum_bytes, (size_t)delta_bytes);
      break;
    case MI_MEMORY_FREE:
      mi_atomic_decrement_relaxed(&memevt_live_count);
      break;
    case MI_MEMORY_RESIZE:
      if (delta_bytes > 0) mi_atomic_add_relaxed(&memevt_accum_bytes, (size_t)delta_bytes);
      break;
    default:
      break;
  }

  mi_lock_acquire(&memevt_cb_lock);
  mi_memory_change_fun* handler = memevt_handlers[kind];
  void* handler_arg = memevt_args[kind];
  mi_lock_release(&memevt_cb_lock);
  if (handler == NULL) return;

  mi_memory_change_t change;
  change.kind = kind;
  change.total_bytes = (uint64_t)live_bytes_after;
  change.delta_bytes = delta_bytes;
  change.request_size = request_size;

  hooks->memevt_suppress_depth++;
  handler(&change, handler_arg);
  hooks->memevt_suppress_depth--;
}
#endif // MI_MEMEVT

#if MI_MEMEVT || MI_DHAT
// ---------------------------------------------------------------------------------------
// Hook entry points: the `_slow` bodies of the `static inline` `_mi_memevt_on_*` wrappers
// in internal.h (#371). The wrapper does the disabled-hot-path test -- one relaxed load of
// `_mi_observers_armed` compared with zero (`_mi_observers_idle`) -- and calls a body
// below only when some compiled-in observer is on or still unresolved. Each body starts
// with the hooks-tld peek, then the suppression depth; `memevt_state` is read only after
// that (MEMEVT_UNINIT is resolved once, in the alloc body). No accounting atomic and no
// callback-table lock/lookup occur unless the state is MEMEVT_ENABLED.
// ---------------------------------------------------------------------------------------

/* DHAT and the public callback table are independent observers.  The detailed
   identity context is prepared before dispatch, while the allocator still has the
   original pointers available; it is committed only after the user callback returns.
   The shared suppression depth excludes callback-internal and moving-realloc internals
   from both observers. */
void _mi_memevt_on_alloc_slow(mi_page_t* page, void* p, size_t request_size) {
  #if !MI_DHAT
  MI_UNUSED(p);  // only DHAT consumes the block address (#373)
  #endif
  // #266: must be the very first thing touched -- see hooks-tld.h's file comment. A
  // thread mid-init (inside `_mi_thread_init_with_heap` -> `_mi_meta_zalloc`, allocating
  // its OWN tld/theap) reaches this hook too; peeking (rather than touching any TLS
  // state directly) lets us bail out before doing anything else. Such a call is always
  // for a meta allocation anyway -- the `_mi_meta_is_meta_page` check below would have
  // excluded it regardless, but that check itself must not run first (it does not touch
  // TLS, but ordering it before the peek would defeat the point: bail before ANY other
  // per-thread work).
  mi_hooks_tld_t* const hooks = _mi_hooks_tld_peek();
  if (hooks == NULL) return;
  if (hooks->memevt_suppress_depth > 0) return;
  // #266: never report allocator-internal metadata (mi_tld_t / mi_theap_t, allocated via
  // _mi_meta_zalloc onto subproc->theap_meta) as a user allocation. This is the sole
  // entry point DHAT's begin_alloc is reached through, so this one check excludes both
  // observers; the matching _mi_memevt_on_free check below keeps ALLOCATE/FREE balanced
  // (memevt_live_bytes/memevt_live_count are running deltas, so an unmatched free would
  // under/overflow them) -- DHAT's own free path needs no matching check since it looks
  // up the pointer in its own record table and no-ops when the alloc was never recorded.
  if (_mi_meta_is_meta_page_safe(page)) return;  // adapted for issue #271: was _mi_meta_is_meta_page(mi_page_subproc(page), page)
  #if MI_DHAT
  _mi_dhat_begin_alloc(page, p, request_size);
  #endif
  #if MI_MEMEVT
  size_t state = mi_atomic_load_relaxed(&memevt_state);
  if (state == MEMEVT_UNINIT) { memevt_resolve_env(); state = mi_atomic_load_relaxed(&memevt_state); }
  if (state == MEMEVT_ENABLED) {
    const size_t usable = mi_page_usable_block_size(page);
    memevt_dispatch(hooks, MI_MEMORY_ALLOCATE, (int64_t)usable, (uint64_t)request_size);
  }
  #else
  MI_UNUSED(request_size);  // #414: memory-events compiled out; only DHAT observes here
  #endif
  #if MI_DHAT
  _mi_dhat_finish_event();
  #endif
}

// #266: unlike _mi_memevt_on_alloc above, the free/resize hooks are never reachable
// from inside `_mi_meta_zalloc`'s own call chain (meta allocations only ever allocate,
// never free or resize), so a NULL peek here does NOT mean "meta allocation, drop it" --
// it means a thread with no tld of its own is legitimately freeing (or resizing)
// something, e.g. `test_free_from_foreign_thread` (test-memory-events.c): a thread whose
// very first ever mimalloc call is a cross-thread mi_free must still be accounted for.
// But forcing thread init here would be just as unsafe as in the alloc path -- unsafe
// not for the mid-init reason (frees can't happen there) but because it is equally
// reachable while THIS thread is mid *teardown* (mi_thread_theaps_done resets the
// default theap to the empty sentinel before freeing this thread's own theaps precisely
// so nothing re-initializes it in that window) or after teardown already completed (see
// free.c's "free'd after thread_done" comment). So: peek, and fall back to a local,
// per-call scratch `mi_hooks_tld_t` instead of forcing -- see hooks-tld.h.
void _mi_memevt_on_free_slow(mi_page_t* page, void* p) {
  #if !MI_DHAT
  MI_UNUSED(p);  // only DHAT consumes the block address (#373)
  #endif
  mi_hooks_tld_t local_hooks;
  mi_hooks_tld_t* const hooks = _mi_hooks_tld_peek_or_local(&local_hooks);
  if (hooks->memevt_suppress_depth > 0) return;
  // issue #271 (Bun parity P6, "keep our profiler hooks consistent -- a page unpublished
  // from its heap must not be visited with a dangling heap pointer"): this can run for a
  // cross-thread free (mi_free_block_mt) concurrently with a mi_heap_delete/mi_heap_destroy
  // of `page`'s heap on another thread. The block being freed keeps `page` itself alive
  // (see free.c's _mi_page_ptr_unalign comment), but NOT `page->heap` -- reproduced as a
  // SIGSEGV (and, in MI_DEBUG builds, a read of MI_DEBUG_FREED-poisoned memory) reading
  // page->heap->subproc here. _mi_meta_is_meta_page_safe (internal.h) answers the same
  // question from page->memid's arena instead, which never touches page->heap.
  // #266: symmetric with the _mi_memevt_on_alloc check above -- see its comment.
  if (_mi_meta_is_meta_page_safe(page)) return;
  #if MI_DHAT
  _mi_dhat_begin_free(p);
  #endif
  #if MI_MEMEVT
  const size_t state = mi_atomic_load_relaxed(&memevt_state);
  if (state == MEMEVT_ENABLED) {
    const size_t usable = mi_page_usable_block_size(page);
    memevt_dispatch(hooks, MI_MEMORY_FREE, -(int64_t)usable, 0);
  }
  #endif
  #if MI_DHAT
  _mi_dhat_finish_event();
  #endif
}

void _mi_memevt_on_realloc_in_place_slow(mi_page_t* page, void* p, size_t request_size) {
  #if !MI_DHAT
  MI_UNUSED(p);  // only DHAT consumes the block address (#373)
  #endif
  // #266: see _mi_memevt_on_free above.
  mi_hooks_tld_t local_hooks;
  mi_hooks_tld_t* const hooks = _mi_hooks_tld_peek_or_local(&local_hooks);
  if (hooks->memevt_suppress_depth > 0) return;
  #if MI_DHAT
  _mi_dhat_begin_resize(p, p, request_size);
  #endif
  #if MI_MEMEVT
  const size_t state = mi_atomic_load_relaxed(&memevt_state);
  if (state == MEMEVT_ENABLED) {
    // Same page => same block-size class => usable size is identical before and after.
    memevt_dispatch(hooks, MI_MEMORY_RESIZE, 0, (uint64_t)request_size);
  }
  #else
  MI_UNUSED(request_size);  // #414: memory-events compiled out; only DHAT observes here
  #endif
  // Same page => same block-size class => usable size is identical before and after.
  MI_UNUSED(page);
  #if MI_DHAT
  _mi_dhat_finish_event();
  #endif
}

void _mi_memevt_on_resize_slow(void* oldp, void* newp, size_t usable_pre, size_t usable_post, size_t request_size) {
  #if !MI_DHAT
  MI_UNUSED(oldp); MI_UNUSED(newp);  // only DHAT consumes the block addresses (#373)
  #endif
  // #266: see _mi_memevt_on_free above.
  mi_hooks_tld_t local_hooks;
  mi_hooks_tld_t* const hooks = _mi_hooks_tld_peek_or_local(&local_hooks);
  if (hooks->memevt_suppress_depth > 0) return;
  #if MI_DHAT
  _mi_dhat_begin_resize(oldp, newp, request_size);
  #endif
  #if MI_MEMEVT
  const size_t state = mi_atomic_load_relaxed(&memevt_state);
  if (state == MEMEVT_ENABLED) {
    const int64_t delta = (int64_t)usable_post - (int64_t)usable_pre;
    memevt_dispatch(hooks, MI_MEMORY_RESIZE, delta, (uint64_t)request_size);
  }
  #else
  // #414: memory-events compiled out; only DHAT observes here.
  MI_UNUSED(usable_pre); MI_UNUSED(usable_post); MI_UNUSED(request_size);
  #endif
  #if MI_DHAT
  _mi_dhat_finish_event();
  #endif
}
#endif // MI_MEMEVT || MI_DHAT

#if MI_MEMEVT
// ---------------------------------------------------------------------------------------
// Best-effort live-allocation visitor.
//
// NOT built on the public mi_heap_visit_blocks(): that call walks the owning mi_heap_t's
// ARENA-registered page bitmap (heap->arena_pages[]) plus heap->os_abandoned_pages (see
// _mi_heap_visit_blocks in src/arena.c). Both of those are populated only for (a)
// arena-backed pages (memid.memkind == MI_MEM_ARENA) and (b) pages abandoned by a thread
// that has since exited. A page that a still-live theap owns but that was allocated
// directly from the OS (memid.memkind == MI_MEM_OS -- the routine fallback whenever arena
// space/reservation isn't available, e.g. before this process's first arena reservation
// has happened) is in neither structure, so mi_heap_visit_blocks silently skips it. That
// was the actual bug in the previous version of this function: it correctly found this
// thread's one theap (tld->theaps via tnext) and the theap's owning heap, but then handed
// off to mi_heap_visit_blocks, which can only ever see arena-backed pages -- so on a build
// where nothing ever got arena-allocated (confirmed via instrumentation: MinGW static
// EXEs never flip `_mi_preloading()` false because the MSVC-only `#pragma data_seg`/
// `#pragma const_seg` TLS-callback trick in src/prim/windows/prim.c silently does nothing
// under GCC, so `mi_arenas_try_alloc`'s reserve-a-new-arena path is permanently gated off
// by the `if (_mi_preloading()) return NULL;` check in that function -- see src/arena.c),
// every single test page was MI_MEM_OS and mi_heap_visit_blocks visited zero blocks.
//
// The theap's own per-bin page queues (mi_theap_t::pages[MI_BIN_COUNT], the same lists
// _mi_malloc_generic/page.c/theap.c use) are populated for every page the theap owns
// regardless of memkind, so walking those directly (via the already-internal
// _mi_theap_area_visit_blocks, the same per-page block walker mi_heap_visit_blocks itself
// bottoms out on) is both simpler and correct for the "still-live, this-thread" scope this
// API documents, independent of whether arena allocation ever kicked in.
//
// Scope note (deviation from a literal "global" reading -- see final report): this walks
// every theap on the *calling thread* (mi_tld_t::theaps, the same list the runtime uses to
// abandon all of a thread's theaps on thread exit). mimalloc keeps no process-wide registry
// of every thread's theaps, and building one would mean new cross-thread bookkeeping
// infrastructure -- out of scope here. The API is already documented as
// best-effort/non-consistent, and single-threaded or per-thread-tracked callers (the
// common case for this kind of diagnostic) get full coverage; multi-threaded callers get
// their own thread's live allocations only.
// ---------------------------------------------------------------------------------------

typedef struct memevt_visit_ctx_s {
  mi_memory_allocation_visit_fun* visitor;
  void* arg;
} memevt_visit_ctx_t;

static bool mi_cdecl memevt_visit_adapter(const mi_heap_t* heap, const mi_heap_area_t* area, void* block, size_t block_size, void* arg) {
  MI_UNUSED(heap); MI_UNUSED(area);
  if (block == NULL) return true; // area-only callback; visit_blocks=true below still yields one of these per area too.
  memevt_visit_ctx_t* ctx = (memevt_visit_ctx_t*)arg;
  return ctx->visitor(block, block_size, ctx->arg);
}

static bool mi_memory_visit_live_allocations_inner(mi_theap_t* theap, mi_memory_allocation_visit_fun* visitor, void* arg);

// #366: owner-gate site -- `_mi_theap_area_visit_blocks` folds each page's thread frees, an
// owner-private write on this thread's own theaps; be RUNNING for the walk (see theap.c
// `mi_theap_visit_blocks`). One enter, exactly one leave.
bool mi_memory_visit_live_allocations(mi_memory_allocation_visit_fun* visitor, void* arg) mi_attr_noexcept {
  if (visitor == NULL) return false;
  mi_theap_t* theap = _mi_theap_default();
  if (!mi_theap_is_initialized(theap)) return true; // nothing to visit yet on this thread.
  MI_GATE_ENTER(theap);
  const bool ok = mi_memory_visit_live_allocations_inner(theap, visitor, arg);
  MI_GATE_LEAVE(theap->tld);
  return ok;
}

static bool mi_memory_visit_live_allocations_inner(mi_theap_t* theap, mi_memory_allocation_visit_fun* visitor, void* arg) {
  if (theap->tld->hooks.memevt_suppress_depth > 0) return false; // do not reenter while a callback/internal-op is in flight on this thread.
  memevt_visit_ctx_t ctx = { visitor, arg };
  // Walk every theap on this thread (tld->theaps, via tnext), and for each, walk its own
  // page queues directly -- see the comment block above for why mi_heap_visit_blocks is
  // not used here.
  for (mi_theap_t* t = theap->tld->theaps; t != NULL; t = t->tnext) {
    for (size_t bin = 0; bin < MI_BIN_COUNT; bin++) {
      mi_page_queue_t* pq = &t->pages[bin];
      for (mi_page_t* page = pq->first; page != NULL; page = page->next) {
        mi_heap_area_t area;
        _mi_heap_area_init(&area, page);
        if (!_mi_theap_area_visit_blocks(&area, page, &memevt_visit_adapter, &ctx)) return true; // early stop, matching mi_heap_visit_blocks' contract
      }
    }
  }
  return true;
}

#else  // !MI_MEMEVT

// #414: memory-events compiled out. The public API stays present in every configuration --
// same contract as src/profile.c's `#else` block -- so a downstream crate keeps linking and
// `ci/check_rust_surface.py` / the README API table stay valid. Everything reports "off".
bool mi_memory_tracking_set_enabled(bool enabled) mi_attr_noexcept { MI_UNUSED(enabled); return false; }
bool mi_memory_tracking_is_enabled(void) mi_attr_noexcept { return false; }
bool mi_memory_set_callbacks(const mi_memory_callbacks_t* callbacks) mi_attr_noexcept { MI_UNUSED(callbacks); return false; }
bool mi_memory_snapshot(mi_memory_snapshot_t* out) mi_attr_noexcept { MI_UNUSED(out); return false; }
bool mi_memory_visit_live_allocations(mi_memory_allocation_visit_fun* visitor, void* arg) mi_attr_noexcept { MI_UNUSED(visitor); MI_UNUSED(arg); return false; }
// #270: no `memevt_cb_lock` exists when memory-events is off -- nothing to quiesce.
void _mi_memevt_fork_prepare(void) { }
void _mi_memevt_fork_parent(void)  { }
void _mi_memevt_fork_child(void)   { }

#endif // MI_MEMEVT

// ---------------------------------------------------------------------------------------
// Stable public "unwrapped" instrumentation allocation path: backed directly by
// _mi_os_alloc/_mi_os_free (never mi_malloc), page granular, with a small prepended
// header carrying the mi_memid_t provenance token _mi_os_free requires. This mirrors
// _mi_prof_arena_alloc's chunk-header shape in profile.c, but each allocation here owns
// its own OS mapping (paired release, not an arena) since these are meant to be
// individually freed/resized by instrumentation callers, not bump-allocated bookkeeping.
// ---------------------------------------------------------------------------------------

#define MI_UNWRAPPED_MAGIC ((uint32_t)0x6D697577) /* "miuw" */

typedef struct mi_unwrapped_header_s {
  void*      base;          // OS allocation base, for _mi_os_free
  size_t     total_size;    // OS allocation total size, for _mi_os_free
  size_t     payload_size;  // current payload size, for realloc's memcpy
  mi_memid_t memid;         // provenance token, for _mi_os_free
  uint32_t   magic;
} mi_unwrapped_header_t;

static size_t memevt_align_up(size_t sz, size_t alignment) {
  return (sz + (alignment - 1)) & ~(alignment - 1);
}

void* mi_unwrapped_malloc(size_t size, size_t alignment) mi_attr_noexcept {
  // Ensure THIS thread is initialized before touching the OS layer directly -- not just the
  // process. A prior fix here called mi_process_init(), reasoning that every other path into
  // _mi_os_alloc_aligned goes through process init first so the process-global
  // mi_os_mem_config_t is never read torn. That part is true but incomplete: mi_process_init()
  // only runs thread init for the *one* thread that wins its internal mi_atomic_do_once race
  // (see mi_process_init_once in init.c); every other thread that calls mi_process_init()
  // concurrently just blocks on the once-guard and returns *without* its own thread init. On
  // the v2 line that mattered because _mi_os_alloc_aligned's callees read per-thread state:
  // mi_os_prim_alloc_at -> _mi_os_get_aligned_hint drew its address-hint randomness from the
  // thread's default heap in release builds (compiled out under MI_DEBUG>0, which is exactly
  // why this never reproduced in a debug build), and a thread that never initialized still
  // pointed at the `const`, read-only-mapped empty sentinel. The random generator mutates the
  // state it is given, so that was a write into read-only memory: an immediate,
  // near-deterministic SIGSEGV inside chacha_block, reproduced (~100% of runs) via gdb:
  //   mi_unwrapped_malloc -> _mi_os_alloc_aligned -> mi_os_prim_alloc_at -> _mi_prim_alloc ->
  //   _mi_os_get_aligned_hint -> _mi_random_next -> chacha_block (SIGSEGV, all GP regs zeroed)
  // On v3 the names are `_mi_theap_default()`, `_mi_theap_random_next` and `_mi_theap_empty`,
  // and `_mi_os_get_aligned_hint` itself now returns no hint when the default theap is not
  // initialized (see the issue #1267 note there), so this call is belt-and-braces on that path; it
  // still guarantees every later per-thread read here sees a real theap.
  // mi_thread_init() is the right call, not mi_process_init(): `_mi_thread_init_with_heap`
  // calls mi_process_init() itself first (cheap once the once-guard has resolved) and then,
  // for *every* calling thread, initializes its default theap -- an already-initialized
  // check-and-return once the thread has one.
  mi_thread_init();
  if (alignment == 0) alignment = sizeof(void*);
  if ((alignment & (alignment - 1)) != 0) return NULL; // must be a power of two
  const size_t hdr_reserved = memevt_align_up(sizeof(mi_unwrapped_header_t), alignment);
  if (size > SIZE_MAX - hdr_reserved) return NULL; // overflow guard
  const size_t total = hdr_reserved + size;
  mi_memid_t memid;
  uint8_t* base = (uint8_t*)_mi_os_alloc_aligned(_mi_subproc_main(), total, alignment, true /* commit */, false /* allow_large */, &memid);
  if (base == NULL) return NULL;
  uint8_t* user = base + hdr_reserved;
  mi_unwrapped_header_t* hdr = (mi_unwrapped_header_t*)(user - sizeof(mi_unwrapped_header_t));
  hdr->base = base;
  hdr->total_size = total;
  hdr->payload_size = size;
  hdr->memid = memid;
  hdr->magic = MI_UNWRAPPED_MAGIC;
  return user;
}

static mi_unwrapped_header_t* mi_unwrapped_header_of(void* p, const char* msg) {
  mi_unwrapped_header_t* hdr = (mi_unwrapped_header_t*)((uint8_t*)p - sizeof(mi_unwrapped_header_t));
  if (hdr->magic != MI_UNWRAPPED_MAGIC) {
    _mi_error_message(EINVAL, "%s: pointer %p was not returned by mi_unwrapped_malloc/realloc\n", msg, p);
    return NULL;
  }
  return hdr;
}

void mi_unwrapped_free(void* p) mi_attr_noexcept {
  if (p == NULL) return;
  mi_unwrapped_header_t* hdr = mi_unwrapped_header_of(p, "mi_unwrapped_free");
  if (hdr == NULL) return;
  _mi_os_free(_mi_subproc_main(), hdr->base, hdr->total_size, hdr->memid);
}

void* mi_unwrapped_realloc(void* p, size_t new_size, size_t alignment) mi_attr_noexcept {
  if (p == NULL) return mi_unwrapped_malloc(new_size, alignment);
  if (new_size == 0) { mi_unwrapped_free(p); return NULL; }
  mi_unwrapped_header_t* hdr = mi_unwrapped_header_of(p, "mi_unwrapped_realloc");
  if (hdr == NULL) return NULL;
  void* newp = mi_unwrapped_malloc(new_size, alignment);
  if (newp == NULL) return NULL;
  const size_t copy = (hdr->payload_size < new_size ? hdr->payload_size : new_size);
  _mi_memcpy(newp, p, copy);
  mi_unwrapped_free(p);
  return newp;
}
