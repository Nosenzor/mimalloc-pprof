# Large-span CPU, fault and latency diagnostics (#543)

This is an opt-in investigation harness. It does not change allocator hooks, the
default `perf-ab` table, or the published scaling charts. Run it on the same
quiet, isolated Linux host for the fixed old-fork revision and the candidate;
shared hosted-runner results are smoke checks, not evidence of a small CPU effect.

After the current `benchmark-scaling` publication completes and this tooling
workflow is merged to `main`, the manual GitHub Actions path runs all evidence
on one fresh Ubuntu job VM and uploads raw artifacts without publishing charts:

```sh
gh workflow run benchmark-large-span-diagnostic.yml --ref main \
  -f baseline_sha=bedf926e48608b481fb9d9da3809e27e8daf5e7b \
  -f candidate_sha=<full-merged-candidate-commit-sha>
```

The Actions run-local ID proves both diagnostic artifacts came from that one
job VM; it is not a persistent physical-host identifier. Record the run URL,
artifact digest, any unavailable counters, and the classification on #543.

```sh
python3 ci/large_span_diagnostic.py \
  --base bedf926e48608b481fb9d9da3809e27e8daf5e7b \
  --candidate <full-candidate-commit-sha> \
  --reps 7 --isolation isolated --stable-host-id <operator-assigned-stable-host-id> \
  --raw <artifact-directory>/large-span-raw.json \
  --summary <artifact-directory>/large-span-summary.txt \
  --deep-output <artifact-directory>/large-span-deep.json \
  --deep-profile-prefix <artifact-directory>/large-span-profile
```

Without `--cell`, this runs exact 128 KiB/1, random-large-bursty/8,
large-class/8, small/8 and larson/8. `--cell` is for a targeted smoke run. All
arms use the same diagnostic child source, fixed operation count, seed, trace
checksum, CMake flags and phase definition. The old/candidate source paths and
executable paths have equal lengths to avoid stack-layout effects. The raw file
retains every paired repetition, alternating arm order, process start/end
snapshots, derived work/drain deltas and metric values. The validator rejects a
different checksum, completed-operation count, phase boundary or missing
counter status. Keep the raw file and summary together; a summary alone is not
reproducible evidence.

The measured-work phase starts immediately before workers launch and ends once
every worker has finished and freed its slots. Drain starts at that boundary and
ends after the RSS sampling window (twice the release bound). Process CPU/op uses only the measured-work
`getrusage(RUSAGE_SELF)` user+system delta divided by actual completed
operations; worker CPU is summed from `RUSAGE_THREAD` readings, and the
non-worker number is only the residual, not a scavenger attribution. Faults and
context switches are process deltas. VmHWM at the work boundary is the work
peak, and RSS at the end of the full drain window/release time are separate drain outcomes. Paired
bootstrap intervals crossing zero are inconclusive; a confident CPU/op increase
or throughput decrease is a regression even when RSS improves. Zero-baseline
fault counts use absolute differences instead of an undefined percentage.

`--deep-output` replays both the fixed old-fork baseline and candidate **after**
all timed pairs. The paired artifact uses available `perf stat`, `strace`,
cgroup-v2 and THP sources and reports descriptive cycles/instructions per
completed operation for each arm. These separate replays have no timed-run
confidence interval and are not substituted for measured-work CPU. With
`--deep-profile-prefix`, arm-specific CPU and page-fault `perf.data` files and
call-chain reports are retained. Profiling/tracing is never in timed acceptance samples.
Missing permissions or events are `null` with a reason, never zero. Cgroup
counters cover the whole cgroup (and descendants); use a dedicated cgroup if
attributing its deltas to this process. `/proc/vmstat` is host-wide, so its THP
counters are not subtracted and attributed to this process on a shared host.

The latency suite's extra large-object diagnostic sidecar is opt-in and separate
from the five default publication cells. Build the current five-allocator
producer first, then build a historical mimalloc-pprof child from the fixed
old-fork SHA using the same current benchmark-suite source:

```sh
python3 ci/build_old_fork_latency_provenance.py \
  --old-sha bedf926e48608b481fb9d9da3809e27e8daf5e7b \
  --provenance <current-build-root>/allocator-provenance.json \
  --build-root <current-build-root> \
  --output-dir <artifact-directory>/old-fork-latency
benchmark-latency-run --diagnostic-large-object \
  --stable-host-id <operator-assigned-stable-host-id> \
  --diagnostic-old-fork-provenance <artifact-directory>/old-fork-latency/allocator-provenance.json \
  --provenance <current-build-root>/allocator-provenance.json \
  --blocks 7 --output-dir <artifact-directory>/latency
python3 ci/large_span_latency_link.py \
  --large-span <artifact-directory>/large-span-raw.json \
  --latency <artifact-directory>/latency/latency-large-object-diagnostic.json \
  --output <artifact-directory>/large-span-latency-link.json \
  --summary <artifact-directory>/large-span-combined-summary.txt
```

The sidecar retains raw transaction durations, instrumentation controls and
p50/p95/p99. The linker checks source SHAs, worker counts, operation counts,
seeds, trace checksums and raw sample coverage. A confident p95/p99 increase
is a regression even if RSS improves. It does not equate CPU timing with
transaction latency, and its link is smoke-only unless both artifacts report
the same explicit stable-host identifier. A host name or matching CPU model is
not, by itself, an acceptance-grade host identity.
For GitHub Actions, the identifier is scoped to one Ubuntu job VM, and the
entire old/candidate pair sequence must remain in that one job. It does not
identify stable physical hardware across workflow runs or rule out hypervisor
noise; interpret small effects accordingly. The opt-in diagnostic preserves
the requested eight-worker traces on a four-logical-CPU hosted VM, records
the VM's actual topology, and therefore measures an oversubscribed workload;
the default publication suite's topology rules are unchanged. Each diagnostic
arm starts in a fresh child with no prefix warmup, matching the perf-ab trace;
the pinned stateful trace cannot be warmed up with only one operation.

For same-host allocator references, run the existing five-allocator scaling
diagnostic on that host with the candidate build and pinned jemalloc/TCMalloc
builds, then link its raw artifact explicitly:

```sh
benchmark-scaling-run --diagnostic --patterns random-large,large-class-persistent,larson \
  --thread-points 8 --blocks 7 --provenance <allocator-provenance.json> \
  --output-dir <new-scaling-output-directory>
python3 ci/large_span_refs.py \
  --large-span <artifact-directory>/large-span-raw.json \
  --scaling <new-scaling-output-directory>/scaling-raw-run.json \
  --candidate-sha <full-candidate-commit-sha> \
  --output <artifact-directory>/large-span-scaling-refs.json
```

The linker requires candidate SHA, CPU/topology and complete eight-worker
reference coverage, and retains the scaling file's SHA-256. These scaling rows
are contextual same-run jemalloc/TCMalloc comparisons, **not** paired old-fork
versus candidate measurements and not exact trace matches to the `perf-ab`
cells. In particular, scaling's `random-large` has no burst pause; its small
control has a different size distribution; and its Larson implementation has
a separate operation plan. The link artifact labels each relationship rather
than merging unlike measurements into one effect estimate.

For a tooling change, run `uv run ruff check ci/`,
`uv run ruff format --check ci/`, and
`uv run --with pytest --with pyyaml pyright` before publishing artifacts.
The Python diagnostic protocol uses dataclasses for parsed records and
summaries; generic mappings are confined to the JSON boundary.
