/* ----------------------------------------------------------------------------
Copyright (c) 2026, the mimalloc-pprof contributors
This is free software; you can redistribute it and/or modify it under the
terms of the MIT license. A copy of the license can be found in the file
"LICENSE" at the root of this distribution.
-----------------------------------------------------------------------------*/
#pragma once
#ifndef MI_USDT_H
#define MI_USDT_H

/* #573 A5: USDT probes (static tracepoints) at the slow paths.

   Which question does this answer? "How often, and with what arguments, does the allocator do X
   in this process?" -- without rebuilding or patching it. With a build that has the probes,
   `perf stat -e 'sdt_mimalloc:*'` counts them and a bpftrace one-liner prints their arguments:

     bpftrace -e 'usdt:./app:mimalloc:page_fresh { @[arg0] = count(); }'

   A probe is a single NOP until a tracer attaches, survives inlining and LTO, and is put only in
   OUT-OF-LINE slow paths (a fresh page, a repurpose, an arena page allocation or free, a retired
   page's publish and release), never on the allocation or free fast paths, so
   `ci/check_fastpath_identity.py` is unaffected.

   Opt-in and OFF by default (CMake -DMI_USDT=ON, which needs <sys/sdt.h>: systemtap-sdt-dev on
   Debian and Ubuntu). Without it every MI_PROBE* is nothing. The arguments must be plain values
   that are cheap to compute and have no side effects: they are not evaluated when the probes are
   compiled out. */

#if defined(MI_USDT) && MI_USDT && defined(__linux__)
  #if defined(__has_include)
    #if __has_include(<sys/sdt.h>)
      #include <sys/sdt.h>
      #define MI_USDT_ENABLED  1
    #endif
  #endif
#endif
#ifndef MI_USDT_ENABLED
#define MI_USDT_ENABLED  0
#endif

#if MI_USDT_ENABLED
#define MI_PROBE0(name)                     DTRACE_PROBE(mimalloc, name)
#define MI_PROBE1(name, a)                  DTRACE_PROBE1(mimalloc, name, a)
#define MI_PROBE2(name, a, b)               DTRACE_PROBE2(mimalloc, name, a, b)
#define MI_PROBE3(name, a, b, c)            DTRACE_PROBE3(mimalloc, name, a, b, c)
#else
#define MI_PROBE0(name)                     ((void)0)
#define MI_PROBE1(name, a)                  ((void)0)
#define MI_PROBE2(name, a, b)               ((void)0)
#define MI_PROBE3(name, a, b, c)            ((void)0)
#endif

#endif  // MI_USDT_H
