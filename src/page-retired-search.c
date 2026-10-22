/* Owner-side retired-page selection, shared by the C and single-TU builds. */
#include "mimalloc.h"
#include "mimalloc/internal.h"

#if MI_LARGE_REPURPOSE
#include "page-retired-search.h"

// #610: outlining keeps the bounded loop out of mi_page_fresh_alloc. The inline
// body remains shared with structural tests; NULL removes its inspection counter.
mi_decl_noinline mi_page_queue_t* _mi_page_repurpose_find(
    mi_theap_t* theap, mi_page_queue_t* pq, size_t bin_lo, size_t bin_hi,
    size_t want, size_t* best_size) {
  // A local running minimum lets the compiler keep it in a register and write
  // the caller's output only once after selection.
  size_t size;
  mi_page_queue_t* const selected = mi_page_repurpose_find(
      theap, pq, bin_lo, bin_hi, want, &size, NULL);
  *best_size = size;
  return selected;
}
#endif
