# Trace and CSV Schema

This document is the authoritative reference for every field produced
by a benchmark run and every column in the parsed CSV. If a field
isn't described here, treat it as undocumented and don't rely on it.

## Per-run trace file (`*.perf.txt`)

Written by `build_and_run_riscv.sh`. Structure:

```
backend_declared=<string>              # written by the run script, not the benchmark
[perf stat output, if perf was available]
program=<filename>.c                   # written by the benchmark itself
branch_pattern=<name>
cache_pattern=<name>
predictor_stress_type=<heuristic label>
expected_dominant_miss_type=<heuristic label>
size_tier=<name>
backend=<string>                       # compiled in via -DLOWBIT_BACKEND
compiler=<version string>
arch=<riscv64 | riscv32 | x86_64 | aarch64 | unknown>
SIZE=<uint> N=<uint> SEED=<uint>
warmup_iterations=<uint>
taken=<uint> nottaken=<uint>
sink=<uint>
validation=<PASS | FAIL>
roi_elapsed_seconds=<float>
```

| Field | Meaning |
|---|---|
| `backend_declared` | The backend the run script *intended* to use (`native` or `qemu_user_mode`), written before the benchmark runs. Present even if the benchmark itself crashes. |
| `program` | Source filename, self-reported. |
| `branch_pattern`, `cache_pattern` | Which catalogue entries generated this program. See `MANIFEST.csv`. |
| `predictor_stress_type`, `expected_dominant_miss_type` | **Hypotheses, not measurements** -- a keyword-heuristic guess at what kind of predictor/cache behavior this pattern should stress, computed from the pattern's own name/description at generation time. See `LIMITATIONS.md`. |
| `size_tier` | Which of the four default working-set tiers this program defaults to (`small_L1`, `medium_L2`, `large_L3`, `xlarge_DRAM`). Overridable at build time with `-DSIZE=... -DN=...`. |
| `backend` | The backend string baked into the binary via `-DLOWBIT_BACKEND="..."` at compile time -- should match `backend_declared` unless you built the binary yourself with a different flag. |
| `compiler`, `arch` | Compile-time constants (`__VERSION__`, architecture macros) baked into the binary -- describe how the binary was built, not the machine it's currently running on. For host/OS/kernel/QEMU/perf versions, see `ENVIRONMENT.md` (written once per `run` invocation, not per-program). |
| `SIZE`, `N`, `SEED` | The actual working-set size (elements), access count, and PRNG seed used for this run -- reflects any `-D` overrides. |
| `warmup_iterations` | How many untimed warm-up iterations ran before the region of interest (10% of `N`, capped at 200,000). |
| `taken`, `nottaken` | How many of the `N` region-of-interest iterations took the "taken" branch vs. not -- from the benchmark's own bookkeeping, not a hardware counter. |
| `sink` | An accumulator the loop writes to, purely to prevent the compiler from optimizing the loop away. Not meaningful on its own. |
| `validation` | `PASS` if `taken + nottaken == N` (a basic self-consistency check on the accounting, not a proof of correct hardware behavior). |
| `roi_elapsed_seconds` | Wall-clock time for the region-of-interest loop only (excludes allocation, data init, and warm-up), measured with `clock_gettime(CLOCK_MONOTONIC)`. In-process self-timing -- not a substitute for hardware cycle counters. |

The perf-stat block above the `program=` line (when perf was
available) contains the hardware counters requested in
`build_and_run_riscv.sh`'s `PERF_EVENTS` list: `instructions, cycles,
task-clock, context-switches, cpu-migrations, page-faults,
branch-instructions, branch-misses, cache-references, cache-misses`.
Its exact text format depends on your `perf` version and locale --
this is why the CSV parser exists rather than reading this block
directly.

## CSV columns (`lowbit_traces_for_training.csv` or similar)

Produced by `parse_traces_to_csv.py`. One row per program (reps
collapsed into summary statistics if you ran with `REPS > 1`).

| Column | Meaning |
|---|---|
| `filename` | Base program name (rep suffixes stripped). |
| `program`, `branch_pattern`, `cache_pattern`, `size_tier`, `predictor_stress_type`, `expected_dominant_miss_type`, `backend`, `compiler`, `arch` | Copied from the trace file, see above. |
| `SIZE`, `N`, `SEED`, `warmup_iterations`, `taken`, `nottaken`, `sink`, `validation` | Copied from the trace file (first rep's values -- these are identical across reps by construction, since they're determined at compile/generation time). |
| `reps` | How many repetitions were aggregated into this row. |
| `roi_elapsed_seconds_mean/median/stddev` | Statistics over `roi_elapsed_seconds` across reps. `stddev` is `0.0` if `reps == 1`. |
| `perf_available` | `0` if perf could not open hardware counters at all (e.g. `perf_event_paranoid` lockout) -- when `0`, every counter column below is `0` and meaningless, not "zero misses." |
| `instructions_total`, `cycles_total`, `branches_total`, `branch_misses_total`, `cache_references_total`, `cache_misses_total` | Summed across `cpu_atom`/`cpu_core` PMU domains if present (hybrid Intel CPUs), or the single reported value otherwise. Averaged across reps. |
| `task_clock_ms`, `context_switches`, `cpu_migrations`, `page_faults` | Scheduling-noise counters -- non-zero `context_switches`/`cpu_migrations` on a run indicate the measurement may have been disturbed by the OS scheduler; treat such rows with more skepticism. |
| `time_elapsed` | Wall-clock time as reported by `perf stat` itself (distinct from `roi_elapsed_seconds`, which the benchmark measures internally and which excludes `perf`'s own startup/teardown overhead). |
| `observed_ipc`, `observed_cpi` | `instructions_total / cycles_total` and its inverse. `0.0` if `instructions_total` or `cycles_total` is unavailable. |
| `observed_branch_miss_rate` | `branch_misses_total / branches_total`. |
| `observed_cache_miss_rate` | `cache_misses_total / cache_references_total`. |
| `observed_branch_density` | `branches_total / instructions_total` -- how branch-heavy the measured instruction stream was. |
| `observed_cache_refs_per_instruction` | `cache_references_total / instructions_total`. |
| `branches_to_N_ratio`, `plausible` | A sanity check: a correctly-measured run's `branches_total` should be within roughly 0.1x-20x of the benchmark's own reported `N`. Outside that range (and `perf_available == 1`), the measurement most likely didn't reflect the benchmark's actual execution -- see `LIMITATIONS.md`. |

**For ML training**: treat `predictor_stress_type`,
`expected_dominant_miss_type`, `branch_pattern`, and `cache_pattern`
as categorical input features describing how a trace was generated.
Treat the `observed_*` columns (when `perf_available == 1` and
`plausible == 1`) as measured targets/labels. Do not train a model to
predict `predictor_stress_type` from source code -- it's a heuristic
label already derived from that same code, so that would be circular.
