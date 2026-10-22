## Full original v1 cohort: rejected

All 13 original runs completed. Metric verdicts: 1 FAIL, 12 INCONCLUSIVE, 65 PASS. The ephemeral p99 FAIL rejects this source revision; no other result compensates for it. Additional INCONCLUSIVE rows remain labelled as such. All service-release bounds held: True.

Fixed baseline: `2c7fed4edf91cf412c8969cf3cafeb690f0ed3e3`; rejected head: `42d06c341b7d8d636acd0864b260bac5fdb63942`. Each row retains its own host, paired raw samples and actual four executable hashes; no cross-host pooling or unchanged-code retries. The v2 cohort is separate and still pending.

### large-class/8 — [60/arm](https://github.com/zackees/mimalloc-pprof/actions/runs/39736260557)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| large-class/8 | 0% | **PASS** | -0.0% [-0.6, +1.3] / 1.0% **PASS** | -0.9% [-1.2, -0.4] / 0.6% **PASS** | -0.9% [-2.4, +0.9] / 3.1% **PASS** | +0.7% [-0.7, +2.2] / 2.9% **PASS** | +0.0% [-2.6, +0.0] / 2.7% **PASS** | +0.0% [-2.6, +0.0] / 2.7% **PASS** |

### large-class-ephemeral/8 — [60/arm](https://github.com/zackees/mimalloc-pprof/actions/runs/39736263262)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| large-class-ephemeral/8 | 0% | **FAIL** | +0.9% [+0.3, +1.3] / 0.6% **PASS** | -1.0% [-1.2, -0.7] / 0.2% **PASS** | +0.0% [-0.8, +0.8] / 0.8% **PASS** | +3.6% [+2.9, +5.1] / 1.4% **FAIL** | -0.0% [-2.6, +0.0] / 2.6% **PASS** | -0.0% [-2.6, +0.0] / 2.5% **PASS** |

### random-large/8 — [120/arm](https://github.com/zackees/mimalloc-pprof/actions/runs/39736265849)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| random-large/8 | 0% | **INCONCLUSIVE** | +0.0% [-0.8, +1.0] / 0.6% **INCONCLUSIVE** | -0.2% [-0.4, -0.1] / 0.3% **PASS** | +0.4% [-0.7, +0.8] / 0.7% **INCONCLUSIVE** | +0.4% [+0.0, +0.8] / 0.6% **INCONCLUSIVE** | -0.1% [-1.1, +1.1] / 1.2% **PASS** | -0.3% [-1.1, +1.1] / 1.2% **PASS** |

### random-large-bursty/8 — [60/arm](https://github.com/zackees/mimalloc-pprof/actions/runs/39736269074)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| random-large-bursty/8 | 0% | **PASS** | +0.1% [-0.1, +0.2] / 0.3% **PASS** | -0.2% [-1.3, +0.9] / 2.3% **PASS** | -11.4% [-23.6, +3.2] / 18.3% **PASS** | -0.6% [-1.2, +0.4] / 0.8% **PASS** | -1.2% [-3.8, +2.3] / 3.7% **PASS** | -0.8% [-5.3, +2.6] / 5.4% **PASS** |

### random-large/1 — [110/arm](https://github.com/zackees/mimalloc-pprof/actions/runs/39736271804)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| random-large/1 | 0% | **PASS** | -0.1% [-1.3, +0.5] / 2.0% **PASS** | +0.1% [-0.5, +1.6] / 1.8% **PASS** | +0.0% [+0.0, +0.0] / 0.0% **PASS** | +0.4% [-2.7, +1.6] / 2.6% **PASS** | +0.0% [-0.0, +0.0] / 0.0% **PASS** | +0.0% [+0.0, +0.0] / 0.0% **PASS** |

### small/8 (control) — [100/arm](https://github.com/zackees/mimalloc-pprof/actions/runs/39736274160)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| small/8 (control) | 0% | **PASS** | +0.9% [-0.5, +2.2] / 1.7% **PASS** | -0.4% [-2.0, +1.0] / 1.5% **PASS** | +0.0% [+0.0, +0.0] / 0.0% **PASS** | +0.0% [+0.0, +0.0] / 0.0% **PASS** | +0.0% [+0.0, +0.0] / 0.0% **PASS** | +0.0% [+0.0, +0.0] / 0.0% **PASS** |

### larson/1 — [70/arm](https://github.com/zackees/mimalloc-pprof/actions/runs/39736276667)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| larson/1 | 0% | **PASS** | +0.3% [-1.0, +1.2] / 2.4% **PASS** | -0.3% [-1.2, +1.1] / 2.2% **PASS** | +0.0% [+0.0, +0.0] / 0.0% **PASS** | -0.1% [-0.5, +0.4] / 0.4% **PASS** | +0.0% [+0.0, +0.0] / 0.0% **PASS** | +0.0% [+0.0, +0.0] / 0.0% **PASS** |

### larson/8 — [120/arm](https://github.com/zackees/mimalloc-pprof/actions/runs/39736279187)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| larson/8 | 0% | **INCONCLUSIVE** | +1.5% [+1.0, +2.1] / 1.2% **PASS** | -1.4% [-1.9, -0.5] / 1.2% **PASS** | -0.5% [-8.7, +11.6] / 14.8% **PASS** | +0.0% [+0.0, +0.4] / 0.4% **INCONCLUSIVE** | -0.5% [-2.3, +0.7] / 1.8% **PASS** | +1.5% [+0.1, +3.7] / 2.2% **INCONCLUSIVE** |

### larson/8 (chart build) — [120/arm](https://github.com/zackees/mimalloc-pprof/actions/runs/39736281733)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| larson/8 (chart build) | 0% | **INCONCLUSIVE** | -0.6% [-2.4, +0.7] / 1.8% **INCONCLUSIVE** | +0.3% [-0.9, +2.8] / 2.1% **INCONCLUSIVE** | +8.8% [-3.5, +21.3] / 16.8% **INCONCLUSIVE** | -0.8% [-2.3, +1.5] / 3.3% **PASS** | +0.3% [-1.1, +2.0] / 1.7% **INCONCLUSIVE** | -0.3% [-1.0, +0.3] / 1.8% **PASS** |

### large-class/8 (profiler on) — [60/arm](https://github.com/zackees/mimalloc-pprof/actions/runs/39736284152)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| large-class/8 (profiler on) | 0% | **PASS** | +0.8% [-0.1, +1.3] / 0.9% **PASS** | -0.5% [-1.3, +0.2] / 0.7% **PASS** | -0.1% [-0.8, +0.2] / 0.9% **PASS** | -6.9% [-13.7, +1.2] / 7.5% **PASS** | -0.2% [-2.6, +0.3] / 2.8% **PASS** | -0.2% [-1.4, +1.2] / 1.5% **PASS** |

### large-class-ephemeral/8 (chart build) — [60/arm](https://github.com/zackees/mimalloc-pprof/actions/runs/39736286695)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| large-class-ephemeral/8 (chart build) | 0% | **PASS** | +0.1% [-0.5, +0.6] / 0.7% **PASS** | +0.1% [-0.3, +0.3] / 0.4% **PASS** | +0.0% [-0.8, +0.4] / 1.5% **PASS** | -0.6% [-1.6, +0.5] / 1.1% **PASS** | -0.0% [-2.5, +0.0] / 2.6% **PASS** | -0.0% [-11.3, +0.7] / 11.9% **PASS** |

### large-class-ephemeral/8 short generations (chart build) — [80/arm](https://github.com/zackees/mimalloc-pprof/actions/runs/39736289527)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| large-class-ephemeral/8 short generations (chart build) | 0% | **PASS** | -0.1% [-0.8, +0.6] / 1.5% **PASS** | -0.0% [-0.6, +0.6] / 0.6% **PASS** | -0.2% [-1.1, +0.4] / 0.8% **PASS** | -2.1% [-2.8, -1.3] / 1.4% **PASS** | -0.0% [-2.6, +0.0] / 2.7% **PASS** | -0.0% [-2.6, +0.0] / 2.7% **PASS** |

### sparse-large-buffers 64-slot budget/8 (#422) — [120/arm](https://github.com/zackees/mimalloc-pprof/actions/runs/39736358789)


Rule-12 ledger (CLAUDE.md; ci/perf_ab_ledger.py): `saved%` of the reducible peak-RSS gap, then per metric the paired change [95% sign-test interval] against its allowance (saved%/3 for CPU, throughput and faults; noise for memory and for cells that saved none). INCONCLUSIVE is not a pass.

| workload | saved% | verdict | ops/s | cpu s | minor faults | p99 operation ns | peak RSS MiB | RSS 0.5 s after drain MiB |
|---|---|---|---|---|---|---|---|---|
| sparse-large-buffers 64-slot budget/8 (#422) | 0% | **INCONCLUSIVE** | +0.0% [-0.6, +0.8] / 1.0% **PASS** | -0.1% [-0.4, +0.3] / 0.5% **PASS** | +0.8% [+0.0, +1.9] / 0.8% **INCONCLUSIVE** | +0.1% [-0.6, +0.8] / 1.1% **PASS** | +1.2% [-0.1, +3.6] / 1.3% **INCONCLUSIVE** | +1.1% [-0.6, +3.5] / 1.6% **INCONCLUSIVE** |

