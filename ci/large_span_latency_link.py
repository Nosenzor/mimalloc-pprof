#!/usr/bin/env python3
"""Link matched #543 CPU diagnostics and transaction-latency diagnostics."""

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

import large_span_diagnostic_model as span_model

LATENCY_SCHEMA = "transaction-latency-v1"
LINK_SCHEMA = "large-span-latency-link-v1"
MEASUREMENT_SCOPE = (
    "paired per-operation latency replay of the exact perf-ab seeded transaction stream; "
    "Rust child monotonic timing with sampled operations and a no-allocation control; "
    "not perf-ab aggregate CPU timing"
)
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")


class LinkError(ValueError):
    """The inputs do not contain a trustworthy matched latency comparison."""


@dataclass(frozen=True)
class DiagnosticHost:
    cpu_model: str
    physical_cores: int
    logical_cores: int
    transparent_hugepage: str
    stable_host_id: str
    runner_fingerprint_sha256: str
    stable_host_identity_status: str


@dataclass(frozen=True)
class DiagnosticSource:
    source_sha: str
    library_sha256: str
    child_binary_sha256: str


@dataclass(frozen=True)
class Distribution:
    count: int
    p50_ns: float
    p95_ns: float
    p99_ns: float
    min_ns: int
    max_ns: int
    zero_count: int


@dataclass(frozen=True)
class AllocatorSummary:
    measured: Distribution
    control: Distribution
    overhead_valid: bool


@dataclass(frozen=True)
class ConfidenceInterval:
    lower: float
    upper: float
    confidence_level: float


@dataclass(frozen=True)
class PairedEffect:
    candidate_id: str
    reference_id: str
    direction: str
    block_count: int
    effect: float
    confidence_interval: ConfidenceInterval
    informational: bool


@dataclass(frozen=True)
class QuantileSummary:
    quantile: str
    summary: PairedEffect


@dataclass(frozen=True)
class LatencyCell:
    scenario_id: str
    workload_id: str
    trace_checksum: str
    thread_point: str
    thread_count: int
    transactions_per_worker: int
    sample_denominator: int
    transaction_definition: str
    old_fork: AllocatorSummary
    candidate: AllocatorSummary
    paired_summaries: tuple[QuantileSummary, ...]


@dataclass(frozen=True)
class ChildIdentity:
    control: bool
    completed_transactions: int
    checksum: int
    observation_count: int
    thread_count: int


@dataclass(frozen=True)
class LatencySample:
    arm: str
    execution_order: int
    block_id: int
    scenario_id: str
    thread_point: str
    thread_count: int
    workload_seed: int
    allocator_source_sha: str
    sample_denominator: int
    measured: ChildIdentity
    control: ChildIdentity


@dataclass(frozen=True)
class LatencySampleEnvelope:
    arm: str
    execution_order: int
    sample: LatencySample


@dataclass(frozen=True)
class LatencyRaw:
    metric_schema_version: str
    status: str
    run_seed: int
    measurement_scope: str
    host: DiagnosticHost
    old_fork: DiagnosticSource
    candidate: DiagnosticSource
    cells: tuple[LatencyCell, ...]
    samples: tuple[LatencySampleEnvelope, ...]


@dataclass(frozen=True)
class SpanIdentity:
    cell_name: str
    workers: int
    operations_per_worker: int
    seed: str
    trace_checksum: str


@dataclass(frozen=True)
class SampleCoverage:
    arm: str
    block_ids: tuple[int, ...]
    sample_count: int
    measured_observations: int
    control_observations: int


@dataclass(frozen=True)
class TailLatencyEffect:
    quantile: str
    candidate_change_percent: float
    ci95_low_percent: float
    ci95_high_percent: float


@dataclass(frozen=True)
class CombinedAssessment:
    decision: str
    latency_tail_regression: bool
    tail_effects: tuple[TailLatencyEffect, ...]
    span_cpu_direction: str
    span_rss_direction: str
    span_cpu_op_baseline_ns: float
    span_cpu_op_candidate_ns: float
    span_peak_rss_baseline_mib: float
    span_peak_rss_candidate_mib: float
    metric_scope_note: str


@dataclass(frozen=True)
class LinkedCell:
    workload_id: str
    scenario_id: str
    workers: int
    operations_per_worker: int
    total_operations: int
    seed: str
    trace_checksum: str
    transaction_definition: str
    block_count: int
    old_fork: AllocatorSummary
    candidate: AllocatorSummary
    paired_summaries: tuple[QuantileSummary, ...]
    sample_coverage: tuple[SampleCoverage, ...]
    assessment: CombinedAssessment


@dataclass(frozen=True)
class HostMatch:
    cpu_model: str
    physical_cores: int
    logical_cores: int
    transparent_hugepage_matches: bool
    stable_host_id: str
    identity_comparable: bool
    identity_limitation: str


@dataclass(frozen=True)
class ArtifactReference:
    path: str
    sha256: str


@dataclass(frozen=True)
class LinkedArtifact:
    schema_version: str
    baseline_sha: str
    candidate_sha: str
    host_match: HostMatch
    large_span_artifact: ArtifactReference
    latency_artifact: ArtifactReference
    interpretation: str
    acceptance_eligible: bool
    cells: tuple[LinkedCell, ...]


REQUIRED_OVERLAPS = (
    ("size 128 KiB/1 (#422)", "size 128 KiB/1 (#422)", "large-object-128k-diagnostic", 1),
    ("random-large-bursty/8", "random-large-bursty/8", "random-large-bursty-diagnostic", 8),
    ("large-class/8", "large-class/8", "large-class-diagnostic", 8),
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LinkError(message)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    _require(isinstance(value, Mapping), f"{label} must be an object")
    mapping = cast(Mapping[object, object], value)
    _require(all(isinstance(key, str) for key in mapping), f"{label} keys must be strings")
    return cast(Mapping[str, object], value)


def _sequence(value: object, label: str) -> tuple[object, ...]:
    _require(isinstance(value, list), f"{label} must be an array")
    return tuple(cast(list[object], value))


def _text(value: object, label: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{label} must be a non-empty string")
    return cast(str, value)


def _host_id(value: object) -> str:
    _require(isinstance(value, str), "latency stable_host_id must be a string")
    return cast(str, value)


def _int(value: object, label: str, minimum: int = 0) -> int:
    _require(type(value) is int and value >= minimum, f"{label} must be an integer >= {minimum}")
    return cast(int, value)


def _float(value: object, label: str, minimum: float = 0.0) -> float:
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool), f"{label} must be numeric"
    )
    result = float(cast(int | float, value))
    _require(result >= minimum, f"{label} must be >= {minimum}")
    return result


def _sha(value: object, label: str, *, length: int = 40) -> str:
    pattern = SHA40 if length == 40 else SHA64
    _require(
        isinstance(value, str) and pattern.fullmatch(value) is not None,
        f"{label} must be a full lowercase {length}-hex digest",
    )
    return cast(str, value)


def _read(path: Path) -> tuple[Mapping[str, object], bytes]:
    try:
        content = path.read_bytes()
        raw: object = json.loads(content)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LinkError(f"cannot read {path}: {error}") from error
    return _mapping(raw, f"{path} root"), content


def _parse_host(value: object) -> DiagnosticHost:
    raw = _mapping(value, "latency host")
    host = DiagnosticHost(
        cpu_model=_text(raw.get("cpu_model"), "latency CPU model"),
        physical_cores=_int(raw.get("physical_cores"), "latency physical cores", 1),
        logical_cores=_int(raw.get("logical_cores"), "latency logical cores", 1),
        transparent_hugepage=_text(raw.get("transparent_hugepage"), "latency THP policy"),
        stable_host_id=_host_id(raw.get("stable_host_id")),
        runner_fingerprint_sha256=_sha(
            raw.get("runner_fingerprint_sha256"), "runner fingerprint", length=64
        ),
        stable_host_identity_status=_text(
            raw.get("stable_host_identity_status"), "stable host identity status"
        ),
    )
    _require(
        host.stable_host_identity_status in ("reported", "not-provided"),
        "invalid stable host identity status",
    )
    _require(
        (host.stable_host_identity_status == "reported") == bool(host.stable_host_id),
        "stable host ID and identity status disagree",
    )
    return host


def _parse_source(value: object, label: str) -> DiagnosticSource:
    raw = _mapping(value, label)
    return DiagnosticSource(
        source_sha=_sha(raw.get("source_sha"), f"{label} source SHA"),
        library_sha256=_sha(raw.get("library_sha256"), f"{label} library SHA", length=64),
        child_binary_sha256=_sha(raw.get("child_binary_sha256"), f"{label} binary SHA", length=64),
    )


def _parse_distribution(value: object, label: str) -> Distribution:
    raw = _mapping(value, label)
    return Distribution(
        count=_int(raw.get("count"), f"{label} count", 1),
        p50_ns=_float(raw.get("p50_ns"), f"{label} p50", 1),
        p95_ns=_float(raw.get("p95_ns"), f"{label} p95", 1),
        p99_ns=_float(raw.get("p99_ns"), f"{label} p99", 1),
        min_ns=_int(raw.get("min_ns"), f"{label} minimum", 1),
        max_ns=_int(raw.get("max_ns"), f"{label} maximum", 1),
        zero_count=_int(raw.get("zero_count"), f"{label} zero count"),
    )


def _parse_allocator_summary(value: object, label: str) -> AllocatorSummary:
    raw = _mapping(value, label)
    valid = raw.get("overhead_valid")
    _require(type(valid) is bool, f"{label} overhead_valid must be boolean")
    result = AllocatorSummary(
        measured=_parse_distribution(raw.get("measured"), f"{label} measured"),
        control=_parse_distribution(raw.get("control"), f"{label} control"),
        overhead_valid=cast(bool, valid),
    )
    _require(result.overhead_valid, f"{label} measured/control overhead is invalid")
    _require(
        result.measured.count == result.control.count,
        f"{label} measured/control observation counts differ",
    )
    return result


def _parse_effect(value: object, quantile: str) -> PairedEffect:
    raw = _mapping(value, f"{quantile} paired effect")
    interval = _mapping(raw.get("confidence_interval"), f"{quantile} confidence interval")
    effect = _float(raw.get("effect"), f"{quantile} paired effect", 0.0000000001)
    lower = _float(interval.get("lower"), f"{quantile} CI lower", 0.0000000001)
    upper = _float(interval.get("upper"), f"{quantile} CI upper", 0.0000000001)
    _require(lower <= upper, f"{quantile} confidence interval is reversed")
    _require(
        _float(interval.get("confidence_level"), f"{quantile} confidence level") == 0.95,
        f"{quantile} confidence level must be 0.95",
    )
    _require(
        raw.get("candidate_id") == "mimalloc-pprof-candidate"
        and raw.get("reference_id") == "mimalloc-pprof-old-fork"
        and raw.get("direction") == "lower-is-better",
        f"{quantile} effect has incorrect arm identity or direction",
    )
    _require(raw.get("informational") is True, f"{quantile} effect must be labeled informational")
    return PairedEffect(
        candidate_id="mimalloc-pprof-candidate",
        reference_id="mimalloc-pprof-old-fork",
        direction="lower-is-better",
        block_count=_int(raw.get("block_count"), f"{quantile} block count", 1),
        effect=effect,
        confidence_interval=ConfidenceInterval(lower, upper, 0.95),
        informational=True,
    )


def _parse_cell(value: object) -> LatencyCell:
    raw = _mapping(value, "latency diagnostic cell")
    quantiles: list[QuantileSummary] = []
    for item in _sequence(raw.get("paired_summaries"), "paired summaries"):
        pair = _mapping(item, "paired summary")
        quantile = _text(pair.get("quantile"), "paired quantile")
        _require(quantile in ("p50", "p95", "p99"), "unexpected paired quantile")
        quantiles.append(QuantileSummary(quantile, _parse_effect(pair.get("summary"), quantile)))
    _require(
        tuple(item.quantile for item in quantiles) == ("p50", "p95", "p99"),
        "paired summaries must contain p50, p95, p99 in order",
    )
    return LatencyCell(
        scenario_id=_text(raw.get("scenario_id"), "latency scenario ID"),
        workload_id=_text(raw.get("workload_id"), "latency workload ID"),
        trace_checksum=_text(raw.get("trace_checksum"), "latency trace checksum"),
        thread_point=_text(raw.get("thread_point"), "latency thread point"),
        thread_count=_int(raw.get("thread_count"), "latency thread count", 1),
        transactions_per_worker=_int(
            raw.get("transactions_per_worker"), "transactions per worker", 1
        ),
        sample_denominator=_int(raw.get("sample_denominator"), "sample denominator", 1),
        transaction_definition=_text(raw.get("transaction_definition"), "transaction definition"),
        old_fork=_parse_allocator_summary(raw.get("old_fork"), "old-fork summary"),
        candidate=_parse_allocator_summary(raw.get("candidate"), "candidate summary"),
        paired_summaries=tuple(quantiles),
    )


def _parse_child(value: object, label: str) -> ChildIdentity:
    raw = _mapping(value, label)
    control = raw.get("control")
    _require(type(control) is bool, f"{label} control flag must be boolean")
    observations = _sequence(raw.get("observations"), f"{label} observations")
    _require(bool(observations), f"{label} has no observations")
    return ChildIdentity(
        control=cast(bool, control),
        completed_transactions=_int(
            raw.get("completed_transactions"), f"{label} completed transactions", 1
        ),
        checksum=_int(raw.get("checksum"), f"{label} checksum", 1),
        observation_count=len(observations),
        thread_count=_int(
            _mapping(raw.get("scheduling"), f"{label} scheduling").get("thread_count"),
            f"{label} scheduled threads",
            1,
        ),
    )


def _parse_latency_sample(value: object) -> LatencySampleEnvelope:
    outer = _mapping(value, "latency diagnostic sample row")
    sample = _mapping(outer.get("sample"), "latency raw sample")
    arm = _text(outer.get("arm"), "sample arm")
    order = _int(outer.get("execution_order"), "sample execution order")
    return LatencySampleEnvelope(
        arm=arm,
        execution_order=order,
        sample=LatencySample(
            arm=arm,
            execution_order=order,
            block_id=_int(sample.get("block_id"), "latency block ID"),
            scenario_id=_text(sample.get("scenario_id"), "sample scenario ID"),
            thread_point=_text(sample.get("thread_point"), "sample thread point"),
            thread_count=_int(sample.get("thread_count"), "sample thread count", 1),
            workload_seed=_int(sample.get("workload_seed"), "sample workload seed", 1),
            allocator_source_sha=_sha(
                sample.get("allocator_source_sha"), "sample allocator source SHA"
            ),
            sample_denominator=_int(sample.get("sample_denominator"), "sample denominator", 1),
            measured=_parse_child(sample.get("measured"), "measured child"),
            control=_parse_child(sample.get("control"), "control child"),
        ),
    )


def _workload_for_scenario(scenario_id: str, thread_count: int) -> str:
    if scenario_id == "large-object-128k-diagnostic":
        return "size 128 KiB/1 (#422)" if thread_count == 1 else "size 128 KiB/8 (diagnostic)"
    for expected_workload, _span_name, expected_scenario, _workers in REQUIRED_OVERLAPS:
        if scenario_id == expected_scenario:
            return expected_workload
    raise LinkError(f"latency raw sample has unexpected scenario {scenario_id}")


def _parse_latency(value: Mapping[str, object]) -> LatencyRaw:
    return LatencyRaw(
        metric_schema_version=_text(value.get("metric_schema_version"), "latency schema"),
        status=_text(value.get("status"), "latency status"),
        run_seed=_int(value.get("run_seed"), "latency run seed", 1),
        measurement_scope=_text(value.get("measurement_scope"), "latency measurement scope"),
        host=_parse_host(value.get("host")),
        old_fork=_parse_source(value.get("old_fork"), "old-fork source"),
        candidate=_parse_source(value.get("candidate"), "candidate source"),
        cells=tuple(_parse_cell(item) for item in _sequence(value.get("cells"), "latency cells")),
        samples=tuple(
            _parse_latency_sample(item)
            for item in _sequence(value.get("samples"), "latency samples")
        ),
    )


def _span_identities(raw: span_model.RawRun) -> tuple[SpanIdentity, ...]:
    identities: list[SpanIdentity] = []
    for name, _latency_name, _scenario, _workers in REQUIRED_OVERLAPS:
        cell = next((item for item in raw.cells if item.name == name), None)
        _require(cell is not None, f"#543 raw run is missing overlapping cell {name}")
        assert cell is not None
        checksums = tuple(pair.baseline.trace_checksum for pair in cell.pairs) + tuple(
            pair.candidate.trace_checksum for pair in cell.pairs
        )
        _require(len(set(checksums)) == 1, f"#543 cell {name} has inconsistent trace checksums")
        identities.append(
            SpanIdentity(
                cell_name=cell.name,
                workers=cell.workers,
                operations_per_worker=cell.params.ops,
                seed=cell.seed,
                trace_checksum=checksums[0],
            )
        )
    return tuple(identities)


def _match_host(span: span_model.RawRun, latency: DiagnosticHost) -> HostMatch:
    _require(span.host.cpu_model == latency.cpu_model, "host CPU model mismatch")
    _require(
        span.host.physical_cores == latency.physical_cores, "host physical-core count mismatch"
    )
    _require(span.host.logical_cores == latency.logical_cores, "host logical-core count mismatch")
    _require(
        span.host.thp_policy == latency.transparent_hugepage,
        "host transparent huge-page policy mismatch",
    )
    _require(
        not span.host.stable_host_id
        or not latency.stable_host_id
        or span.host.stable_host_id == latency.stable_host_id,
        "stable host ID mismatch between artifacts",
    )
    identity_comparable = bool(span.host.stable_host_id and latency.stable_host_id)
    limitation = (
        "stable host IDs match"
        if identity_comparable
        else "one or both artifacts omit stable_host_id; smoke linkage only"
    )
    return HostMatch(
        cpu_model=latency.cpu_model,
        physical_cores=latency.physical_cores,
        logical_cores=latency.logical_cores,
        transparent_hugepage_matches=True,
        stable_host_id=span.host.stable_host_id or latency.stable_host_id,
        identity_comparable=identity_comparable,
        identity_limitation=limitation,
    )


def _linked_cells(span: span_model.RawRun, latency: LatencyRaw) -> tuple[LinkedCell, ...]:
    links: list[LinkedCell] = []
    identities = _span_identities(span)
    for (_span_name, workload_id, scenario_id, workers), identity in zip(
        REQUIRED_OVERLAPS, identities
    ):
        cell = next((item for item in latency.cells if item.workload_id == workload_id), None)
        _require(cell is not None, f"latency raw run is missing workload {workload_id}")
        assert cell is not None
        _require(cell.scenario_id == scenario_id, f"latency scenario mismatch for {workload_id}")
        _require(
            cell.thread_count == workers == identity.workers,
            f"worker-count mismatch for {workload_id}",
        )
        _require(
            cell.transactions_per_worker == identity.operations_per_worker,
            f"operation-count mismatch for {workload_id}",
        )
        _require(
            cell.trace_checksum == identity.trace_checksum,
            f"trace-checksum mismatch for {workload_id}",
        )
        _require(cell.thread_point == str(workers), f"thread-point mismatch for {workload_id}")
        _require(
            cell.old_fork.overhead_valid and cell.candidate.overhead_valid,
            f"measured/control overhead invalid for {workload_id}",
        )
        blocks = cell.paired_summaries[0].summary.block_count
        _require(
            all(item.summary.block_count == blocks for item in cell.paired_summaries),
            f"paired quantile block counts differ for {workload_id}",
        )
        rows = tuple(
            envelope.sample
            for envelope in latency.samples
            if _workload_for_scenario(envelope.sample.scenario_id, envelope.sample.thread_count)
            == workload_id
            and envelope.sample.thread_count == workers
        )
        expected_rows = blocks * 2
        _require(
            len(rows) == expected_rows,
            f"raw sample coverage for {workload_id}: expected {expected_rows}, found {len(rows)}",
        )
        coverage: list[SampleCoverage] = []
        for arm in ("old-fork", "candidate"):
            arm_rows = tuple(row for row in rows if row.arm == arm)
            _require(
                len(arm_rows) == blocks, f"raw {arm} sample coverage incomplete for {workload_id}"
            )
            block_ids = tuple(sorted(row.block_id for row in arm_rows))
            _require(
                block_ids == tuple(range(blocks)),
                f"raw {arm} block IDs incomplete or duplicated for {workload_id}",
            )
            _require(
                all(row.execution_order in (0, 1) for row in arm_rows),
                f"raw execution order invalid for {workload_id}",
            )
            for row in arm_rows:
                first_arm = "old-fork" if row.block_id % 2 == 0 else "candidate"
                _require(
                    (row.execution_order == 0) == (row.arm == first_arm),
                    f"raw paired arm order mismatch for {workload_id} block {row.block_id}",
                )
            source = latency.old_fork if arm == "old-fork" else latency.candidate
            _require(
                all(row.allocator_source_sha == source.source_sha for row in arm_rows),
                f"raw {arm} source SHA mismatch for {workload_id}",
            )
            for row in arm_rows:
                _require(
                    row.scenario_id == scenario_id and row.thread_point == str(workers),
                    f"raw sample cell identity mismatch for {workload_id}",
                )
                _require(
                    row.workload_seed == int(identity.seed, 16),
                    f"raw sample seed mismatch for {workload_id}",
                )
                _require(
                    row.sample_denominator == cell.sample_denominator,
                    f"raw sample denominator mismatch for {workload_id}",
                )
                total = identity.operations_per_worker * workers
                _require(
                    row.measured.completed_transactions
                    == total
                    == row.control.completed_transactions,
                    f"raw sample completed-operation count mismatch for {workload_id}",
                )
                _require(
                    row.measured.checksum == int(identity.trace_checksum, 16),
                    f"raw measured checksum mismatch for {workload_id}",
                )
                _require(
                    row.measured.control is False and row.control.control is True,
                    f"raw sample control identity mismatch for {workload_id}",
                )
                _require(
                    row.measured.thread_count == workers == row.control.thread_count,
                    f"raw sample scheduling mismatch for {workload_id}",
                )
            coverage.append(
                SampleCoverage(
                    arm=arm,
                    block_ids=block_ids,
                    sample_count=len(arm_rows),
                    measured_observations=sum(row.measured.observation_count for row in arm_rows),
                    control_observations=sum(row.control.observation_count for row in arm_rows),
                )
            )
        links.append(
            LinkedCell(
                workload_id=cell.workload_id,
                scenario_id=cell.scenario_id,
                workers=workers,
                operations_per_worker=identity.operations_per_worker,
                total_operations=identity.operations_per_worker * workers,
                seed=identity.seed,
                trace_checksum=identity.trace_checksum,
                transaction_definition=cell.transaction_definition,
                block_count=blocks,
                old_fork=cell.old_fork,
                candidate=cell.candidate,
                paired_summaries=cell.paired_summaries,
                sample_coverage=tuple(coverage),
                assessment=_combined_assessment(span, identity, cell.paired_summaries),
            )
        )
    return tuple(links)


def _combined_assessment(
    raw: span_model.RawRun, identity: SpanIdentity, latency: tuple[QuantileSummary, ...]
) -> CombinedAssessment:
    report = span_model.summarize(raw)
    span_cell = next(item for item in report.cells if item.name == identity.cell_name)
    cpu = span_cell.metric("process_cpu_per_operation_ns")
    rss = span_cell.metric("peak_work_rss_mib")
    p50 = next(item for item in latency if item.quantile == "p50")
    p95 = next(item for item in latency if item.quantile == "p95")
    p99 = next(item for item in latency if item.quantile == "p99")
    tail_regression = (
        p95.summary.confidence_interval.upper < 1.0 or p99.summary.confidence_interval.upper < 1.0
    )
    decision = "REGRESSION" if tail_regression else span_cell.assessment
    tail_effects = tuple(
        TailLatencyEffect(
            quantile=item.quantile,
            candidate_change_percent=(1.0 / item.summary.effect - 1.0) * 100.0,
            ci95_low_percent=(1.0 / item.summary.confidence_interval.upper - 1.0) * 100.0,
            ci95_high_percent=(1.0 / item.summary.confidence_interval.lower - 1.0) * 100.0,
        )
        for item in (p50, p95, p99)
    )
    return CombinedAssessment(
        decision=decision,
        latency_tail_regression=tail_regression,
        tail_effects=tail_effects,
        span_cpu_direction=cpu.direction,
        span_rss_direction=rss.direction,
        span_cpu_op_baseline_ns=cpu.baseline_median,
        span_cpu_op_candidate_ns=cpu.candidate_median,
        span_peak_rss_baseline_mib=rss.baseline_median,
        span_peak_rss_candidate_mib=rss.candidate_median,
        metric_scope_note=(
            "Latency is paired per-operation timing; CPU/op and peak RSS are #543 process/work "
            "metrics. Scopes remain separate. A confident p95 or p99 latency increase is a "
            "regression even when span RSS improves."
        ),
    )


def render_linked_summary(linked: LinkedArtifact) -> str:
    lines = [
        "#543 matched latency + process diagnostic",
        f"baseline SHA: {linked.baseline_sha}",
        f"candidate SHA: {linked.candidate_sha}",
        f"acceptance eligible: {'yes' if linked.acceptance_eligible else 'no (smoke/context only)'}",
        f"host identity: {linked.host_match.identity_limitation}",
        "Metric scopes: latency is paired per-operation timing; CPU/op and peak RSS are #543 "
        "process/work metrics. These are separate measurements, not a merged metric.",
        "",
    ]
    for cell in linked.cells:
        assessment = cell.assessment
        p50 = next(item for item in assessment.tail_effects if item.quantile == "p50")
        p95 = next(item for item in assessment.tail_effects if item.quantile == "p95")
        p99 = next(item for item in assessment.tail_effects if item.quantile == "p99")
        old_coverage = next(item for item in cell.sample_coverage if item.arm == "old-fork")
        candidate_coverage = next(item for item in cell.sample_coverage if item.arm == "candidate")
        lines.extend(
            (
                f"{cell.workload_id} ({cell.workers} workers; {cell.total_operations} operations):",
                "  #543 work CPU/op: "
                f"{assessment.span_cpu_op_baseline_ns:.4g} -> "
                f"{assessment.span_cpu_op_candidate_ns:.4g} ns "
                f"({assessment.span_cpu_direction})",
                "  #543 peak work RSS: "
                f"{assessment.span_peak_rss_baseline_mib:.4g} -> "
                f"{assessment.span_peak_rss_candidate_mib:.4g} MiB "
                f"({assessment.span_rss_direction})",
                "  matched per-operation latency: "
                f"p50 {p50.candidate_change_percent:+.2f}% "
                f"[95% CI {p50.ci95_low_percent:+.2f}%, {p50.ci95_high_percent:+.2f}%]; "
                f"p95 {p95.candidate_change_percent:+.2f}% "
                f"[95% CI {p95.ci95_low_percent:+.2f}%, {p95.ci95_high_percent:+.2f}%]; "
                f"p99 {p99.candidate_change_percent:+.2f}% "
                f"[95% CI {p99.ci95_low_percent:+.2f}%, {p99.ci95_high_percent:+.2f}%]",
                "  instrumentation control p50: "
                f"old-fork {cell.old_fork.control.p50_ns:.4g} ns "
                f"(overhead_valid={cell.old_fork.overhead_valid}); candidate "
                f"{cell.candidate.control.p50_ns:.4g} ns "
                f"(overhead_valid={cell.candidate.overhead_valid})",
                "  raw timed sample coverage: "
                f"old-fork {old_coverage.sample_count} samples/{old_coverage.measured_observations} "
                f"measured observations/{old_coverage.control_observations} control observations; "
                f"candidate {candidate_coverage.sample_count} samples/"
                f"{candidate_coverage.measured_observations} measured observations/"
                f"{candidate_coverage.control_observations} control observations",
                f"  combined assessment: {assessment.decision}",
                "",
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def link_artifacts(span_path: Path, latency_path: Path) -> LinkedArtifact:
    span_bytes = span_path.read_bytes()
    try:
        span_value: object = json.loads(span_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LinkError(f"cannot parse #543 JSON: {error}") from error
    try:
        span = span_model.RawRun.parse(span_value)
    except ValueError as error:
        raise LinkError(f"invalid #543 raw run: {error}") from error
    latency_root, latency_bytes = _read(latency_path)
    latency = _parse_latency(latency_root)
    _require(
        latency.metric_schema_version == LATENCY_SCHEMA, f"latency schema must be {LATENCY_SCHEMA}"
    )
    _require(latency.status == "diagnostic", "latency run status must be diagnostic")
    _require(
        latency.measurement_scope == MEASUREMENT_SCOPE,
        "latency measurement scope does not match the diagnostic protocol",
    )
    _require(
        latency.old_fork.source_sha == span.baseline_sha,
        "old-fork source SHA mismatch between artifacts",
    )
    _require(
        latency.candidate.source_sha == span.candidate_sha,
        "candidate source SHA mismatch between artifacts",
    )
    host = _match_host(span, latency.host)
    cells = _linked_cells(span, latency)
    return LinkedArtifact(
        schema_version=LINK_SCHEMA,
        baseline_sha=span.baseline_sha,
        candidate_sha=span.candidate_sha,
        host_match=host,
        large_span_artifact=ArtifactReference(
            str(span_path.resolve()), hashlib.sha256(span_bytes).hexdigest()
        ),
        latency_artifact=ArtifactReference(
            str(latency_path.resolve()), hashlib.sha256(latency_bytes).hexdigest()
        ),
        interpretation=(
            "Latency observations are from the same declared transactions, worker counts, "
            "operation counts, seeds, and trace checksums as the overlapping #543 cells. "
            "Latency and process CPU remain separate metrics with separate timing boundaries; "
            "scopes remain separate and are not a merged measurement."
        ),
        acceptance_eligible=host.identity_comparable and span.host.isolation == "isolated",
        cells=cells,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--large-span", type=Path, required=True)
    parser.add_argument("--latency", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    if args.summary is not None and args.summary.resolve() == args.output.resolve():
        parser.error("--summary and --output must be different paths")
    for output_path in (args.output, args.summary):
        if output_path is not None and output_path.exists():
            parser.error(f"output already exists: {output_path}")
    try:
        linked = link_artifacts(args.large_span, args.latency)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(asdict(linked), indent=2) + "\n", encoding="utf-8")
        if args.summary is not None:
            args.summary.parent.mkdir(parents=True, exist_ok=True)
            args.summary.write_text(render_linked_summary(linked), encoding="utf-8")
    except (LinkError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Linked matched latency diagnostic: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
