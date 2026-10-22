/* #610: shared selection logic for the owner-side retired-page reuse slow path.
   The optional inspection count is used by structural tests; production passes NULL,
   and force-inlining removes both counter branches and writes from that call. */
#ifndef MI_PAGE_RETIRED_SEARCH_H
#define MI_PAGE_RETIRED_SEARCH_H

static mi_decl_forceinline mi_page_queue_t* mi_page_repurpose_find(
    mi_theap_t* theap, mi_page_queue_t* pq, size_t bin_lo, size_t bin_hi,
    size_t want, size_t* best_size, size_t* inspected) {
  mi_page_queue_t* best_pq = NULL;
  *best_size = SIZE_MAX;
  if (inspected != NULL) *inspected = 0;
  // #610: retirement lowers this owner-side bound before returning; collection
  // only raises it after checking every retired queue. It can be stale and
  // over-inclusive, but covers unpublished pages as well as scavenger slots.
  // Skip the empty prefix; retain the constant upper limit to avoid computing
  // a second dynamic loop endpoint for the small remaining suffix.
  if (bin_lo < theap->page_retired_min) bin_lo = theap->page_retired_min;
  for (size_t bin = bin_lo; bin <= bin_hi; bin++) {
    if (inspected != NULL) (*inspected)++;
    mi_page_queue_t* const q = &theap->pages[bin];
    mi_page_t* const page = q->first;
    // only a retired page (the bin's only one, empty) whose meta lives outside its slices, so the
    // block area starts at the slice start whatever the block size
    if (q == pq || page == NULL || page != q->last || page->retire_expire == 0 || page->used != 0) continue;
    if (page->memid.memkind != MI_MEM_ARENA || !mi_page_meta_is_separated(page)) continue;
    #if MI_PPROF
    if (page->has_metadata) continue;   // (an empty page holds no sample record, but be sure)
    #endif
    const size_t size = mi_size_of_slices(page->memid.mem.arena.slice_count);
    if (size < want || size >= *best_size) continue;
    best_pq = q; *best_size = size;   // best fit: the smallest that is large enough
    // #585: no later page can improve an exact fit. Keep the same first-best
    // selection while avoiding the remaining bins on every such re-carve.
    if (size == want) break;
  }
  return best_pq;
}

#endif
