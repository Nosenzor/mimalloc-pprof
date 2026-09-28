#!/usr/bin/env python3
"""Build a historical mimalloc-pprof child for the #543 latency comparison.

The old source checkout is detached and temporary. The current benchmark-suite
and adapter are used to compile the child, while the old checkout supplies both
the allocator archive and headers. Only the mimalloc-pprof row in the producer
provenance is replaced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import build_benchmark_allocators as builder


class HistoricalBuildError(RuntimeError):
    """The requested historical allocator could not be built safely."""


@dataclass(frozen=True)
class BuildRequest:
    old_sha: str
    provenance_path: Path
    build_root: Path
    output_dir: Path


@dataclass(frozen=True)
class HistoricalArtifact:
    library: Path
    child: Path
    library_sha256: str
    child_sha256: str
    source_tree_sha256: str
    commands: tuple[tuple[str, ...], ...]
    link_inputs: tuple[LinkInputRecord, ...]
    link_identity: LinkIdentity
    adapter_smoke: AdapterSmoke


@dataclass(frozen=True)
class LinkInputRecord:
    kind: str
    path: str | None = None
    sha256: str | None = None
    value: str | None = None


@dataclass(frozen=True)
class LinkIdentity:
    expected_allocator_symbol: str
    forbidden_allocator_symbols_absent: tuple[str, ...]
    needed_libraries: tuple[str, ...]


@dataclass(frozen=True)
class AdapterSmoke:
    allocator_id: str
    allocator_version: str
    source_sha: str
    library_sha256: str
    child_binary_sha256: str
    checksum: int
    usable_size: int


def _positive_int(value: object, label: str) -> int:
    if not isinstance(value, int) or value <= 0:
        raise HistoricalBuildError(f"adapter smoke {label} is invalid")
    return value


def validate_sha(value: str) -> None:
    if not builder.is_hex_digest(value, builder.GIT_SHA_LENGTH):
        raise HistoricalBuildError("old SHA must be a full lowercase 40-character commit")


def _json_object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise HistoricalBuildError(f"{label} must be a JSON object")
    raw = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in raw):
        raise HistoricalBuildError(f"{label} keys must be strings")
    return cast(Mapping[str, object], value)


def _run(command: list[str], *, cwd: Path) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=builder.command_environment(),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _resolve_commit(old_sha: str, repo: Path) -> str:
    validate_sha(old_sha)
    resolved = _run(["git", "rev-parse", f"{old_sha}^{{commit}}"], cwd=repo)
    if resolved != old_sha:
        raise HistoricalBuildError("Git did not resolve the supplied SHA exactly")
    return resolved


def _source_tree_hash(old_sha: str, repo: Path) -> str:
    result = subprocess.run(
        ["git", "archive", "--format=tar", old_sha],
        cwd=repo,
        env=builder.command_environment(),
        check=True,
        capture_output=True,
    )
    return hashlib.sha256(result.stdout).hexdigest()


def _build_historical(
    old_sha: str,
    worktree: Path,
    build_root: Path,
    output_dir: Path,
    diagnostic_cppdefs: tuple[str, ...],
) -> HistoricalArtifact:
    repo = builder.repository_root()
    lock_records = builder.read_lockfile(builder.default_lockfile())
    record = next(item for item in lock_records if item.get("id") == "mimalloc-pprof")
    build_spec = builder.require_mapping(record.get("build"), "mimalloc-pprof.build")
    templates = builder.require_commands(build_spec.get("commands"), "build.commands")
    commands = builder.with_fork_cppdefs(
        "mimalloc-pprof",
        [builder.expand_command(item, worktree, build_root / "cmake", 1) for item in templates],
        diagnostic_cppdefs,
    )
    build_root.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    logs = build_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "old-fork-cmake.log").open("w", encoding="utf-8") as log:
        for command in commands:
            log.write("$ " + " ".join(command) + "\n")
            log.flush()
            subprocess.run(
                command,
                cwd=worktree,
                env=builder.command_environment(),
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
    library_in_build = builder.find_primary_library(record, worktree, build_root / "cmake")
    library = output_dir / "old-libmimalloc.a"
    shutil.copyfile(library_in_build, library)
    library_sha = builder.sha256_file(library)
    source_tree_sha = _source_tree_hash(old_sha, repo)
    child, child_commands, link_inputs, link_identity = builder.build_child(
        record,
        worktree,
        build_root / "cmake",
        library,
        build_root,
        logs,
        old_sha,
    )
    durable_child = output_dir / "benchmark-child-old-fork"
    shutil.copyfile(child, durable_child)
    durable_child.chmod(0o755)
    child_sha = builder.sha256_file(durable_child)
    smoke_raw = builder.run_adapter_smoke(
        "mimalloc-pprof", old_sha, old_sha, library_sha, durable_child
    )
    smoke = AdapterSmoke(
        allocator_id=str(smoke_raw["allocator_id"]),
        allocator_version=str(smoke_raw["allocator_version"]),
        source_sha=str(smoke_raw["source_sha"]),
        library_sha256=str(smoke_raw["library_sha256"]),
        child_binary_sha256=str(smoke_raw["child_binary_sha256"]),
        checksum=_positive_int(smoke_raw.get("checksum"), "checksum"),
        usable_size=_positive_int(smoke_raw.get("usable_size"), "usable_size"),
    )
    all_commands = tuple(tuple(command) for command in [*commands, *child_commands])
    encoded_inputs = tuple(
        LinkInputRecord(
            kind=str(row.get("kind", "")),
            path=str(row["path"]) if "path" in row else None,
            sha256=str(row["sha256"]) if "sha256" in row else None,
            value=str(row["value"]) if "value" in row else None,
        )
        for row in link_inputs
    )
    identity_object = _json_object(link_identity, "link identity")
    identity_symbols = identity_object.get("forbidden_allocator_symbols_absent", [])
    needed_libraries = identity_object.get("needed_libraries", [])
    if not isinstance(identity_symbols, list) or not all(
        isinstance(item, str) for item in cast(list[object], identity_symbols)
    ):
        raise HistoricalBuildError("link identity forbidden symbols are invalid")
    if not isinstance(needed_libraries, list) or not all(
        isinstance(item, str) for item in cast(list[object], needed_libraries)
    ):
        raise HistoricalBuildError("link identity needed libraries are invalid")
    identity = LinkIdentity(
        expected_allocator_symbol=str(identity_object.get("expected_allocator_symbol", "")),
        forbidden_allocator_symbols_absent=tuple(cast(list[str], identity_symbols)),
        needed_libraries=tuple(cast(list[str], needed_libraries)),
    )
    return HistoricalArtifact(
        library=library,
        child=durable_child,
        library_sha256=library_sha,
        child_sha256=child_sha,
        source_tree_sha256=source_tree_sha,
        commands=all_commands,
        link_inputs=encoded_inputs,
        link_identity=identity,
        adapter_smoke=smoke,
    )


def validate_current_provenance(provenance_path: Path, build_root: Path) -> Mapping[str, object]:
    root = _json_object(json.loads(provenance_path.read_text(encoding="utf-8")), "provenance")
    expected_lock_hash = builder.sha256_file(builder.default_lockfile())
    if root.get("schema_version") != 1 or root.get("lockfile_sha256") != expected_lock_hash:
        raise HistoricalBuildError("current provenance schema or allocator lock hash is invalid")
    rows_value = root.get("allocators")
    if not isinstance(rows_value, list):
        raise HistoricalBuildError("current provenance allocators must be a list")
    rows = [_json_object(row, "allocator row") for row in cast(list[object], rows_value)]
    if len(rows) != 5 or tuple(row.get("id") for row in rows) != builder.EXPECTED_IDS:
        raise HistoricalBuildError("current provenance must contain the five locked allocator rows")
    current_pprof = rows[-1]
    current_sha = builder.checkout_commit()
    if current_pprof.get("source_sha") != current_sha:
        raise HistoricalBuildError(
            "current pprof provenance source SHA does not match the checked-out HEAD"
        )
    environment_value = root.get("environment")
    if not isinstance(environment_value, dict):
        raise HistoricalBuildError("current provenance environment must be an object")
    saved_environment = _json_object(cast(object, environment_value), "provenance environment")
    runtime_environment = builder.command_environment()
    if any(saved_environment.get(key) != value for key, value in runtime_environment.items()):
        raise HistoricalBuildError(
            "current producer environment differs from this build environment; refusing to "
            "claim matching historical build flags"
        )
    resolved_root = build_root.resolve(strict=True)
    for row in rows:
        for path_key, hash_key in (
            ("library", "library_sha256"),
            ("child_binary", "child_binary_sha256"),
        ):
            raw_path = row.get(path_key)
            raw_hash = row.get(hash_key)
            if not isinstance(raw_path, str) or not isinstance(raw_hash, str):
                raise HistoricalBuildError(f"current provenance has invalid {path_key} identity")
            artifact_path = Path(raw_path).resolve(strict=True)
            if not artifact_path.is_relative_to(resolved_root):
                raise HistoricalBuildError(f"current {path_key} is outside the supplied build root")
            if builder.sha256_file(artifact_path) != raw_hash:
                raise HistoricalBuildError(f"current {path_key} hash mismatch: {artifact_path}")
    return root


def replace_pprof_row(
    provenance_path: Path, old_sha: str, artifact: HistoricalArtifact, output_path: Path
) -> None:
    # JSON dictionaries exist only at this serialization boundary; all build
    # state and inputs above use typed dataclasses.
    root = _json_object(json.loads(provenance_path.read_text(encoding="utf-8")), "provenance")
    rows_value = root.get("allocators")
    if not isinstance(rows_value, list):
        raise HistoricalBuildError("provenance allocators must be a list")
    rows = cast(list[object], rows_value)
    matches = [_json_object(row, "allocator row") for row in rows]
    pprof = [row for row in matches if row.get("id") == "mimalloc-pprof"]
    if len(pprof) != 1 or len(matches) != 5:
        raise HistoricalBuildError("provenance must contain exactly five rows and one pprof row")
    current = pprof[0]
    old_row = dict(current)
    old_row.update(
        {
            "version": old_sha,
            "source_sha": old_sha,
            "source_tree_sha256": artifact.source_tree_sha256,
            "library": str(artifact.library.resolve()),
            "library_sha256": artifact.library_sha256,
            "child_binary": str(artifact.child.resolve()),
            "child_binary_sha256": artifact.child_sha256,
            "commands": [list(command) for command in artifact.commands],
            "link_inputs": [
                {
                    key: value
                    for key, value in (
                        ("kind", item.kind),
                        ("path", item.path),
                        ("sha256", item.sha256),
                        ("value", item.value),
                    )
                    if value is not None
                }
                for item in artifact.link_inputs
            ],
            "link_identity": {
                "expected_allocator_symbol": artifact.link_identity.expected_allocator_symbol,
                "forbidden_allocator_symbols_absent": list(
                    artifact.link_identity.forbidden_allocator_symbols_absent
                ),
                "needed_libraries": list(artifact.link_identity.needed_libraries),
            },
            "adapter_smoke": {
                "allocator_id": artifact.adapter_smoke.allocator_id,
                "allocator_version": artifact.adapter_smoke.allocator_version,
                "source_sha": artifact.adapter_smoke.source_sha,
                "library_sha256": artifact.adapter_smoke.library_sha256,
                "child_binary_sha256": artifact.adapter_smoke.child_binary_sha256,
                "checksum": artifact.adapter_smoke.checksum,
                "usable_size": artifact.adapter_smoke.usable_size,
            },
        }
    )
    output_root = cast(dict[str, object], root)
    output_root["allocators"] = [
        old_row if row.get("id") == "mimalloc-pprof" else row for row in matches
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output_root, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def collect(request: BuildRequest) -> Path:
    repo = builder.repository_root()
    _resolve_commit(request.old_sha, repo)
    output = request.output_dir / "allocator-provenance.json"
    if request.provenance_path.resolve() == output.resolve():
        raise HistoricalBuildError("output must not overwrite the input provenance")
    if request.output_dir.exists() and any(request.output_dir.iterdir()):
        raise HistoricalBuildError("output directory must be empty")
    current = validate_current_provenance(request.provenance_path, request.build_root)
    diagnostic_value = current.get("diagnostic_cppdefs", [])
    if not isinstance(diagnostic_value, list) or not all(
        isinstance(value, str) for value in cast(list[object], diagnostic_value)
    ):
        raise HistoricalBuildError("diagnostic_cppdefs must be a string list")
    diagnostic_cppdefs = tuple(cast(list[str], diagnostic_value))
    request.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="old-fork-worktree-") as temporary:
        temp = Path(temporary)
        checkout = temp / "source"
        _run(["git", "worktree", "add", "--detach", str(checkout), request.old_sha], cwd=repo)
        try:
            artifact = _build_historical(
                request.old_sha,
                checkout,
                request.output_dir / "build",
                request.output_dir,
                diagnostic_cppdefs,
            )
        finally:
            _run(["git", "worktree", "remove", "--force", str(checkout)], cwd=repo)
    replace_pprof_row(request.provenance_path, request.old_sha, artifact, output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-sha", required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        output = collect(
            BuildRequest(args.old_sha, args.provenance, args.build_root, args.output_dir)
        )
    except (
        HistoricalBuildError,
        builder.ArchiveError,
        builder.LockfileError,
        subprocess.CalledProcessError,
    ) as error:
        parser.error(str(error))
    print(f"PASS historical allocator provenance written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
