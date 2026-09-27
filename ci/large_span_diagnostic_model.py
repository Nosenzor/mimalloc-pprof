"""Typed raw protocol and derived metrics for #543's paired Linux diagnostic."""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from typing import Literal, TypeAlias, cast

import perf_ab

SCHEMA_VERSION = "large-span-diagnostic-v1"
CHILD_VERSION = "perf-ab-child-diagnostic-v1"
STREAM_SEED_BASE = "000000005eed0000"
LARSON_TABLE_SEED_BASE = "000000001a750000"
PHASE_DEFINITION = (
    "work=pre-worker-start..all-workers-drained; drain=then..end-of-2x-bound-RSS-window"
)
BOOTSTRAP_SEED = 543
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_CI_LOW_INDEX = 50
BOOTSTRAP_CI_HIGH_INDEX = 1949
PHASE_COUNTER_ROUNDING_TOLERANCE = 1e-6
WORKER_CPU_ROUNDING_TOLERANCE_S = 1e-3
METRIC_VALIDATION_RELATIVE_TOLERANCE = 1e-9
METRIC_VALIDATION_ABSOLUTE_TOLERANCE = 1e-9
Availability: TypeAlias = Literal["available", "unavailable"]
Direction: TypeAlias = Literal["increase", "decrease", "inconclusive"]
Assessment: TypeAlias = Literal["REGRESSION", "IMPROVEMENT", "INCONCLUSIVE"]
Arm: TypeAlias = Literal["baseline", "candidate"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in cast(Mapping[object, object], value)
    ):
        raise ValueError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return cast(Sequence[object], value)


def string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing {label}")
    return value


def integer(value: object, label: str, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"invalid {label}")
    return value


def number(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"invalid {label}")
    return float(value)


@dataclass(frozen=True)
class Snapshot:
    monotonic_s: float
    user_s: float
    system_s: float
    minor_faults: int
    major_faults: int
    voluntary_context_switches: int
    involuntary_context_switches: int
    rss_bytes: int
    peak_rss_bytes: int

    @classmethod
    def parse(cls, value: object) -> Snapshot:
        obj = mapping(value, "phase snapshot")
        return cls(
            monotonic_s=number(obj.get("monotonic_s"), "monotonic_s"),
            user_s=number(obj.get("user_s"), "user_s"),
            system_s=number(obj.get("system_s"), "system_s"),
            minor_faults=integer(obj.get("minor_faults"), "minor_faults"),
            major_faults=integer(obj.get("major_faults"), "major_faults"),
            voluntary_context_switches=integer(
                obj.get("voluntary_context_switches"), "voluntary_context_switches"
            ),
            involuntary_context_switches=integer(
                obj.get("involuntary_context_switches"), "involuntary_context_switches"
            ),
            rss_bytes=integer(obj.get("rss_bytes"), "rss_bytes"),
            peak_rss_bytes=integer(obj.get("peak_rss_bytes"), "peak_rss_bytes"),
        )

    def delta_to(self, end: Snapshot) -> PhaseDelta:
        delta = PhaseDelta(
            elapsed_s=end.monotonic_s - self.monotonic_s,
            user_s=end.user_s - self.user_s,
            system_s=end.system_s - self.system_s,
            minor_faults=end.minor_faults - self.minor_faults,
            major_faults=end.major_faults - self.major_faults,
            voluntary_context_switches=end.voluntary_context_switches
            - self.voluntary_context_switches,
            involuntary_context_switches=end.involuntary_context_switches
            - self.involuntary_context_switches,
        )
        require(delta.elapsed_s > 0, "phase duration must be positive")
        require(
            all(
                getattr(delta, field.name) >= -PHASE_COUNTER_ROUNDING_TOLERANCE
                for field in fields(delta)
            ),
            "phase counter moved backward",
        )
        return delta


@dataclass(frozen=True)
class PhaseDelta:
    elapsed_s: float
    user_s: float
    system_s: float
    minor_faults: int
    major_faults: int
    voluntary_context_switches: int
    involuntary_context_switches: int

    @classmethod
    def parse(cls, value: object) -> PhaseDelta:
        obj = mapping(value, "phase delta")
        return cls(
            elapsed_s=number(obj.get("elapsed_s"), "elapsed_s"),
            user_s=number(obj.get("user_s"), "user_s"),
            system_s=number(obj.get("system_s"), "system_s"),
            minor_faults=integer(obj.get("minor_faults"), "minor_faults"),
            major_faults=integer(obj.get("major_faults"), "major_faults"),
            voluntary_context_switches=integer(
                obj.get("voluntary_context_switches"), "voluntary_context_switches"
            ),
            involuntary_context_switches=integer(
                obj.get("involuntary_context_switches"), "involuntary_context_switches"
            ),
        )


@dataclass(frozen=True)
class WorkMetrics:
    elapsed_work_ms: float
    throughput_ops_per_s: float
    process_user_work_ms: float
    process_system_work_ms: float
    process_cpu_per_operation_ns: float
    worker_cpu_work_ms: float
    non_worker_residual_work_ms: float
    minor_faults_work: int
    major_faults_work: int
    voluntary_context_switches_work: int
    involuntary_context_switches_work: int
    peak_work_rss_mib: float

    @classmethod
    def parse(cls, value: object) -> WorkMetrics:
        obj = mapping(value, "work metrics")
        return cls(
            elapsed_work_ms=number(obj.get("elapsed_work_ms"), "elapsed_work_ms"),
            throughput_ops_per_s=number(obj.get("throughput_ops_per_s"), "throughput_ops_per_s"),
            process_user_work_ms=number(obj.get("process_user_work_ms"), "process_user_work_ms"),
            process_system_work_ms=number(
                obj.get("process_system_work_ms"), "process_system_work_ms"
            ),
            process_cpu_per_operation_ns=number(
                obj.get("process_cpu_per_operation_ns"), "process_cpu_per_operation_ns"
            ),
            worker_cpu_work_ms=number(obj.get("worker_cpu_work_ms"), "worker_cpu_work_ms"),
            non_worker_residual_work_ms=number(
                obj.get("non_worker_residual_work_ms"), "non_worker_residual_work_ms"
            ),
            minor_faults_work=integer(obj.get("minor_faults_work"), "minor_faults_work"),
            major_faults_work=integer(obj.get("major_faults_work"), "major_faults_work"),
            voluntary_context_switches_work=integer(
                obj.get("voluntary_context_switches_work"), "voluntary_context_switches_work"
            ),
            involuntary_context_switches_work=integer(
                obj.get("involuntary_context_switches_work"), "involuntary_context_switches_work"
            ),
            peak_work_rss_mib=number(obj.get("peak_work_rss_mib"), "peak_work_rss_mib"),
        )


@dataclass(frozen=True)
class DrainMetrics:
    process_user_drain_ms: float
    process_system_drain_ms: float
    minor_faults_drain: int
    major_faults_drain: int
    voluntary_context_switches_drain: int
    involuntary_context_switches_drain: int
    rss_after_drain_mib: float
    rss_at_release_bound_mib: float
    release_ms: int

    @classmethod
    def parse(cls, value: object) -> DrainMetrics:
        obj = mapping(value, "drain metrics")
        return cls(
            process_user_drain_ms=number(obj.get("process_user_drain_ms"), "process_user_drain_ms"),
            process_system_drain_ms=number(
                obj.get("process_system_drain_ms"), "process_system_drain_ms"
            ),
            minor_faults_drain=integer(obj.get("minor_faults_drain"), "minor_faults_drain"),
            major_faults_drain=integer(obj.get("major_faults_drain"), "major_faults_drain"),
            voluntary_context_switches_drain=integer(
                obj.get("voluntary_context_switches_drain"), "voluntary_context_switches_drain"
            ),
            involuntary_context_switches_drain=integer(
                obj.get("involuntary_context_switches_drain"), "involuntary_context_switches_drain"
            ),
            rss_after_drain_mib=number(obj.get("rss_after_drain_mib"), "rss_after_drain_mib"),
            rss_at_release_bound_mib=number(
                obj.get("rss_at_release_bound_mib"), "rss_at_release_bound_mib"
            ),
            release_ms=integer(obj.get("release_ms"), "release_ms"),
        )


@dataclass(frozen=True)
class ChildSample:
    protocol_version: str
    phase_definition: str
    work_start: Snapshot
    work_end: Snapshot
    drain_start: Snapshot
    drain_end: Snapshot
    worker_cpu_s: float
    completed_operations: int
    trace_checksum: str
    stream_seed_base: str
    larson_table_seed_base: str
    peak_work_rss_bytes: int
    rss_after_drain_bytes: int
    rss_at_release_bound_bytes: int
    release_ms: int
    work_delta: PhaseDelta
    drain_delta: PhaseDelta
    work_metrics: WorkMetrics
    drain_metrics: DrainMetrics

    def validate(self) -> None:
        require(self.protocol_version == CHILD_VERSION, "child protocol mismatch")
        require(self.phase_definition == PHASE_DEFINITION, "child phase definition mismatch")
        require(
            self.stream_seed_base == STREAM_SEED_BASE
            and self.larson_table_seed_base == LARSON_TABLE_SEED_BASE,
            "child seed identity mismatch",
        )
        require(self.drain_start == self.work_end, "work/drain phase boundary differs")
        require(
            self.work_delta == self.work_start.delta_to(self.work_end)
            and self.drain_delta == self.drain_start.delta_to(self.drain_end),
            "phase deltas disagree with snapshots",
        )
        require(
            self.completed_operations > 0 and self.worker_cpu_s >= 0,
            "invalid completed operations or worker CPU",
        )
        require(self.peak_work_rss_bytes == self.work_end.peak_rss_bytes, "work peak RSS mismatch")
        require(
            len(self.trace_checksum) == 16
            and all(ch in "0123456789abcdef" for ch in self.trace_checksum),
            "invalid trace checksum",
        )
        work_cpu = self.work_delta.user_s + self.work_delta.system_s
        require(
            work_cpu - self.worker_cpu_s >= -WORKER_CPU_ROUNDING_TOLERANCE_S,
            "worker CPU exceeds measured-work process CPU",
        )
        require(
            math.isclose(
                self.work_metrics.process_cpu_per_operation_ns,
                work_cpu * 1e9 / self.completed_operations,
                rel_tol=METRIC_VALIDATION_RELATIVE_TOLERANCE,
            )
            and self.work_metrics.minor_faults_work == self.work_delta.minor_faults
            and self.work_metrics.major_faults_work == self.work_delta.major_faults,
            "derived work metrics disagree with phase counters",
        )
        require(
            self.drain_metrics.minor_faults_drain == self.drain_delta.minor_faults
            and self.drain_metrics.major_faults_drain == self.drain_delta.major_faults
            and self.drain_metrics.release_ms == self.release_ms,
            "derived drain metrics disagree with phase counters",
        )

    @classmethod
    def parse(cls, value: object, *, derived_required: bool = False) -> ChildSample:
        obj = mapping(value, "child sample")
        start = Snapshot.parse(obj.get("work_start"))
        end = Snapshot.parse(obj.get("work_end"))
        drain_start = Snapshot.parse(obj.get("drain_start"))
        drain_end = Snapshot.parse(obj.get("drain_end"))
        require(drain_start == end, "work/drain phase boundary differs")
        work = start.delta_to(end)
        drain = drain_start.delta_to(drain_end)
        operations = integer(obj.get("completed_operations"), "completed operations", 1)
        worker = number(obj.get("worker_cpu_s"), "worker CPU")
        require(worker >= 0, "worker CPU cannot be negative")
        process_cpu = work.user_s + work.system_s
        residual = process_cpu - worker
        require(
            residual >= -WORKER_CPU_ROUNDING_TOLERANCE_S,
            "worker CPU exceeds measured-work process CPU",
        )
        peak = integer(obj.get("peak_work_rss_bytes"), "peak work RSS")
        after = integer(obj.get("rss_after_drain_bytes"), "RSS after drain")
        at_bound = integer(obj.get("rss_at_release_bound_bytes"), "RSS at release bound")
        release = integer(obj.get("release_ms"), "release time")
        require(peak == end.peak_rss_bytes, "work peak RSS mismatch")
        checksum = string(obj.get("trace_checksum"), "trace checksum")
        require(
            len(checksum) == 16 and all(ch in "0123456789abcdef" for ch in checksum),
            "invalid trace checksum",
        )
        work_metrics = WorkMetrics(
            elapsed_work_ms=work.elapsed_s * 1000,
            throughput_ops_per_s=operations / work.elapsed_s,
            process_user_work_ms=work.user_s * 1000,
            process_system_work_ms=work.system_s * 1000,
            process_cpu_per_operation_ns=process_cpu * 1e9 / operations,
            worker_cpu_work_ms=worker * 1000,
            non_worker_residual_work_ms=residual * 1000,
            minor_faults_work=work.minor_faults,
            major_faults_work=work.major_faults,
            voluntary_context_switches_work=work.voluntary_context_switches,
            involuntary_context_switches_work=work.involuntary_context_switches,
            peak_work_rss_mib=peak / (1 << 20),
        )
        drain_metrics = DrainMetrics(
            process_user_drain_ms=drain.user_s * 1000,
            process_system_drain_ms=drain.system_s * 1000,
            minor_faults_drain=drain.minor_faults,
            major_faults_drain=drain.major_faults,
            voluntary_context_switches_drain=drain.voluntary_context_switches,
            involuntary_context_switches_drain=drain.involuntary_context_switches,
            rss_after_drain_mib=after / (1 << 20),
            rss_at_release_bound_mib=at_bound / (1 << 20),
            release_ms=release,
        )
        sample = cls(
            protocol_version=string(obj.get("protocol_version"), "child protocol"),
            phase_definition=string(obj.get("phase_definition"), "child phase definition"),
            work_start=start,
            work_end=end,
            drain_start=drain_start,
            drain_end=drain_end,
            worker_cpu_s=worker,
            completed_operations=operations,
            trace_checksum=checksum,
            stream_seed_base=string(obj.get("stream_seed_base"), "stream seed"),
            larson_table_seed_base=string(obj.get("larson_table_seed_base"), "Larson seed"),
            peak_work_rss_bytes=peak,
            rss_after_drain_bytes=after,
            rss_at_release_bound_bytes=at_bound,
            release_ms=release,
            work_delta=work,
            drain_delta=drain,
            work_metrics=work_metrics,
            drain_metrics=drain_metrics,
        )
        require(sample.protocol_version == CHILD_VERSION, "child protocol mismatch")
        require(sample.phase_definition == PHASE_DEFINITION, "child phase definition mismatch")
        require(
            sample.stream_seed_base == STREAM_SEED_BASE
            and sample.larson_table_seed_base == LARSON_TABLE_SEED_BASE,
            "child seed identity mismatch",
        )
        if derived_required:
            require(
                PhaseDelta.parse(obj.get("work_delta")) == work
                and PhaseDelta.parse(obj.get("drain_delta")) == drain,
                "missing or incorrect phase deltas",
            )
            require(
                WorkMetrics.parse(obj.get("work_metrics")) == work_metrics
                and DrainMetrics.parse(obj.get("drain_metrics")) == drain_metrics,
                "missing or incorrect derived metrics",
            )
        sample.validate()
        return sample


@dataclass(frozen=True)
class CounterSpec:
    name: str
    source: str
    scope: str
    phase: str
    unit: str
    status: Availability
    value: float | None = None
    reason: str | None = None

    @classmethod
    def parse(cls, value: object) -> CounterSpec:
        obj = mapping(value, "counter spec")
        status = string(obj.get("status"), "counter status")
        require(status in ("available", "unavailable"), "invalid counter status")
        raw_value = obj.get("value")
        return cls(
            string(obj.get("name"), "counter name"),
            string(obj.get("source"), "counter source"),
            string(obj.get("scope"), "counter scope"),
            string(obj.get("phase"), "counter phase"),
            string(obj.get("unit"), "counter unit"),
            cast(Availability, status),
            None if raw_value is None else number(raw_value, "counter value"),
            None if obj.get("reason") is None else string(obj.get("reason"), "counter reason"),
        )


@dataclass(frozen=True)
class HostMetadata:
    cpu_model: str
    physical_cores: int
    logical_cores: int
    thp_policy: str
    perf_event_paranoid: str
    yama_ptrace_scope: str
    effective_capabilities_hex: str
    isolation: Literal["isolated", "shared"]
    stable_host_id: str = ""

    @classmethod
    def parse(cls, value: object) -> HostMetadata:
        obj = mapping(value, "host metadata")
        isolation = string(obj.get("isolation"), "host isolation")
        require(isolation in ("isolated", "shared"), "invalid host isolation")
        stable_host_id = obj.get("stable_host_id", "")
        require(isinstance(stable_host_id, str), "stable host ID must be a string")
        return cls(
            string(obj.get("cpu_model"), "CPU model"),
            integer(obj.get("physical_cores"), "physical cores", 1),
            integer(obj.get("logical_cores"), "logical cores", 1),
            string(obj.get("thp_policy"), "THP policy"),
            string(obj.get("perf_event_paranoid"), "perf permissions"),
            string(obj.get("yama_ptrace_scope"), "ptrace permissions"),
            string(obj.get("effective_capabilities_hex"), "capabilities"),
            cast(Literal["isolated", "shared"], isolation),
            cast(str, stable_host_id),
        )


@dataclass(frozen=True)
class EnvOption:
    name: str
    value: str

    @classmethod
    def parse(cls, value: object) -> EnvOption:
        obj = mapping(value, "environment option")
        raw_value = obj.get("value")
        require(isinstance(raw_value, str), "invalid environment value")
        return cls(
            string(obj.get("name"), "environment name"),
            cast(str, raw_value),
        )


@dataclass(frozen=True)
class RuntimeOptions:
    baseline: tuple[EnvOption, ...]
    candidate: tuple[EnvOption, ...]

    @classmethod
    def parse(cls, value: object) -> RuntimeOptions:
        obj = mapping(value, "runtime options")
        return cls(
            tuple(
                EnvOption.parse(item) for item in sequence(obj.get("baseline"), "baseline options")
            ),
            tuple(
                EnvOption.parse(item)
                for item in sequence(obj.get("candidate"), "candidate options")
            ),
        )


@dataclass(frozen=True)
class WorkloadParams:
    threads: int
    generations: int
    min_size: int
    max_size: int
    ops: int
    pause_ms: int
    table_slots: int
    sizes: str
    slots: int

    @classmethod
    def parse(cls, value: object) -> WorkloadParams:
        obj = mapping(value, "workload parameters")
        return cls(
            integer(obj.get("threads"), "threads", 1),
            integer(obj.get("generations"), "generations", 1),
            integer(obj.get("min_size"), "minimum size"),
            integer(obj.get("max_size"), "maximum size"),
            integer(obj.get("ops"), "operations", 1),
            integer(obj.get("pause_ms"), "pause"),
            integer(obj.get("table_slots"), "table slots"),
            string(obj.get("sizes"), "size plan"),
            integer(obj.get("slots"), "slots"),
        )

    @classmethod
    def from_perf_ab(cls, value: perf_ab.Params) -> WorkloadParams:
        return cls(*value)

    def child_args(self) -> tuple[str, ...]:
        return tuple(str(getattr(self, field.name)) for field in fields(self))


@dataclass(frozen=True)
class PairRaw:
    repetition: int
    arm_order: tuple[Arm, Arm]
    baseline: ChildSample
    candidate: ChildSample

    @classmethod
    def parse(cls, value: object) -> PairRaw:
        obj = mapping(value, "paired sample")
        order = sequence(obj.get("arm_order"), "arm order")
        require(len(order) == 2 and set(order) == {"baseline", "candidate"}, "invalid arm order")
        return cls(
            integer(obj.get("repetition"), "repetition"),
            (cast(Arm, order[0]), cast(Arm, order[1])),
            ChildSample.parse(obj.get("baseline"), derived_required=True),
            ChildSample.parse(obj.get("candidate"), derived_required=True),
        )


@dataclass(frozen=True)
class CellRaw:
    name: str
    workers: int
    seed: str
    larson_table_seed_base: str
    phase_definition: str
    release_bound_ms: int
    params: WorkloadParams
    pairs: tuple[PairRaw, ...]

    @classmethod
    def parse(cls, value: object) -> CellRaw:
        obj = mapping(value, "cell")
        return cls(
            string(obj.get("name"), "cell name"),
            integer(obj.get("workers"), "workers", 1),
            string(obj.get("seed"), "cell seed"),
            string(obj.get("larson_table_seed_base"), "Larson seed"),
            string(obj.get("phase_definition"), "phase definition"),
            integer(obj.get("release_bound_ms"), "release bound", 1),
            WorkloadParams.parse(obj.get("params")),
            tuple(PairRaw.parse(item) for item in sequence(obj.get("pairs"), "pairs")),
        )


@dataclass(frozen=True)
class DeepEventEstimate:
    event: str
    baseline_per_operation: float | None
    candidate_per_operation: float | None
    paired_change_percent: float | None
    availability: Availability
    reason: str | None = None

    @classmethod
    def parse(cls, value: object) -> DeepEventEstimate:
        obj = mapping(value, "deep event estimate")
        status = string(obj.get("availability"), "deep event availability")
        require(status in ("available", "unavailable"), "invalid deep event availability")
        base = obj.get("baseline_per_operation")
        candidate = obj.get("candidate_per_operation")
        change = obj.get("paired_change_percent")
        reason = obj.get("reason")
        return cls(
            string(obj.get("event"), "deep event name"),
            None if base is None else number(base, "baseline deep event"),
            None if candidate is None else number(candidate, "candidate deep event"),
            None if change is None else number(change, "deep event change"),
            cast(Availability, status),
            None if reason is None else string(reason, "deep event unavailability reason"),
        )

    def validate(self) -> None:
        require(self.event in ("cycles", "instructions"), "unexpected deep event")
        if self.availability == "available":
            require(
                self.baseline_per_operation is not None
                and self.candidate_per_operation is not None
                and self.paired_change_percent is not None
                and self.reason is None,
                "available deep event is incomplete",
            )
            baseline = self.baseline_per_operation
            candidate = self.candidate_per_operation
            change = self.paired_change_percent
            assert baseline is not None and candidate is not None and change is not None
            require(
                baseline > 0
                and candidate >= 0
                and math.isclose(
                    change,
                    (candidate - baseline) / baseline * 100,
                    rel_tol=METRIC_VALIDATION_RELATIVE_TOLERANCE,
                    abs_tol=METRIC_VALIDATION_ABSOLUTE_TOLERANCE,
                ),
                "deep event per-operation arithmetic mismatch",
            )
        else:
            require(
                self.availability == "unavailable"
                and self.paired_change_percent is None
                and bool(self.reason),
                "unavailable deep event must have null effect and reason",
            )


@dataclass(frozen=True)
class RawRun:
    schema_version: str
    baseline_sha: str
    candidate_sha: str
    build_flags: tuple[str, ...]
    runtime_options: RuntimeOptions
    host: HostMetadata
    counter_catalog: tuple[CounterSpec, ...]
    cells: tuple[CellRaw, ...]
    separate_deep_artifact: str | None = None
    deep_cell: str | None = None
    deep_event_estimates: tuple[DeepEventEstimate, ...] = ()

    @classmethod
    def parse(cls, value: object) -> RawRun:
        obj = mapping(value, "raw diagnostic run")
        deep = obj.get("separate_deep_artifact")
        deep_cell = obj.get("deep_cell")
        result = cls(
            string(obj.get("schema_version"), "schema version"),
            string(obj.get("baseline_sha"), "baseline SHA"),
            string(obj.get("candidate_sha"), "candidate SHA"),
            tuple(
                string(item, "build flag")
                for item in sequence(obj.get("build_flags"), "build flags")
            ),
            RuntimeOptions.parse(obj.get("runtime_options")),
            HostMetadata.parse(obj.get("host")),
            tuple(
                CounterSpec.parse(item)
                for item in sequence(obj.get("counter_catalog"), "counter catalog")
            ),
            tuple(CellRaw.parse(item) for item in sequence(obj.get("cells"), "cells")),
            None if deep is None else string(deep, "deep artifact path"),
            None if deep_cell is None else string(deep_cell, "deep cell"),
            tuple(
                DeepEventEstimate.parse(item)
                for item in sequence(obj.get("deep_event_estimates", []), "deep event estimates")
            ),
        )
        result.validate()
        return result

    def validate(self) -> None:
        require(self.schema_version == SCHEMA_VERSION, "schema version mismatch")
        for label, sha in (("baseline", self.baseline_sha), ("candidate", self.candidate_sha)):
            require(
                len(sha) == 40 and all(ch in "0123456789abcdef" for ch in sha),
                f"invalid full {label} SHA",
            )
        require(bool(self.build_flags), "missing build flags")
        require(
            self.runtime_options.baseline == self.runtime_options.candidate,
            "runtime options differ between paired arms",
        )
        require(
            self.host.physical_cores > 0 and self.host.logical_cores >= self.host.physical_cores,
            "invalid host topology",
        )
        names = {spec.name for spec in self.counter_catalog}
        required = {
            "user_s",
            "system_s",
            "worker_cpu_s",
            "minor_faults",
            "major_faults",
            "voluntary_context_switches",
            "involuntary_context_switches",
            "rss_bytes",
            "peak_work_rss_bytes",
            "host_thp_allocations",
        }
        require(
            required.issubset(names) and len(names) == len(self.counter_catalog),
            "missing or duplicate counter provenance",
        )
        for spec in self.counter_catalog:
            require(
                all((spec.source, spec.scope, spec.phase, spec.unit, spec.status)),
                f"counter {spec.name} missing provenance/scope/phase/unit/status",
            )
            require(
                spec.status in ("available", "unavailable"),
                f"counter {spec.name} has invalid availability status",
            )
            if spec.status == "unavailable":
                require(
                    spec.value is None and bool(spec.reason),
                    f"unavailable counter {spec.name} must be null with reason",
                )
        require(bool(self.cells), "missing cells")
        require(
            self.deep_cell is None
            or (
                self.separate_deep_artifact is not None
                and any(cell.name == self.deep_cell for cell in self.cells)
            ),
            "deep cell must identify a measured cell and separate artifact",
        )
        require(
            not self.deep_event_estimates
            or (self.separate_deep_artifact is not None and self.deep_cell is not None),
            "deep event estimates require a separate deep artifact and workload identity",
        )
        require(
            len({item.event for item in self.deep_event_estimates})
            == len(self.deep_event_estimates),
            "duplicate deep event estimates",
        )
        for estimate in self.deep_event_estimates:
            estimate.validate()
        for cell in self.cells:
            require(
                cell.workers > 0 and cell.workers == cell.params.threads,
                "workload parameters disagree with worker count",
            )
            require(
                cell.seed == STREAM_SEED_BASE
                and cell.larson_table_seed_base == LARSON_TABLE_SEED_BASE,
                "cell seed identity mismatch",
            )
            require(cell.phase_definition == PHASE_DEFINITION, "phase definition mismatch")
            require(
                cell.release_bound_ms > 0 and bool(cell.pairs), "missing release bound or pairs"
            )
            expected_operations = cell.params.threads * cell.params.ops
            first_identity: tuple[int, str] | None = None
            for index, pair in enumerate(cell.pairs):
                expected_order: tuple[Arm, Arm] = (
                    ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")
                )
                require(
                    pair.repetition == index and pair.arm_order == expected_order,
                    "paired repetition/arm order mismatch",
                )
                for sample in (pair.baseline, pair.candidate):
                    sample.validate()
                    require(
                        sample.completed_operations == expected_operations,
                        "completed operation count differs from fixed workload plan",
                    )
                    identity = (sample.completed_operations, sample.trace_checksum)
                    if first_identity is None:
                        first_identity = identity
                    require(
                        identity == first_identity,
                        "work count or checksum differs across pair/repetitions",
                    )


@dataclass(frozen=True)
class MetricEffect:
    name: str
    baseline_median: float
    candidate_median: float
    paired_change: float
    ci95_low: float
    ci95_high: float
    effect_unit: Literal["percent", "absolute"]
    direction: Direction


@dataclass(frozen=True)
class DrainSummary:
    name: str
    baseline_median: float
    candidate_median: float


@dataclass(frozen=True)
class CellReport:
    name: str
    workers: int
    paired_repetitions: int
    completed_operations: int
    seed: str
    phase_definition: str
    release_bound_ms: int
    trace_checksum: str
    metrics: tuple[MetricEffect, ...]
    drain_metrics: tuple[DrainSummary, ...]
    assessment: Assessment

    def metric(self, name: str) -> MetricEffect:
        return next(item for item in self.metrics if item.name == name)

    def drain_metric(self, name: str) -> DrainSummary:
        return next(item for item in self.drain_metrics if item.name == name)


@dataclass(frozen=True)
class Report:
    schema_version: str
    baseline_sha: str
    candidate_sha: str
    host: HostMetadata
    build_flags: tuple[str, ...]
    runtime_options: RuntimeOptions
    counter_catalog: tuple[CounterSpec, ...]
    cells: tuple[CellReport, ...]
    separate_deep_artifact: str | None = None
    deep_cell: str | None = None
    deep_event_estimates: tuple[DeepEventEstimate, ...] = ()


def metric_effect(name: str, base: Sequence[float], candidate: Sequence[float]) -> MetricEffect:
    if any(value == 0 for value in base):
        differences = [head - old for old, head in zip(base, candidate)]
        rng = random.Random(BOOTSTRAP_SEED)
        boots = sorted(
            statistics.median(rng.choices(differences, k=len(differences)))
            for _ in range(BOOTSTRAP_SAMPLES)
        )
        change, low, high = (
            statistics.median(differences),
            boots[BOOTSTRAP_CI_LOW_INDEX],
            boots[BOOTSTRAP_CI_HIGH_INDEX],
        )
        unit: Literal["percent", "absolute"] = "absolute"
    else:
        change, low, high = perf_ab.paired(list(base), list(candidate))
        unit = "percent"
    direction: Direction = "increase" if low > 0 else "decrease" if high < 0 else "inconclusive"
    return MetricEffect(
        name,
        statistics.median(base),
        statistics.median(candidate),
        change,
        low,
        high,
        unit,
        direction,
    )


def summarize(raw: RawRun) -> Report:
    raw.validate()
    reports: list[CellReport] = []
    for cell in raw.cells:
        effects = tuple(
            metric_effect(
                field.name,
                [float(getattr(pair.baseline.work_metrics, field.name)) for pair in cell.pairs],
                [float(getattr(pair.candidate.work_metrics, field.name)) for pair in cell.pairs],
            )
            for field in fields(WorkMetrics)
        )
        drain = tuple(
            DrainSummary(
                field.name,
                statistics.median(
                    float(getattr(pair.baseline.drain_metrics, field.name)) for pair in cell.pairs
                ),
                statistics.median(
                    float(getattr(pair.candidate.drain_metrics, field.name)) for pair in cell.pairs
                ),
            )
            for field in fields(DrainMetrics)
        )
        cpu = next(effect for effect in effects if effect.name == "process_cpu_per_operation_ns")
        throughput = next(effect for effect in effects if effect.name == "throughput_ops_per_s")
        regression = cpu.direction == "increase" or throughput.direction == "decrease"
        improvement = cpu.direction == "decrease" or throughput.direction == "increase"
        assessment: Assessment = (
            "REGRESSION" if regression else "IMPROVEMENT" if improvement else "INCONCLUSIVE"
        )
        reports.append(
            CellReport(
                cell.name,
                cell.workers,
                len(cell.pairs),
                cell.pairs[0].baseline.completed_operations,
                cell.seed,
                cell.phase_definition,
                cell.release_bound_ms,
                cell.pairs[0].baseline.trace_checksum,
                effects,
                drain,
                assessment,
            )
        )
    return Report(
        SCHEMA_VERSION,
        raw.baseline_sha,
        raw.candidate_sha,
        raw.host,
        raw.build_flags,
        raw.runtime_options,
        raw.counter_catalog,
        tuple(reports),
        raw.separate_deep_artifact,
        raw.deep_cell,
        raw.deep_event_estimates,
    )
