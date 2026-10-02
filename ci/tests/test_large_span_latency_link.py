"""Typed fixture coverage for the #543/transaction-latency linker."""

from __future__ import annotations

# pyright: reportMissingTypeStubs=false
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

import large_span_diagnostic_model as span_model
import large_span_latency_link as link
import perf_ab

BASELINE = "a" * 40
CANDIDATE = "b" * 40
OLD_HASH = "c" * 64
NEW_HASH = "d" * 64
TRACE = "0123456789abcdef"
SEED = span_model.STREAM_SEED_BASE


def _snapshot(t: float, user: float, sys: float, rss: int) -> span_model.Snapshot:
    return span_model.Snapshot(t, user, sys, 0, 0, 1, 0, rss, rss)


def _child_sample(checksum: str, operations: int) -> span_model.ChildSample:
    start = _snapshot(1.0, 0.0, 0.0, 1 << 20)
    end = _snapshot(2.0, 0.5, 0.2, 2 << 20)
    drain_end = _snapshot(3.0, 0.51, 0.21, 1 << 20)
    work_delta = start.delta_to(end)
    drain_delta = end.delta_to(drain_end)
    return span_model.ChildSample(
        protocol_version=span_model.CHILD_VERSION,
        phase_definition=span_model.PHASE_DEFINITION,
        work_start=start,
        work_end=end,
        drain_start=end,
        drain_end=drain_end,
        worker_cpu_s=0.6,
        completed_operations=operations,
        trace_checksum=checksum,
        stream_seed_base=SEED,
        larson_table_seed_base=span_model.LARSON_TABLE_SEED_BASE,
        peak_work_rss_bytes=end.peak_rss_bytes,
        rss_after_drain_bytes=1 << 20,
        rss_at_release_bound_bytes=1 << 20,
        release_ms=10,
        work_delta=work_delta,
        drain_delta=drain_delta,
        work_metrics=span_model.WorkMetrics(
            work_delta.elapsed_s * 1000,
            operations / work_delta.elapsed_s,
            work_delta.user_s * 1000,
            work_delta.system_s * 1000,
            (work_delta.user_s + work_delta.system_s) * 1e9 / operations,
            0.6 * 1000,
            (work_delta.user_s + work_delta.system_s - 0.6) * 1000,
            work_delta.minor_faults,
            work_delta.major_faults,
            work_delta.voluntary_context_switches,
            work_delta.involuntary_context_switches,
            end.peak_rss_bytes / (1 << 20),
        ),
        drain_metrics=span_model.DrainMetrics(
            drain_delta.user_s * 1000,
            drain_delta.system_s * 1000,
            drain_delta.minor_faults,
            drain_delta.major_faults,
            drain_delta.voluntary_context_switches,
            drain_delta.involuntary_context_switches,
            (1 << 20) / (1 << 20),
            (1 << 20) / (1 << 20),
            10,
        ),
    )


def span_raw() -> span_model.RawRun:
    specs = (
        ("size 128 KiB/1 (#422)", perf_ab.DIAGNOSTIC_WORKLOADS["size 128 KiB/1 (#422)"][1], 1),
        ("random-large-bursty/8", perf_ab.WORKLOADS["random-large-bursty/8"][1], 8),
        ("large-class/8", perf_ab.WORKLOADS["large-class/8"][1], 8),
    )
    cells: list[span_model.CellRaw] = []
    for name, params, workers in specs:
        plan = span_model.WorkloadParams.from_perf_ab(params)
        operations = plan.ops * workers
        pairs = tuple(
            span_model.PairRaw(
                index,
                ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline"),
                _child_sample(TRACE, operations),
                _child_sample(TRACE, operations),
            )
            for index in range(2)
        )
        cells.append(
            span_model.CellRaw(
                name,
                workers,
                SEED,
                span_model.LARSON_TABLE_SEED_BASE,
                span_model.PHASE_DEFINITION,
                1500,
                plan,
                pairs,
            )
        )
    counter_catalog = (
        *tuple(
            span_model.CounterSpec(name, "fixture source", "process", "work", "count", "available")
            for name in (
                "user_s",
                "system_s",
                "worker_cpu_s",
                "minor_faults",
                "major_faults",
                "voluntary_context_switches",
                "involuntary_context_switches",
                "rss_bytes",
                "peak_work_rss_bytes",
            )
        ),
        span_model.CounterSpec(
            "host_thp_allocations",
            "fixture",
            "host",
            "work",
            "count",
            "unavailable",
            None,
            "fixture unavailable",
        ),
    )
    return span_model.RawRun(
        span_model.SCHEMA_VERSION,
        BASELINE,
        CANDIDATE,
        ("-DMI_PPROF=OFF",),
        span_model.RuntimeOptions((), ()),
        span_model.HostMetadata(
            "fixture CPU", 8, 16, "[madvise] always", "2", "1", "0000000000000000", "isolated"
        ),
        counter_catalog,
        tuple(cells),
    )


def distribution(value: float = 100.0) -> link.Distribution:
    return link.Distribution(100, value, value * 1.5, value * 2, 1, int(value * 3), 0)


def allocator_summary() -> link.AllocatorSummary:
    return link.AllocatorSummary(distribution(), distribution(2), True)


def effect(quantile: str) -> link.QuantileSummary:
    return link.QuantileSummary(
        quantile,
        link.PairedEffect(
            "mimalloc-pprof-candidate",
            "mimalloc-pprof-old-fork",
            "lower-is-better",
            2,
            0.95,
            link.ConfidenceInterval(0.9, 1.0, 0.95),
            True,
        ),
    )


def latency_raw() -> link.LatencyRaw:
    host = link.DiagnosticHost(
        "fixture CPU", 8, 16, "[madvise] always", "host-id", "e" * 64, "reported"
    )
    cells: list[link.LatencyCell] = []
    samples: list[link.LatencySampleEnvelope] = []
    for _span_name, workload_id, scenario, workers in link.REQUIRED_OVERLAPS:
        span_cell = next(item for item in span_raw().cells if item.name == _span_name)
        checksum = span_cell.pairs[0].baseline.trace_checksum
        ops_per_worker = span_cell.params.ops
        total = ops_per_worker * workers
        cells.append(
            link.LatencyCell(
                scenario,
                workload_id,
                checksum,
                str(workers),
                workers,
                ops_per_worker,
                4,
                "matched perf-ab transaction definition",
                allocator_summary(),
                allocator_summary(),
                (effect("p50"), effect("p95"), effect("p99")),
            )
        )
        for block in range(2):
            old_arm = "old-fork" if block % 2 == 0 else "candidate"
            for arm in (old_arm, "candidate" if old_arm == "old-fork" else "old-fork"):
                source = BASELINE if arm == "old-fork" else CANDIDATE
                sample = link.LatencySample(
                    arm,
                    0 if arm == old_arm else 1,
                    block,
                    scenario,
                    str(workers),
                    workers,
                    int(SEED, 16),
                    source,
                    4,
                    link.ChildIdentity(False, total, int(checksum, 16), 5, workers),
                    link.ChildIdentity(True, total, 1, 5, workers),
                )
                samples.append(link.LatencySampleEnvelope(arm, 0 if arm == old_arm else 1, sample))
    return link.LatencyRaw(
        link.LATENCY_SCHEMA,
        "diagnostic",
        1234,
        link.MEASUREMENT_SCOPE,
        host,
        link.DiagnosticSource(BASELINE, OLD_HASH, OLD_HASH),
        link.DiagnosticSource(CANDIDATE, NEW_HASH, NEW_HASH),
        tuple(cells),
        tuple(samples),
    )


def write_inputs(
    tmp_path: Path, *, span: span_model.RawRun | None = None, latency: link.LatencyRaw | None = None
) -> tuple[Path, Path]:
    span_path = tmp_path / "large-span.json"
    latency_path = tmp_path / "latency-large-object-diagnostic.json"
    span_path.write_text(json.dumps(asdict(span or span_raw())), encoding="utf-8")
    latency_json = asdict(latency or latency_raw())
    for row in latency_json["samples"]:
        sample = row["sample"]
        for child_name in ("measured", "control"):
            child = sample[child_name]
            child["observations"] = [{} for _ in range(child.pop("observation_count"))]
            child["scheduling"] = {"thread_count": child.pop("thread_count")}
    latency_path.write_text(json.dumps(latency_json), encoding="utf-8")
    return span_path, latency_path


def test_links_exact_work_identity_quantiles_coverage_and_host(tmp_path: Path) -> None:
    span_path, latency_path = write_inputs(tmp_path)
    result = link.link_artifacts(span_path, latency_path)
    assert result.baseline_sha == BASELINE
    assert result.candidate_sha == CANDIDATE
    assert result.host_match.transparent_hugepage_matches
    assert len(result.cells) == 3
    assert result.cells[1].workload_id == "random-large-bursty/8"
    assert [item.quantile for item in result.cells[0].paired_summaries] == ["p50", "p95", "p99"]
    assert result.cells[0].sample_coverage[0].block_ids == (0, 1)
    assert result.cells[0].total_operations == result.cells[0].operations_per_worker
    assert not result.acceptance_eligible
    assert "scopes remain separate" in result.interpretation


def test_smoke_links_without_stable_host_id_but_is_not_acceptance_eligible(
    tmp_path: Path,
) -> None:
    raw = latency_raw()
    host = replace(raw.host, stable_host_id="", stable_host_identity_status="not-provided")
    span_path, latency_path = write_inputs(tmp_path, latency=replace(raw, host=host))
    result = link.link_artifacts(span_path, latency_path)
    assert not result.acceptance_eligible
    assert not result.host_match.identity_comparable
    assert "omit stable_host_id" in result.host_match.identity_limitation


def test_matching_explicit_host_ids_and_isolation_enable_acceptance(tmp_path: Path) -> None:
    span = span_raw()
    span = replace(span, host=replace(span.host, stable_host_id="host-id"))
    span_path, latency_path = write_inputs(tmp_path, span=span)
    result = link.link_artifacts(span_path, latency_path)
    assert result.acceptance_eligible
    assert result.host_match.identity_comparable
    assert result.host_match.stable_host_id == "host-id"


def test_rejects_nonmatching_stable_host_ids(tmp_path: Path) -> None:
    span = span_raw()
    span = replace(span, host=replace(span.host, stable_host_id="other-host"))
    span_path, latency_path = write_inputs(tmp_path, span=span)
    with pytest.raises(link.LinkError, match="stable host ID mismatch"):
        link.link_artifacts(span_path, latency_path)


def test_matching_host_id_on_shared_host_is_not_acceptance_eligible(tmp_path: Path) -> None:
    span = span_raw()
    host = replace(span.host, stable_host_id="host-id", isolation="shared")
    span_path, latency_path = write_inputs(tmp_path, span=replace(span, host=host))
    result = link.link_artifacts(span_path, latency_path)
    assert result.host_match.identity_comparable
    assert not result.acceptance_eligible


def test_confident_tail_latency_regression_overrides_improving_span_rss(
    tmp_path: Path,
) -> None:
    raw = span_raw()
    cell = raw.cells[0]
    changed_pairs: list[span_model.PairRaw] = []
    for pair in cell.pairs:
        candidate = pair.candidate
        low_peak = 1 << 20
        low_end = replace(candidate.work_end, peak_rss_bytes=low_peak)
        low_candidate = replace(
            candidate,
            work_end=low_end,
            drain_start=low_end,
            peak_work_rss_bytes=low_peak,
            work_metrics=replace(candidate.work_metrics, peak_work_rss_mib=low_peak / (1 << 20)),
        )
        changed_pairs.append(replace(pair, candidate=low_candidate))
    cells = (replace(cell, pairs=tuple(changed_pairs)), *raw.cells[1:])
    latency = latency_raw()
    first = latency.cells[0]
    tail_quantiles = tuple(
        replace(
            item,
            summary=replace(
                item.summary,
                effect=0.8,
                confidence_interval=link.ConfidenceInterval(0.7, 0.9, 0.95),
            ),
        )
        if item.quantile in ("p95", "p99")
        else item
        for item in first.paired_summaries
    )
    linked_latency = replace(
        latency, cells=(replace(first, paired_summaries=tail_quantiles), *latency.cells[1:])
    )
    span_path, latency_path = write_inputs(
        tmp_path, span=replace(raw, cells=cells), latency=linked_latency
    )
    result = link.link_artifacts(span_path, latency_path)
    assessment = result.cells[0].assessment
    assert assessment.span_rss_direction == "decrease"
    assert assessment.latency_tail_regression
    assert assessment.decision == "REGRESSION"
    p95 = next(item for item in assessment.tail_effects if item.quantile == "p95")
    assert p95.ci95_low_percent > 10.0
    summary = link.render_linked_summary(result)
    assert "#543 work CPU/op:" in summary
    assert "#543 peak work RSS: 2 -> 1 MiB (decrease)" in summary
    assert "p50 +5.26%" in summary
    assert "p95 +25.00% [95% CI +11.11%, +42.86%]" in summary
    assert (
        "instrumentation control p50: old-fork 2 ns (overhead_valid=True); candidate 2 ns "
        "(overhead_valid=True)"
    ) in summary
    assert (
        "raw timed sample coverage: old-fork 2 samples/10 measured observations/10 control "
        "observations; candidate 2 samples/10 measured observations/10 control observations"
    ) in summary
    assert "combined assessment: REGRESSION" in summary
    serialized_link = json.dumps(asdict(result))
    assert '"decision": "REGRESSION"' in serialized_link
    assert assessment.decision in summary


def test_confident_lower_candidate_latency_is_not_a_tail_regression(tmp_path: Path) -> None:
    raw = latency_raw()
    first = raw.cells[0]
    tail_quantiles = tuple(
        replace(
            item,
            summary=replace(
                item.summary,
                confidence_interval=link.ConfidenceInterval(1.1, 1.3, 0.95),
            ),
        )
        if item.quantile in ("p95", "p99")
        else item
        for item in first.paired_summaries
    )
    linked_latency = replace(
        raw, cells=(replace(first, paired_summaries=tail_quantiles), *raw.cells[1:])
    )
    span_path, latency_path = write_inputs(tmp_path, latency=linked_latency)
    result = link.link_artifacts(span_path, latency_path)
    assessment = result.cells[0].assessment
    assert not assessment.latency_tail_regression
    assert assessment.decision != "REGRESSION"
    p95 = next(item for item in assessment.tail_effects if item.quantile == "p95")
    assert p95.ci95_high_percent < 0


def test_rejects_source_sha_mismatch(tmp_path: Path) -> None:
    raw = latency_raw()
    latency = replace(raw, candidate=replace(raw.candidate, source_sha="f" * 40))
    span_path, latency_path = write_inputs(tmp_path, latency=latency)
    with pytest.raises(link.LinkError, match="candidate source SHA mismatch"):
        link.link_artifacts(span_path, latency_path)


def test_rejects_host_mismatch(tmp_path: Path) -> None:
    raw = latency_raw()
    latency = replace(raw, host=replace(raw.host, logical_cores=32))
    span_path, latency_path = write_inputs(tmp_path, latency=latency)
    with pytest.raises(link.LinkError, match="logical-core"):
        link.link_artifacts(span_path, latency_path)


def test_rejects_workload_operation_or_trace_mismatch(tmp_path: Path) -> None:
    raw = latency_raw()
    cells = (
        replace(raw.cells[0], transactions_per_worker=raw.cells[0].transactions_per_worker + 1),
        *raw.cells[1:],
    )
    span_path, latency_path = write_inputs(tmp_path, latency=replace(raw, cells=cells))
    with pytest.raises(link.LinkError, match="operation-count"):
        link.link_artifacts(span_path, latency_path)

    raw = latency_raw()
    cells = (replace(raw.cells[0], trace_checksum="fedcba9876543210"), *raw.cells[1:])
    span_path, latency_path = write_inputs(tmp_path, latency=replace(raw, cells=cells))
    with pytest.raises(link.LinkError, match="trace-checksum"):
        link.link_artifacts(span_path, latency_path)


def test_invalid_overhead_is_published_without_a_latency_decision(tmp_path: Path) -> None:
    raw = latency_raw()
    first = raw.cells[0]
    tail_quantiles = tuple(
        replace(
            item,
            summary=replace(
                item.summary,
                effect=0.8,
                confidence_interval=link.ConfidenceInterval(0.7, 0.9, 0.95),
            ),
        )
        if item.quantile in ("p95", "p99")
        else item
        for item in first.paired_summaries
    )
    cells = (
        replace(
            first,
            candidate=replace(first.candidate, overhead_valid=False),
            paired_summaries=tail_quantiles,
        ),
        *raw.cells[1:],
    )
    span_path, latency_path = write_inputs(tmp_path, latency=replace(raw, cells=cells))
    result = link.link_artifacts(span_path, latency_path)
    assessment = result.cells[0].assessment
    assert not result.acceptance_eligible
    assert not assessment.latency_control_valid
    assert assessment.latency_tail_regression is None
    assert assessment.decision == span_model.summarize(span_raw()).cells[0].assessment
    summary = link.render_linked_summary(result)
    assert "tail effect estimates are descriptive only" in summary
    assert "overhead_valid=False" in summary


def test_rejects_missing_latency_samples(tmp_path: Path) -> None:
    raw = latency_raw()

    raw = latency_raw()
    samples = tuple(
        item
        for item in raw.samples
        if not (item.sample.scenario_id == raw.cells[0].scenario_id and item.arm == "candidate")
    )
    span_path, latency_path = write_inputs(tmp_path, latency=replace(raw, samples=samples))
    with pytest.raises(link.LinkError, match="raw sample coverage"):
        link.link_artifacts(span_path, latency_path)


def test_rejects_ci_without_positive_ordered_interval(tmp_path: Path) -> None:
    raw = latency_raw()
    bad_effect = replace(
        raw.cells[0].paired_summaries[0].summary,
        confidence_interval=link.ConfidenceInterval(1.1, 1.0, 0.95),
    )
    quantiles = (
        replace(raw.cells[0].paired_summaries[0], summary=bad_effect),
        *raw.cells[0].paired_summaries[1:],
    )
    cells = (replace(raw.cells[0], paired_summaries=quantiles), *raw.cells[1:])
    span_path, latency_path = write_inputs(tmp_path, latency=replace(raw, cells=cells))
    with pytest.raises(link.LinkError, match="reversed"):
        link.link_artifacts(span_path, latency_path)
