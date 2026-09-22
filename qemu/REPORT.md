# qemu/ backend -- Report

Based on `lowbit_traces_qemu.csv` (500/500 programs, one row each --
`traces/` contains 500 populated `.perf.txt` files). Run with
`riscv64-linux-gnu-gcc` (glibc, static) + `qemu-riscv64` (user-mode
emulation, dynamic binary translation) -- see `ENVIRONMENT.md` for the
exact toolchain/host snapshot from the run this report describes.

**Read this section before anything else if you're reusing this
CSV.** An earlier version of `build_and_run_riscv.sh` tried to collect
an *exact guest instruction count* via `qemu-riscv64 -singlestep -d
exec`, and had two stacked bugs: the size-based safety cap never
triggered (it checked for a `-DN=` compile flag that a normal build
never sets), and `-d exec` silently produces zero log lines on a stock
(non `--enable-debug-tcg`) qemu-user build regardless of what `-d help`
claims is available. The combination meant every one of the 500 runs
either took a impractically long, singlestepped path or came back with
a bogus `qemu_guest_instructions_retired=0`, and -- worse -- the
benchmark's own output wasn't reliably captured either, so the CSV
from that version was 500 rows of essentially empty data
(`validation`, `arch`, `compiler` all blank). That code path has been
removed entirely. The current script always runs the benchmark
directly (`qemu-riscv64 ./binary`, capturing its real stdout) as the
one thing that's never skipped or gated; an exact guest instruction
count is now purely optional, off by default, and only ever collected
through a TCG plugin (`QEMU_INSN_PLUGIN=/path/to/plugin.so`) as a
separate, isolated invocation that cannot affect the primary run. This
report is generated from a run of the corrected script.

## Correctness

- **500/500 programs passed `validation` (`taken + nottaken == N`)**.
- **`backend=qemu_user_mode`, `arch=riscv64`** on every row -- this
  really did cross-compile and run under RISC-V emulation, it isn't
  the native-x86-64 fallback silently kicking in (check `ENVIRONMENT.md`'s
  `backend:` line if you want to confirm this yourself on a re-run).
- **Cross-backend determinism check**: `taken`, `nottaken`, and `sink`
  match `../x86-64/lowbit_traces_native.csv` **exactly, for all 500
  programs, zero mismatches** -- same fixed seed, same deterministic
  logic, completely different ISA (RISC-V vs x86-64) and execution
  path (emulated vs native), identical outcome. This is real evidence
  the benchmark logic itself is correct and architecture-portable, not
  an artifact of either backend.
- `instruction_count_source` is `qemu_guest_none` for every row in
  this CSV (no `QEMU_INSN_PLUGIN` was set for this run) -- expected,
  not an error; `instructions_total` and friends are `None` for the
  same reason. Set `QEMU_INSN_PLUGIN` and re-run if you want exact
  guest instruction counts alongside the timing below.
- `perf_available=0` on every row **by design** -- this backend no
  longer wraps `perf` around `qemu-riscv64` at all, because doing so
  measures the **host** process translating and dispatching RISC-V
  instructions, not guest RISC-V behavior (see `../LIMITATIONS.md`).
  Earlier versions of this corpus did wrap perf here and a `plausible`
  heuristic flagged ~2/3 of those rows as implausible -- that was the
  expected symptom of exactly this host-vs-guest confusion, now
  avoided by simply not collecting those columns in the first place
  rather than collecting misleading ones.

## Timing (wall-clock, region-of-interest only)

| | Value |
|---|---|
| Min `roi_elapsed_seconds` | 0.0019 s |
| Max `roi_elapsed_seconds` | 0.7993 s |
| Mean | 0.0487 s |
| Median | 0.0255 s |

Size-tier distribution: `small_L1`=125, `medium_L2`=130,
`large_L3`=125, `xlarge_DRAM`=120 (same per-program tiers as
`../x86-64/`, since both use each program's real default `SIZE`/`N`
rather than a capped value).

**Slowest 5** (all `xlarge_DRAM`, all involve `c10_pointer_chase_linked_list`
or `c32_hilbert_curve_traversal`):
`030_b03_alternating__c10_pointer_chase_linked_list` (0.799 s),
`070_b07_nested_dependent__c10_pointer_chase_linked_list` (0.796 s),
`362_b37_value_driven_unswitch_run__c32_hilbert_curve_traversal` (0.538 s),
`322_b33_uniform_random_block_runs__c32_hilbert_curve_traversal` (0.528 s),
`466_b47_mutual_ping_pong_recursion__c46_cantor_gap_sparse` (0.423 s).

**Fastest 5** (all `small_L1`):
`014_b02_always_not_taken__c04_fixed_stride_large` (0.0019 s),
`005_b01_always_taken__c05_random_small_working_set` (0.0022 s),
`241_b25_rare_exception_1_in_1024__c21_pyramid_multiresolution_stride` (0.0024 s),
`254_b26_warmup_then_steady__c24_banked_interleaved_access` (0.0025 s),
`123_b13_single_phase_change__c13_two_stream_interleaved` (0.0025 s).

**Cache-family averages** (mean `roi_elapsed_seconds` across each
cache pattern's 50 branch-class pairings):

| Fastest cache families | Mean roi (s) | Slowest cache families | Mean roi (s) |
|---|---|---|---|
| `c01_sequential_unit_stride` | 0.0138 | `c44_naive_gemm_row_col_alternation` | 0.0839 |
| `c05_random_small_working_set` | 0.0152 | `c42_cache_oblivious_funnel_stride` | 0.1111 |
| `c02_sequential_wraparound_large` | 0.0167 | `c46_cantor_gap_sparse` | 0.1178 |
| `c24_banked_interleaved_access` | 0.0184 | `c10_pointer_chase_linked_list` | 0.2018 |
| `c16_block_sparse` | 0.0202 | `c32_hilbert_curve_traversal` | 0.2560 |

`c10_pointer_chase_linked_list` and `c32_hilbert_curve_traversal` are
the two slowest cache families here **and** in `../x86-64/REPORT.md`,
on two completely different backends -- consistent, cross-validated
signal that these patterns are genuinely the hardest on real memory
hierarchies, not a fluke of one specific host or execution model.

## Caveats

- These are host-process wall-clock timings while QEMU translates and
  dispatches RISC-V instructions -- not RISC-V hardware cycles. Don't
  present this backend's absolute numbers as RISC-V performance; use
  them for pattern-family *relative* comparison (as above) or for the
  cross-backend correctness check, not for anything claiming to
  characterize real RISC-V hardware speed. `../orange-pi/` is the
  backend that would give you that, once run.
- Same shared, virtualized, 1-vCPU-class host caveat as `../x86-64/REPORT.md`
  -- absolute numbers are indicative, not publication-grade.