## Full original v2 cohort: rejected

All 13 original runs are terminal: 12 complete comparisons and 1 incomplete sanity failure. Complete-comparison metric verdicts: 1 FAIL, 11 INCONCLUSIVE, 60 PASS. No six-cell verdict is invented for the incomplete row. The chart-ephemeral p99 FAIL rejects this source independently of the retained random-large/8 RSS sanity failure. Supported main/sparse CPU gains do not compensate.

Baseline: `2c7fed4edf91cf412c8969cf3cafeb690f0ed3e3`; rejected source: `a718c4e7d3e7be00eb65e4f8895d03bd35d3ffa5`. Every complete row retains its own same-host base/head/null data and four actual linked binary hashes; no cross-host pooling, sample removal, gate relaxation or unchanged-code retries. All complete rows held their release bounds: True. V3 is a separate source and cohort.

### large-class/8 — [original run 39740287661](https://github.com/zackees/mimalloc-pprof/actions/runs/39740287661)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| large-class/8 | 0% | **PASS** | +1.0% [+0.4, +1.6] / 1.2% **PASS** | -0.4% [-0.6, -0.1] / 0.2% **PASS** | +0.0% [-1.3, +0.9] / 3.0% **PASS** | +0.0% [-0.6, +1.0] / 1.1% **PASS** | -0.0% [-2.6, +0.0] / 2.6% **PASS** | -0.1% [-1.7, +1.8] / 1.9% **PASS** |

### large-class-ephemeral/8 — [original run 39740290429](https://github.com/zackees/mimalloc-pprof/actions/runs/39740290429)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| large-class-ephemeral/8 | 0% | **INCONCLUSIVE** | -0.0% [-0.4, +0.3] / 0.4% **PASS** | -0.1% [-0.3, +0.1] / 0.4% **PASS** | +0.0% [-0.4, +0.4] / 0.8% **PASS** | -0.6% [-1.1, +0.0] / 1.1% **PASS** | -0.0% [-0.1, +0.1] / 0.1% **INCONCLUSIVE** | +0.0% [-9.8, +0.1] / 9.8% **PASS** |

### random-large/8 — [original run 39740293226](https://github.com/zackees/mimalloc-pprof/actions/runs/39740293226)

**Incomplete, one sample/arm; no numeric ledger verdict.** Original failure:

```text
sanity gate (first repetition, #573):
  [head] random-large/8: RSS 0.5 s after drain MiB 84.6 -> 205.5 (more than 1.5x base + 8 MiB)
```

### random-large-bursty/8 — [original run 39740296082](https://github.com/zackees/mimalloc-pprof/actions/runs/39740296082)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| random-large-bursty/8 | 0% | **INCONCLUSIVE** | +0.0% [-0.1, +0.1] / 0.1% **PASS** | -0.6% [-0.8, -0.3] / 0.3% **PASS** | -14.8% [-22.8, -0.2] / 17.7% **PASS** | +0.7% [+0.4, +1.0] / 0.6% **INCONCLUSIVE** | +0.1% [-1.6, +2.4] / 3.7% **PASS** | -0.4% [-3.7, +1.1] / 4.9% **PASS** |

### random-large/1 — [original run 39740298710](https://github.com/zackees/mimalloc-pprof/actions/runs/39740298710)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| random-large/1 | 0% | **PASS** | +0.2% [-0.2, +0.8] / 0.4% **PASS** | -0.4% [-1.4, +0.8] / 1.0% **PASS** | +0.0% [+0.0, +0.0] / 0.0% **PASS** | -0.6% [-0.9, +0.5] / 0.8% **PASS** | +0.0% [+0.0, +0.0] / 0.0% **PASS** | +0.0% [+0.0, +0.0] / 0.0% **PASS** |

### small/8 (control) — [original run 39740301874](https://github.com/zackees/mimalloc-pprof/actions/runs/39740301874)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| small/8 (control) | 0% | **INCONCLUSIVE** | -1.0% [-2.0, +0.0] / 1.7% **INCONCLUSIVE** | +0.6% [-1.0, +1.6] / 1.1% **INCONCLUSIVE** | +0.0% [+0.0, +0.6] / 0.6% **PASS** | +0.0% [+0.0, +0.0] / 0.0% **PASS** | +0.0% [+0.0, +0.0] / 0.0% **PASS** | +0.0% [+0.0, +0.0] / 0.0% **PASS** |

### larson/1 — [original run 39740304408](https://github.com/zackees/mimalloc-pprof/actions/runs/39740304408)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| larson/1 | 0% | **PASS** | -0.4% [-1.8, +0.9] / 2.3% **PASS** | +0.6% [-1.6, +2.3] / 2.6% **PASS** | +0.0% [-0.5, +0.0] / 0.0% **PASS** | -0.2% [-1.1, +1.1] / 2.4% **PASS** | +0.0% [-0.0, +0.0] / 1.4% **PASS** | +0.0% [+0.0, +0.0] / 0.0% **PASS** |

### larson/8 — [original run 39740306851](https://github.com/zackees/mimalloc-pprof/actions/runs/39740306851)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| larson/8 | 0% | **PASS** | +0.6% [-0.0, +1.5] / 1.7% **PASS** | -0.3% [-1.4, +1.0] / 1.1% **PASS** | -0.2% [-1.3, +1.5] / 1.7% **PASS** | +0.0% [+0.0, +0.0] / 0.5% **PASS** | -0.0% [-1.9, +2.7] / 3.4% **PASS** | +0.1% [-0.1, +0.3] / 0.4% **PASS** |

### larson/8 (chart build) — [original run 39740309455](https://github.com/zackees/mimalloc-pprof/actions/runs/39740309455)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| larson/8 (chart build) | 0% | **PASS** | +0.1% [-0.8, +1.6] / 1.5% **PASS** | +0.4% [-0.8, +1.2] / 1.3% **PASS** | +2.7% [-13.4, +10.3] / 26.3% **PASS** | +0.0% [-0.4, +0.0] / 0.4% **PASS** | -0.7% [-2.1, +2.3] / 3.2% **PASS** | +0.4% [-0.5, +1.1] / 1.4% **PASS** |

### large-class/8 (profiler on) — [original run 39740311694](https://github.com/zackees/mimalloc-pprof/actions/runs/39740311694)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| large-class/8 (profiler on) | 0% | **INCONCLUSIVE** | +0.5% [+0.3, +0.8] / 0.3% **PASS** | -0.5% [-0.7, -0.3] / 0.3% **PASS** | +0.1% [-0.1, +0.3] / 0.2% **INCONCLUSIVE** | -0.9% [-2.3, +0.2] / 2.6% **PASS** | -0.1% [-0.3, +0.0] / 0.2% **PASS** | -0.0% [-0.4, +0.3] / 0.2% **INCONCLUSIVE** |

### large-class-ephemeral/8 (chart build) — [original run 39740313954](https://github.com/zackees/mimalloc-pprof/actions/runs/39740313954)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| large-class-ephemeral/8 (chart build) | 0% | **FAIL** | -0.5% [-1.1, -0.1] / 0.5% **INCONCLUSIVE** | +0.2% [+0.0, +0.5] / 0.3% **INCONCLUSIVE** | +0.4% [-0.8, +0.8] / 0.8% **PASS** | +2.1% [+1.1, +3.2] / 1.1% **FAIL** | -0.0% [-0.0, +0.0] / 2.6% **PASS** | +0.0% [-0.0, +0.7] / 10.6% **PASS** |

### large-class-ephemeral/8 short generations (chart build) — [original run 39740316584](https://github.com/zackees/mimalloc-pprof/actions/runs/39740316584)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| large-class-ephemeral/8 short generations (chart build) | 0% | **INCONCLUSIVE** | -0.3% [-0.7, +0.7] / 0.6% **INCONCLUSIVE** | +0.1% [-0.3, +0.6] / 0.4% **INCONCLUSIVE** | -0.4% [-0.8, +0.0] / 0.8% **PASS** | -4.8% [-6.0, -4.1] / 1.9% **PASS** | -0.0% [-2.6, +0.0] / 2.6% **PASS** | -0.0% [-2.6, +0.0] / 2.6% **PASS** |

### sparse-large-buffers 64-slot budget/8 (#422) — [original run 39740319128](https://github.com/zackees/mimalloc-pprof/actions/runs/39740319128)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| sparse-large-buffers 64-slot budget/8 (#422) | 0% | **INCONCLUSIVE** | +0.8% [+0.2, +1.4] / 1.0% **PASS** | -0.6% [-0.9, -0.3] / 0.3% **PASS** | -0.6% [-1.2, +0.4] / 0.8% **PASS** | +0.1% [-0.2, +0.6] / 0.6% **INCONCLUSIVE** | -0.1% [-1.3, +1.1] / 1.2% **PASS** | -0.6% [-1.9, +0.7] / 1.2% **PASS** |

