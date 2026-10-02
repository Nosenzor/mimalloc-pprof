//! The RSS-floor consistency rule, against the table `ci/tests/test_benchmark_report.py` also
//! loads (#573 B9). A floor is consistent with the lowest RSS measured in its cell when
//! `floor * 100 <= lowest * (100 + SCALING_RSS_FLOOR_SLACK_PERCENT)`; the producer and the Python
//! validator disagreed on it once, and every full run failed from 2026-09-28 until #574.

use benchmark_suite::scaling::{rss_floor_within_slack, SCALING_RSS_FLOOR_SLACK_PERCENT};
use serde_json::Value;

const VECTORS: &str = include_str!("fixtures/rss_floor_vectors.json");

#[test]
fn the_shared_table_agrees_with_the_rust_rule() {
    let table: Value = serde_json::from_str(VECTORS).expect("the vector table is JSON");
    assert_eq!(
        table["slack_percent"].as_u64(),
        Some(SCALING_RSS_FLOOR_SLACK_PERCENT),
        "the table was written for another slack; regenerate it with the constant"
    );
    let vectors = table["vectors"].as_array().expect("vectors is an array");
    assert!(
        vectors.len() >= 10,
        "the table is the contract; do not shrink it"
    );
    for vector in vectors {
        let floor = vector[0].as_u64().expect("floor");
        let lowest = vector[1].as_u64().expect("lowest");
        let expected = vector[2].as_bool().expect("expected");
        assert_eq!(
            rss_floor_within_slack(floor, lowest),
            expected,
            "rss_floor_within_slack({floor}, {lowest})"
        );
    }
}
