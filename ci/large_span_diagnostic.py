#!/usr/bin/env python3
"""Issue #543: phase-aligned, paired Linux allocator diagnostic.

Timed samples use getrusage and /proc/self RSS. Costly profiling and tracing
run in a separate replay after all timed pairs.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Literal, cast

import large_span_diagnostic_model as model
import perf_ab

SCHEMA_VERSION = model.SCHEMA_VERSION
CHILD_VERSION = model.CHILD_VERSION
STREAM_SEED_BASE = model.STREAM_SEED_BASE
LARSON_TABLE_SEED_BASE = model.LARSON_TABLE_SEED_BASE
PHASE_DEFINITION = model.PHASE_DEFINITION
CELLS = (
    "size 128 KiB/1 (#422)",
    "random-large-bursty/8",
    "large-class/8",
    "small/8 (control)",
    "larson/8",
)
WORK_METRIC_UNITS = (
    ("elapsed_work_ms", "ms"),
    ("throughput_ops_per_s", "ops/s"),
    ("process_user_work_ms", "ms"),
    ("process_system_work_ms", "ms"),
    ("process_cpu_per_operation_ns", "ns/op"),
    ("worker_cpu_work_ms", "ms"),
    ("non_worker_residual_work_ms", "ms"),
    ("minor_faults_work", "count"),
    ("major_faults_work", "count"),
    ("voluntary_context_switches_work", "count"),
    ("involuntary_context_switches_work", "count"),
    ("peak_work_rss_mib", "MiB"),
)
DRAIN_METRIC_UNITS = (
    ("process_user_drain_ms", "ms"),
    ("process_system_drain_ms", "ms"),
    ("minor_faults_drain", "count"),
    ("major_faults_drain", "count"),
    ("voluntary_context_switches_drain", "count"),
    ("involuntary_context_switches_drain", "count"),
    ("rss_after_drain_mib", "MiB"),
    ("rss_at_release_bound_mib", "MiB"),
    ("release_ms", "ms"),
)

COUNTER_SPECS = (
    model.CounterSpec(
        "user_s", "getrusage(RUSAGE_SELF)", "process", "work+drain", "s", "available"
    ),
    model.CounterSpec(
        "system_s", "getrusage(RUSAGE_SELF)", "process", "work+drain", "s", "available"
    ),
    model.CounterSpec(
        "worker_cpu_s", "getrusage(RUSAGE_THREAD)", "worker-thread sum", "work", "s", "available"
    ),
    model.CounterSpec(
        "minor_faults", "getrusage(RUSAGE_SELF)", "process", "work+drain", "count", "available"
    ),
    model.CounterSpec(
        "major_faults", "getrusage(RUSAGE_SELF)", "process", "work+drain", "count", "available"
    ),
    model.CounterSpec(
        "voluntary_context_switches",
        "getrusage(RUSAGE_SELF)",
        "process",
        "work+drain",
        "count",
        "available",
    ),
    model.CounterSpec(
        "involuntary_context_switches",
        "getrusage(RUSAGE_SELF)",
        "process",
        "work+drain",
        "count",
        "available",
    ),
    model.CounterSpec(
        "rss_bytes", "/proc/self/statm", "process", "work+drain", "bytes", "available"
    ),
    model.CounterSpec(
        "peak_work_rss_bytes", "/proc/self/status VmHWM", "process", "work", "bytes", "available"
    ),
    model.CounterSpec(
        "host_thp_allocations",
        "/proc/vmstat",
        "host-wide; unattributable on shared host",
        "work",
        "count",
        "unavailable",
        None,
        "host-wide/unattributable on shared host; see separate deep artifact",
    ),
)


class DiagnosticError(ValueError):
    """Malformed or inconsistent diagnostic input."""


def portable_counter_catalog() -> tuple[model.CounterSpec, ...]:
    return COUNTER_SPECS


def validate_raw(raw: object) -> model.RawRun:
    try:
        parsed = raw if isinstance(raw, model.RawRun) else model.RawRun.parse(raw)
        parsed.validate()
        return parsed
    except ValueError as error:
        raise DiagnosticError(str(error)) from error


def summarize(raw: object) -> model.Report:
    parsed = validate_raw(raw)
    try:
        return model.summarize(parsed)
    except ValueError as error:
        raise DiagnosticError(str(error)) from error


def _metric_unit(units: tuple[tuple[str, str], ...], name: str) -> str:
    for metric_name, unit in units:
        if metric_name == name:
            return unit
    raise DiagnosticError(f"missing display unit for metric {name}")


def render(report: model.Report) -> str:
    host = report.host
    lines = [
        f"{report.schema_version}  baseline_sha={report.baseline_sha}  candidate_sha={report.candidate_sha}",
        f"host={host.isolation}-linux  cpu={host.cpu_model}  "
        f"topology={host.physical_cores} physical/{host.logical_cores} logical  "
        f"THP={host.thp_policy}  perf_event_paranoid={host.perf_event_paranoid}  "
        f"ptrace_scope={host.yama_ptrace_scope}  CapEff={host.effective_capabilities_hex}",
        f"build={report.build_flags}  runtime={report.runtime_options}",
    ]
    lines.append("counter provenance (source | scope | phase | unit | availability):")
    for item in report.counter_catalog:
        availability = item.status
        if item.status == "unavailable":
            availability += f" (null; {item.reason})"
        lines.append(
            f"{item.name} | {item.source} | {item.scope} | {item.phase} | {item.unit} | {availability}"
        )
    if report.separate_deep_artifact is not None:
        lines.append(
            "separate untimed deep diagnostic: "
            f"{report.separate_deep_artifact}; cell={report.deep_cell}; "
            "never included in timed samples"
        )
    if report.deep_event_estimates:
        lines.append(
            f"deep perf-stat replay estimates for {report.deep_cell} "
            "(descriptive; no timed-run CI): "
            "event | old-fork per operation | candidate per operation | change"
        )
        for estimate in report.deep_event_estimates:
            if estimate.availability == "available":
                baseline_value = estimate.baseline_per_operation
                candidate_value = estimate.candidate_per_operation
                change_value = estimate.paired_change_percent
                model.require(
                    baseline_value is not None
                    and candidate_value is not None
                    and change_value is not None,
                    "available deep event estimate is incomplete",
                )
                assert baseline_value is not None and candidate_value is not None
                assert change_value is not None
                lines.append(
                    f"{estimate.event} | {baseline_value:,.4g} | "
                    f"{candidate_value:,.4g} | {change_value:+.1f}%"
                )
            else:
                lines.append(f"{estimate.event} | unavailable (null; {estimate.reason})")
    for cell in report.cells:
        lines.extend(
            (
                f"\ncell={cell.name}  workers={cell.workers}  paired_repetitions={cell.paired_repetitions}",
                f"work: operations={cell.completed_operations}/arm  seed={cell.seed}  trace_checksum={cell.trace_checksum}",
                f"phases: {cell.phase_definition}; release_bound_ms={cell.release_bound_ms}",
                "metric (measured work; unit) | baseline | candidate | paired change [95% CI]",
            )
        )
        for metric in cell.metrics:
            unit = "%" if metric.effect_unit == "percent" else " (absolute)"
            lines.append(
                f"{metric.name} ({_metric_unit(WORK_METRIC_UNITS, metric.name)}) | "
                f"{metric.baseline_median:,.4g} | {metric.candidate_median:,.4g} | "
                f"{metric.paired_change:+.1f}{unit} [{metric.ci95_low:+.1f},{metric.ci95_high:+.1f}]"
            )
        lines.append(
            "non-worker residual = process work CPU - summed worker CPU; not attributed to scavenger"
        )
        lines.append("drain metrics (drain phase; unit):")
        for name, unit in DRAIN_METRIC_UNITS:
            item = cell.drain_metric(name)
            lines.append(
                f"{name} ({unit}) | {item.baseline_median:,.4g} | {item.candidate_median:,.4g}"
            )
        user = cell.drain_metric("process_user_drain_ms")
        system = cell.drain_metric("process_system_drain_ms")
        release = cell.drain_metric("release_ms")
        lines.append(
            "drain (excluded from measured-work CPU/op and assessment): "
            f"process CPU {user.baseline_median + system.baseline_median:.4g} -> "
            f"{user.candidate_median + system.candidate_median:.4g} ms; "
            f"release {release.baseline_median:.4g} -> {release.candidate_median:.4g} ms"
        )
        lines.append(f"assessment: {cell.assessment}")
    return "\n".join(lines) + "\n"


def _host_metadata(isolation: str, stable_host_id: str = "") -> model.HostMetadata:
    cpuinfo = Path("/proc/cpuinfo").read_text()
    core_ids: set[tuple[str, str]] = set()
    for block in cpuinfo.split("\n\n"):
        physical_id: str | None = None
        core_id: str | None = None
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key.strip() == "physical id":
                physical_id = value.strip()
            elif key.strip() == "core id":
                core_id = value.strip()
        if physical_id is not None and core_id is not None:
            core_ids.add((physical_id, core_id))
    thp = Path("/sys/kernel/mm/transparent_hugepage/enabled")
    paranoid = Path("/proc/sys/kernel/perf_event_paranoid")
    ptrace_scope = Path("/proc/sys/kernel/yama/ptrace_scope")
    effective_capabilities = next(
        (
            line.split(":", 1)[1].strip()
            for line in Path("/proc/self/status").read_text().splitlines()
            if line.startswith("CapEff:")
        ),
        "unavailable",
    )
    return model.HostMetadata(
        cpu_model=perf_ab.cpu_model(),
        physical_cores=len(core_ids) or (os.cpu_count() or 1),
        logical_cores=os.cpu_count() or 1,
        thp_policy=thp.read_text().strip() if thp.exists() else "unavailable",
        perf_event_paranoid=paranoid.read_text().strip() if paranoid.exists() else "unavailable",
        yama_ptrace_scope=ptrace_scope.read_text().strip()
        if ptrace_scope.exists()
        else "unavailable",
        effective_capabilities_hex=effective_capabilities,
        isolation=cast(Literal["isolated", "shared"], isolation),
        stable_host_id=stable_host_id,
    )


def _resolve_sha(ref: str) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=perf_ab.ROOT, text=True
    ).strip()


def _runtime_options() -> model.RuntimeOptions:
    values = tuple(
        model.EnvOption(key, value)
        for key, value in sorted(os.environ.items())
        if key.startswith("MIMALLOC_")
    )
    return model.RuntimeOptions(values, values)


def _child_sample(output: str) -> model.ChildSample:
    try:
        value: object = json.loads(output)
        return model.ChildSample.parse(value)
    except (json.JSONDecodeError, ValueError) as error:
        raise DiagnosticError(f"invalid child diagnostic response: {error}") from error


def collect(
    base_ref: str,
    candidate_ref: str,
    repetitions: int,
    isolation: str,
    cells: tuple[str, ...] = CELLS,
    deep_output: Path | None = None,
    deep_profile_prefix: str | None = None,
    stable_host_id: str = "",
) -> model.RawRun:
    model.require(repetitions >= 2, "at least two paired repetitions required")
    base_sha, candidate_sha = _resolve_sha(base_ref), _resolve_sha(candidate_ref)
    bound_ms = int(json.loads(perf_ab.RATCHET.read_text())["bound_ms"])
    workloads = perf_ab.WORKLOADS | perf_ab.DIAGNOSTIC_WORKLOADS
    rows = tuple((name, workloads[name]) for name in cells)
    raw_cells: list[model.CellRaw] = []
    separate_deep_artifact: str | None = None
    deep_cell: str | None = None
    deep_event_estimates: tuple[model.DeepEventEstimate, ...] = ()
    with tempfile.TemporaryDirectory(prefix="large-span-diagnostic-") as directory:
        work = Path(directory)
        try:
            baseline_exe = perf_ab.build("base", base_sha, work, "plain", [], diagnostic=True)
            candidate_exe = perf_ab.build("head", candidate_sha, work, "plain", [], diagnostic=True)
            for name, (_kind, params) in rows:
                plan = model.WorkloadParams.from_perf_ab(params)
                pairs: list[model.PairRaw] = []
                for repetition in range(repetitions):
                    arm_order: tuple[model.Arm, model.Arm] = (
                        ("baseline", "candidate")
                        if repetition % 2 == 0
                        else ("candidate", "baseline")
                    )
                    first_exe = baseline_exe if arm_order[0] == "baseline" else candidate_exe
                    second_exe = baseline_exe if arm_order[1] == "baseline" else candidate_exe
                    first_output = perf_ab.run(
                        [str(first_exe), *plan.child_args(), str(bound_ms)],
                        env={**os.environ, "PERF_AB_DIAGNOSTIC": "1"},
                    )
                    second_output = perf_ab.run(
                        [str(second_exe), *plan.child_args(), str(bound_ms)],
                        env={**os.environ, "PERF_AB_DIAGNOSTIC": "1"},
                    )
                    first_child = _child_sample(first_output)
                    second_child = _child_sample(second_output)
                    baseline_child = first_child if arm_order[0] == "baseline" else second_child
                    candidate_child = first_child if arm_order[0] == "candidate" else second_child
                    pairs.append(
                        model.PairRaw(repetition, arm_order, baseline_child, candidate_child)
                    )
                raw_cells.append(
                    model.CellRaw(
                        name=name,
                        workers=params.threads,
                        seed=STREAM_SEED_BASE,
                        larson_table_seed_base=LARSON_TABLE_SEED_BASE,
                        phase_definition=PHASE_DEFINITION,
                        release_bound_ms=bound_ms,
                        params=plan,
                        pairs=tuple(pairs),
                    )
                )
            if deep_output is not None:
                import large_span_deep

                name = next(
                    (cell_name for cell_name, _row in rows if cell_name == "random-large-bursty/8"),
                    rows[0][0],
                )
                params = model.WorkloadParams.from_perf_ab(
                    next(row for cell_name, row in rows if cell_name == name)[1]
                )
                selected_cell = next(cell for cell in raw_cells if cell.name == name)
                baseline_command = [str(baseline_exe), *params.child_args(), str(bound_ms)]
                candidate_command = [str(candidate_exe), *params.child_args(), str(bound_ms)]
                artifact = large_span_deep.collect_paired_deep_diagnostic(
                    baseline_command,
                    candidate_command,
                    selected_cell.pairs[0].baseline.completed_operations,
                    base_sha,
                    candidate_sha,
                    name,
                    deep_output,
                    Path(deep_profile_prefix) if deep_profile_prefix is not None else None,
                )
                model.require(not deep_output.exists(), "deep output already exists")
                deep_output.write_text(json.dumps(asdict(artifact), indent=2) + "\n")
                separate_deep_artifact = str(deep_output)
                deep_cell = name
                deep_event_estimates = tuple(
                    model.DeepEventEstimate(
                        event=estimate.event,
                        baseline_per_operation=(
                            float(estimate.baseline_per_operation.value)
                            if isinstance(estimate.baseline_per_operation.value, (int, float))
                            else None
                        ),
                        candidate_per_operation=(
                            float(estimate.candidate_per_operation.value)
                            if isinstance(estimate.candidate_per_operation.value, (int, float))
                            else None
                        ),
                        paired_change_percent=(
                            float(estimate.paired_change_percent.value)
                            if isinstance(estimate.paired_change_percent.value, (int, float))
                            else None
                        ),
                        availability=(
                            "available"
                            if estimate.paired_change_percent.availability == "available"
                            else "unavailable"
                        ),
                        reason=estimate.paired_change_percent.reason,
                    )
                    for estimate in artifact.event_estimates
                )
        finally:
            for tree in work.glob("src-*"):
                perf_ab.run(["git", "worktree", "remove", "--force", str(tree)], cwd=perf_ab.ROOT)
    raw = model.RawRun(
        schema_version=SCHEMA_VERSION,
        baseline_sha=base_sha,
        candidate_sha=candidate_sha,
        build_flags=tuple(perf_ab.FLAGS),
        runtime_options=_runtime_options(),
        host=_host_metadata(isolation, stable_host_id),
        counter_catalog=portable_counter_catalog(),
        cells=tuple(raw_cells),
        separate_deep_artifact=separate_deep_artifact,
        deep_cell=deep_cell,
        deep_event_estimates=deep_event_estimates,
    )
    raw.validate()
    return raw


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="fixed old-fork git commit")
    parser.add_argument("--candidate", required=True, help="candidate git commit")
    parser.add_argument("--reps", type=int, default=7)
    parser.add_argument(
        "--cell",
        action="append",
        choices=CELLS,
        help="run only this cell (repeatable); default: all required #543 cells",
    )
    parser.add_argument("--isolation", choices=("isolated", "shared"), required=True)
    parser.add_argument(
        "--stable-host-id",
        default="",
        help="explicit stable host identity for cross-artifact matching; omit for smoke only",
    )
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument(
        "--deep-output",
        type=Path,
        help="optional separate old/candidate replay with perf/strace/cgroup/THP data",
    )
    parser.add_argument(
        "--deep-profile-prefix", help="optional perf record prefix; requires --deep-output"
    )
    args = parser.parse_args(argv)
    if args.deep_profile_prefix and args.deep_output is None:
        parser.error("--deep-profile-prefix requires --deep-output")
    for path in (args.raw, args.summary, args.deep_output):
        if path is not None and path.exists():
            parser.error(f"output already exists: {path}")
    if args.deep_profile_prefix:
        for suffix in (".cpu.data", ".faults.data"):
            if Path(args.deep_profile_prefix + suffix).exists():
                parser.error(f"profile output already exists: {args.deep_profile_prefix + suffix}")
    try:
        raw = collect(
            args.base,
            args.candidate,
            args.reps,
            args.isolation,
            tuple(args.cell) if args.cell else CELLS,
            args.deep_output,
            args.deep_profile_prefix,
            args.stable_host_id,
        )
        report = model.summarize(raw)
    except (DiagnosticError, ValueError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    args.raw.write_text(json.dumps(asdict(raw), indent=2) + "\n")
    args.summary.write_text(render(report))
    print(render(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
