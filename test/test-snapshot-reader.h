/* ----------------------------------------------------------------------------
Copyright (c) 2026 mimalloc-pprof contributors
This is free software; you can redistribute it and/or modify it under the
terms of the MIT license. A copy of the license can be found in the file
"LICENSE" at the root of this distribution.
-----------------------------------------------------------------------------*/

/* #338: a strict, independent reader of heap snapshot format version 1, shared by
   test-snapshot-exit.c and test-snapshot-walk.c.

   It is not the writer's code and not examples/heap-snapshot/mi_snapshot.py: it re-derives
   the layout from the format description, so a writer change that breaks the format fails
   here. Like mi_snapshot.py it expects exactly the header's `arena_count` ARNA records, each
   followed by its PAGE list, then the writer thread's PAGE list, then HEAP records until END,
   a footer page count equal to the records read, and nothing after the footer. */

#pragma once
#include <stdio.h>
#include <stdint.h>
#include <string.h>

#define SNAP_MAGIC      0x5348494Du
#define SNAP_VERSION    1u
#define SNAP_SEC_ARENA  0x414E5241u
#define SNAP_SEC_HEAP   0x50414548u
#define SNAP_SEC_PAGE   0x45474150u
#define SNAP_SEC_END    0x444E4520u
#define SNAP_MAX_ARENAS 1024u   /* ARNA records kept for the caller's cross-checks; more is a failure */

typedef struct snap_info_s {
  uint32_t flags;
  uint32_t arena_count;                    /* as declared in the header */
  uint32_t arenas_read;                    /* ARNA records actually parsed */
  uint64_t arena_base[SNAP_MAX_ARENAS];    /* per record: `base`, i.e. the `mi_arena_t*` */
  uint64_t arena_pages[SNAP_MAX_ARENAS];   /* per record: the pages in its PAGE list */
  uint64_t heaps;                          /* HEAP records */
  uint64_t heaps_seq0;                     /* ... with heap_seq 0: one per sub-process's main heap */
  uint64_t pages;                          /* page records in every list */
  uint64_t footer_pages;                   /* the END footer's page_count */
  int      has_freemap;                    /* some page carried a free map */
} snap_info_t;

typedef struct snap_rd_s { FILE* f; int err; } snap_rd_t;

static uint32_t snap_u32(snap_rd_t* r) { uint32_t v = 0; if (fread(&v, 4, 1, r->f) != 1) r->err = 1; return v; }
static uint64_t snap_u64(snap_rd_t* r) { uint64_t v = 0; if (fread(&v, 8, 1, r->f) != 1) r->err = 1; return v; }
static uint8_t  snap_u8 (snap_rd_t* r) { uint8_t  v = 0; if (fread(&v, 1, 1, r->f) != 1) r->err = 1; return v; }
static void snap_skip(snap_rd_t* r, size_t n) { if (fseek(r->f, (long)n, SEEK_CUR) != 0) r->err = 1; }
/* u32 chunk_count | u32 chunk_bytes (MI_BCHUNK_SIZE in BYTES) | chunk_count * chunk_bytes bytes */
static void snap_bitmap(snap_rd_t* r) { uint32_t chunks = snap_u32(r); uint32_t chunk_bytes = snap_u32(r); snap_skip(r, (size_t)chunks * chunk_bytes); }

/* One page record; the caller already consumed its non-zero page_start. */
static void snap_page_after_start(snap_rd_t* r, snap_info_t* s) {
  (void)snap_u64(r); (void)snap_u64(r);                        /* slice_start, block_size */
  (void)snap_u32(r); (void)snap_u32(r); (void)snap_u32(r);     /* reserved, capacity, used */
  (void)snap_u64(r); (void)snap_u64(r); (void)snap_u64(r);     /* committed, tid, heap_seq */
  (void)snap_u32(r); (void)snap_u32(r); (void)snap_u32(r);     /* arena_idx, slice_index, slice_count */
  (void)snap_u8(r); (void)snap_u8(r); (void)snap_u8(r); (void)snap_u8(r);   /* memkind, kind, abandoned, full */
  const uint8_t has_freemap = snap_u8(r); (void)snap_u8(r); (void)snap_u8(r); (void)snap_u8(r);
  if (has_freemap) { s->has_freemap = 1; snap_skip(r, snap_u32(r)); }   /* u32 byte count, then the map */
}

/* u32 'PAGE' | page... | u64 0. Returns the number of pages, or -1 on a malformed list. */
static long snap_pages(snap_rd_t* r, snap_info_t* s) {
  const uint32_t tag = snap_u32(r);
  if (r->err || tag != SNAP_SEC_PAGE) { fprintf(stderr, "FAIL: expected a PAGE section, got 0x%08x\n", tag); return -1; }
  long n = 0;
  for (;;) {
    const uint64_t start = snap_u64(r);
    if (r->err) { fprintf(stderr, "FAIL: page list truncated\n"); return -1; }
    if (start == 0) return n;                                  /* sentinel */
    snap_page_after_start(r, s);
    if (r->err) { fprintf(stderr, "FAIL: page record truncated\n"); return -1; }
    n++; s->pages++;
  }
}

static int snap_parse(snap_rd_t* r, snap_info_t* s) {
  if (snap_u32(r) != SNAP_MAGIC)   { fprintf(stderr, "FAIL: bad magic\n"); return 1; }
  if (snap_u32(r) != SNAP_VERSION) { fprintf(stderr, "FAIL: unexpected version\n"); return 1; }
  (void)snap_u32(r); (void)snap_u32(r);                        /* ptr_size, slice_size */
  s->flags = snap_u32(r); (void)snap_u32(r);                   /* flags, reserved */
  (void)snap_u64(r); (void)snap_u64(r);                        /* clock, writer tid */
  s->arena_count = snap_u32(r);
  if (r->err) { fprintf(stderr, "FAIL: header truncated\n"); return 1; }
  for (uint32_t a = 0; a < s->arena_count; a++) {
    const uint32_t tag = snap_u32(r);
    if (r->err || tag != SNAP_SEC_ARENA) {
      fprintf(stderr, "FAIL: expected ARNA record %u of the %u the header declares, got 0x%08x\n", a + 1, s->arena_count, tag);
      return 1;
    }
    if (a >= SNAP_MAX_ARENAS) { fprintf(stderr, "FAIL: more than %u arenas\n", SNAP_MAX_ARENAS); return 1; }
    (void)snap_u32(r);                                         /* idx */
    s->arena_base[a] = snap_u64(r);
    (void)snap_u64(r); (void)snap_u32(r); (void)snap_u32(r); (void)snap_u32(r);   /* size, slice_count, info_slices, numa */
    (void)snap_u8(r); (void)snap_u8(r); (void)snap_u8(r); (void)snap_u8(r);       /* pinned, exclusive, pad */
    snap_bitmap(r); snap_bitmap(r); snap_bitmap(r);            /* committed, free, purge */
    const long n = snap_pages(r, s);
    if (n < 0) return 1;
    s->arena_pages[a] = (uint64_t)n;
    s->arenas_read++;
  }
  if (snap_pages(r, s) < 0) return 1;                          /* the writer thread's non-arena pages */
  for (;;) {                                                   /* heaps until END */
    const uint32_t tag = snap_u32(r);
    if (r->err) { fprintf(stderr, "FAIL: truncated before END\n"); return 1; }
    if (tag == SNAP_SEC_END) break;
    if (tag != SNAP_SEC_HEAP) { fprintf(stderr, "FAIL: unexpected section 0x%08x\n", tag); return 1; }
    const uint64_t seq = snap_u64(r); (void)snap_u32(r); (void)snap_u64(r);   /* heap_seq, numa, exclusive_arena */
    s->heaps++;
    if (seq == 0) s->heaps_seq0++;
    if (snap_pages(r, s) < 0) return 1;
  }
  s->footer_pages = snap_u64(r);
  if (r->err) { fprintf(stderr, "FAIL: footer truncated\n"); return 1; }
  uint8_t extra;
  if (fread(&extra, 1, 1, r->f) == 1) { fprintf(stderr, "FAIL: bytes after the footer\n"); return 1; }
  if (s->footer_pages != s->pages) {
    fprintf(stderr, "FAIL: footer page_count %llu != records %llu\n", (unsigned long long)s->footer_pages, (unsigned long long)s->pages);
    return 1;
  }
  return 0;
}

/* 0 when `path` is a well-formed version-1 snapshot; otherwise prints why and returns 1. */
static int snap_read_file(const char* path, snap_info_t* s) {
  memset(s, 0, sizeof(*s));
  snap_rd_t r;
  r.f = fopen(path, "rb");
  r.err = 0;
  if (r.f == NULL) { fprintf(stderr, "FAIL: no snapshot at %s\n", path); return 1; }
  const int rc = snap_parse(&r, s);
  fclose(r.f);
  return rc;
}
