/* #554: destroying a sub-process must not release the calling thread's thread locals
   when that thread belongs to another sub-process. mi_subproc_unsafe_destroy called
   _mi_thread_locals_thread_done() unconditionally, which freed this thread's TLS slot
   array (its theap for every heap of the MAIN sub-process) and cleared the fast slot.
   A heap created afterwards could then hand out a page whose theap is not the one its
   slot names, tripping mi_page_is_valid_init (src/page.c) in MI_DEBUG>=3 builds.
   The public-API sequence below is the reproducer from the issue, plus a heap that is
   live across the sub-process lifecycle. */
#include <mimalloc.h>
#include <stdio.h>

static int check(mi_heap_t* heap, const char* what) {
  void* p[64];
  for (int i = 0; i < 64; i++) {
    p[i] = mi_heap_malloc(heap, 16 + (size_t)i * 8);
    if (p[i] == NULL || !mi_heap_contains(heap, p[i])) {
      printf("FAIL: %s: block %d not owned by its heap\n", what, i);
      return 1;
    }
  }
  for (int i = 0; i < 64; i++) mi_free(p[i]);
  return 0;
}

int main(void) {
  mi_heap_t* live = mi_heap_new();              // live across the sub-process lifecycle
  if (live == NULL || check(live, "live heap before")) return 1;

  mi_heap_t* h = mi_heap_new();
  mi_heap_destroy(h);                           // frees h's thread-local slot
  mi_subproc_id_t sp = mi_subproc_new();
  mi_subproc_destroy(sp);                       // must leave this thread's slots alone

  mi_heap_t* h2 = mi_heap_new();                // reuses h's slot index
  if (h2 == NULL || check(h2, "new heap after subproc destroy")) return 1;
  if (check(live, "live heap after")) return 1;
  if (check(mi_heap_main(), "main heap after")) return 1;
  mi_heap_destroy(h2);
  mi_heap_destroy(live);
  printf("ok\n");
  return 0;
}
