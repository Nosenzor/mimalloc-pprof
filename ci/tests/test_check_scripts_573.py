"""The #573 static checks: page-geometry writes and the struct size budget."""

from __future__ import annotations

import json
from pathlib import Path

import check_page_geometry_writes
import check_struct_sizes

ROOT = Path(__file__).resolve().parents[2]


def test_geometry_check_selftest_and_tree_are_clean() -> None:
    assert check_page_geometry_writes.selftest() == 0
    assert check_page_geometry_writes.check() == 0


def test_an_unmarked_write_is_reported_with_its_line() -> None:
    text = "void f(mi_page_t* page) {\n  page->reserved = 3;\n}\n"
    (found,) = check_page_geometry_writes.violations("x.c", text)
    assert found.startswith("x.c:2:")


def test_the_only_writers_are_the_owning_sites() -> None:
    marked = [
        path.name
        for path in sorted((ROOT / "src").rglob("*.c"))
        if check_page_geometry_writes.MARKER in path.read_text(errors="replace")
        and check_page_geometry_writes.WRITE_RE.search(path.read_text(errors="replace"))
    ]
    assert marked == ["arena.c", "page.c"]


def test_size_budget_file_matches_the_header_and_the_probe_configs_fit() -> None:
    assert check_struct_sizes.selftest() == 0
    budget = json.loads(check_struct_sizes.BUDGET.read_text())
    assert check_struct_sizes.header_budget() == budget["mi_theap_t_plus_padding"]
    assert check_struct_sizes.check() == 0


def test_usdt_probe_parsing_and_the_probe_list() -> None:
    import check_usdt_probes

    assert check_usdt_probes.selftest() == 0
    # every probe named in the check exists in the sources, and no source names one the check lacks
    sources = "".join(p.read_text(errors="replace") for p in (ROOT / "src").rglob("*.c"))
    used = set(__import__("re").findall(r"MI_PROBE\d\((\w+)", sources))
    assert used == set(check_usdt_probes.EXPECTED)
