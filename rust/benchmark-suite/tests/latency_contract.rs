use std::collections::BTreeMap;

use benchmark_suite::execution::expected_touch_checksum;
use benchmark_suite::latency::{
    block_bootstrap_quantile_effect, build_latency_diagnostic_cell_summary,
    deterministic_sample_indices, latency_diagnostic_scenario_cells, latency_scenario_cells,
    overhead_is_valid, summarize_latency, transaction_definition, validate_latency_diagnostic_run,
    validate_latency_raw_run, ContextSwitchCounts, LatencyChildRequest, LatencyChildResponse,
    LatencyClock, LatencyDiagnosticHost, LatencyDiagnosticRun, LatencyDiagnosticSample,
    LatencyDiagnosticSource, LatencyObservation, LatencyRawRun, LatencyRawSample,
    LatencyScheduling, LATENCY_CHILD_PROTOCOL_VERSION, LATENCY_DIAGNOSTIC_ISOLATION_CLAIM,
    LATENCY_DIAGNOSTIC_SCOPE, LATENCY_SCHEMA_VERSION,
};
use benchmark_suite::model::{
    AllocatorIdentity, BenchmarkChildRequest, CellCalibration, RunnerMetadata, ToolchainMetadata,
};
use benchmark_suite::scenarios::{card, CardId, ScenarioCell, ThreadPoint, Topology};
use benchmark_suite::validate::synthetic_full_fixture;

fn response(control: bool, values: &[u64]) -> LatencyChildResponse {
    LatencyChildResponse {
        protocol_version: LATENCY_CHILD_PROTOCOL_VERSION.into(),
        metric_schema_version: LATENCY_SCHEMA_VERSION.into(),
        control,
        completed_transactions: values.len() as u64,
        checksum: 1,
        observations: values
            .iter()
            .enumerate()
            .map(|(index, duration_ns)| LatencyObservation {
                thread_index: 0,
                transaction_index: index as u64,
                duration_ns: *duration_ns,
            })
            .collect(),
        scheduling: LatencyScheduling {
            affinity_policy: "linux:unrestricted".into(),
            actual_cpu_ids: vec![Some(0)],
            thread_count: 1,
            physical_cores: 1,
            logical_cores: 1,
            context_switches: ContextSwitchCounts {
                voluntary: 0,
                involuntary: 0,
            },
            runner_class: "test".into(),
            clock: LatencyClock {
                source: "monotonic".into(),
                implementation: "fixture".into(),
                resolution_ns: 1,
            },
        },
    }
}

fn sample(block: u32, allocator: &str, measured: &[u64]) -> LatencyRawSample {
    LatencyRawSample {
        metric_schema_version: LATENCY_SCHEMA_VERSION.into(),
        block_id: block,
        ordinal: 0,
        workload_seed: 17 + u64::from(block),
        allocator_id: allocator.into(),
        allocator_source_sha: "a".repeat(40),
        child_binary_sha256: "b".repeat(64),
        scenario_id: "tiny-fixed-64".into(),
        thread_point: "1".into(),
        thread_count: 1,
        sample_denominator: 1,
        transaction_definition: transaction_definition(CardId::TinyFixed64).into(),
        measured: response(false, measured),
        control: response(true, &vec![1; measured.len()]),
    }
}

fn diagnostic_sample(
    block: u32,
    ordinal: u8,
    arm: &str,
    card: CardId,
    point: ThreadPoint,
    denominator: u64,
) -> LatencyDiagnosticSample {
    let count = match point {
        ThreadPoint::One => 1,
        ThreadPoint::Eight => 8,
        _ => unreachable!(),
    };
    let workload = benchmark_suite::perf_ab_trace::workload(card, count as usize).unwrap();
    let seed = benchmark_suite::perf_ab_trace::PERF_AB_STREAM_SEED_BASE;
    let transactions = workload.operations_per_worker;
    let mut value = sample(block, "mimalloc-pprof", &[100]);
    value.workload_seed = seed;
    value.ordinal = ordinal;
    value.scenario_id = card.as_str().into();
    value.thread_point = point.name().into();
    value.thread_count = count;
    value.sample_denominator = denominator;
    value.transaction_definition = transaction_definition(card).into();
    value.allocator_source_sha = if arm == "old-fork" {
        "c".repeat(40)
    } else {
        "a".repeat(40)
    };
    value.child_binary_sha256 = if arm == "old-fork" {
        "d".repeat(64)
    } else {
        "b".repeat(64)
    };
    value.measured.checksum =
        benchmark_suite::perf_ab_trace::trace_checksum(workload, count as usize);
    value.control.checksum = 1;
    for (control, response) in [(&mut value.control, true), (&mut value.measured, false)] {
        control.completed_transactions = transactions * count as u64;
        control.observations = (0..count)
            .flat_map(|worker| {
                benchmark_suite::latency::deterministic_sample_indices(
                    seed,
                    worker,
                    transactions,
                    denominator,
                )
                .unwrap()
                .into_iter()
                .map(move |transaction| LatencyObservation {
                    thread_index: worker,
                    transaction_index: transaction,
                    duration_ns: if response {
                        10
                    } else {
                        100 + u64::from(ordinal) * 10
                    },
                })
            })
            .collect();
        control.scheduling.thread_count = count;
        control.scheduling.physical_cores = 8;
        control.scheduling.logical_cores = 8;
        control.scheduling.affinity_policy = "linux:unrestricted".into();
        control.scheduling.actual_cpu_ids = vec![Some(0); count as usize];
    }
    LatencyDiagnosticSample {
        arm: arm.into(),
        execution_order: ordinal,
        sample: value,
    }
}

#[test]
fn reciprocal_throughput_is_not_latency_input() {
    let reciprocal = br#"{"metric_schema_version":"transaction-latency-v1","ns_per_op":12.5}"#;
    assert!(serde_json::from_slice::<benchmark_suite::latency::LatencyRawRun>(reciprocal).is_err());
}

#[test]
fn default_latency_child_request_serialization_is_unchanged() {
    let allocator = AllocatorIdentity {
        allocator_id: "mimalloc-pprof".into(),
        allocator_version: "test".into(),
        source_sha: "a".repeat(40),
        library_sha256: "b".repeat(64),
        child_binary_sha256: "c".repeat(64),
    };
    let request = LatencyChildRequest {
        protocol_version: LATENCY_CHILD_PROTOCOL_VERSION.into(),
        metric_schema_version: LATENCY_SCHEMA_VERSION.into(),
        sample_denominator: 1,
        expected_trace_checksum: None,
        control: false,
        runner_class: "test".into(),
        affinity_policy: "linux:unrestricted".into(),
        benchmark: BenchmarkChildRequest {
            protocol_version: "benchmark-child-v1".into(),
            schema_version: benchmark_suite::RAW_SCHEMA_VERSION.into(),
            suite_version: benchmark_suite::CORE_SUITE_VERSION.into(),
            run_kind: "headline".into(),
            execution_mode: "normal".into(),
            run_seed: 1,
            block_id: 0,
            ordinal: 0,
            workload_seed: 1,
            allocator,
            scenario_id: CardId::TinyFixed64.as_str().into(),
            scenario_version: benchmark_suite::CORE_SUITE_VERSION.into(),
            thread_point: "1".into(),
            physical_cores: 1,
            logical_cores: 1,
            transactions_per_worker: 1,
            warmup_transactions_per_worker: 1,
            reproduction_command: "test".into(),
            runner: RunnerMetadata {
                os: "linux".into(),
                architecture: "x86_64".into(),
                physical_cores: 1,
                logical_cores: 1,
            },
            toolchain: ToolchainMetadata {
                rustc: "test".into(),
                target: "x86_64-unknown-linux-gnu".into(),
                compiler: "test".into(),
                linker: "test".into(),
            },
        },
    };
    let value = serde_json::to_value(request).unwrap();
    assert_eq!(value["protocol_version"], "transaction-latency-child-v1");
    assert!(value.get("expected_trace_checksum").is_none());
}

#[test]
fn exact_128k_one_and_eight_worker_cells_are_opt_in() {
    let topology = Topology {
        physical_cores: 8,
        logical_cores: 8,
    };
    assert_eq!(latency_scenario_cells(topology).unwrap().len(), 5);
    let diagnostic = latency_diagnostic_scenario_cells(topology).unwrap();
    assert_eq!(diagnostic.len(), 4);
    assert_eq!(diagnostic[0].0, CardId::LargeObject128KiB);
    assert_eq!(diagnostic[0].1.name(), "1");
    assert_eq!(diagnostic[1].1.name(), "8");

    let mut samples = Vec::new();
    let mut cells = Vec::new();
    for &(card, point, _) in &diagnostic {
        let denominator = 1024;
        let rows = (0..2)
            .flat_map(|block| {
                let first = if block % 2 == 0 {
                    "old-fork"
                } else {
                    "candidate"
                };
                [
                    first,
                    if first == "old-fork" {
                        "candidate"
                    } else {
                        "old-fork"
                    },
                ]
                .into_iter()
                .enumerate()
                .map(move |(order, arm)| {
                    diagnostic_sample(block, order as u8, arm, card, point, denominator)
                })
            })
            .collect::<Vec<_>>();
        cells.push(
            build_latency_diagnostic_cell_summary(
                0x543,
                point.name(),
                &rows.iter().collect::<Vec<_>>(),
            )
            .unwrap(),
        );
        samples.extend(rows);
    }
    assert_eq!(cells[1].thread_count, 8);
    assert_eq!(cells[0].paired_summaries.len(), 3);
    assert!(cells[0]
        .paired_summaries
        .iter()
        .all(|value| value.summary.block_count == 2));

    let run = LatencyDiagnosticRun {
        metric_schema_version: LATENCY_SCHEMA_VERSION.into(),
        status: "diagnostic".into(),
        run_seed: 0x543,
        measurement_scope: LATENCY_DIAGNOSTIC_SCOPE.into(),
        host: LatencyDiagnosticHost {
            stable_host_id: "test-host".into(),
            stable_host_identity_status: "reported".into(),
            stable_host_identity_source: "runner-reported".into(),
            runner_fingerprint_sha256: "f".repeat(64),
            cpu_model: "test-cpu".into(),
            physical_cores: 8,
            logical_cores: 8,
            target: "x86_64-unknown-linux-gnu".into(),
            transparent_hugepage: "[madvise] always never".into(),
            affinity_policy: "linux:unrestricted".into(),
            affinity_logical_cpu_ids: (0..8).collect(),
            isolation_claim: LATENCY_DIAGNOSTIC_ISOLATION_CLAIM.into(),
        },
        old_fork: LatencyDiagnosticSource {
            source_sha: "c".repeat(40),
            library_sha256: "e".repeat(64),
            child_binary_sha256: "d".repeat(64),
        },
        candidate: LatencyDiagnosticSource {
            source_sha: "a".repeat(40),
            library_sha256: "f".repeat(64),
            child_binary_sha256: "b".repeat(64),
        },
        cells,
        samples,
    };
    validate_latency_diagnostic_run(&run, 2).unwrap();

    let mut missing_pair = run.clone();
    missing_pair.samples.pop();
    assert!(validate_latency_diagnostic_run(&missing_pair, 2).is_err());
    let mut wrong_cell = run.clone();
    wrong_cell.cells[1].thread_count = 4;
    assert!(validate_latency_diagnostic_run(&wrong_cell, 2).is_err());

    let mut unpaired_trace = run.clone();
    unpaired_trace.samples[1].sample.workload_seed += 1;
    assert!(validate_latency_diagnostic_run(&unpaired_trace, 2).is_err());
    let mut non_alternating = run;
    non_alternating.samples[0].execution_order = 1;
    assert!(validate_latency_diagnostic_run(&non_alternating, 2).is_err());
}

#[test]
fn deterministic_schedule_replays_and_pairs_allocators() {
    let first = deterministic_sample_indices(123, 2, 10_000, 1024).unwrap();
    let second = deterministic_sample_indices(123, 2, 10_000, 1024).unwrap();
    let other_seed = deterministic_sample_indices(124, 2, 10_000, 1024).unwrap();
    assert_eq!(first, second);
    assert_ne!(first, other_seed);
    assert!(first.windows(2).all(|pair| pair[1] - pair[0] == 1024));
}

#[test]
fn type7_quantiles_mad_iqr_and_extreme_tail_are_retained() {
    let ordinary = summarize_latency(&[1, 2, 3, 4]).unwrap();
    assert_eq!(ordinary.p50_ns, 2.5);
    assert_eq!(ordinary.p95_ns, 3.8499999999999996);
    assert_eq!(ordinary.iqr_ns, 1.5);
    assert_eq!(ordinary.median_absolute_deviation_ns, 1.0);

    let mut tail = vec![100; 99];
    tail.push(100_000);
    let summary = summarize_latency(&tail).unwrap();
    assert_eq!(summary.count, 100);
    assert_eq!(summary.max_ns, 100_000);
    assert!(summary.p99_ns > 100.0);
}

#[test]
fn zero_duration_and_overhead_thresholds_fail_closed() {
    assert!(summarize_latency(&[1, 0, 2]).is_err());
    let measured = summarize_latency(&vec![1_000; 100]).unwrap();
    let good_control = summarize_latency(&vec![40; 100]).unwrap();
    let median_too_high = summarize_latency(&vec![51; 100]).unwrap();
    let tail_too_close = summarize_latency(&vec![600; 100]).unwrap();
    assert!(overhead_is_valid(&measured, &good_control));
    assert!(!overhead_is_valid(&measured, &median_too_high));
    assert!(!overhead_is_valid(&measured, &tail_too_close));
}

#[test]
fn block_bootstrap_keeps_within_block_transactions_and_is_lower_better() {
    let samples = vec![
        sample(0, "mimalloc-pprof", &[50, 50, 50]),
        sample(0, "upstream-mimalloc", &[100, 100, 100]),
        sample(1, "mimalloc-pprof", &[100, 100, 100]),
        sample(1, "upstream-mimalloc", &[200, 200, 200]),
    ];
    let effect = block_bootstrap_quantile_effect(
        99,
        "tiny-fixed-64/1/p99",
        "mimalloc-pprof",
        "upstream-mimalloc",
        0.99,
        &samples,
    )
    .unwrap();
    assert_eq!(effect.effect, 2.0);
    assert_eq!(effect.confidence_interval.lower, 2.0);
    assert_eq!(effect.confidence_interval.upper, 2.0);
    assert_eq!(effect.block_count, 2);
    assert_eq!(
        effect.bootstrap.method,
        "percentile-whole-block-transaction-quantile-type7-v1"
    );
}

#[test]
fn transaction_labels_are_end_to_end_not_allocator_calls() {
    for card in [
        CardId::TinyFixed64,
        CardId::SmallLogMixed,
        CardId::CrossThreadProducerConsumer,
        CardId::LargeObjects,
    ] {
        let definition = transaction_definition(card);
        assert!(!definition.contains("allocator-call"));
        assert!(definition.contains("allocation"));
        assert!(definition.contains("free"));
    }
}

fn complete_raw_fixture() -> LatencyRawRun {
    let throughput = synthetic_full_fixture().unwrap();
    let topology = Topology {
        physical_cores: throughput.runner.physical_cores as usize,
        logical_cores: throughput.runner.logical_cores as usize,
    };
    let cells = benchmark_suite::latency::latency_scenario_cells(topology).unwrap();
    let cell_keys = cells
        .iter()
        .map(|(card, point, _)| (card.as_str(), point.name()))
        .collect::<Vec<_>>();
    let mut transactions = BTreeMap::new();
    let calibrations = cells
        .iter()
        .map(|(card_id, point, _)| {
            let threads = topology.resolve(*point).unwrap();
            let count =
                benchmark_suite::latency::minimum_transactions_per_worker(threads, 15, 1, 10_000)
                    .unwrap();
            transactions.insert((card_id.as_str(), point.name()), count);
            let cell = ScenarioCell::new(*card_id, *point, topology, count, 1).unwrap();
            CellCalibration {
                scenario_id: card_id.as_str().into(),
                thread_point: point.name().into(),
                thread_count: threads as u32,
                transactions_per_worker: count,
                warmup_transactions_per_worker: 1,
                operation_count: card(*card_id).operation_count(&cell.expected_counts().unwrap()),
                elapsed_ns: 1_000_000,
            }
        })
        .collect::<Vec<_>>();
    let orders =
        benchmark_suite::orchestration::balanced_block_orders(15, throughput.run_seed).unwrap();
    let samples = throughput
        .samples
        .iter()
        .filter(|sample| {
            cell_keys.contains(&(sample.scenario_id.as_str(), sample.thread_point.as_str()))
        })
        .map(|sample| {
            let order = &orders[sample.block_id as usize];
            let card_id = CardId::parse(&sample.scenario_id).unwrap();
            let point = ThreadPoint::parse(&sample.thread_point).unwrap();
            let count = transactions[&(card_id.as_str(), point.name())];
            let cell =
                ScenarioCell::new(card_id, point, topology, count, order.workload_seed).unwrap();
            let schedule = (0..cell.threads)
                .flat_map(|worker| {
                    deterministic_sample_indices(
                        cell.seed,
                        worker as u32,
                        cell.transactions_per_worker,
                        1,
                    )
                    .unwrap()
                    .into_iter()
                    .map(move |transaction_index| (worker as u32, transaction_index))
                })
                .collect::<Vec<_>>();
            let allocator_offset = match sample.allocator_id.as_str() {
                "tcmalloc" => 300,
                "jemalloc" => 200,
                "upstream-mimalloc" => 100,
                "bun-mimalloc" => 50,
                "mimalloc-pprof" => 0,
                _ => unreachable!(),
            };
            let scheduling = LatencyScheduling {
                affinity_policy: throughput.runner.affinity.policy.clone(),
                actual_cpu_ids: vec![None; cell.threads],
                thread_count: cell.threads as u32,
                physical_cores: throughput.runner.physical_cores,
                logical_cores: throughput.runner.logical_cores,
                context_switches: ContextSwitchCounts {
                    voluntary: 0,
                    involuntary: 0,
                },
                runner_class: throughput.runner.runner_class.clone(),
                clock: LatencyClock {
                    source: "monotonic".into(),
                    implementation: "fixture-clock".into(),
                    resolution_ns: 1,
                },
            };
            let observations = schedule
                .iter()
                .map(|(thread_index, transaction_index)| LatencyObservation {
                    thread_index: *thread_index,
                    transaction_index: *transaction_index,
                    duration_ns: 1_000 + allocator_offset + transaction_index % 41,
                })
                .collect::<Vec<_>>();
            let controls = schedule
                .iter()
                .map(|(thread_index, transaction_index)| LatencyObservation {
                    thread_index: *thread_index,
                    transaction_index: *transaction_index,
                    duration_ns: 40 + transaction_index % 3,
                })
                .collect::<Vec<_>>();
            LatencyRawSample {
                metric_schema_version: LATENCY_SCHEMA_VERSION.into(),
                block_id: sample.block_id,
                ordinal: order
                    .allocator_ids
                    .iter()
                    .position(|allocator| allocator == &sample.allocator_id)
                    .unwrap() as u8,
                workload_seed: order.workload_seed,
                allocator_id: sample.allocator_id.clone(),
                allocator_source_sha: sample.allocator_source_sha.clone(),
                child_binary_sha256: sample.child_binary_sha256.clone(),
                scenario_id: sample.scenario_id.clone(),
                thread_point: sample.thread_point.clone(),
                thread_count: cell.threads as u32,
                sample_denominator: 1,
                transaction_definition: transaction_definition(card_id).into(),
                measured: LatencyChildResponse {
                    protocol_version: LATENCY_CHILD_PROTOCOL_VERSION.into(),
                    metric_schema_version: LATENCY_SCHEMA_VERSION.into(),
                    control: false,
                    completed_transactions: cell.requested_transactions(),
                    checksum: expected_touch_checksum(&cell).unwrap(),
                    observations,
                    scheduling: scheduling.clone(),
                },
                control: LatencyChildResponse {
                    protocol_version: LATENCY_CHILD_PROTOCOL_VERSION.into(),
                    metric_schema_version: LATENCY_SCHEMA_VERSION.into(),
                    control: true,
                    completed_transactions: cell.requested_transactions(),
                    checksum: 1,
                    observations: controls,
                    scheduling,
                },
            }
        })
        .collect::<Vec<_>>();
    LatencyRawRun {
        metric_schema_version: LATENCY_SCHEMA_VERSION.into(),
        status: "complete".into(),
        run_seed: throughput.run_seed,
        run: throughput.run,
        runner: throughput.runner,
        allocator_lock_sha256: throughput.allocator_lock_sha256,
        allocators: throughput.allocators,
        calibrations,
        sampling_denominators: cells
            .into_iter()
            .map(|(card, point, _)| (format!("{}/{}", card.as_str(), point.name()), 1))
            .collect(),
        samples,
    }
}

#[test]
fn complete_latency_raw_matrix_is_bound_to_calibration_provenance_and_schedule() {
    let raw = complete_raw_fixture();
    validate_latency_raw_run(&raw).unwrap();

    {
        let mut wrong_schedule = raw.clone();
        wrong_schedule.samples[0].measured.observations[0].transaction_index += 1;
        assert!(validate_latency_raw_run(&wrong_schedule).is_err());
    }
    {
        let mut wrong_provenance = raw.clone();
        wrong_provenance.samples[0].child_binary_sha256 = "f".repeat(64);
        assert!(validate_latency_raw_run(&wrong_provenance).is_err());
    }
    {
        let mut mixed_clock = raw.clone();
        mixed_clock.samples[0]
            .measured
            .scheduling
            .clock
            .resolution_ns = 2;
        mixed_clock.samples[0]
            .control
            .scheduling
            .clock
            .resolution_ns = 2;
        assert!(validate_latency_raw_run(&mixed_clock).is_err());
    }
    let mut wrong_checksum = raw;
    wrong_checksum.samples[0].measured.checksum ^= 1;
    assert!(validate_latency_raw_run(&wrong_checksum).is_err());
}

#[test]
fn overlay_accepts_a_newer_fork_build_but_not_a_moved_competitor_pin() {
    use benchmark_suite::latency::{attach_latency_report, build_latency_report};

    // Reproduces the first live run's failure: the sweep runs weekly and
    // overlays onto whichever daily core envelope is published, so
    // mimalloc-pprof is normally built from a newer commit than the base.
    let report = build_latency_report(&complete_raw_fixture()).unwrap();
    let core = synthetic_full_fixture().unwrap();
    let validation = benchmark_suite::validate::validate_publication_raw(&core).unwrap();
    let base = benchmark_suite::report::build_latest_report(&core, validation)
        .unwrap()
        .0;

    let mut newer_fork = report.clone();
    for sample in &mut newer_fork.raw_samples {
        if sample.allocator_id == "mimalloc-pprof" {
            sample.allocator_source_sha = "a".repeat(40);
        }
    }
    let mut latest = base.clone();
    attach_latency_report(&mut latest, newer_fork).expect("a newer fork build must still overlay");
    assert!(latest.latency.is_some());
    assert!(!latest
        .pending_metrics
        .iter()
        .any(|value| value.metric_id == "latency"));

    let mut moved_pin = report.clone();
    for sample in &mut moved_pin.raw_samples {
        if sample.allocator_id == "upstream-mimalloc" {
            sample.allocator_source_sha = "f".repeat(40);
        }
    }
    let mut moved_pin_latest = base.clone();
    assert!(
        attach_latency_report(&mut moved_pin_latest, moved_pin)
            .unwrap_err()
            .contains("provenance"),
        "a competitor built from a different commit must be rejected"
    );

    let mut fork_missing = report.clone();
    fork_missing
        .raw_samples
        .retain(|sample| sample.allocator_id != "mimalloc-pprof");
    let mut fork_missing_latest = base.clone();
    assert!(
        attach_latency_report(&mut fork_missing_latest, fork_missing).is_err(),
        "a run without the fork must be rejected"
    );

    let mut mixed_fork = report;
    let mut changed_first = false;
    for sample in &mut mixed_fork.raw_samples {
        if sample.allocator_id == "mimalloc-pprof" && !changed_first {
            sample.allocator_source_sha = "b".repeat(40);
            changed_first = true;
        }
    }
    assert!(changed_first);
    let mut mixed_fork_latest = base;
    let error = attach_latency_report(&mut mixed_fork_latest, mixed_fork)
        .expect_err("a run mixing two fork commits must be rejected");
    assert!(error.contains("several mimalloc-pprof builds"), "{error}");
    assert!(mixed_fork_latest.latency.is_none());
}
