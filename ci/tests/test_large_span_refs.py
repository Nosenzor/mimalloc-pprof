"""Typed fixture contract for #543's contextual scaling-reference link."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

import large_span_refs as refs

CANDIDATE = "b" * 40
OTHER_SOURCE = "c" * 40


def large_span(host: refs.HostMetadata | None = None) -> refs.LargeSpanRawArtifact:
    return refs.LargeSpanRawArtifact(
        schema_version=refs.LARGE_SPAN_SCHEMA,
        candidate_sha=CANDIDATE,
        host=host or refs.HostMetadata("fixture CPU", 8, 16, "isolated"),
    )


def response(allocator_id: str) -> refs.ScalingResponse:
    return refs.ScalingResponse(
        protocol_version="throughput-scaling-sparse-child-v2",
        metric_schema_version=refs.SCALING_SCHEMA,
        allocator_id=allocator_id,
        thread_count=8,
        alloc_calls=256,
        realloc_calls=0,
        free_calls=256,
        operation_count=512,
        checksum=1234,
        remote_free_calls=0,
        producer_fallback_frees=0,
        setup_ns=1,
        warmup_ns=0,
        elapsed_ns=1_000_000,
        teardown_ns=1,
        throughput_operations_per_second=512.0,
    )


def sample(pattern: str, allocator_id: str, source_sha: str, block: int) -> refs.ScalingRawSample:
    return refs.ScalingRawSample(
        metric_schema_version=refs.SCALING_SCHEMA,
        block_id=block,
        ordinal=block,
        pattern=pattern,
        thread_count=8,
        allocator_id=allocator_id,
        allocator_source_sha=source_sha,
        child_binary_sha256="d" * 64,
        operations_per_worker=512,
        reproduction_command="benchmark-scaling-run --diagnostic",
        peak_rss_bytes=1 << 20,
        diagnostic_peak_rss_bytes=1 << 20,
        live_requested_bytes_at_diagnostic_peak_rss=1 << 19,
        diagnostic_peak_live_requested_bytes=1 << 19,
        response=response(allocator_id),
    )


def scaling_raw() -> refs.ScalingRawArtifact:
    allocators = ("tcmalloc", "jemalloc", "mimalloc-pprof")
    builds = tuple(
        refs.AllocatorBuild(
            allocator_id=allocator,
            source_sha=CANDIDATE if allocator == "mimalloc-pprof" else OTHER_SOURCE,
        )
        for allocator in allocators
    )
    samples = tuple(
        sample(
            pattern,
            allocator,
            CANDIDATE if allocator == "mimalloc-pprof" else OTHER_SOURCE,
            block,
        )
        for pattern in refs.REFERENCE_PATTERNS
        for allocator in allocators
        for block in range(2)
    )
    return refs.ScalingRawArtifact(
        metric_schema_version=refs.SCALING_SCHEMA,
        status="diagnostic",
        run=refs.ScalingRunIdentity(source_sha=CANDIDATE),
        runner=refs.ScalingRunner("fixture CPU", 8, 16),
        topology=refs.ScalingTopology(8, 16, 16, "unrestricted"),
        diagnostic=refs.ScalingDiagnosticSelection(
            publishable=False,
            applies_to="mimalloc-pprof",
            patterns=refs.REFERENCE_PATTERNS,
            thread_points=(8,),
            blocks=2,
        ),
        allocators=builds,
        samples=samples,
    )


def write_artifacts(
    tmp_path: Path,
    *,
    large: refs.LargeSpanRawArtifact | None = None,
    scaling: refs.ScalingRawArtifact | None = None,
) -> tuple[Path, Path]:
    large_path = tmp_path / "large-span.json"
    scaling_path = tmp_path / "scaling-diagnostic.json"
    large_path.write_text(json.dumps(asdict(large or large_span())), encoding="utf-8")
    scaling_path.write_text(json.dumps(asdict(scaling or scaling_raw())), encoding="utf-8")
    return large_path, scaling_path


def test_link_carries_hash_host_match_coverage_and_non_equivalence(tmp_path: Path) -> None:
    large_path, scaling_path = write_artifacts(tmp_path)
    linked = refs.link_artifacts(large_path, scaling_path, CANDIDATE)

    assert linked.candidate_sha == CANDIDATE
    assert linked.scaling_artifact.path == str(scaling_path.resolve())
    assert linked.scaling_artifact.sha256 == hashlib.sha256(scaling_path.read_bytes()).hexdigest()
    assert linked.host_match.physical_cores == 8
    assert len(linked.raw_samples) == 3 * 3 * 2
    assert "not paired repetitions" in linked.interpretation
    relationships = {item.cell: item for item in linked.perf_ab_cell_relationships}
    assert "not equivalent" in relationships["random-large-bursty/8"].relationship
    assert relationships["small/8 (control)"].scaling_pattern is None


@pytest.mark.parametrize("sha", ["b" * 39, "B" * 40, "candidate"])
def test_candidate_sha_must_be_full_lowercase_commit(sha: str, tmp_path: Path) -> None:
    large_path, scaling_path = write_artifacts(tmp_path)
    with pytest.raises(refs.ReferenceLinkError, match="full lowercase 40-hex"):
        refs.link_artifacts(large_path, scaling_path, sha)


def test_host_topology_must_match(tmp_path: Path) -> None:
    bad_topology = replace(scaling_raw().topology, logical_cores=8)
    scaling = replace(scaling_raw(), topology=bad_topology)
    large_path, scaling_path = write_artifacts(tmp_path, scaling=scaling)
    with pytest.raises(refs.ReferenceLinkError, match="host mismatch for logical_cores"):
        refs.link_artifacts(large_path, scaling_path, CANDIDATE)


def test_candidate_sha_must_match_both_artifacts(tmp_path: Path) -> None:
    raw = scaling_raw()
    builds = tuple(
        replace(item, source_sha="e" * 40) if item.allocator_id == "mimalloc-pprof" else item
        for item in raw.allocators
    )
    scaling = replace(raw, allocators=builds)
    large_path, scaling_path = write_artifacts(tmp_path, scaling=scaling)
    with pytest.raises(refs.ReferenceLinkError, match="source SHA"):
        refs.link_artifacts(large_path, scaling_path, CANDIDATE)


def test_requires_all_reference_cells_and_raw_blocks(tmp_path: Path) -> None:
    raw = scaling_raw()
    samples = tuple(
        item
        for item in raw.samples
        if not (item.pattern == "larson" and item.allocator_id == "jemalloc")
    )
    large_path, scaling_path = write_artifacts(tmp_path, scaling=replace(raw, samples=samples))
    with pytest.raises(refs.ReferenceLinkError, match="raw sample coverage for larson/8 jemalloc"):
        refs.link_artifacts(large_path, scaling_path, CANDIDATE)


def test_rejects_shared_large_span_host(tmp_path: Path) -> None:
    shared_host = refs.HostMetadata("fixture CPU", 8, 16, "shared")
    large_path, scaling_path = write_artifacts(tmp_path, large=large_span(shared_host))
    with pytest.raises(refs.ReferenceLinkError, match="isolated host"):
        refs.link_artifacts(large_path, scaling_path, CANDIDATE)


def test_rejects_missing_allocator_reference(tmp_path: Path) -> None:
    raw = scaling_raw()
    builds = tuple(item for item in raw.allocators if item.allocator_id != "tcmalloc")
    large_path, scaling_path = write_artifacts(tmp_path, scaling=replace(raw, allocators=builds))
    with pytest.raises(refs.ReferenceLinkError, match="missing tcmalloc"):
        refs.link_artifacts(large_path, scaling_path, CANDIDATE)
