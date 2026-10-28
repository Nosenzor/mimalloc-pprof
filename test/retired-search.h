/* #610: exercise the production selector with synthetic owner queues. No arena
   traversal or allocation is performed by the selector, so these records need
   only the fields it reads. Selection and bin visits are deterministic. */
#ifndef MI_TEST_RETIRED_SEARCH_H
#define MI_TEST_RETIRED_SEARCH_H

#if MI_LARGE_REPURPOSE && MI_PAGE_META_IS_SEPARATED
#include "../src/page-retired-search.h"

// Compare the actual compiled entry point with the same counted loop used for
// work assertions. This also exercises its C/C++ linkage and output parameter.
static mi_page_queue_t* retired_search_checked(
    mi_theap_t* heap, mi_page_queue_t* pq, size_t lo, size_t hi,
    size_t want, size_t* size, size_t* visits) {
  mi_page_queue_t* const expected = mi_page_repurpose_find(heap, pq, lo, hi, want, size, visits);
  size_t compiled_size;
  mi_page_queue_t* const selected = _mi_page_repurpose_find(heap, pq, lo, hi, want, &compiled_size);
  CHECK(selected == expected && compiled_size == *size,
        "(i) compiled selector must match the counted shared body");
  *size = compiled_size;
  return selected;
}

static void case_retired_search(void) {
  mi_theap_t heap;
  mi_page_t records[3];
  memset(&heap, 0, sizeof(heap));
  memset(records, 0, sizeof(records));
  const size_t lo = _mi_bin(MI_MEDIUM_MAX_OBJ_SIZE + 1);
  const size_t hi = _mi_bin(MI_LARGE_MAX_OBJ_SIZE);
  const size_t first = lo + 2;
  const size_t last = lo + 4;
  assert(last <= hi);
  mi_page_t* page = &records[0];
  if (_mi_is_aligned(page, MI_ARENA_SLICE_ALIGN)) page = &records[1];
  page->memid.memkind = MI_MEM_ARENA;
  page->memid.mem.arena.slice_count = 16;
  page->retire_expire = 1;
  assert(mi_page_meta_is_separated(page));
  size_t size = 0, visits = 0;
  heap.page_retired_min = MI_BIN_FULL;
  heap.page_retired_max = 0;
  mi_page_queue_t* selected = retired_search_checked(&heap, &heap.pages[lo], lo, hi,
                                                    mi_size_of_slices(16), &size, &visits);
  CHECK(selected == NULL, "(i) empty retired range must have no candidate");
  CHECK(visits == 0, "(i) empty retired range inspected %zu bins, expected zero", visits);

  heap.pages[first].first = heap.pages[first].last = page;
  heap.page_retired_min = heap.page_retired_max = first;
  selected = retired_search_checked(&heap, &heap.pages[lo], lo, hi,
                                    mi_size_of_slices(16), &size, &visits);
  CHECK(selected == &heap.pages[first] && size == mi_size_of_slices(16),
        "(i) exact-fit unpublished page must remain eligible");
  CHECK(visits == 1, "(i) single retired bin inspected %zu bins, expected one", visits);

  mi_page_t* second = NULL;
  for (size_t i = 0; i < sizeof(records)/sizeof(records[0]); i++) {
    if (&records[i] != page && !_mi_is_aligned(&records[i], MI_ARENA_SLICE_ALIGN)) {
      second = &records[i]; break;
    }
  }
  assert(second != NULL);
  second->memid.memkind = MI_MEM_ARENA;
  second->memid.mem.arena.slice_count = page->memid.mem.arena.slice_count;
  second->retire_expire = 1;
  heap.pages[last].first = heap.pages[last].last = second;
  heap.page_retired_max = last;
  selected = retired_search_checked(&heap, &heap.pages[lo], lo, hi,
                                    mi_size_of_slices(8), &size, &visits);
  CHECK(selected == &heap.pages[first], "(i) equal fits must keep the lowest-bin candidate");
  CHECK(visits == last - first + 1,
        "(i) search inspected %zu bins outside the retired interval", visits);
  selected = retired_search_checked(&heap, &heap.pages[first], lo, hi,
                                    mi_size_of_slices(16), &size, &visits);
  CHECK(selected == &heap.pages[last], "(i) destination queue must not serve itself");
  // Publication slots are not the owner queue's candidate index. A full array
  // must not hide an unpublished page, and a borrowed slot must not hide the
  // page it represents. This tests selection only; unpublish owns the handshake.
  mi_tld_t tld;
  memset(&tld, 0, sizeof(tld));
  heap.tld = &tld;
  tld.retired_used = SIZE_MAX;
  for (size_t i = 0; i < MI_RETIRED_PAGE_SLOTS; i++) {
    mi_atomic_store_ptr_relaxed(mi_page_t, &tld.retired_pages[i], second);
  }
  selected = retired_search_checked(&heap, &heap.pages[lo], lo, hi,
                                    mi_size_of_slices(16), &size, &visits);
  CHECK(selected == &heap.pages[first] && page->retired_slot == NULL,
        "(i) full publication slots must not hide an unpublished page");
  // page-holes.c uses pointer value 1 for its transient busy marker.
  mi_atomic_store_ptr_relaxed(mi_page_t, &tld.retired_pages[0], (mi_page_t*)(uintptr_t)1);
  page->retired_slot = &tld.retired_pages[0];
  selected = retired_search_checked(&heap, &heap.pages[lo], lo, hi,
                                    mi_size_of_slices(16), &size, &visits);
  CHECK(selected == &heap.pages[first], "(i) busy publication slot must not hide its owner queue");
  page->retired_slot = NULL;
  page->used = 1;
  second->retire_expire = 0;
  selected = retired_search_checked(&heap, &heap.pages[lo], lo, hi,
                                    mi_size_of_slices(16), &size, &visits);
  CHECK(selected == NULL, "(i) active or expired pages must not be selected");
  fprintf(stderr, "(i) retired-search selection and bounded bin visits checked\n");
}

static THREAD_RET retired_search_budget_main(void* arg) {
  (void)arg;
  void* const a = mi_malloc(SIZE_A);
  assert(a != NULL);
  mi_page_t* const page = _mi_ptr_page(a);
  mi_theap_t* const owner = mi_page_theap(page);
  const void* const slices = mi_page_slice_start(page);
  mi_free(a);
  assert(page->retire_expire != 0);
  const uint32_t budget = owner->tld->large_repurpose_left;
  owner->tld->large_repurpose_left = 0;
  void* const b = mi_malloc(SIZE_B);
  assert(b != NULL);
  CHECK(mi_page_slice_start(_mi_ptr_page(b)) != slices,
        "(i) exhausted budget must not repurpose the retired page");
  CHECK(owner->tld->large_repurpose_left == 0, "(i) denied search must preserve exhausted budget");
  owner->tld->large_repurpose_left = budget;
  mi_free(b);
  return THREAD_OK;
}

static void case_retired_search_budget(void) {
  thread_t t;
  thread_start(&t, &retired_search_budget_main, NULL);
  thread_join(t);
}
#else
static void case_retired_search(void) { }
static void case_retired_search_budget(void) { }
#endif

#endif
