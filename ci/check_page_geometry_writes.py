#!/usr/bin/env python3
"""Reject writes to a page's geometry outside the places that own them (#573).

`mi_page_t::block_size` and `reserved` are labelled `const:` in types.h, but nothing enforced it.
#572 re-carved retired pages by writing both fields in a caller and forgetting to re-register the
page map over its new extent, so blocks past the old extent mapped to no page: a bug seen only in a
multi-threaded debug run. Now the only writers are

  - `mi_page_set_geometry` (src/page.c): changes an existing page, and owns the re-registration;
  - the arena, when it creates a page (`mi_arenas_page_alloc_fresh`) or invalidates the field of a
    page it is freeing.

Each permitted write carries the marker comment `page-geometry` on its line. Any other assignment to
`page->block_size` or `page->reserved` under src/ fails. (`heap-dump.c`, `theap.c` and the holes
report write fields of their own structs, not of a page, and are not matched: the pattern is the
identifier `page` only.)

    python3 ci/check_page_geometry_writes.py
    python3 ci/check_page_geometry_writes.py --selftest
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = "page-geometry"
# an assignment (=, +=, -=, ...; not ==) to page->block_size or page->reserved
WRITE_RE = re.compile(r"\bpage->(?:block_size|reserved)\s*(?:[-+*/|&^]|<<|>>)?=(?!=)")


def violations(name: str, text: str) -> list[str]:
    found: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        code = line.split("//", 1)[0]
        if WRITE_RE.search(code) and MARKER not in line:
            found.append(
                f"{name}:{number}: write to a page's geometry outside mi_page_set_geometry "
                f"(add the `{MARKER}` marker only at the arena's create/free sites): {line.strip()}"
            )
    return found


def sources() -> Iterable[Path]:
    return sorted((ROOT / "src").rglob("*.c"))


def check() -> int:
    bad: list[str] = []
    for path in sources():
        bad += violations(
            str(path.relative_to(ROOT)), path.read_text(encoding="utf-8", errors="replace")
        )
    for line in bad:
        print(line)
    if not bad:
        print("page geometry writes: only the owning sites write block_size / reserved of a page")
    return 1 if bad else 0


def selftest() -> int:
    cases = [
        ("page->block_size = 5;", 1),
        ("  page->reserved = (uint16_t)n;", 1),
        ("page->reserved += 1;", 1),
        ("page->block_size = 0;  // page-geometry: freeing", 0),
        ("if (page->block_size == 5) {}", 0),
        ("if (page->reserved >= 2) return;", 0),
        ("r->block_size = bs;", 0),
        ("area->reserved = page->reserved * bsize;", 0),
        ("// page->block_size = 5;", 0),
    ]
    failed = 0
    for text, expected in cases:
        got = len(violations("t.c", text))
        if got != expected:
            print(f"selftest FAIL: {text!r}: {got} violations, expected {expected}")
            failed += 1
    if failed == 0:
        print(f"selftest: {len(cases)} cases ok")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(selftest() if "--selftest" in sys.argv[1:] else check())
