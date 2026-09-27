//! C-compatible deterministic operation stream for the opt-in #543 latency cards.
//!
//! This mirrors `ci/perf_ab.c`'s `next`, `draw_size`, `run_ops`, and diagnostic
//! trace checksum. It is isolated from the publication throughput catalogue.

use crate::scenarios::{CardId, PERF_AB_SLOT_COUNT};

pub const PERF_AB_STREAM_SEED_BASE: u64 = 0x5eed_0000;
pub const PERF_AB_SLOTS: usize = PERF_AB_SLOT_COUNT;
pub const PERF_AB_BURSTS: u32 = 8;
pub const PERF_AB_BURST_PAUSE_MS: u32 = 300;
pub const PERF_AB_DIAGNOSTIC_OPS: u64 = 200_000;
pub const PERF_AB_DIAGNOSTIC_BYTES: u64 = PERF_AB_DIAGNOSTIC_OPS * 4 * 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PerfAbWorkload {
    pub workload_id: &'static str,
    pub min_size: usize,
    pub max_size: usize,
    pub operations_per_worker: u64,
    pub bursts: u32,
    pub pause_ms: u32,
}

pub const fn workload(card: CardId, thread_count: usize) -> Option<PerfAbWorkload> {
    match card {
        CardId::LargeObject128KiB if thread_count == 1 || thread_count == 8 => {
            Some(PerfAbWorkload {
                workload_id: if thread_count == 1 {
                    "size 128 KiB/1 (#422)"
                } else {
                    "size 128 KiB/8 (diagnostic)"
                },
                min_size: 128 * 1024,
                max_size: 128 * 1024,
                operations_per_worker: PERF_AB_DIAGNOSTIC_BYTES
                    / (128 * 1024)
                    / thread_count as u64,
                bursts: 1,
                pause_ms: 0,
            })
        }
        CardId::RandomLargeBursty if thread_count == 8 => Some(PerfAbWorkload {
            workload_id: "random-large-bursty/8",
            min_size: 64 * 1024,
            max_size: 4 * 1024 * 1024,
            operations_per_worker: 40_000,
            bursts: PERF_AB_BURSTS,
            pause_ms: PERF_AB_BURST_PAUSE_MS,
        }),
        CardId::LargeClassPersistent if thread_count == 8 => Some(PerfAbWorkload {
            workload_id: "large-class/8",
            min_size: 96 * 1024,
            max_size: 512 * 1024,
            operations_per_worker: 400_000,
            bursts: 1,
            pause_ms: 0,
        }),
        _ => None,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PerfAbOperation {
    Free { slot: usize },
    Allocate { slot: usize, size: usize },
}

pub struct PerfAbStream {
    state: u64,
    low: usize,
    high: usize,
    slots: usize,
    fifo: [usize; 4096],
    head: usize,
    tail: usize,
    completed: u64,
    worker: usize,
}

impl PerfAbStream {
    pub fn new(workload: PerfAbWorkload, worker: usize) -> Self {
        Self {
            state: PERF_AB_STREAM_SEED_BASE + worker as u64,
            low: workload.min_size,
            high: workload.max_size,
            slots: PERF_AB_SLOTS,
            fifo: [0; 4096],
            head: 0,
            tail: 0,
            completed: 0,
            worker,
        }
    }

    pub fn next_operation(&mut self) -> PerfAbOperation {
        let choice = (next(&mut self.state) % 16) as usize;
        let mut slot = (next(&mut self.state) % self.slots as u64) as usize;
        if (8..14).contains(&choice) && self.head != self.tail {
            slot = self.fifo[self.head % self.fifo.len()];
            self.head += 1;
        }
        self.completed += 1;
        if choice >= 8 {
            PerfAbOperation::Free { slot }
        } else {
            let size =
                self.low + (next(&mut self.state) % (self.high - self.low + 1) as u64) as usize;
            self.fifo[self.tail % self.fifo.len()] = slot;
            self.tail += 1;
            PerfAbOperation::Allocate { slot, size }
        }
    }

    pub fn final_state(&self) -> u64 {
        self.state
    }

    pub fn worker_checksum(&self) -> u64 {
        let mut state = self.state
            ^ self.completed
            ^ self.low as u64
            ^ ((self.high as u64) << 1)
            ^ ((self.slots as u64) << 32)
            ^ self.worker as u64;
        next(&mut state)
    }
}

pub fn trace_checksum(workload: PerfAbWorkload, thread_count: usize) -> u64 {
    let mut checksum = 0;
    for worker in 0..thread_count {
        let mut stream = PerfAbStream::new(workload, worker);
        for _ in 0..workload.operations_per_worker {
            stream.next_operation();
        }
        checksum ^= stream.worker_checksum();
    }
    checksum
}

fn next(state: &mut u64) -> u64 {
    let mut value = state.wrapping_add(0x9e37_79b9_7f4a_7c15);
    *state = value;
    value = (value ^ (value >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    value = (value ^ (value >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    value ^ (value >> 31)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn diagnostic_params_match_perf_ab_rows() {
        let exact = workload(CardId::LargeObject128KiB, 1).unwrap();
        assert_eq!(
            exact.operations_per_worker,
            PERF_AB_DIAGNOSTIC_BYTES / (128 * 1024)
        );
        assert_eq!((exact.min_size, exact.max_size), (128 * 1024, 128 * 1024));
        assert_eq!(trace_checksum(exact, 1), 0x1a9d_374d_39f8_b871);
        let bursty = workload(CardId::RandomLargeBursty, 8).unwrap();
        assert_eq!(
            (bursty.operations_per_worker, bursty.bursts, bursty.pause_ms),
            (40_000, 8, 300)
        );
        assert_eq!(trace_checksum(bursty, 8), 0x0f34_62cc_17e1_474a);
        let large_class = workload(CardId::LargeClassPersistent, 8).unwrap();
        assert_eq!(
            (
                large_class.operations_per_worker,
                large_class.min_size,
                large_class.max_size
            ),
            (400_000, 96 * 1024, 512 * 1024)
        );
        assert_eq!(trace_checksum(large_class, 8), 0x8836_df89_bfe8_ce0c);
    }
}
