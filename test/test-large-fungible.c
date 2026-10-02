/* #575: a large page's bytes are fungible across the large bins (96-512 KiB blocks).

   large-class-persistent/8 held 154 MiB for 10.5 MiB of live blocks (jemalloc and TCMalloc: 61 MiB)
   because every thread kept a page per large bin -- 1 to 4 MiB each, all of it resident once the
   bin had held its blocks -- while its 8 live blocks needed about three bins at a time. Two
   mechanisms, both zero-refault (a resident byte is reused, never returned to the OS):

   (a) the span cap (MI_LARGE_SPAN_MAX_KIB, `mi_option_large_span_max`): a demand-grown page stops at
       1 MiB, so a thread that fills a bin gets more small pages, not one 4 MiB page;
   (b) the repurpose budget (MI_LARGE_REPURPOSE_PER_TICK): a bin that needs a page re-carves another
       bin's retired page, and a thread that rotates through the bins keeps ONE page between them.
       With the #530 bound of 64 per heartbeat the rotation ran out of budget, every bin then took a
       fresh page from the arena, and the thread kept one page per bin for good.

   Deterministic: structural checks on the pages the blocks land in, no RSS and no timing. A fresh
   thread starts each case, so no other case's pages or heartbeat budget are involved. Against a
   tree without #575 (no MI_LARGE_SPAN_MAX_KIB) the checks compile and FAIL: RED. */

#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <mimalloc.h>
#include "mimalloc/internal.h"   // _mi_ptr_page, mi_page_t, the span constants

#ifdef _WIN32
#include <windows.h>
typedef HANDLE thread_t;
typedef DWORD (WINAPI *thread_fun_t)(void*);
#define THREAD_RET DWORD WINAPI
#define THREAD_OK  0
static void thread_start(thread_t* t, thread_fun_t fn, void* arg) { *t = CreateThread(NULL, 0, fn, arg, 0, NULL); assert(*t != NULL); }
static void thread_join(thread_t t) { assert(WaitForSingleObject(t, INFINITE) == WAIT_OBJECT_0); CloseHandle(t); }
#else
#include <pthread.h>
typedef pthread_t thread_t;
typedef void* (*thread_fun_t)(void*);
#define THREAD_RET void*
#define THREAD_OK  NULL
static void thread_start(thread_t* t, thread_fun_t fn, void* arg) { assert(pthread_create(t, NULL, fn, arg) == 0); }
static void thread_join(thread_t t) { assert(pthread_join(t, NULL) == 0); }
#endif

static int failures = 0;
#define CHECK(cond, ...) do { if (!(cond)) { fprintf(stderr, "FAILED: " __VA_ARGS__); fprintf(stderr, "\n"); failures++; } } while (0)

#define KIB 1024
#define MAX_BLOCKS 256
#define ROTATIONS  16                      // x the 10 bins below: 160 page requests, under one heartbeat (1000 mallocs)
// ten large bins (distinct 12.5% size classes)
static const size_t BIN_SIZES[] = { 112, 128, 160, 192, 224, 256, 320, 384, 448, 512 };   // KiB
#define NBINS ((sizeof(BIN_SIZES) / sizeof(BIN_SIZES[0])))

static size_t span_of(const void* p) {
  const mi_page_t* const page = _mi_ptr_page(p);
  return (page->memid.memkind == MI_MEM_ARENA ? page->memid.mem.arena.slice_count : 0);
}

/* ---- (a) a bin that keeps filling its pages stops at the cap -------------- */

static size_t max_span_seen;

static THREAD_RET grow_main(void* arg) {
  (void)arg;
  void* blocks[MAX_BLOCKS];
  size_t n = 0;
  max_span_seen = 0;
  while (n < MAX_BLOCKS) {   // 200 KiB blocks, all kept: sustained demand, so the #532 policy grows the span
    void* const p = mi_malloc(200 * KIB);
    assert(p != NULL);
    memset(p, 0x5a, 64);
    blocks[n++] = p;
    const size_t s = span_of(p);
    if (s > max_span_seen) { max_span_seen = s; }
  }
  for (size_t i = 0; i < n; i++) { mi_free(blocks[i]); }
  return THREAD_OK;
}

static void case_cap(void) {
  #ifdef MI_LARGE_SPAN_MAX_KIB
  thread_t t; thread_start(&t, &grow_main, NULL); thread_join(t);
  const size_t cap = mi_slice_count_of_size(MI_LARGE_SPAN_MAX_KIB * (size_t)KIB);
  fprintf(stderr, "(a) largest span of a busy 200 KiB bin: %zu slices (cap %zu, full %zu)\n", max_span_seen, cap, mi_slice_count_of_size(MI_LARGE_PAGE_SIZE));
  CHECK(max_span_seen <= cap + 1, "(a) a busy large bin grew its page to %zu slices; the cap is %zu", max_span_seen, cap);
  #if MI_LARGE_SPAN_MAX_KIB > 0
  {  // the option: 0 is the #532 policy again (the full span)
    const long saved = mi_option_get(mi_option_large_span_max);
    mi_option_set(mi_option_large_span_max, 0);
    thread_t t2; thread_start(&t2, &grow_main, NULL); thread_join(t2);
    CHECK(max_span_seen == mi_slice_count_of_size(MI_LARGE_PAGE_SIZE), "(a) with mi_option_large_span_max=0 a busy bin should reach the full span (got %zu)", max_span_seen);
    mi_option_set(mi_option_large_span_max, saved);
  }
  #endif
  #else
  // a tree without #575 has no cap: the default grows to the full span (RED)
  thread_t t; thread_start(&t, &grow_main, NULL); thread_join(t);
  CHECK(max_span_seen <= 16 + 1, "(a) a busy large bin grew its page to %zu slices (no span cap in this tree)", max_span_seen);
  #endif
}

/* ---- (b) a thread that rotates through the bins keeps one page ------------- */

static size_t distinct_pages;

static THREAD_RET rotate_main(void* arg) {
  (void)arg;
  uint8_t* starts[NBINS * ROTATIONS];
  size_t n = 0;
  distinct_pages = 0;
  for (size_t r = 0; r < ROTATIONS; r++) {
    for (size_t b = 0; b < NBINS; b++) {
      void* const p = mi_malloc(BIN_SIZES[b] * KIB);   // one live block at a time: the previous bin's page has just emptied
      assert(p != NULL);
      memset(p, 0x33, 64);
      uint8_t* const s = mi_page_slice_start(_mi_ptr_page(p));
      size_t i = 0;
      while (i < n && starts[i] != s) { i++; }
      if (i == n) { starts[n++] = s; }
      mi_free(p);
    }
  }
  distinct_pages = n;
  return THREAD_OK;
}

static void case_rotate(void) {
  thread_t t; thread_start(&t, &rotate_main, NULL); thread_join(t);
  fprintf(stderr, "(b) %zu distinct pages for %zu allocations rotating over %zu bins\n", distinct_pages, (size_t)(NBINS * ROTATIONS), (size_t)NBINS);
  #if MI_LARGE_REPURPOSE
  // one live block at a time needs one page. Allow a couple for the first bins and for a heartbeat inside the loop.
  CHECK(distinct_pages <= 3, "(b) rotating over %zu bins with one live block kept %zu pages (want at most 3)", (size_t)NBINS, distinct_pages);
  #endif
}

int main(void) {
  #if !MI_ENABLE_LARGE_PAGES
  fprintf(stderr, "skipped: no large pages in this build\n");
  return 0;
  #else
  #if defined(MI_GUARDED) && MI_GUARDED
  if (mi_option_get(mi_option_guarded_sample_rate) != 0) {
    fprintf(stderr, "skipped: guarded sampling is on\n");
    return 0;
  }
  #endif
  #if defined(MI_LARGE_SPAN) && !MI_LARGE_SPAN
  fprintf(stderr, "skipped: MI_LARGE_SPAN=0\n");
  return 0;
  #else
  mi_option_set(mi_option_page_reserve, 0);
  case_cap();
  case_rotate();
  #endif
  if (failures > 0) { fprintf(stderr, "%d check(s) failed\n", failures); return 1; }
  fprintf(stderr, "ok\n");
  return 0;
  #endif
}
