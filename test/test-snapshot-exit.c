/* ----------------------------------------------------------------------------
Copyright (c) 2026 mimalloc-pprof contributors
This is free software; you can redistribute it and/or modify it under the
terms of the MIT license. A copy of the license can be found in the file
"LICENSE" at the root of this distribution.
-----------------------------------------------------------------------------*/

/* #338: the exit-time snapshot (`MIMALLOC_SNAPSHOT_ON_EXIT=2`, `MIMALLOC_SNAPSHOT_PATH`).

   Pins two decisions: (1) `_mi_heap_snapshot_on_exit` runs from `mi_process_done` after
   the scavenger stopped and before any teardown, so the file is complete and flag 2's
   free-list collection still finds live theaps; (2) format version 1's record layout, by
   parsing the file with an independent reader (test-snapshot-reader.h, shared with
   test-snapshot-walk.c; the Python reader in examples/heap-snapshot/ is the third).

   Runs itself as a child with the environment set, then parses what the child wrote:
   magic/version, at least one page, at least one page with a free map (flag 2), and the
   END footer whose page_count matches the records seen. */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <mimalloc.h>

#if defined(_WIN32)
#include <process.h>
#include <windows.h>
#else
#include <unistd.h>
#include <sys/wait.h>
#include <pthread.h>
#endif

#include "test-snapshot-reader.h"

/* ---- child: allocate on two threads, exit normally ---- */
static void* churn(void* arg) {
  (void)arg;
  void* keep[256];
  for (int i = 0; i < 256; i++) { keep[i] = mi_malloc(64 + (i % 900)); memset(keep[i], 1, 8); }
  for (int i = 0; i < 256; i += 2) mi_free(keep[i]);   /* leave the pages partially used: free lists to collect */
  return NULL;
}
static int child_main(void) {
  static void* hold[64];
  for (int i = 0; i < 64; i++) { hold[i] = mi_malloc(4096 * (1 + i % 5)); memset(hold[i], 2, 16); }
  for (int i = 1; i < 64; i += 3) mi_free(hold[i]);
  #if defined(_WIN32)
  HANDLE t = (HANDLE)_beginthreadex(NULL, 0, (unsigned (__stdcall*)(void*))churn, NULL, 0, NULL);
  WaitForSingleObject(t, INFINITE); CloseHandle(t);
  #else
  pthread_t t; pthread_create(&t, NULL, churn, NULL); pthread_join(t, NULL);
  #endif
  return 0;   /* normal exit -> mi_process_done -> _mi_heap_snapshot_on_exit */
}

/* ---- parent: a second, independent reader of format v1 (test-snapshot-reader.h) ---- */
static int parent_check(const char* path) {
  snap_info_t s;
  if (snap_read_file(path, &s) != 0) return 1;
  printf("snapshot: arenas=%u pages=%llu footer_page_count=%llu freemap_seen=%d\n",
         s.arena_count, (unsigned long long)s.pages, (unsigned long long)s.footer_pages, s.has_freemap);
  if (!(s.flags & MI_SNAPSHOT_BLOCKS)) { fprintf(stderr, "FAIL: option value 2 must set MI_SNAPSHOT_BLOCKS (flags=%u)\n", s.flags); return 1; }
  if (s.pages == 0) { fprintf(stderr, "FAIL: no pages\n"); return 1; }
  if (!s.has_freemap) { fprintf(stderr, "FAIL: flag 2 set but no page carried a free map -- exit ordering lost the calling thread's pages?\n"); return 1; }
  printf("test-snapshot-exit: OK\n");
  return 0;
}

int main(int argc, char** argv) {
  if (argc > 1 && strcmp(argv[1], "--child") == 0) return child_main();
  if (argc < 2) { fprintf(stderr, "usage: %s <snapshot-path>\n", argv[0]); return 2; }
  const char* path = argv[1];
  remove(path);
  #if defined(_WIN32)
  _putenv("MIMALLOC_SNAPSHOT_ON_EXIT=2");
  { char buf[1024]; snprintf(buf, sizeof buf, "MIMALLOC_SNAPSHOT_PATH=%s", path); _putenv(buf); }
  const char* args[] = { argv[0], "--child", NULL };
  const intptr_t child_rc = _spawnv(_P_WAIT, argv[0], args);
  if (child_rc != 0) { fprintf(stderr, "FAIL: child exited %d\n", (int)child_rc); return 1; }
  #else
  setenv("MIMALLOC_SNAPSHOT_ON_EXIT", "2", 1);
  setenv("MIMALLOC_SNAPSHOT_PATH", path, 1);
  pid_t pid = fork();
  if (pid == 0) { execl(argv[0], argv[0], "--child", (char*)NULL); _exit(127); }
  int status = 0; waitpid(pid, &status, 0);
  if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) { fprintf(stderr, "FAIL: child status %d\n", status); return 1; }
  #endif
  const int rc = parent_check(path);
  remove(path);   /* leave nothing behind */
  return rc;
}
