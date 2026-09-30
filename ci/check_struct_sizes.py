#!/usr/bin/env python3
"""Per-configuration size budget for the structs at a size-class edge (#573).

`mi_theap_t` (plus block padding) is allocated from the meta-allocator and sits just under its
8 KiB size class; `mi_tld_t` sits at the 512-byte class. Growing either by a few bytes made CI ASan
`test-resident-first-churn` flaky in #572, and the only warning was a stale comment. The compile-time
`MI_THEAP_META_MAX_SIZE` assert in types.h covers the build being compiled; this script covers the
configurations CI compiles (padding, secure, debug, and every observability subsystem on), compares
them against `ci/struct_size_budget.json` (the ratchet: raise it deliberately), and checks the
budget file and the header agree.

To add a field to `mi_theap_t` or `mi_tld_t`: (1) run this script; (2) if it fails, the field does
not fit -- put the state in the other struct, or shrink something; (3) only then raise the budget,
with the size class checked, in the same commit as the header constant.

    python3 ci/check_struct_sizes.py
    python3 ci/check_struct_sizes.py --selftest
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUDGET = ROOT / "ci/struct_size_budget.json"
TYPES_H = ROOT / "include/mimalloc/types.h"

CONFIGS: dict[str, list[str]] = {
    "default": [],
    "debug3": ["-DMI_DEBUG=3"],
    "secure4": ["-DMI_SECURE=4"],
    "no-padding": ["-DMI_PADDING=0"],
    "everything": [
        "-DMI_PPROF=1", "-DMI_MEMEVT=1", "-DMI_DIAGNOSTICS=1", "-DMI_DHAT=1", "-DMI_OWNER_GATE=1", "-DMI_DEBUG=3",
    ],
}  # fmt: skip

PROBE = r"""
#include "mimalloc.h"
#include "mimalloc/internal.h"
#include <stdio.h>
int main(void) {
  printf("%zu %zu\n", sizeof(mi_theap_t) + (size_t)MI_PADDING_SIZE, sizeof(mi_tld_t));
  return 0;
}
"""


def header_budget() -> int:
    match = re.search(r"#define\s+MI_THEAP_META_MAX_SIZE\s+\((\d+)\)", TYPES_H.read_text())
    if match is None:
        raise SystemExit("MI_THEAP_META_MAX_SIZE not found in types.h")
    return int(match.group(1))


def measure(cc: str, defines: list[str], work: Path) -> tuple[int, int]:
    src, exe = work / "probe.c", work / "probe"
    src.write_text(PROBE)
    cmd = [
        cc,
        "-I",
        str(ROOT / "include"),
        "-I",
        str(ROOT / "src"),
        "-DMI_STATIC_LIB",
        *defines,
        str(src),
        "-o",
        str(exe),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    theap, tld = subprocess.run(
        [str(exe)], check=True, capture_output=True, text=True
    ).stdout.split()
    return int(theap), int(tld)


def judge(sizes: dict[str, tuple[int, int]], budget: dict[str, int], header: int) -> list[str]:
    problems: list[str] = []
    if header != budget["mi_theap_t_plus_padding"]:
        problems.append(
            f"MI_THEAP_META_MAX_SIZE ({header}) in types.h differs from mi_theap_t_plus_padding "
            f"({budget['mi_theap_t_plus_padding']}) in ci/struct_size_budget.json"
        )
    for name, (theap, tld) in sizes.items():
        if theap > budget["mi_theap_t_plus_padding"]:
            problems.append(
                f"{name}: sizeof(mi_theap_t) + padding = {theap} > budget {budget['mi_theap_t_plus_padding']}"
            )
        if tld > budget["mi_tld_t"]:
            problems.append(f"{name}: sizeof(mi_tld_t) = {tld} > budget {budget['mi_tld_t']}")
    return problems


def check() -> int:
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        print("no C compiler on PATH: skipped")
        return 0
    budget = json.loads(BUDGET.read_text())
    with tempfile.TemporaryDirectory() as tmp:
        sizes = {name: measure(cc, defines, Path(tmp)) for name, defines in CONFIGS.items()}
    for name, (theap, tld) in sizes.items():
        print(
            f"{name:<11} mi_theap_t+padding {theap:>5} / {budget['mi_theap_t_plus_padding']}   mi_tld_t {tld:>4} / {budget['mi_tld_t']}"
        )
    problems = judge(sizes, budget, header_budget())
    for problem in problems:
        print("FAIL:", problem)
    return 1 if problems else 0


def selftest() -> int:
    budget = {"mi_theap_t_plus_padding": 8176, "mi_tld_t": 512}
    assert judge({"a": (8176, 512)}, budget, 8176) == []
    assert len(judge({"a": (8177, 512)}, budget, 8176)) == 1
    assert len(judge({"a": (8000, 513)}, budget, 8176)) == 1
    assert len(judge({"a": (8000, 500)}, budget, 8200)) == 1
    assert len(judge({"a": (9000, 600)}, budget, 8200)) == 3
    print("selftest: 5 cases ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest() if "--selftest" in sys.argv[1:] else check())
