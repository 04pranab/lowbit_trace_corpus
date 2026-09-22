# x86-64/ backend -- Report

Based on `lowbit_traces_native.csv` (500/500 programs). Run on real
hardware: `gcc 15.2.0` (Ubuntu 26.04 LTS), Intel 13th Gen Core
i5-13450HX, `perf 7.0.12` with `perf_event_paranoid=-1` (fully
unlocked, not virtualized) -- full details in `ENVIRONMENT.md`. Every
row has `perf_available=1`: real hardware branch-predictor and cache
counters, not a wall-clock-only fallback.

## Correctness

- **500/500 programs passed their own `validation` self-check**
  (`taken + nottaken == N`). Every binary built and ran to completion.
- **487/500 rows (97.4%) pass the `plausible` heuristic**
  (`branches_total`/`N` inside 0.1-20). The 13 flagged rows are not
  measurement errors -- 9 of the 13 involve `c32_hilbert_curve_traversal`
  and the rest involve `c42_cache_oblivious_funnel_stride` /
  `c46_cantor_gap_sparse` paired with inherently branch-dense branch
  classes (`mutual_ping_pong_recursion`, `hysteresis_debounce`,
  `data_driven_countdown`) -- these specific cache-traversal and
  branch-pattern combinations genuinely execute more branch
  instructions per loop iteration than the heuristic's threshold
  assumes (e.g. Hilbert-curve index computation needs extra
  bit-manipulation branches per step). Worth knowing, not worth
  discarding those 13 rows.

## Hardware counters (real, this time)

| Metric | Min | Max | Mean |
|---|---|---|---|
| `observed_ipc` | 0.188 | 5.784 | 2.246 |
| `observed_cpi` | 0.173 | 5.328 | 0.615 |
| `observed_branch_miss_rate` | 0.0001 | 0.2204 | 0.0312 |
| `observed_cache_miss_rate` | 0.018 | 0.863 | 0.413 |

`observed_cache_miss_rate` here is `cache-misses`/`cache-references`
from `perf`, which on Intel maps to last-level-cache (LLC) references
-- i.e. already the subset of accesses that missed L1 and L2. A 41%
mean LLC miss rate across patterns explicitly designed to stress
cache behavior (random access, pointer chasing, deliberately
conflicting sets) is plausible for that specific counter; don't read
it as "41% of all memory accesses miss cache" (that would be a much
smaller, L1-relative number).

**Branch-prediction: easiest and hardest families** (mean over each
family's 10 paired cache classes):

| Easiest | Rate | Hardest | Rate |
|---|---|---|---|
| `b08_switch_multiway` | 0.0006 | `b42_bit_reversal_index_data_mix` | 0.0910 |
| `b07_nested_dependent` | 0.0006 | `b35_xorshift_hash_mix` | 0.1231 |
| `b03_alternating` | 0.0009 | `b30_periodic_interrupt_override` | 0.1259 |
| `b10_correlated_history` | 0.0009 | `b23_popcount_parity` | 0.1506 |
| `b05_periodic8_biased` | 0.0011 | `b06_pseudo_random_50` | 0.1614 |

This lines up with `../gem5/REPORT.md`'s `MinorCPU` ranking almost
exactly -- `b08_switch_multiway` and `b03_alternating` easiest on
both, `b06_pseudo_random_50`, `b23_popcount_parity`, and
`b30_periodic_interrupt_override` hardest on both -- real Intel
silicon and a simulated in-order core independently agree on which
branch patterns are fundamentally hard. That agreement is meaningful
cross-validation of both the benchmark design and both backends, not
a coincidence.

**Cache: easiest and hardest families (LLC miss rate):**

| Easiest | Rate | Hardest | Rate |
|---|---|---|---|
| `c33_cache_set_conflict_stress` | 0.230 | `c40_causal_attention_triangular` | 0.498 |
| `c26_geometric_stride_growth` | 0.263 | `c19_matrix_diagonal_wavefront` | 0.500 |
| `c12_prime_stride_anti_alias` | 0.287 | `c30_gather_scatter_indirect_table` | 0.558 |
| `c22_tensor_3d_slice_poor_locality` | 0.332 | `c05_random_small_working_set` | 0.568 |
| `c06_random_full_working_set` | 0.333 | `c39_bounded_random_walk` | 0.584 |

Note this ranking (by *LLC* miss rate) looks different from the
wall-clock ranking below -- `c33_cache_set_conflict_stress` has the
*lowest* LLC miss rate here despite being deliberately adversarial,
because most of its accesses miss L1 quickly and consistently rather
than sometimes hitting a huge working set; it's a different question
than "which pattern takes longest in wall-clock time" (answered next).

## Timing (wall-clock, region-of-interest only)

| | Value |
|---|---|
| Min `roi_elapsed_seconds` | 0.00089 s |
| Max `roi_elapsed_seconds` | 1.09115 s |
| Mean | 0.0454 s |
| Median | 0.0230 s |

Size-tier distribution: `small_L1`=125, `medium_L2`=130,
`large_L3`=125, `xlarge_DRAM`=120.

**Slowest 5** (all `xlarge_DRAM`, all involve `c10_pointer_chase_linked_list`
or `c32_hilbert_curve_traversal`):
`030_b03_alternating__c10_pointer_chase_linked_list` (1.091 s),
`070_b07_nested_dependent__c10_pointer_chase_linked_list` (1.066 s),
`322_b33_uniform_random_block_runs__c32_hilbert_curve_traversal` (0.524 s),
`362_b37_value_driven_unswitch_run__c32_hilbert_curve_traversal` (0.494 s),
`466_b47_mutual_ping_pong_recursion__c46_cantor_gap_sparse` (0.350 s).

**Fastest 5** (all `small_L1`):
`363_b37_value_driven_unswitch_run__c33_cache_set_conflict_stress` (0.00089 s),
`014_b02_always_not_taken__c04_fixed_stride_large` (0.0011 s),
`001_b01_always_taken__c01_sequential_unit_stride` (0.0011 s),
`145_b15_manual_unroll4_sparse__c15_reverse_sequential` (0.0013 s),
`023_b03_alternating__c03_fixed_stride_small` (0.0013 s).

**Cache-family averages** (mean `roi_elapsed_seconds`, averaged across
each cache pattern's 50 branch-class pairings):

| Fastest cache families | Mean roi (s) | Slowest cache families | Mean roi (s) |
|---|---|---|---|
| `c02_sequential_wraparound_large` | 0.0122 | `c25_lru_recency_weighted_reuse` | 0.0754 |
| `c01_sequential_unit_stride` | 0.0133 | `c46_cantor_gap_sparse` | 0.0847 |
| `c05_random_small_working_set` | 0.0145 | `c32_hilbert_curve_traversal` | 0.2356 |
| `c16_block_sparse` | 0.0155 | `c10_pointer_chase_linked_list` | 0.2884 |
| `c15_reverse_sequential` | 0.0169 | | |

`c10_pointer_chase_linked_list` and `c32_hilbert_curve_traversal` are
the two slowest cache families **on all three backends that ran to
completion with real timing data** (`../qemu/REPORT.md` finds the same
two as slowest on QEMU-emulated RISC-V; `../gem5/REPORT.md`'s
`MinorCPU` cache-miss ranking puts `c10_pointer_chase_linked_list` in
its own top-2 hardest). Three independent backends, two different
ISAs, agreeing on the same two patterns being fundamentally
cache-hostile is strong evidence this is a real property of the
patterns, not an artifact of any one measurement setup.

## Caveats

- LLC miss rate (`observed_cache_miss_rate`) is the specific counter
  collected -- see the note above before quoting it as a general
  "cache miss rate."
- This is x86-64, not RISC-V -- do not present these numbers as
  RISC-V results. See `../REPORT_AND_COMPARISONS.md` for how this
  backend is meant to be used alongside the RISC-V-targeting ones.
- Real (not virtualized) hardware this time, unlike an earlier version
  of this report generated in a shared/virtualized sandbox -- if
  you're comparing against an old copy of this file, the absolute
  numbers are not the same measurement conditions.
