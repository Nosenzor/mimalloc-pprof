# V3 original performance cohort

Baseline: `2c7fed4edf91cf412c8969cf3cafeb690f0ed3e3`. Candidate: `b2978df6ae0266e8ebd47f116874fba72a38e340`.

13/13 complete original comparisons. Metric counts: 62 PASS, 16 INCONCLUSIVE, 0 FAIL. Counts are not an aggregate performance score.

Changes below are paired percentages with 95% confidence intervals. CPU time is proportional to CPU/op within each fixed-operation workload. Throughput increasing is beneficial; other metrics decreasing is beneficial. A PASS is a regression-gate result, not proof of improvement or equality. No RSS saving or memory credit is claimed. Each same-host comparison stands alone; hosts are not pooled.

| Workload / original run | Throughput | CPU/op | Minor faults | p99 | Peak RSS | After-drain RSS |
|---|---|---|---|---|---|---|
| [large-class/8](https://github.com/zackees/mimalloc-pprof/actions/runs/39746382593) | -0.239% [-1.174, +0.653] **INCONCLUSIVE** | -0.205% [-0.439, +0.125] **PASS** | -0.429% [-2.110, +1.739] **PASS** | +0.000% [-0.729, +1.427] **PASS** | -0.005% [-2.551, +0.000] **PASS** | -0.005% [-2.551, +0.000] **PASS** |
| [large-class-ephemeral/8](https://github.com/zackees/mimalloc-pprof/actions/runs/39746384622) | +0.048% [-0.329, +0.312] **PASS** | -0.031% [-0.203, +0.089] **PASS** | +0.000% [-0.769, +0.389] **PASS** | +0.547% [+0.000, +1.122] **INCONCLUSIVE** | -0.010% [-0.082, +0.079] **PASS** | -0.182% [-9.626, +0.000] **PASS** |
| [large-class-ephemeral/8 (chart build)](https://github.com/zackees/mimalloc-pprof/actions/runs/39746388712) | +0.241% [-0.448, +0.872] **PASS** | -0.619% [-1.077, -0.093] **PASS** | -0.381% [-1.149, +0.380] **PASS** | -0.510% [-1.620, +0.982] **PASS** | -0.029% [-2.540, +0.021] **PASS** | -0.022% [-11.358, +0.710] **PASS** |
| [random-large/8](https://github.com/zackees/mimalloc-pprof/actions/runs/39751358216) | +0.314% [-0.429, +1.289] **PASS** | -0.679% [-1.130, +0.388] **PASS** | -0.556% [-1.460, +0.377] **PASS** | -0.094% [-2.844, +3.230] **PASS** | -0.555% [-2.114, +1.109] **PASS** | -0.019% [-0.284, +0.019] **PASS** |
| [random-large-bursty/8](https://github.com/zackees/mimalloc-pprof/actions/runs/39751362279) | -0.030% [-0.117, +0.112] **PASS** | +0.198% [-0.467, +0.550] **PASS** | -12.387% [-25.371, +19.427] **PASS** | -0.414% [-1.404, +0.651] **PASS** | -0.834% [-2.241, +1.193] **PASS** | +0.603% [-4.200, +2.789] **PASS** |
| [random-large/1](https://github.com/zackees/mimalloc-pprof/actions/runs/39751366096) | -0.818% [-1.268, -0.312] **INCONCLUSIVE** | +0.885% [+0.156, +1.700] **INCONCLUSIVE** | +0.000% [+0.000, +0.000] **PASS** | -0.215% [-0.973, +0.555] **PASS** | +0.000% [+0.000, +0.000] **PASS** | +0.000% [+0.000, +0.000] **PASS** |
| [small/8 (control)](https://github.com/zackees/mimalloc-pprof/actions/runs/39751370377) | -0.019% [-1.833, +1.226] **PASS** | +0.356% [-0.801, +1.850] **INCONCLUSIVE** | +0.000% [+0.000, +0.602] **INCONCLUSIVE** | +0.000% [+0.000, +0.000] **PASS** | +0.000% [+0.000, +0.000] **PASS** | +0.000% [+0.000, +0.000] **PASS** |
| [larson/1](https://github.com/zackees/mimalloc-pprof/actions/runs/39751374268) | -0.503% [-1.201, -0.084] **INCONCLUSIVE** | +0.591% [-0.131, +1.296] **INCONCLUSIVE** | +0.000% [+0.000, +0.000] **PASS** | -0.517% [-1.448, +0.705] **PASS** | +0.000% [-0.038, +0.002] **PASS** | +0.000% [+0.000, +0.000] **PASS** |
| [larson/8](https://github.com/zackees/mimalloc-pprof/actions/runs/39751378411) | +1.516% [-1.058, +2.958] **PASS** | -0.613% [-2.068, +1.749] **PASS** | +6.779% [-2.890, +26.954] **PASS** | +0.792% [-2.046, +2.989] **PASS** | +0.302% [-1.559, +1.698] **PASS** | +0.069% [-1.044, +1.210] **PASS** |
| [larson/8 (chart build)](https://github.com/zackees/mimalloc-pprof/actions/runs/39751381893) | +0.039% [-1.164, +0.917] **INCONCLUSIVE** | +0.339% [-0.739, +1.712] **INCONCLUSIVE** | +0.454% [-0.699, +1.370] **INCONCLUSIVE** | +0.000% [-0.552, +0.476] **PASS** | +1.395% [-0.642, +3.391] **INCONCLUSIVE** | -0.030% [-0.270, +0.096] **PASS** |
| [large-class/8 (profiler on)](https://github.com/zackees/mimalloc-pprof/actions/runs/39751386417) | -0.312% [-0.688, -0.130] **INCONCLUSIVE** | +0.451% [+0.166, +0.687] **INCONCLUSIVE** | +0.061% [-0.185, +0.307] **PASS** | -1.284% [-5.978, +2.658] **PASS** | -0.074% [-0.387, +0.120] **PASS** | -0.096% [-0.416, +0.245] **PASS** |
| [large-class-ephemeral/8 short generations (chart build)](https://github.com/zackees/mimalloc-pprof/actions/runs/39751391093) | +0.569% [+0.071, +1.082] **PASS** | -0.174% [-1.006, +0.462] **PASS** | -0.386% [-1.481, +0.772] **PASS** | +0.000% [-0.594, +0.546] **PASS** | +0.000% [-2.676, +0.006] **PASS** | +0.000% [-2.676, +0.006] **PASS** |
| [sparse-large-buffers 64-slot budget/8 (#422)](https://github.com/zackees/mimalloc-pprof/actions/runs/39751394764) | +0.258% [-0.590, +0.799] **PASS** | +0.252% [+0.022, +0.589] **PASS** | +0.187% [-0.392, +1.167] **PASS** | +0.031% [-0.258, +0.275] **PASS** | +0.075% [-1.185, +3.439] **INCONCLUSIVE** | +0.670% [-1.237, +3.582] **INCONCLUSIVE** |

## Run integrity

| Original run | Samples per arm | Host CPU | p95 release / bound | Zero-credit shadow |
|---|---|---|---|---|
| 39746382593 | 120 | AMD EPYC 9V74 80-Core Processor | 1120/1300 ms | 5 PASS, 1 INCONCLUSIVE, 0 FAIL |
| 39746384622 | 120 | AMD EPYC 7763 64-Core Processor | 1050/1300 ms | 5 PASS, 1 INCONCLUSIVE, 0 FAIL |
| 39746388712 | 60 | AMD EPYC 7763 64-Core Processor | 1050/1300 ms | 6 PASS, 0 INCONCLUSIVE, 0 FAIL |
| 39751358216 | 60 | AMD EPYC 9V45 96-Core Processor | 1110/1300 ms | 6 PASS, 0 INCONCLUSIVE, 0 FAIL |
| 39751362279 | 90 | AMD EPYC 7763 64-Core Processor | 1060/1300 ms | 6 PASS, 0 INCONCLUSIVE, 0 FAIL |
| 39751366096 | 120 | AMD EPYC 9V45 96-Core Processor | 1100/1300 ms | 4 PASS, 2 INCONCLUSIVE, 0 FAIL |
| 39751370377 | 120 | AMD EPYC 7763 64-Core Processor | 0/1300 ms | 4 PASS, 2 INCONCLUSIVE, 0 FAIL |
| 39751374268 | 120 | AMD EPYC 9V74 80-Core Processor | 600/1300 ms | 4 PASS, 2 INCONCLUSIVE, 0 FAIL |
| 39751378411 | 60 | INTEL(R) XEON(R) PLATINUM 8573C | 490/1300 ms | 6 PASS, 0 INCONCLUSIVE, 0 FAIL |
| 39751381893 | 120 | AMD EPYC 9V45 96-Core Processor | 640/1300 ms | 2 PASS, 4 INCONCLUSIVE, 0 FAIL |
| 39751386417 | 120 | AMD EPYC 7763 64-Core Processor | 1070/1300 ms | 4 PASS, 2 INCONCLUSIVE, 0 FAIL |
| 39751391093 | 60 | AMD EPYC 9V45 96-Core Processor | 1100/1300 ms | 6 PASS, 0 INCONCLUSIVE, 0 FAIL |
| 39751394764 | 120 | INTEL(R) XEON(R) PLATINUM 8573C | 1090/1300 ms | 4 PASS, 2 INCONCLUSIVE, 0 FAIL |

The first three measurements were not repeated. Remaining ten were dispatched once under the predeclared expansion condition. Null arm, RSS sanity screen, traces, seeds, touch pattern, operation count and thresholds are unchanged. Original raw samples, binary manifests and all four measured executables are retained for each run. Rejected V1/V2 measurements and the separate baseline-only diagnostic remain part of the evidence archive.

Acceptance remains a separate recorded decision. Main-throughput INCONCLUSIVE is outside the automatic ancillary-uncertainty rule in docs/dev-loop.md. No new tolerance is introduced and no INCONCLUSIVE label is converted into PASS.

## Supported direction and unresolved risk

Supported direction means the full 95% interval lies on one side of zero. It is separate from the regression gate, which compares costs to measured noise and any permitted credit. Even an adverse supported direction can be PASS or INCONCLUSIVE against that gate. No metric compensates for a different cell.

| Workload | Metric | Supported direction | Change / 95% CI | Gate |
|---|---|---|---|---|
| large-class-ephemeral/8 (chart build) | cpu s | beneficial | -0.619% [-1.077, -0.093] | PASS |
| random-large/1 | ops/s | adverse | -0.818% [-1.268, -0.312] | INCONCLUSIVE |
| random-large/1 | cpu s | adverse | +0.885% [+0.156, +1.700] | INCONCLUSIVE |
| larson/1 | ops/s | adverse | -0.503% [-1.201, -0.084] | INCONCLUSIVE |
| large-class/8 (profiler on) | ops/s | adverse | -0.312% [-0.688, -0.130] | INCONCLUSIVE |
| large-class/8 (profiler on) | cpu s | adverse | +0.451% [+0.166, +0.687] | INCONCLUSIVE |
| large-class-ephemeral/8 short generations (chart build) | ops/s | beneficial | +0.569% [+0.071, +1.082] | PASS |
| sparse-large-buffers 64-slot budget/8 (#422) | cpu s | adverse | +0.252% [+0.022, +0.589] | PASS |

### Every unresolved cell

| Workload | Metric | Adverse-change interval | Canonical allowance | Zero-credit allowance |
|---|---|---|---|---|
| large-class/8 | ops/s | [-0.653, +1.174]% | 0.710% | 0.710% |
| large-class-ephemeral/8 | p99 operation ns | [+0.000, +1.122]% | 1.097% | 1.097% |
| random-large/1 | ops/s | [+0.312, +1.268]% | 0.459% | 0.459% |
| random-large/1 | cpu s | [+0.156, +1.700]% | 0.669% | 0.669% |
| small/8 (control) | cpu s | [-0.801, +1.850]% | 1.755% | 1.755% |
| small/8 (control) | minor faults | [+0.000, +0.602]% | 0.000% | 0.000% |
| larson/1 | ops/s | [+0.084, +1.201]% | 0.722% | 0.722% |
| larson/1 | cpu s | [-0.131, +1.296]% | 0.772% | 0.772% |
| larson/8 (chart build) | ops/s | [-0.917, +1.164]% | 0.894% | 0.894% |
| larson/8 (chart build) | cpu s | [-0.739, +1.712]% | 1.160% | 1.160% |
| larson/8 (chart build) | minor faults | [-0.699, +1.370]% | 1.168% | 1.168% |
| larson/8 (chart build) | peak RSS MiB | [-0.642, +3.391]% | 1.994% | 1.994% |
| large-class/8 (profiler on) | ops/s | [+0.130, +0.688]% | 0.322% | 0.322% |
| large-class/8 (profiler on) | cpu s | [+0.166, +0.687]% | 0.510% | 0.510% |
| sparse-large-buffers 64-slot budget/8 (#422) | peak RSS MiB | [-1.185, +3.439]% | 2.344% | 2.344% |
| sparse-large-buffers 64-slot budget/8 (#422) | RSS 0.5 s after drain MiB | [-1.237, +3.582]% | 2.683% | 2.683% |
