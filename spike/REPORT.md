# spike/ backend -- Report

Based on `lowbit_traces_spike.csv` (500/500 programs, one row each --
`spike_traces/` contains 500 populated `.spike.txt` files). Built with
`riscv64-unknown-elf-gcc` (newlib) and run under `spike` + `pk`
(golden-model RISC-V ISA simulator + proxy kernel). `SIZE=4096`,
`N=20000` (same capped scale as `../gem5/`, for consistency with that
backend rather than for speed reasons -- spike itself runs this scale
in well under a second per program).

**This required one real fix to the shared benchmark runtime, not
just this backend's script.** `riscv64-unknown-elf-gcc` links against
newlib, which -- unlike glibc -- implements neither `posix_memalign`
nor `clock_gettime` under any feature-test macro; those aren't merely
hidden behind a header guard, they're simply not present in newlib's C
library. `../programs/lowbit_runtime.h` now has a `__NEWLIB__`-gated
fallback: a manual aligned allocator on top of plain `malloc`, and a
RISC-V `rdcycle` CSR read in place of `clock_gettime` (needs no
OS/syscall support at all, which suits `pk`'s minimal syscall surface
well). The fallback only activates when compiling with
`riscv64-unknown-elf-gcc` specifically -- every other backend's build
is byte-for-byte unaffected; verified by compiling and running the
unpatched code path under plain `gcc` before and after the change.

## Correctness

- **500/500 programs passed their own `validation` self-check**
  (`taken + nottaken == N`).
- **`perf_available=0` for all 500 rows, correctly.** This backend has
  no perf integration at all -- `run_spike.sh` never invokes `perf`,
  and the parser now forces this column to `0` unconditionally rather
  than defaulting to `1` on the absence of a perf-failure string (a
  real bug in an earlier version of the parser, since fixed -- see the
  parser's own comment for the fix, and `git log`/prior conversation
  history if you're curious about the specific failure mode it
  corrected).
- **Cross-check against `../qemu/`**: `branch_pattern`/`cache_pattern`
  metadata matches exactly for all 500 programs (as it must -- both
  are generated from the same `../programs/*.c` sources). Because
  spike runs at `N=20000` and qemu runs at each program's full-scale
  `N` (up to 8,000,000), exact `taken`/`nottaken` totals differ by
  design; comparing the *ratio* `taken/N` instead (which should be
  roughly N-invariant for most branch patterns) finds **441/500
  (88.2%) matching within 2%** across the two backends. The other
  11.8% are not a correctness concern -- they cluster around
  N-scale-dependent branch families by design (`b26_warmup_then_steady`,
  `b13_single_phase_change`, `b22_drifting_threshold_periodic_reset`,
  and similar phase/warmup patterns whose taken-ratio genuinely shifts
  as N grows, since a fixed-length warmup phase becomes a smaller
  fraction of a larger N). This is the expected shape for that
  specific comparison, not a red flag.

## Timing (spike's `rdcycle`-based `roi_elapsed_seconds`, not wall-clock)

**Read this before using these numbers for anything.** Spike is a
purely functional ISA simulator -- it has no pipeline, branch
predictor, or cache model, and its `rdcycle` counter increments
roughly once per retired instruction, not once per real hardware
cycle on any actual core. `roi_elapsed_seconds` here is that cycle
count divided by an assumed 1 GHz, purely so the unit is internally
consistent within this one backend -- it is **not** comparable to
wall-clock seconds from `../qemu/` or `../x86-64/`, and it is **not**
a timing simulation the way `../gem5/` is. Treat it as "how many
instructions did this program execute," relabeled as seconds for
convenience, nothing more.

| | Value |
|---|---|
| Min | 0.000196 s |
| Max | 0.002639 s |
| Mean | 0.000576 s |

**Branch-family ranking** (mean, this metric): the "hardest" (slowest)
families are `b47_mutual_ping_pong_recursion`, `b09_recursive_calls`,
and `b42_bit_reversal_index_data_mix`; the "easiest" are
`b02_always_not_taken`, `b01_always_taken`, and `b05_periodic8_biased`.
This is genuinely a different axis than `../gem5/REPORT.md`'s
`MinorCPU` branch-*miss-rate* ranking (which has `b06_pseudo_random_50`
and `b23_popcount_parity` as hardest) -- spike has no predictor to
mispredict, so what it's actually measuring here is **how much extra
control-flow/instruction overhead** a branch pattern's own
implementation requires (recursive function-call overhead, extra
bit-manipulation instructions), not predictability. Both are real,
useful, and simply not the same question.

**Cache-family ranking** (same metric): `c32_hilbert_curve_traversal`
and `c42_cache_oblivious_funnel_stride` are slowest,
`c33_cache_set_conflict_stress` and `c12_prime_stride_anti_alias` are
fastest. `c32_hilbert_curve_traversal` being slowest here matches its
being one of the two slowest wall-clock cache families on **every
other backend that produced real timing data** (`../qemu/`,
`../x86-64/`) and one of the hardest under `../gem5/`'s simulated
cache -- for the same underlying reason on spike as everywhere else:
Hilbert-curve index computation needs more instructions per access
step, and spike's cycle count (unlike its complete lack of a cache
model) does reflect raw instruction volume accurately.

## Caveats

- No timing model, no cache model, no branch predictor -- see the
  "Timing" section above before using `roi_elapsed_seconds` for
  anything beyond a rough instruction-volume proxy.
- Cross-validated for functional correctness against `../qemu/`
  (metadata match + 88.2% taken-ratio match, explained above) but not
  yet against `../orange-pi/` (real hardware) -- see
  `../REPORT_AND_COMPARISONS.md` for what that comparison would add.
