"""#543: typed executable contract for phase-correct diagnostics."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import cast

import pytest

import large_span_deep
import large_span_diagnostic as diagnostic
import large_span_diagnostic_model as model


def snapshot(
    t: float, user: float, system: float, minor: int, rss: int, *, major: int = 0
) -> model.Snapshot:
    return model.Snapshot(t, user, system, minor, major, 10, 2, rss, rss)


def arm(
    candidate: bool,
    *,
    drain_user_extra: float = 0,
    drain_system_extra: float = 0,
    release_extra: int = 0,
    major_faults: int = 0,
) -> model.ChildSample:
    start = snapshot(1, 0, 0, 0, 32 << 20)
    elapsed = 0.51 if candidate else 0.5
    work_user = 0.86 if candidate else 0.85
    work_system = 0.22 if candidate else 0.15
    end = snapshot(
        1 + elapsed,
        work_user,
        work_system,
        1800 if candidate else 1200,
        (220 if candidate else 500) << 20,
        major=major_faults,
    )
    drain_end = snapshot(
        1 + elapsed + 1,
        work_user + 0.02 + drain_user_extra,
        work_system + 0.005 + drain_system_extra,
        end.minor_faults,
        40 << 20,
        major=major_faults,
    )
    work_delta = start.delta_to(end)
    drain_delta = end.delta_to(drain_end)
    completed = 800_000
    worker = 0.96 if candidate else 0.92
    peak = end.peak_rss_bytes
    after = 40 << 20
    release_ms = (20 if candidate else 25) + release_extra
    work_metrics = model.WorkMetrics(
        elapsed_work_ms=work_delta.elapsed_s * 1000,
        throughput_ops_per_s=completed / work_delta.elapsed_s,
        process_user_work_ms=work_delta.user_s * 1000,
        process_system_work_ms=work_delta.system_s * 1000,
        process_cpu_per_operation_ns=(work_delta.user_s + work_delta.system_s) * 1e9 / completed,
        worker_cpu_work_ms=worker * 1000,
        non_worker_residual_work_ms=(work_delta.user_s + work_delta.system_s - worker) * 1000,
        minor_faults_work=work_delta.minor_faults,
        major_faults_work=work_delta.major_faults,
        voluntary_context_switches_work=work_delta.voluntary_context_switches,
        involuntary_context_switches_work=work_delta.involuntary_context_switches,
        peak_work_rss_mib=peak / (1 << 20),
    )
    drain_metrics = model.DrainMetrics(
        process_user_drain_ms=drain_delta.user_s * 1000,
        process_system_drain_ms=drain_delta.system_s * 1000,
        minor_faults_drain=drain_delta.minor_faults,
        major_faults_drain=drain_delta.major_faults,
        voluntary_context_switches_drain=drain_delta.voluntary_context_switches,
        involuntary_context_switches_drain=drain_delta.involuntary_context_switches,
        rss_after_drain_mib=after / (1 << 20),
        rss_at_release_bound_mib=after / (1 << 20),
        release_ms=release_ms,
    )
    return model.ChildSample(
        protocol_version=model.CHILD_VERSION,
        phase_definition=model.PHASE_DEFINITION,
        work_start=start,
        work_end=end,
        drain_start=end,
        drain_end=drain_end,
        worker_cpu_s=worker,
        completed_operations=completed,
        trace_checksum="abc0123456789def",
        stream_seed_base=model.STREAM_SEED_BASE,
        larson_table_seed_base=model.LARSON_TABLE_SEED_BASE,
        peak_work_rss_bytes=peak,
        rss_after_drain_bytes=after,
        rss_at_release_bound_bytes=after,
        release_ms=release_ms,
        work_delta=work_delta,
        drain_delta=drain_delta,
        work_metrics=work_metrics,
        drain_metrics=drain_metrics,
    )


def fixture(
    *,
    candidate_faults: int = 0,
    drain_user_extra: float = 0,
    drain_system_extra: float = 0,
    release_extra: int = 0,
) -> model.RawRun:
    params = model.WorkloadParams.from_perf_ab(
        diagnostic.perf_ab.WORKLOADS["random-large-bursty/8"][1]._replace(ops=100_000)
    )
    pairs = tuple(
        model.PairRaw(
            repetition=index,
            arm_order=("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline"),
            baseline=arm(False),
            candidate=arm(
                True,
                drain_user_extra=drain_user_extra,
                drain_system_extra=drain_system_extra,
                release_extra=release_extra,
                major_faults=candidate_faults,
            ),
        )
        for index in range(7)
    )
    cell = model.CellRaw(
        name="random-large-bursty/8",
        workers=8,
        seed=model.STREAM_SEED_BASE,
        larson_table_seed_base=model.LARSON_TABLE_SEED_BASE,
        phase_definition=model.PHASE_DEFINITION,
        release_bound_ms=1500,
        params=params,
        pairs=pairs,
    )
    return model.RawRun(
        schema_version=model.SCHEMA_VERSION,
        baseline_sha="a" * 40,
        candidate_sha="b" * 40,
        build_flags=("-DMI_PPROF=OFF",),
        runtime_options=model.RuntimeOptions((), ()),
        host=model.HostMetadata(
            "fixture CPU", 8, 16, "madvise", "2", "1", "0000000000000000", "isolated"
        ),
        counter_catalog=diagnostic.portable_counter_catalog(),
        cells=(cell,),
    )


def json_boundary(raw: model.RawRun) -> dict[str, object]:
    value: object = json.loads(json.dumps(asdict(raw)))
    return dict(model.mapping(value, "test JSON fixture"))


def _nested_object(value: object) -> dict[str, object]:
    return cast(dict[str, object], model.mapping(value, "test nested JSON object"))


def _cells(raw: dict[str, object]) -> list[object]:
    return list(model.sequence(raw["cells"], "test cells"))


def _pairs(raw: dict[str, object]) -> list[object]:
    cell = _nested_object(_cells(raw)[0])
    return list(model.sequence(cell["pairs"], "test pairs"))


def _sample_at(raw: dict[str, object], pair_index: int, arm_name: str) -> dict[str, object]:
    pair = _nested_object(_pairs(raw)[pair_index])
    return _nested_object(pair[arm_name])


def test_synthetic_fixture_is_regression_despite_rss_win() -> None:
    report = diagnostic.summarize(fixture())
    text = diagnostic.render(report)
    cell = report.cells[0]
    assert cell.assessment == "REGRESSION"
    assert cell.metric("process_cpu_per_operation_ns").baseline_median == 1250
    assert cell.metric("process_cpu_per_operation_ns").candidate_median == 1350
    assert cell.metric("throughput_ops_per_s").baseline_median == 1_600_000
    assert cell.metric("peak_work_rss_mib").candidate_median == 220
    assert "non-worker residual" in text
    assert "REGRESSION" in text


@pytest.mark.parametrize("missing", ["work_start", "drain_start", "worker_cpu_s", "trace_checksum"])
def test_missing_phase_or_work_identity_rejected(missing: str) -> None:
    raw = json_boundary(fixture())
    del _sample_at(raw, 0, "baseline")[missing]
    with pytest.raises(ValueError):
        diagnostic.validate_raw(raw)


def test_mismatched_checksum_rejected() -> None:
    raw = json_boundary(fixture())
    _sample_at(raw, 0, "candidate")["trace_checksum"] = "bad"
    with pytest.raises(ValueError, match="checksum"):
        diagnostic.validate_raw(raw)


def test_mismatched_child_seed_rejected() -> None:
    raw = json_boundary(fixture())
    _sample_at(raw, 0, "candidate")["stream_seed_base"] = "0000000000000001"
    with pytest.raises(ValueError, match="seed"):
        diagnostic.validate_raw(raw)


def test_equal_but_wrong_work_count_rejected() -> None:
    raw = json_boundary(fixture())
    cell = _nested_object(_cells(raw)[0])
    params = _nested_object(cell["params"])
    params["ops"] = 200_000
    cell["params"] = params
    with pytest.raises(ValueError, match="fixed workload plan"):
        diagnostic.validate_raw(raw)


def test_missing_counter_provenance_or_availability_rejected() -> None:
    raw = json_boundary(fixture())
    catalog = list(model.sequence(raw["counter_catalog"], "counter catalog"))
    bad = _nested_object(catalog[3])
    bad.pop("source")
    catalog[3] = bad
    raw["counter_catalog"] = catalog
    with pytest.raises(ValueError, match="counter"):
        diagnostic.validate_raw(raw)

    raw = json_boundary(fixture())
    catalog = list(model.sequence(raw["counter_catalog"], "counter catalog"))
    bad = _nested_object(catalog[3])
    bad.pop("scope")
    catalog[3] = bad
    raw["counter_catalog"] = catalog
    with pytest.raises(ValueError, match="counter"):
        diagnostic.validate_raw(raw)

    raw = json_boundary(fixture())
    catalog = list(model.sequence(raw["counter_catalog"], "counter catalog"))
    catalog[-1] = {
        "name": "host_thp_allocations",
        "source": "/proc/vmstat",
        "scope": "host-wide",
        "phase": "work",
        "unit": "count",
        "status": "unavailable",
        "value": 0,
    }
    raw["counter_catalog"] = catalog
    with pytest.raises(ValueError, match="unavailable"):
        diagnostic.validate_raw(raw)


def test_missing_phase_definition_rejected() -> None:
    raw = json_boundary(fixture())
    cell = _nested_object(_cells(raw)[0])
    del cell["phase_definition"]
    with pytest.raises(ValueError, match="phase"):
        diagnostic.validate_raw(raw)
    raw = json_boundary(fixture())
    _sample_at(raw, 0, "candidate")["phase_definition"] = "different phase"
    with pytest.raises(ValueError, match="phase"):
        diagnostic.validate_raw(raw)


def test_drain_only_change_leaves_work_metrics_and_decision_unchanged() -> None:
    first = diagnostic.summarize(fixture()).cells[0]
    changed = diagnostic.summarize(
        fixture(drain_user_extra=25, drain_system_extra=30, release_extra=100)
    ).cells[0]
    assert first.metrics == changed.metrics
    assert first.assessment == changed.assessment


def test_overlapping_interval_is_inconclusive() -> None:
    raw = fixture()
    cell = raw.cells[0]
    pairs = tuple(replace(pair, candidate=pair.baseline) for pair in cell.pairs)
    raw = replace(raw, cells=(replace(cell, pairs=pairs),))
    assert diagnostic.summarize(raw).cells[0].assessment == "INCONCLUSIVE"


def test_zero_baseline_fault_count_uses_absolute_effect_not_fake_percentage() -> None:
    report = diagnostic.summarize(fixture(candidate_faults=2))
    fault = report.cells[0].metric("major_faults_work")
    assert fault.effect_unit == "absolute"
    assert fault.paired_change == 2
    assert [fault.ci95_low, fault.ci95_high] == [2, 2]


def test_deep_event_rows_match_typed_raw_artifact() -> None:
    raw = replace(
        fixture(),
        separate_deep_artifact="deep.json",
        deep_cell="random-large-bursty/8",
        deep_event_estimates=(
            model.DeepEventEstimate("cycles", 2000, 2180, 9, "available"),
            model.DeepEventEstimate(
                "instructions", None, None, None, "unavailable", "PMU permission denied"
            ),
        ),
    )
    round_tripped = model.RawRun.parse(json.loads(json.dumps(asdict(raw))))
    assert round_tripped.deep_event_estimates == raw.deep_event_estimates
    report = diagnostic.render(diagnostic.summarize(round_tripped))
    assert "cycles | 2,000 | 2,180 | +9.0%" in report
    assert "instructions | unavailable (null; PMU permission denied)" in report
    assert "no timed-run CI" in report
    assert "for random-large-bursty/8" in report
    invalid = replace(
        round_tripped,
        deep_event_estimates=(
            replace(round_tripped.deep_event_estimates[0], paired_change_percent=8),
        ),
    )
    with pytest.raises(ValueError, match="arithmetic"):
        invalid.validate()


def test_deep_replay_starts_only_after_all_timed_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    def fake_sha(ref: str) -> str:
        return "a" * 40 if ref == "old" else "b" * 40

    def fake_host(isolation: str, stable_host_id: str = "") -> model.HostMetadata:
        return fixture().host

    def fake_build(
        arm_name: str, sha: str, work: Path, kind: str, defs: list[str], diagnostic: bool = False
    ) -> Path:
        assert diagnostic
        return work / arm_name / "perf_ab"

    monkeypatch.setattr(diagnostic, "_resolve_sha", fake_sha)
    monkeypatch.setattr(diagnostic, "_host_metadata", fake_host)
    monkeypatch.setattr(diagnostic.perf_ab, "build", fake_build)

    def fake_run(
        command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None
    ) -> str:
        events.append("timed")
        assert env is not None and env["PERF_AB_DIAGNOSTIC"] == "1"
        sample = arm(False)
        sample = replace(sample, completed_operations=int(command[1]) * int(command[5]))
        return json.dumps(asdict(sample))

    @dataclass
    class FakeDeepArtifact:
        schema_version: str
        baseline_sha: str
        candidate_sha: str
        cell: str
        event_estimates: tuple[large_span_deep.PairedEventEstimate, ...] = ()

    def fake_deep(
        baseline_command: list[str],
        candidate_command: list[str],
        operations_per_arm: int,
        baseline_sha: str,
        candidate_sha: str,
        cell: str,
        output: Path,
        profile_prefix: Path | None = None,
    ) -> FakeDeepArtifact:
        events.append("deep")
        assert profile_prefix is None
        assert baseline_command[1:] == candidate_command[1:]
        assert operations_per_arm == 6_400_000
        return FakeDeepArtifact("large-span-paired-deep-v1", baseline_sha, candidate_sha, cell)

    monkeypatch.setattr(diagnostic.perf_ab, "run", fake_run)
    monkeypatch.setattr(large_span_deep, "collect_paired_deep_diagnostic", fake_deep)
    deep = tmp_path / "deep.json"
    raw = diagnostic.collect("old", "new", 2, "shared", (diagnostic.CELLS[0],), deep_output=deep)
    assert events == ["timed", "timed", "timed", "timed", "deep"]
    assert raw.separate_deep_artifact == str(deep)
    assert raw.deep_cell == diagnostic.CELLS[0]
    assert json.loads(deep.read_text())["candidate_sha"] == "b" * 40
    assert json.loads(deep.read_text())["baseline_sha"] == "a" * 40
