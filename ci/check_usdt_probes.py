#!/usr/bin/env python3
"""The USDT probes (#573 A5) are emitted when asked for, and only then.

Compiles the single-translation-unit amalgamation source `src/static.c` twice and reads the ELF
notes with `readelf -n`:

  - with -DMI_USDT=1 every probe in EXPECTED must be present as a `stapsdt` note of provider
    `mimalloc` (they let `perf stat -e 'sdt_mimalloc:*'` and bpftrace count slow-path events without
    a rebuild);
  - without it there must be none (the default build is untouched, which
    ci/check_fastpath_identity.py also proves for the fast path).

Needs <sys/sdt.h> (systemtap-sdt-dev) and `readelf`; without either it says so and passes, unless
--require is given (CI installs the package and requires it).

    python3 ci/check_usdt_probes.py [--require]
    python3 ci/check_usdt_probes.py --selftest
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = frozenset(
    {
        "page_fresh",
        "page_repurpose",
        "arena_page_alloc",
        "arena_page_free",
        "retired_publish",
        "retired_release",
    }
)
NOTE_RE = re.compile(r"Provider:\s*(\S+)\s+Name:\s*(\S+)")


def probes(readelf_output: str) -> set[str]:
    """The probe names of provider `mimalloc` in `readelf -n` output."""
    return {name for provider, name in NOTE_RE.findall(readelf_output) if provider == "mimalloc"}


def compile_and_read(cc: str, readelf: str, defines: list[str], work: Path) -> set[str]:
    obj = work / "static.o"
    cmd = [
        cc,
        "-c",
        "-O2",
        "-I",
        str(ROOT / "include"),
        "-I",
        str(ROOT / "src"),
        "-DMI_STATIC_LIB",
        *defines,
        str(ROOT / "src/static.c"),
        "-o",
        str(obj),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return probes(
        subprocess.run([readelf, "-n", str(obj)], check=True, capture_output=True, text=True).stdout
    )


def has_sdt(cc: str) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "t.c"
        src.write_text("#include <sys/sdt.h>\nint main(void){return 0;}\n")
        return subprocess.run([cc, "-fsyntax-only", str(src)], capture_output=True).returncode == 0


def check(require: bool) -> int:
    cc, readelf = shutil.which("cc") or shutil.which("gcc"), shutil.which("readelf")
    if cc is None or readelf is None or not has_sdt(cc):
        print("skipped: needs a C compiler, readelf and <sys/sdt.h> (systemtap-sdt-dev)")
        return 1 if require else 0
    with tempfile.TemporaryDirectory() as tmp:
        on = compile_and_read(cc, readelf, ["-DMI_USDT=1"], Path(tmp))
        off = compile_and_read(cc, readelf, [], Path(tmp))
    problems = []
    if on != EXPECTED:
        problems.append(f"MI_USDT=1 emits {sorted(on)}, expected {sorted(EXPECTED)}")
    if off:
        problems.append(f"the default build emits probes: {sorted(off)}")
    for problem in problems:
        print("FAIL:", problem)
    if not problems:
        print(
            f"MI_USDT=1 emits {len(on)} probes ({', '.join(sorted(on))}); the default build emits none"
        )
    return 1 if problems else 0


def selftest() -> int:
    text = "  stapsdt   0x40  NT_STAPSDT\n    Provider: mimalloc\n    Name: page_fresh\n    Location: 0x1\n    Provider: other\n    Name: x\n"
    assert probes(text) == {"page_fresh"}
    assert probes("") == set()
    print("selftest: 2 cases ok")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    raise SystemExit(selftest() if "--selftest" in args else check("--require" in args))
