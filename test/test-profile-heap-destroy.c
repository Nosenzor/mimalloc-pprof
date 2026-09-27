/* test-profile-heap-destroy.c -- sampled blocks that die with their heap.

   `mi_heap_destroy` releases every page of a heap at once, with no per-block free, so the
   profiler's free hooks never see the heap's live blocks go. Their sample records must still
   leave with the pages. Before the fix they stayed on the profiler's record list:
   `live_samples`, `live_bytes` and the stacks' `curobjs` stayed inflated, and the next
   `mi_prof_stop` wrote `page->metadata`/`has_metadata` into pages that had already gone back
   to the arena or the OS, which is a use-after-free. A debug build trips
   `mi_arenas_page_free_ex`'s `!page->has_metadata` assertion at the destroy itself.

   Every scenario samples EVERY allocation (interval 1, so no seed can make it vacuous),
   keeps control blocks alive in the default heap, and compares the counters with the values
   they had before the scenario's heap existed:

   1. destroy: blocks on every page kind (see `block_kinds`) in a `mi_heap_new` heap, then
      `mi_heap_destroy`. Every counter returns to its baseline and the control blocks stay
      counted.
   2. delete: the same with `mi_heap_delete`, whose pages MOVE to the main heap with their
      blocks still live. The records must survive the delete and go when the blocks are
      freed, which guards against a fix that forgets too much.
   3. visit: the destroy runs inside a `mi_prof_visit` callback, which already holds the
      profiler's lock. The forget must take the lock-owner path rather than deadlock.
   4. subproc: a thread in a new sub-process leaves blocks live in the sub-process's main
      heap and in a heap of its own, then `mi_subproc_destroy` releases the sub-process's
      arenas. That path goes through `_mi_heap_force_destroy` too, main heap included.

   Each scenario ends in `mi_prof_stop`, the call that wrote into the freed pages. Before the
   fix that write faulted outright for the OS page of scenario 1 and for the sub-process's
   arenas of scenario 4, both of which the destroy unmaps.
*/
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "mimalloc/profile.h"

#ifdef _WIN32
#include <windows.h>
typedef HANDLE thread_t;
typedef DWORD (WINAPI *thread_fun_t)(void*);
#define THREAD_RET DWORD WINAPI
#define THREAD_OK 0
static void thread_start(thread_t* thread, thread_fun_t fn, void* arg) {
  *thread = CreateThread(NULL, 0, fn, arg, 0, NULL);
  assert(*thread != NULL);
}
static void thread_join(thread_t thread) {
  assert(WaitForSingleObject(thread, INFINITE) == WAIT_OBJECT_0);
  CloseHandle(thread);
}
#else
#include <pthread.h>
typedef pthread_t thread_t;
typedef void* (*thread_fun_t)(void*);
#define THREAD_RET void*
#define THREAD_OK NULL
static void thread_start(thread_t* thread, thread_fun_t fn, void* arg) {
  assert(pthread_create(thread, NULL, fn, arg) == 0);
}
static void thread_join(thread_t thread) {
  assert(pthread_join(thread, NULL) == 0);
}
#endif

// Every page kind a heap can hold: small, medium and large arena pages; a singleton arena
// page above MI_ARENA_MAX_CHUNK_OBJ_SIZE (32 MiB) that spans several chunks; and a page
// straight from the OS, which an alignment above MI_PAGE_MAX_OVERALLOC_ALIGN (64 KiB)
// forces, and which destroying the heap unmaps. One block of the 40 MiB kind is enough, and
// it keeps the scenarios' footprint small on platforms that commit eagerly.
typedef struct block_kind_s { size_t size; size_t alignment; size_t count; } block_kind_t;
#define OS_PAGE_ALIGNMENT ((size_t)128 * 1024)
static const block_kind_t block_kinds[] = {
  { 16, 0, 4 }, { 1000, 0, 4 }, { 20000, 0, 4 }, { 200000, 0, 4 },
  { (size_t)40 * 1024 * 1024, 0, 1 },
  { 1000, OS_PAGE_ALIGNMENT, 2 },
};
#define BLOCK_KINDS   (sizeof(block_kinds) / sizeof(block_kinds[0]))
#define HEAP_BLOCKS   19   // the sum of block_kinds[].count, checked by fill_heap
#define CONTROL_BLOCKS 8
#define CONTROL_SIZE  64

typedef struct counts_s {
  size_t samples, bytes;             // mi_prof_stats_t live_samples / live_bytes
  size_t stack_objs, stack_bytes;    // the stacks' curobjs / curbytes, summed by mi_prof_visit
  size_t accum_samples, accum_bytes; // cumulative (accum mode): a destroy must not move them
  size_t stacks;                     // unique_stacks
  bool accum;
} counts_t;

static bool sum_visitor(const mi_prof_sample_info_t* info, void* arg) {
  counts_t* c = (counts_t*)arg;
  c->stack_objs += info->live_objects; c->stack_bytes += info->live_bytes;
  return true;
}

static counts_t counts_now(void) {
  // A remote free only drops its record when the owner collects (profiler-internals §4.6).
  mi_collect(true);
  mi_prof_stats_t_decl(stats);
  assert(mi_prof_stats_get(&stats));
  assert(stats.enabled);
  counts_t c;
  memset(&c, 0, sizeof(c));
  c.samples = stats.live_samples; c.bytes = stats.live_bytes;
  c.accum_samples = stats.accum_samples; c.accum_bytes = stats.accum_bytes;
  c.stacks = stats.unique_stacks; c.accum = stats.accum;
  assert(mi_prof_visit(sum_visitor, &c));
  return c;
}

static void print_counts(const char* label, counts_t c) {
  fprintf(stderr, "  %-28s live_samples=%zu live_bytes=%zu stack_objs=%zu stack_bytes=%zu unique_stacks=%zu accum_samples=%zu\n",
          label, c.samples, c.bytes, c.stack_objs, c.stack_bytes, c.stacks, c.accum_samples);
}

// Live counters return to `before`. Cumulative ones count allocations, not frees, so a
// destroy must leave them where they were once the heap was full (`during`).
static void expect_baseline(const char* scenario, counts_t got, counts_t before, counts_t during) {
  bool ok = (got.samples == before.samples && got.bytes == before.bytes &&
             got.stack_objs == before.stack_objs && got.stack_bytes == before.stack_bytes &&
             got.accum_samples == during.accum_samples && got.accum_bytes == during.accum_bytes);
  // Accum mode keeps a stack entry until mi_prof_reset, so only then may the count differ.
  if (!got.accum && got.stacks != before.stacks) ok = false;
  if (!ok) {
    fprintf(stderr, "%s: the profiler counters did not return to their pre-heap values\n", scenario);
    print_counts("before the heap existed:", before);
    print_counts("with the heap full:", during);
    print_counts("after it was released:", got);
  }
  assert(ok);
}

// The live control blocks in the default heap: they must never be forgotten.
static void* control[CONTROL_BLOCKS];
static void control_alloc(void) {
  for (size_t i = 0; i < CONTROL_BLOCKS; i++) { control[i] = mi_malloc(CONTROL_SIZE); assert(control[i] != NULL); }
}
static void control_free(void) {
  for (size_t i = 0; i < CONTROL_BLOCKS; i++) { mi_free(control[i]); control[i] = NULL; }
}

static void fill_heap(mi_heap_t* heap, void** blocks) {
  size_t n = 0;
  for (size_t k = 0; k < BLOCK_KINDS; k++) {
    for (size_t i = 0; i < block_kinds[k].count; i++) {
      void* p = (block_kinds[k].alignment == 0 ? mi_heap_malloc(heap, block_kinds[k].size)
                                                : mi_heap_malloc_aligned(heap, block_kinds[k].size, block_kinds[k].alignment));
      assert(p != NULL);
      memset(p, 0xA5, 16);
      if (blocks != NULL) blocks[n] = p;
      n++;
    }
  }
  assert(n == HEAP_BLOCKS);
}

static size_t heap_bytes(void) {
  size_t total = 0;
  for (size_t k = 0; k < BLOCK_KINDS; k++) total += block_kinds[k].count * block_kinds[k].size;
  return total;
}

static void start_profiler(void) {
  assert(mi_prof_start_seeded(1, 0x5eedULL));  // interval 1: every allocation is sampled
  control_alloc();
}

static void stop_profiler(counts_t baseline) {
  // The control blocks are still counted, and freeing them leaves nothing behind. Checked
  // before the free so a fix that swept the wrong heap cannot pass.
  counts_t c = counts_now();
  assert(c.samples == baseline.samples && c.samples >= CONTROL_BLOCKS);
  control_free();
  c = counts_now();
  assert(c.samples == baseline.samples - CONTROL_BLOCKS);
  // Before the fix this wrote into every destroyed page that still carried a record.
  mi_prof_stop();
  assert(!mi_prof_is_enabled());
}

static void scenario_destroy(void) {
  start_profiler();
  const counts_t before = counts_now();
  mi_heap_t* heap = mi_heap_new();
  assert(heap != NULL);
  fill_heap(heap, NULL);
  const counts_t during = counts_now();
  assert(during.samples >= before.samples + HEAP_BLOCKS);
  assert(during.bytes >= before.bytes + heap_bytes());
  mi_heap_destroy(heap);
  expect_baseline("mi_heap_destroy", counts_now(), before, during);
  stop_profiler(before);
}

static void scenario_delete(void) {
  start_profiler();
  const counts_t before = counts_now();
  void* blocks[HEAP_BLOCKS];
  mi_heap_t* heap = mi_heap_new();
  assert(heap != NULL);
  fill_heap(heap, blocks);
  const counts_t during = counts_now();
  assert(during.samples >= before.samples + HEAP_BLOCKS);
  mi_heap_delete(heap);  // the blocks live on in the main heap, and so must their records
  const counts_t moved = counts_now();
  assert(moved.samples >= before.samples + HEAP_BLOCKS);
  assert(moved.bytes >= before.bytes + heap_bytes());
  for (size_t i = 0; i < HEAP_BLOCKS; i++) mi_free(blocks[i]);
  expect_baseline("mi_heap_delete + mi_free", counts_now(), before, during);
  stop_profiler(before);
}

typedef struct subproc_worker_arg_s {
  mi_subproc_id_t subproc;
  bool joined;
} subproc_worker_arg_t;

static THREAD_RET subproc_worker(void* arg) {
  subproc_worker_arg_t* const worker = (subproc_worker_arg_t*)arg;
  mi_subproc_add_current_thread(worker->subproc);
  worker->joined = (mi_subproc_current()._mi_subproc_id == worker->subproc._mi_subproc_id);
  // A Windows shared-library thread may already have been initialized by the DLL's
  // thread-attach callback. Such a thread cannot move into another sub-process.
  if (!worker->joined) return THREAD_OK;
  // The sub-process's main heap, which mi_heap_main() names for this thread now.
  fill_heap(mi_heap_main(), NULL);
  // And a heap of the sub-process's own. Neither is freed before the thread exits.
  mi_heap_t* heap = mi_heap_new();
  assert(heap != NULL);
  fill_heap(heap, NULL);
  return THREAD_OK;
}

// Run once before any baseline, with the profiler off: glibc allocates a new thread's TLS
// block (DTV) with the overridden calloc on the CREATING thread, and keeps it with the stack
// in its dead-stack cache after the join. With every allocation sampled, the subproc
// scenario's own pthread_create would otherwise leave a live record in the main heap that no
// destroy takes away. After this one, that scenario reuses the cached stack. The thread
// itself never calls mimalloc, so it does not count as a second thread of the main
// sub-process either (which would start the scavenger).
static THREAD_RET warmup_worker(void* arg) { (void)arg; return THREAD_OK; }
static void warmup_thread_cache(void) {
  thread_t worker;
  thread_start(&worker, warmup_worker, NULL);
  thread_join(worker);
}

static void scenario_subproc(void) {
  start_profiler();
  const counts_t before = counts_now();
  mi_subproc_id_t subproc = mi_subproc_new();
  assert(subproc._mi_subproc_id != NULL);
  thread_t worker;
  subproc_worker_arg_t worker_arg = { subproc, false };
  thread_start(&worker, subproc_worker, &worker_arg);
  thread_join(worker);
#ifdef _WIN32
  if (!worker_arg.joined) {
    mi_subproc_destroy(subproc);
    mi_prof_stop();
    puts("skip: Windows DLL initialized the worker before sub-process assignment");
    return;
  }
#else
  assert(worker_arg.joined);
#endif
  const counts_t during = counts_now();
  assert(during.samples >= before.samples + 2 * HEAP_BLOCKS);
  mi_subproc_destroy(subproc);
  expect_baseline("mi_subproc_destroy", counts_now(), before, during);
  stop_profiler(before);
}

// Destroys the heap on the first visited stack, while mi_prof_visit holds the profiler's lock.
typedef struct visit_destroy_s { mi_heap_t* heap; size_t visited; } visit_destroy_t;
static bool destroy_in_visit(const mi_prof_sample_info_t* info, void* arg) {
  (void)info;
  visit_destroy_t* v = (visit_destroy_t*)arg;
  if (v->visited++ == 0) mi_heap_destroy(v->heap);
  return true;
}

static void scenario_visit(void) {
  start_profiler();
  const counts_t before = counts_now();
  mi_heap_t* heap = mi_heap_new();
  assert(heap != NULL);
  fill_heap(heap, NULL);
  const counts_t during = counts_now();
  assert(during.samples >= before.samples + HEAP_BLOCKS);
  visit_destroy_t v = { heap, 0 };
  assert(mi_prof_visit(destroy_in_visit, &v));
  assert(v.visited > 0);
  // The visit pinned every stack entry; the sweep at its end drops the ones left unused.
  expect_baseline("mi_heap_destroy inside mi_prof_visit", counts_now(), before, during);
  stop_profiler(before);
}

int main(void) {
  // Warm up once with the profiler off, so lazily created process-lifetime state (TLS slot
  // tables, a cached thread stack, and the like) exists before any baseline is taken.
  mi_heap_t* warm = mi_heap_new();
  assert(warm != NULL);
  fill_heap(warm, NULL);
  mi_heap_destroy(warm);
  warmup_thread_cache();

  scenario_destroy();
  puts("ok: mi_heap_destroy forgets the heap's sampled blocks");
  scenario_delete();
  puts("ok: mi_heap_delete keeps them until they are freed");
  scenario_visit();
  puts("ok: a mi_heap_destroy inside a mi_prof_visit callback forgets them too");
  // Last: creating a heap on the main thread after a heap destroy followed by a
  // mi_subproc_new/mi_subproc_destroy pair trips an MI_DEBUG=3 assertion in page.c, with the
  // profiler playing no part in it (#554). So nothing here creates a heap after this scenario.
  scenario_subproc();
  puts("ok: mi_subproc_destroy forgets the sub-process's sampled blocks");
  return 0;
}
