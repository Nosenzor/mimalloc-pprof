from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import cast

import build_old_fork_latency_provenance as historical


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HistoricalProvenanceTests(unittest.TestCase):
    def test_replaces_only_pprof_artifact_identity_and_preserves_other_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_lib = root / "old.a"
            old_child = root / "old-child"
            old_lib.write_bytes(b"old library")
            old_child.write_bytes(b"old child")
            rows: list[dict[str, object]] = [
                {
                    "id": name,
                    "version": "before",
                    "source_sha": "1" * 40,
                    "source_tree_sha256": "2" * 64,
                    "library": f"{name}.a",
                    "library_sha256": "3" * 64,
                    "child_binary": f"{name}-child",
                    "child_binary_sha256": str(index) * 64,
                    "custom": {"preserve": True},
                }
                for index, name in enumerate(
                    ("tcmalloc", "jemalloc", "upstream-mimalloc", "bun-mimalloc"), 1
                )
            ]
            rows.append(
                {
                    "id": "mimalloc-pprof",
                    "version": "current",
                    "source_sha": "a" * 40,
                    "source_tree_sha256": "b" * 64,
                    "library": "current.a",
                    "library_sha256": "c" * 64,
                    "child_binary": "current-child",
                    "child_binary_sha256": "d" * 64,
                    "build_flags": ["Release", "MI_PPROF=ON"],
                }
            )
            provenance_path = root / "input.json"
            output_path = root / "result.json"
            original = {"schema_version": 1, "allocators": rows, "environment": {"x": "y"}}
            provenance_path.write_text(json.dumps(original), encoding="utf-8")
            smoke = historical.AdapterSmoke(
                "mimalloc-pprof", "e" * 40, "e" * 40, digest(old_lib), digest(old_child), 12, 128
            )
            artifact = historical.HistoricalArtifact(
                library=old_lib,
                child=old_child,
                library_sha256=digest(old_lib),
                child_sha256=digest(old_child),
                source_tree_sha256="f" * 64,
                commands=(("cmake", "--build"),),
                link_inputs=(historical.LinkInputRecord("archive", str(old_lib), digest(old_lib)),),
                link_identity=historical.LinkIdentity("mi_malloc", ("je_malloc",), ("libc",)),
                adapter_smoke=smoke,
            )
            historical.replace_pprof_row(provenance_path, "e" * 40, artifact, output_path)
            result = cast(dict[str, object], json.loads(output_path.read_text(encoding="utf-8")))
            new_rows = cast(list[dict[str, object]], result["allocators"])
            self.assertEqual(rows[:4], new_rows[:4])
            self.assertEqual("e" * 40, new_rows[4]["source_sha"])
            self.assertEqual(str(old_lib.resolve()), new_rows[4]["library"])
            self.assertEqual(digest(old_child), new_rows[4]["child_binary_sha256"])
            self.assertEqual(rows[4]["build_flags"], new_rows[4]["build_flags"])

    def test_rejects_abbreviated_and_non_lowercase_sha(self) -> None:
        for invalid in ("bedf926e", "A" * 40):
            with self.subTest(invalid=invalid), self.assertRaises(historical.HistoricalBuildError):
                historical.validate_sha(invalid)


if __name__ == "__main__":
    unittest.main()
