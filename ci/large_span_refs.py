#!/usr/bin/env python3
"""Link same-host scaling diagnostic references to a #543 large-span run.

The linked jemalloc/TCMalloc rows are contextual measurements from the scaling
harness. They are not paired with the #543 old-fork/candidate repetitions and
do not claim identical allocation traces.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

SCALING_SCHEMA = "throughput-scaling-sparse-v2"
LARGE_SPAN_SCHEMA = "large-span-diagnostic-v1"
ALLOCATORS = ("tcmalloc", "jemalloc")
REFERENCE_PATTERNS = ("random-large", "large-class-persistent", "larson")
WORKERS = 8
SHA40 = re.compile(r"^[0-9a-f]{40}$")


class ReferenceLinkError(ValueError):
    """Input artifacts cannot safely be linked as contextual references."""


@dataclass(frozen=True)
class HostMetadata:
    cpu_model: str
    physical_cores: int
    logical_cores: int
    isolation: str


@dataclass(frozen=True)
class LargeSpanRawArtifact:
    schema_version: str
    candidate_sha: str
    host: HostMetadata


@dataclass(frozen=True)
class ScalingTopology:
    physical_cores: int
    logical_cores: int
    allowed_logical_cpus: int
    affinity_policy: str


@dataclass(frozen=True)
class ScalingRunner:
    cpu_model: str
    physical_cores: int
    logical_cores: int


@dataclass(frozen=True)
class ScalingDiagnosticSelection:
    publishable: bool
    applies_to: str
    patterns: tuple[str, ...]
    thread_points: tuple[int, ...]
    blocks: int


@dataclass(frozen=True)
class ScalingRunIdentity:
    source_sha: str


@dataclass(frozen=True)
class AllocatorBuild:
    allocator_id: str
    source_sha: str


@dataclass(frozen=True)
class ScalingSizeHistogramBucket:
    lower_inclusive_bytes: int
    upper_inclusive_bytes: int
    allocation_count: int


@dataclass(frozen=True)
class ScalingRssPhase:
    phase: str
    samples: int
    first_offset_ns: int
    last_offset_ns: int
    peak_rss_bytes: int
    live_requested_bytes_at_peak_rss: int
    last_rss_bytes: int


@dataclass(frozen=True)
class ScalingResponse:
    protocol_version: str
    metric_schema_version: str
    allocator_id: str
    thread_count: int
    alloc_calls: int
    realloc_calls: int
    free_calls: int
    operation_count: int
    checksum: int
    remote_free_calls: int
    producer_fallback_frees: int
    setup_ns: int
    warmup_ns: int
    elapsed_ns: int
    teardown_ns: int
    throughput_operations_per_second: float
    worker_seeds: tuple[int, ...] = ()
    size_histogram: tuple[ScalingSizeHistogramBucket, ...] = ()
    peak_live_requested_bytes: int = 0
    baseline_rss_bytes: int = 0
    post_drain_offsets_ns: tuple[int, ...] = ()
    post_drain_rss_bytes: tuple[int, ...] = ()


@dataclass(frozen=True)
class ScalingRawSample:
    metric_schema_version: str
    block_id: int
    ordinal: int
    pattern: str
    thread_count: int
    allocator_id: str
    allocator_source_sha: str
    child_binary_sha256: str
    operations_per_worker: int
    reproduction_command: str
    peak_rss_bytes: int
    diagnostic_peak_rss_bytes: int
    live_requested_bytes_at_diagnostic_peak_rss: int
    diagnostic_peak_live_requested_bytes: int
    response: ScalingResponse
    diagnostic_rss_phases: tuple[ScalingRssPhase, ...] = ()


@dataclass(frozen=True)
class ScalingRawArtifact:
    metric_schema_version: str
    status: str
    run: ScalingRunIdentity
    runner: ScalingRunner
    topology: ScalingTopology
    diagnostic: ScalingDiagnosticSelection
    allocators: tuple[AllocatorBuild, ...]
    samples: tuple[ScalingRawSample, ...]


@dataclass(frozen=True)
class HostMatch:
    cpu_model: str
    physical_cores: int
    logical_cores: int
    large_span_isolation: str
    scaling_affinity_policy: str
    scaling_allowed_logical_cpus: int
    match_basis: str


@dataclass(frozen=True)
class ScalingArtifactReference:
    path: str
    sha256: str


@dataclass(frozen=True)
class PerfABCellRelationship:
    cell: str
    scaling_pattern: str | None
    relationship: str


@dataclass(frozen=True)
class ScalingReferenceCell:
    pattern: str
    workers: int
    allocators: tuple[str, ...]
    relationship: str


@dataclass(frozen=True)
class LinkedArtifact:
    schema_version: str
    candidate_sha: str
    large_span_artifact: str
    scaling_artifact: ScalingArtifactReference
    host_match: HostMatch
    interpretation: str
    perf_ab_cell_relationships: tuple[PerfABCellRelationship, ...]
    scaling_reference_cells: tuple[ScalingReferenceCell, ...]
    raw_samples: tuple[ScalingRawSample, ...]


PERF_AB_RELATIONSHIPS = (
    PerfABCellRelationship(
        "size 128 KiB/1 (#422)",
        None,
        "not equivalent; no matching scaling diagnostic cell",
    ),
    PerfABCellRelationship(
        "random-large-bursty/8",
        "random-large",
        "not equivalent; scaling random-large has no 300 ms burst pause and uses its own calibrated stream",
    ),
    PerfABCellRelationship(
        "large-class/8",
        "large-class-persistent",
        "not equivalent; similar requested-size and live-slot shape, but different operation plan and trace",
    ),
    PerfABCellRelationship(
        "small/8 (control)",
        None,
        "not equivalent; no matching scaling diagnostic cell",
    ),
    PerfABCellRelationship(
        "larson/8",
        "larson",
        "not equivalent; similar workload shape, but separate implementation, operation plan, and trace",
    ),
)


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ReferenceLinkError(message)


def _object(value: object, message: str) -> Mapping[str, object]:
    _need(isinstance(value, Mapping), message)
    return cast(Mapping[str, object], value)


def _array(value: object, message: str) -> tuple[object, ...]:
    _need(isinstance(value, list), message)
    return tuple(cast(list[object], value))


def _text(value: object, label: str) -> str:
    _need(isinstance(value, str) and bool(value), f"{label} must be a non-empty string")
    return cast(str, value)


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    _need(type(value) is int and value >= minimum, f"{label} must be an integer >= {minimum}")
    return cast(int, value)


def _boolean(value: object, label: str) -> bool:
    _need(type(value) is bool, f"{label} must be a boolean")
    return cast(bool, value)


def _full_sha(value: object, label: str) -> str:
    _need(
        isinstance(value, str) and SHA40.fullmatch(value) is not None,
        f"{label} must be a full lowercase 40-hex commit SHA",
    )
    return cast(str, value)


def _read_json(path: Path) -> tuple[Mapping[str, object], bytes]:
    try:
        raw_bytes = path.read_bytes()
        value: object = json.loads(raw_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReferenceLinkError(f"cannot read JSON artifact {path}: {error}") from error
    return _object(value, f"artifact {path} must contain a JSON object"), raw_bytes


def _parse_host(value: object) -> HostMetadata:
    raw = _object(value, "#543 artifact is missing host metadata")
    return HostMetadata(
        cpu_model=_text(raw.get("cpu_model"), "#543 host cpu_model"),
        physical_cores=_integer(raw.get("physical_cores"), "#543 host physical_cores", minimum=1),
        logical_cores=_integer(raw.get("logical_cores"), "#543 host logical_cores", minimum=1),
        isolation=_text(raw.get("isolation"), "#543 host isolation"),
    )


def _parse_large_span_artifact(raw: Mapping[str, object]) -> LargeSpanRawArtifact:
    return LargeSpanRawArtifact(
        schema_version=_text(raw.get("schema_version"), "#543 schema_version"),
        candidate_sha=_full_sha(raw.get("candidate_sha"), "#543 candidate SHA"),
        host=_parse_host(raw.get("host")),
    )


def _parse_runner(value: object) -> ScalingRunner:
    raw = _object(value, "scaling raw run is missing runner metadata")
    return ScalingRunner(
        cpu_model=_text(raw.get("cpu_model"), "scaling runner cpu_model"),
        physical_cores=_integer(
            raw.get("physical_cores"), "scaling runner physical_cores", minimum=1
        ),
        logical_cores=_integer(raw.get("logical_cores"), "scaling runner logical_cores", minimum=1),
    )


def _parse_topology(value: object) -> ScalingTopology:
    raw = _object(value, "scaling raw run is missing topology")
    return ScalingTopology(
        physical_cores=_integer(
            raw.get("physical_cores"), "scaling topology physical_cores", minimum=1
        ),
        logical_cores=_integer(
            raw.get("logical_cores"), "scaling topology logical_cores", minimum=1
        ),
        allowed_logical_cpus=_integer(
            raw.get("allowed_logical_cpus"), "scaling allowed_logical_cpus", minimum=1
        ),
        affinity_policy=_text(raw.get("affinity_policy"), "scaling affinity_policy"),
    )


def _parse_selection(value: object) -> ScalingDiagnosticSelection:
    raw = _object(value, "invalid scaling diagnostic metadata")
    patterns = tuple(
        _text(item, "diagnostic pattern")
        for item in _array(raw.get("patterns"), "diagnostic patterns must be an array")
    )
    points = tuple(
        _integer(item, "diagnostic thread point", minimum=1)
        for item in _array(raw.get("thread_points"), "diagnostic thread_points must be an array")
    )
    return ScalingDiagnosticSelection(
        publishable=_boolean(raw.get("publishable"), "diagnostic publishable"),
        applies_to=_text(raw.get("applies_to"), "diagnostic applies_to"),
        patterns=patterns,
        thread_points=points,
        blocks=_integer(raw.get("blocks"), "diagnostic blocks", minimum=1),
    )


def _parse_builds(value: object) -> tuple[AllocatorBuild, ...]:
    builds: list[AllocatorBuild] = []
    for item in _array(value, "scaling artifact allocator provenance must be an array"):
        raw = _object(item, "scaling artifact has invalid allocator provenance row")
        builds.append(
            AllocatorBuild(
                allocator_id=_text(raw.get("allocator_id"), "allocator provenance ID"),
                source_sha=_full_sha(raw.get("source_sha"), "allocator source SHA"),
            )
        )
    return tuple(builds)


def _parse_response(value: object) -> ScalingResponse:
    raw = _object(value, "scaling sample response must be an object")
    worker_seeds = tuple(
        _integer(item, "response worker seed")
        for item in _array(raw.get("worker_seeds", []), "response worker_seeds must be an array")
    )
    size_histogram = tuple(
        ScalingSizeHistogramBucket(
            lower_inclusive_bytes=_integer(
                _object(item, "invalid response histogram bucket").get("lower_inclusive_bytes"),
                "histogram lower bound",
            ),
            upper_inclusive_bytes=_integer(
                _object(item, "invalid response histogram bucket").get("upper_inclusive_bytes"),
                "histogram upper bound",
            ),
            allocation_count=_integer(
                _object(item, "invalid response histogram bucket").get("allocation_count"),
                "histogram allocation_count",
            ),
        )
        for item in _array(
            raw.get("size_histogram", []), "response size_histogram must be an array"
        )
    )
    offsets = tuple(
        _integer(item, "post-drain offset")
        for item in _array(
            raw.get("post_drain_offsets_ns", []), "post_drain_offsets_ns must be an array"
        )
    )
    rss_values = tuple(
        _integer(item, "post-drain RSS")
        for item in _array(
            raw.get("post_drain_rss_bytes", []), "post_drain_rss_bytes must be an array"
        )
    )
    throughput = raw.get("throughput_operations_per_second")
    _need(isinstance(throughput, (int, float)), "response throughput must be numeric")
    return ScalingResponse(
        protocol_version=_text(raw.get("protocol_version"), "response protocol_version"),
        metric_schema_version=_text(
            raw.get("metric_schema_version"), "response metric_schema_version"
        ),
        allocator_id=_text(raw.get("allocator_id"), "response allocator_id"),
        thread_count=_integer(raw.get("thread_count"), "response thread_count", minimum=1),
        alloc_calls=_integer(raw.get("alloc_calls"), "response alloc_calls"),
        realloc_calls=_integer(raw.get("realloc_calls"), "response realloc_calls"),
        free_calls=_integer(raw.get("free_calls"), "response free_calls"),
        operation_count=_integer(raw.get("operation_count"), "response operation_count"),
        checksum=_integer(raw.get("checksum"), "response checksum"),
        remote_free_calls=_integer(raw.get("remote_free_calls"), "response remote_free_calls"),
        producer_fallback_frees=_integer(
            raw.get("producer_fallback_frees"), "response producer_fallback_frees"
        ),
        setup_ns=_integer(raw.get("setup_ns"), "response setup_ns"),
        warmup_ns=_integer(raw.get("warmup_ns"), "response warmup_ns"),
        elapsed_ns=_integer(raw.get("elapsed_ns"), "response elapsed_ns"),
        teardown_ns=_integer(raw.get("teardown_ns"), "response teardown_ns"),
        throughput_operations_per_second=float(cast(int | float, throughput)),
        worker_seeds=worker_seeds,
        size_histogram=size_histogram,
        peak_live_requested_bytes=_integer(
            raw.get("peak_live_requested_bytes", 0), "response peak_live_requested_bytes"
        ),
        baseline_rss_bytes=_integer(
            raw.get("baseline_rss_bytes", 0), "response baseline_rss_bytes"
        ),
        post_drain_offsets_ns=offsets,
        post_drain_rss_bytes=rss_values,
    )


def _parse_sample(value: object) -> ScalingRawSample:
    raw = _object(value, "scaling artifact contains a non-object raw sample")
    diagnostic_phases = tuple(
        ScalingRssPhase(
            phase=_text(phase.get("phase"), "RSS phase name"),
            samples=_integer(phase.get("samples"), "RSS phase sample count"),
            first_offset_ns=_integer(phase.get("first_offset_ns"), "RSS phase first offset"),
            last_offset_ns=_integer(phase.get("last_offset_ns"), "RSS phase last offset"),
            peak_rss_bytes=_integer(phase.get("peak_rss_bytes"), "RSS phase peak"),
            live_requested_bytes_at_peak_rss=_integer(
                phase.get("live_requested_bytes_at_peak_rss"), "RSS phase live bytes"
            ),
            last_rss_bytes=_integer(phase.get("last_rss_bytes"), "RSS phase last RSS"),
        )
        for phase_value in _array(
            raw.get("diagnostic_rss_phases", []), "sample diagnostic_rss_phases must be an array"
        )
        for phase in (_object(phase_value, "invalid diagnostic RSS phase"),)
    )
    return ScalingRawSample(
        metric_schema_version=_text(
            raw.get("metric_schema_version"), "sample metric_schema_version"
        ),
        block_id=_integer(raw.get("block_id"), "sample block_id"),
        ordinal=_integer(raw.get("ordinal"), "sample ordinal"),
        pattern=_text(raw.get("pattern"), "sample pattern"),
        thread_count=_integer(raw.get("thread_count"), "sample thread_count", minimum=1),
        allocator_id=_text(raw.get("allocator_id"), "sample allocator_id"),
        allocator_source_sha=_full_sha(
            raw.get("allocator_source_sha"), "sample allocator_source_sha"
        ),
        child_binary_sha256=_text(raw.get("child_binary_sha256"), "sample child_binary_sha256"),
        operations_per_worker=_integer(
            raw.get("operations_per_worker"), "sample operations_per_worker"
        ),
        reproduction_command=_text(raw.get("reproduction_command"), "sample reproduction_command"),
        peak_rss_bytes=_integer(raw.get("peak_rss_bytes", 0), "sample peak_rss_bytes"),
        diagnostic_peak_rss_bytes=_integer(
            raw.get("diagnostic_peak_rss_bytes", 0), "sample diagnostic_peak_rss_bytes"
        ),
        live_requested_bytes_at_diagnostic_peak_rss=_integer(
            raw.get("live_requested_bytes_at_diagnostic_peak_rss", 0),
            "sample live_requested_bytes_at_diagnostic_peak_rss",
        ),
        diagnostic_peak_live_requested_bytes=_integer(
            raw.get("diagnostic_peak_live_requested_bytes", 0),
            "sample diagnostic_peak_live_requested_bytes",
        ),
        response=_parse_response(raw.get("response")),
        diagnostic_rss_phases=diagnostic_phases,
    )


def _parse_samples(value: object) -> tuple[ScalingRawSample, ...]:
    return tuple(
        _parse_sample(item) for item in _array(value, "scaling artifact samples must be an array")
    )


def _parse_scaling_artifact(raw: Mapping[str, object]) -> ScalingRawArtifact:
    run = _object(raw.get("run"), "scaling raw run is missing run identity")
    return ScalingRawArtifact(
        metric_schema_version=_text(
            raw.get("metric_schema_version"), "scaling metric_schema_version"
        ),
        status=_text(raw.get("status"), "scaling status"),
        run=ScalingRunIdentity(
            source_sha=_full_sha(run.get("source_sha"), "scaling run source SHA")
        ),
        runner=_parse_runner(raw.get("runner")),
        topology=_parse_topology(raw.get("topology")),
        diagnostic=_parse_selection(raw.get("diagnostic")),
        allocators=_parse_builds(raw.get("allocators")),
        samples=_parse_samples(raw.get("samples")),
    )


def _same_host(
    large_host: HostMetadata, runner: ScalingRunner, topology: ScalingTopology
) -> HostMatch:
    _need(
        large_host.cpu_model == runner.cpu_model,
        f"host mismatch for cpu_model: #543={large_host.cpu_model!r}, scaling={runner.cpu_model!r}",
    )
    _need(
        large_host.physical_cores == topology.physical_cores,
        f"host mismatch for physical_cores: #543={large_host.physical_cores}, scaling={topology.physical_cores}",
    )
    _need(
        large_host.logical_cores == topology.logical_cores,
        f"host mismatch for logical_cores: #543={large_host.logical_cores}, scaling={topology.logical_cores}",
    )
    _need(
        runner.physical_cores == topology.physical_cores,
        "scaling runner/topology disagree for physical_cores",
    )
    _need(
        runner.logical_cores == topology.logical_cores,
        "scaling runner/topology disagree for logical_cores",
    )
    _need(
        large_host.isolation == "isolated",
        "#543 run must identify an isolated host for contextual scaling references",
    )
    return HostMatch(
        cpu_model=large_host.cpu_model,
        physical_cores=large_host.physical_cores,
        logical_cores=large_host.logical_cores,
        large_span_isolation=large_host.isolation,
        scaling_affinity_policy=topology.affinity_policy,
        scaling_allowed_logical_cpus=topology.allowed_logical_cpus,
        match_basis="CPU model and physical/logical topology; scaling schema has no comparable host identity in #543 metadata",
    )


def _reference_samples(
    scaling: ScalingRawArtifact,
    candidate_sha: str,
) -> tuple[ScalingRawSample, ...]:
    _need(
        scaling.metric_schema_version == SCALING_SCHEMA,
        f"scaling artifact schema must be {SCALING_SCHEMA}",
    )
    _need(scaling.status == "diagnostic", "scaling artifact status must be diagnostic")
    _need(
        scaling.run.source_sha == candidate_sha,
        "scaling run source SHA does not match the requested candidate SHA",
    )
    _need(not scaling.diagnostic.publishable, "scaling diagnostic must be marked non-publishable")
    _need(
        scaling.diagnostic.applies_to == "mimalloc-pprof",
        "scaling diagnostic target must be mimalloc-pprof",
    )
    _need(
        WORKERS in scaling.diagnostic.thread_points,
        "scaling diagnostic did not select the 8-worker point",
    )
    _need(
        set(REFERENCE_PATTERNS).issubset(scaling.diagnostic.patterns),
        "scaling diagnostic must select random-large, large-class-persistent, and larson",
    )
    _need(
        len({build.allocator_id for build in scaling.allocators}) == len(scaling.allocators),
        "scaling artifact has duplicate allocator provenance",
    )
    for allocator_id in (*ALLOCATORS, "mimalloc-pprof"):
        _need(
            any(build.allocator_id == allocator_id for build in scaling.allocators),
            f"scaling artifact provenance is missing {allocator_id}",
        )
    _need(
        next(
            build.source_sha
            for build in scaling.allocators
            if build.allocator_id == "mimalloc-pprof"
        )
        == candidate_sha,
        "scaling mimalloc-pprof source SHA does not match the requested candidate SHA",
    )
    for pattern in REFERENCE_PATTERNS:
        for allocator_id in (*ALLOCATORS, "mimalloc-pprof"):
            rows = tuple(
                sample
                for sample in scaling.samples
                if sample.pattern == pattern
                and sample.thread_count == WORKERS
                and sample.allocator_id == allocator_id
            )
            _need(
                len(rows) == scaling.diagnostic.blocks,
                f"raw sample coverage for {pattern}/8 {allocator_id}: expected {scaling.diagnostic.blocks}, found {len(rows)}",
            )
            _need(
                sorted(sample.block_id for sample in rows)
                == list(range(scaling.diagnostic.blocks)),
                f"raw sample block IDs for {pattern}/8 {allocator_id} are incomplete or duplicated",
            )
            expected_sha = (
                candidate_sha
                if allocator_id == "mimalloc-pprof"
                else next(
                    build.source_sha
                    for build in scaling.allocators
                    if build.allocator_id == allocator_id
                )
            )
            _need(
                all(sample.allocator_source_sha == expected_sha for sample in rows),
                f"{allocator_id} sample source SHA differs from provenance in {pattern}/8",
            )
    return tuple(
        sample
        for sample in scaling.samples
        if sample.pattern in REFERENCE_PATTERNS
        and sample.thread_count == WORKERS
        and sample.allocator_id in (*ALLOCATORS, "mimalloc-pprof")
    )


def link_artifacts(large_span_path: Path, scaling_path: Path, candidate_sha: str) -> LinkedArtifact:
    candidate_sha = _full_sha(candidate_sha, "candidate SHA")
    large_span_raw, _ = _read_json(large_span_path)
    scaling, scaling_bytes = _read_json(scaling_path)
    large_span = _parse_large_span_artifact(large_span_raw)
    _need(
        large_span.schema_version == LARGE_SPAN_SCHEMA,
        f"#543 artifact schema must be {LARGE_SPAN_SCHEMA}",
    )
    _need(
        large_span.candidate_sha == candidate_sha,
        "#543 candidate SHA does not match the requested candidate SHA",
    )
    scaling_artifact = _parse_scaling_artifact(scaling)
    host = _same_host(large_span.host, scaling_artifact.runner, scaling_artifact.topology)
    selected = _reference_samples(scaling_artifact, candidate_sha)
    return LinkedArtifact(
        schema_version="large-span-contextual-scaling-refs-v1",
        candidate_sha=candidate_sha,
        large_span_artifact=str(large_span_path.resolve()),
        scaling_artifact=ScalingArtifactReference(
            path=str(scaling_path.resolve()),
            sha256=hashlib.sha256(scaling_bytes).hexdigest(),
        ),
        host_match=host,
        interpretation=(
            "Same-host scaling-suite measurements provide contextual allocator references. "
            "They are not paired repetitions with the #543 baseline/candidate arms and do not "
            "establish exact trace equivalence."
        ),
        perf_ab_cell_relationships=PERF_AB_RELATIONSHIPS,
        scaling_reference_cells=tuple(
            ScalingReferenceCell(
                pattern=pattern,
                workers=WORKERS,
                allocators=ALLOCATORS,
                relationship="contextual only; scaling harness operation planner and trace differ from perf-ab",
            )
            for pattern in REFERENCE_PATTERNS
        ),
        raw_samples=selected,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--large-span", type=Path, required=True, help="#543 large-span-diagnostic-v1 raw JSON"
    )
    parser.add_argument(
        "--scaling", type=Path, required=True, help="same-host diagnostic scaling raw JSON"
    )
    parser.add_argument(
        "--candidate-sha",
        required=True,
        help="full candidate commit SHA required in both artifacts",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="new contextual-reference link JSON path"
    )
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ReferenceLinkError(f"output already exists: {args.output}")
        linked = link_artifacts(args.large_span, args.scaling, args.candidate_sha)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(asdict(linked), indent=2) + "\n", encoding="utf-8")
    except (ReferenceLinkError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Linked contextual scaling references: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
