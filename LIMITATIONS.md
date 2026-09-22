# Known Limitations

Read this before using the corpus for a paper, thesis, or any
conclusion about real RISC-V hardware behavior. These are not
boilerplate disclaimers -- one of them (measurement backend, #1 below)
directly affects whether `perf` output collected under QEMU means
what you think it means.

## 1. `perf stat qemu-riscv64 ./binary` measures the HOST, not the guest

`qemu-riscv64` is user-mode emulation: an x86-64 (or whatever your
host ISA is) process that JIT-translates RISC-V instructions and
executes the translation. `perf stat` attached to that process
measures the **host's** hardware counters -- the QEMU process's own
branches, cache accesses, and cycles, dominated by JIT/dispatch
overhead -- not a virtual RISC-V core's counters. If your `perf`
output shows `cpu_atom`/`cpu_core` PMU domains, that's your host's
hybrid-CPU counter domains, which is direct evidence the numbers came
from the host.

A reliable symptom: a correctly-measured run's `branches_total`
should be within roughly 0.1x-20x of the benchmark's own reported
`N` (one loop-control branch plus one benchmark branch per
iteration, roughly). Numbers far outside that range under
`qemu-riscv64` usually mean the counters reflect QEMU's translation
overhead, not the guest program. `parse_traces_to_csv.py` flags this
automatically via the `plausible` column (see `SCHEMA.md`).

**What to do about it**, in order of how trustworthy the result is:

1. **Real RISC-V hardware** (Orange Pi RV2, SiFive board, VisionFive2,
   etc.) with `perf stat` -- ground truth, no caveats.
2. **Full-system QEMU** (`qemu-system-riscv64`, boots a real kernel)
   with `perf` running *inside* the guest VM -- much closer to real,
   though the vPMU may not model every guest microarchitectural event
   depending on your QEMU/KVM configuration.
3. **QEMU TCG instruction/plugin instrumentation**
   (`qemu-riscv64 -plugin contrib/plugins/libinsn.so`, or a custom
   cache-simulating plugin) -- instruments the *guest* instruction
   stream directly, so counts are guest-accurate, but these are
   QEMU's own instrumentation counts, not real hardware PMU behavior
   (no real branch predictor or cache is actually being modeled).
4. **A cycle-accurate simulator** (gem5 in RISC-V mode, Spike with a
   timing model, Sniper) -- models a specific predictor/cache
   microarchitecture explicitly, so results depend on how well that
   model matches the hardware you care about.
5. **Native build + host `perf stat`** -- real hardware counters, but
   for the host's own predictor/cache, not RISC-V. Useful for
   validating that the *benchmark's own logic* behaves as intended
   (does `always_taken` actually mispredict near 0%? does
   `pseudo_random_50` actually mispredict near 50%?), not for
   RISC-V-specific conclusions.

## 2. `perf_event_paranoid` can silently block measurement entirely

Some systems (containers, cloud VMs, some distro defaults, some WSL
configurations) set `kernel.perf_event_paranoid` high enough that
`perf stat` refuses to even launch the child process -- meaning the
benchmark never runs at all, not just that the counters come back
empty. `build_and_run_riscv.sh` checks for this before looping over
all 500 binaries and, if detected, prints the fix
(`sudo sysctl kernel.perf_event_paranoid=-1`, with the
`/etc/sysctl.conf` line to make it permanent) and falls back to
running binaries directly (no perf wrapping) so you still get the
programs' own `taken`/`nottaken`/`sink`/`roi_elapsed_seconds` output
instead of empty error files. `parse_traces_to_csv.py`'s
`perf_available` column distinguishes this case from #1 above --
different problems, different fixes (see `SCHEMA.md`).

## 3. Branch and cache effects are not perfectly isolated

Every iteration does index arithmetic, a load, a branch, and a store.
There is no way to time "just the branch" or "just the cache access"
in a single instruction stream without also paying for the
surrounding plumbing, and the loop-control branch itself is measured
alongside the branch-under-test. This is inherent to measuring one
CPU running one instruction stream; no benchmark design here fixes
it. If you need cleaner isolation, the real fix is a differential
design (run pattern A, run a "null" version of A with the branch or
cache access replaced by a matched-cost no-op, and attribute the
difference) -- a larger redesign than this corpus attempts.

## 4. Compiler dependence -- if-conversion

GCC can rewrite a simple `if (taken) a += x; else a -= y;` into a
branchless conditional-move (`cmov`) sequence at some optimization
levels, which would silently turn a branch-predictor benchmark into
an ALU benchmark. The documented build flags include
`-fno-if-conversion -fno-if-conversion2` specifically to prevent
this, and `build_and_run_riscv.sh verify` spot-checks a sample of
built binaries with `objdump -d` for stray `cmov` instructions. This
is a sample check, not an exhaustive audit of all 500 binaries --
always verify yourself if you change compiler, optimization level, or
these flags.

## 5. `predictor_stress_type` / `expected_dominant_miss_type` are hypotheses

These fields are generated by a keyword-matching heuristic over each
pattern's name/description at generation time, not measured. Two
concrete caveats:

- They can be fooled by comparative language in a pattern's own
  description (e.g. a pattern whose description mentions "distinct
  from X's randomly-triggered behavior" for context can pick up
  "random" as a false-positive keyword match). This is a labeling
  artifact of the heuristic, not a benchmark-logic bug, but it means
  the label should never be treated as authoritative.
- The branch predictor's actual behavior (2-bit vs. GShare vs. TAGE
  vs. perceptron, etc.) determines the *real* misprediction rate for
  a given pattern, and that varies by CPU. `always_taken` will be
  ~0% mispredicted on essentially any real predictor; `pseudo_random_50`
  will be ~50% mispredicted on essentially any real predictor; most of
  the other 48 patterns are NOT that clear-cut, and their true
  difficulty is an empirical question this generator cannot assert.

**For ML training**: treat `branch_pattern` / `cache_pattern` /
`predictor_stress_type` / `expected_dominant_miss_type` as categorical
*input features* describing how a trace was generated, and treat
measured hardware counters (the `observed_*` CSV columns, from a
trustworthy source per #1) as the *labels*/targets. Do not train a
model to predict `predictor_stress_type` from the code -- that's
circular, since it's a heuristic label already derived from the code.

## 6. Memory hierarchy categories are simplified

`expected_dominant_miss_type` uses coarse buckets (compulsory,
capacity, conflict, streaming, temporal, sparse/gather, thrashing).
Real caches exhibit compulsory/capacity/conflict misses
*simultaneously* in proportions that depend on associativity,
replacement policy, prefetcher behavior, and TLB effects -- a
single-word tag is a simplification, not a decomposition. It's
provided as a coarse prior for exploratory analysis, not a rigorous
classification.

## 7. Instruction-cache / front-end effects are not covered

This corpus is data-cache- and branch-predictor-focused. It does not
include I-cache, BTB-capacity, or return-address-stack-specific
stress tests (huge switch statements spanning many pages, deep call
trees with distinct callees per call site, thousands of basic
blocks). That's a legitimate separate benchmark family this corpus
doesn't attempt.

## 8. Warm-up is a fixed heuristic, not adaptive

Every benchmark warms up for `min(N/10, 200000)` iterations before
the timed region starts. This is a fixed rule of thumb, not an
adaptive "run until steady state is detected" scheme -- for some
patterns (e.g. ones with very long-period phase changes) it may not
be enough to reach a true steady state, and for others it's more than
necessary. `warmup_iterations` is recorded per run so you can account
for it in analysis.

## 9. Self-timing is informational, not a hardware-counter substitute

`roi_elapsed_seconds` is measured with `clock_gettime(CLOCK_MONOTONIC)`
inside the process -- portable, but subject to OS scheduling noise,
and it measures wall-clock time, not cycles or instructions. Use it
for regression checks (did this run take a wildly different amount of
time than expected?) and cross-reference with `context_switches` /
`cpu_migrations` in the CSV; don't treat it as a cycle-accurate
measurement.

## 10. No golden-reference correctness check

The `validation` field only checks `taken + nottaken == N` -- basic
loop-accounting self-consistency, not a proof that the branch/cache
pattern executed the "correct" sequence of operations. There's no
independent golden reference for a synthetic access-pattern generator
to check against beyond re-deriving the sequence from the source
itself, which the validation check doesn't attempt.
