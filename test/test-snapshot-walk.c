/* ----------------------------------------------------------------------------
Copyright (c) 2026 mimalloc-pprof contributors
This is free software; you can redistribute it and/or modify it under the
terms of the MIT license. A copy of the license can be found in the file
"LICENSE" at the root of this distribution.
-----------------------------------------------------------------------------*/

/* #338: the heap snapshot writer must emit a file every format-v1 reader accepts, whatever
   state the arena table and the sub-process registry are in.

   W1  A NULL slot inside `arena_count`: `MI_PURGE_RECLAIM` released an arena that was not the
       last one, so its slot is NULL and `arena_count` did not shrink (docs/arena-reclaim.md).
       The header's `arena_count` must equal the ARNA records written, and every live arena
       must be one of them. Before the fix the header counted the NULL slot and every reader
       met the next section where it expected one more ARNA record.
   W2  A second sub-process (`mi_subproc_new`) with memory of its own: its arenas, their pages
       and its main heap must be in the file. Before the fix the walk started at the main
       sub-process and followed `next`, which is always NULL for it (`mi_subproc_init` pushes
       at the head and main registers first), so only the main sub-process was written.

   The file is parsed by test-snapshot-reader.h. The arena table is read directly
   (`subproc->arenas[]`), so this test links the static library, like test-diagnostic-walks.
   The writer sees the same table: nothing else allocates while a snapshot is taken here. */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "mimalloc.h"
#include "mimalloc/internal.h"
#include "mimalloc/atomic.h"

#include "test-snapshot-reader.h"

#ifdef _WIN32
#include <windows.h>
typedef HANDLE thread_t;
#define THREAD_RET DWORD WINAPI
#define THREAD_OK 0
static int thread_run(LPTHREAD_START_ROUTINE fn, void* arg) {
  thread_t t = CreateThread(NULL, 0, fn, arg, 0, NULL);
  if (t == NULL) return 0;
  const DWORD waited = WaitForSingleObject(t, INFINITE);
  CloseHandle(t);
  return (waited == WAIT_OBJECT_0);
}
#else
#include <pthread.h>
#define THREAD_RET void*
#define THREAD_OK NULL
static int thread_run(void* (*fn)(void*), void* arg) {
  pthread_t t;
  if (pthread_create(&t, NULL, fn, arg) != 0) return 0;
  return (pthread_join(t, NULL) == 0);
}
#endif

#define ARENA_RESERVE_KIB   (32L * 1024)                  // `mi_option_arena_reserve` is in KiB: its 32 MiB minimum
#define BIG_BYTES           ((size_t)MI_ARENA_MAX_CHUNK_OBJ_SIZE)   // one object per arena (test-arena-reclaim.cpp)
#define BIG_COUNT           3
#define SUBPROC_BLOCKS      64

static int failures = 0;

static void check(int ok, const char* what) {
  printf("  %-72s %s\n", what, (ok ? "ok" : "FAILED"));
  if (!ok) failures++;
}

// Live arenas of one sub-process, and NULL slots below its `arena_count`.
static size_t live_arenas(mi_subproc_t* sp, size_t* holes) {
  size_t live = 0;
  const size_t count = mi_atomic_load_relaxed(&sp->arena_count);
  for (size_t i = 0; i < count; i++) {
    if (mi_atomic_load_ptr_acquire(mi_arena_t, &sp->arenas[i]) != NULL) { live++; }
    else if (holes != NULL) { (*holes)++; }
  }
  return live;
}

// The record index of the arena at `base` in the file, or -1.
static long find_arena(const snap_info_t* s, const void* base) {
  for (uint32_t a = 0; a < s->arenas_read; a++) {
    if (s->arena_base[a] == (uint64_t)(uintptr_t)base) return (long)a;
  }
  return -1;
}

static int keep_files = 0;   // `--keep`: leave `<path>.W1`/`.W2` behind for the viewers

// Write a snapshot and parse it; check the arena records against every sub-process's table.
static void snapshot_and_check(const char* base, const char* row, snap_info_t* s) {
  char path[512];
  char what[128];
  snprintf(path, sizeof(path), "%s.%s", base, row);
  const int written = mi_heap_snapshot_to_file(path, 0);
  snprintf(what, sizeof(what), "%s: mi_heap_snapshot_to_file returned 0", row);
  check(written == 0, what);
  const int parsed = snap_read_file(path, s);
  if (!keep_files) remove(path);
  snprintf(what, sizeof(what), "%s: the file is a well-formed v1 snapshot (strict reader)", row);
  check(written == 0 && parsed == 0, what);
  if (written != 0 || parsed != 0) return;
  snprintf(what, sizeof(what), "%s: header arena_count equals the ARNA records read", row);
  check(s->arena_count == s->arenas_read, what);

  size_t live = 0;
  int all_found = 1;
  mi_lock(_mi_subprocs_lock()) {
    for (mi_subproc_t* sp = _mi_subprocs_head(); sp != NULL; sp = sp->next) {
      const size_t count = mi_atomic_load_relaxed(&sp->arena_count);
      for (size_t i = 0; i < count; i++) {
        mi_arena_t* const arena = mi_atomic_load_ptr_acquire(mi_arena_t, &sp->arenas[i]);
        if (arena == NULL) continue;
        live++;
        if (find_arena(s, arena) < 0) { all_found = 0; }
      }
    }
  }
  snprintf(what, sizeof(what), "%s: one ARNA record per live arena of every sub-process (%zu)", row, live);
  check(s->arenas_read == live && all_found, what);
}

// ---------------------------------------------------------------------------------------------
// W1: a NULL slot inside arena_count
// ---------------------------------------------------------------------------------------------

static void run_hole_row(const char* path) {
  printf("[W1] snapshot with a released arena's NULL slot inside arena_count\n");
  void* keep_small = mi_malloc(64);
  void* big[BIG_COUNT];
  for (int i = 0; i < BIG_COUNT; i++) {
    big[i] = mi_malloc(BIG_BYTES);
    if (big[i] == NULL) { printf("  out of memory for %zu bytes\n", BIG_BYTES); exit(2); }
    memset(big[i], 0x40 + i, 4096);
  }
  // big[1]'s arena is completely free and big[2]'s arena (added after it) still holds data,
  // so the released slot cannot be the last one.
  mi_free(big[1]);
  mi_purge_all_report_t rep;
  memset(&rep, 0, sizeof(rep));
  for (int i = 0; i < 20; i++) {
    (void)mi_purge_all_ex((mi_purge_flags_t)(MI_PURGE_FORCE | MI_PURGE_RECLAIM), 100, &rep);
    if (rep.reclaimed && rep.arenas_reclaimed > 0) break;
  }
  size_t holes = 0;
  const size_t live = live_arenas(_mi_subproc_main(), &holes);
  printf("  reclaimed %zu arena(s); main sub-process: %zu live, %zu NULL slot(s) below arena_count\n",
         rep.arenas_reclaimed, live, holes);
  check(rep.reclaimed && rep.arenas_reclaimed > 0, "W1: precondition: MI_PURGE_RECLAIM released an arena");
  check(holes > 0, "W1: precondition: a NULL slot is left inside arena_count");

  snap_info_t s;
  snapshot_and_check(path, "W1", &s);

  mi_free(big[0]);
  mi_free(big[2]);
  mi_free(keep_small);
}

// ---------------------------------------------------------------------------------------------
// W2: a second sub-process
// ---------------------------------------------------------------------------------------------

static mi_subproc_id_t w2_subproc;
static void* w2_blocks[SUBPROC_BLOCKS];

static THREAD_RET subproc_worker(void* arg) {
  (void)arg;
  mi_subproc_add_current_thread(w2_subproc);
  for (size_t i = 0; i < SUBPROC_BLOCKS; i++) {
    w2_blocks[i] = mi_malloc(64 << (i % 11));   // 64 B .. 64 KiB
    if (w2_blocks[i] != NULL) memset(w2_blocks[i], 0x5A, 64);
  }
  // Thread exit abandons these pages: they stay in the sub-process's arenas.
  return THREAD_OK;
}

static void run_subproc_row(const char* path) {
  printf("[W2] snapshot with a second sub-process that owns memory\n");
  w2_subproc = mi_subproc_new();
  mi_subproc_t* const sp = _mi_subproc_from_id(w2_subproc);
  check(sp != NULL, "W2: precondition: mi_subproc_new succeeded");
  if (sp == NULL) return;
  check(thread_run(&subproc_worker, NULL), "W2: precondition: the sub-process's thread ran");
  const size_t live = live_arenas(sp, NULL);
  check(live > 0, "W2: precondition: the sub-process reserved an arena of its own");

  snap_info_t s;
  snapshot_and_check(path, "W2", &s);
  if (s.arenas_read > 0) {
    int found = 1, has_pages = 0;
    const size_t count = mi_atomic_load_relaxed(&sp->arena_count);
    for (size_t i = 0; i < count; i++) {
      mi_arena_t* const arena = mi_atomic_load_ptr_acquire(mi_arena_t, &sp->arenas[i]);
      if (arena == NULL) continue;
      const long rec = find_arena(&s, arena);
      if (rec < 0) { found = 0; continue; }
      if (s.arena_pages[rec] > 0) has_pages = 1;
    }
    printf("  %u ARNA records, %llu HEAP records (%llu with seq 0), %llu pages\n", s.arenas_read,
           (unsigned long long)s.heaps, (unsigned long long)s.heaps_seq0, (unsigned long long)s.pages);
    check(found, "W2: the sub-process's arenas are in the file");
    check(has_pages, "W2: ... with the pages its thread left in them");
    check(s.heaps_seq0 == 2, "W2: both sub-processes' main heaps (seq 0) are in the file");
  }
  mi_subproc_destroy(w2_subproc);   // frees the sub-process's heaps and arenas, w2_blocks with them
}

int main(int argc, char** argv) {
  setvbuf(stdout, NULL, _IONBF, 0);  // Preserve the last completed check if a platform crashes.
  const char* path = (argc > 1 ? argv[1] : "test-snapshot-walk.bin");
  keep_files = (argc > 2 && strcmp(argv[2], "--keep") == 0);
  // Before the first allocation: every arena of the process, the first one included, is then
  // reserved at the minimum size, so each of W1's big objects forces an arena of its own.
  mi_option_set(mi_option_arena_reserve, ARENA_RESERVE_KIB);
  mi_thread_init();
  // W2 first: it must fail on its own, not on W1's NULL slot.
  run_subproc_row(path);
  run_hole_row(path);
  if (failures > 0) { printf("test-snapshot-walk: %d check(s) FAILED\n", failures); return 1; }
  printf("test-snapshot-walk: OK\n");
  return 0;
}
