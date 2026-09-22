# LowBit Trace Corpus (LTC)

A deterministic, portable benchmark suite for generating labeled CPU
traces of branch-prediction and cache-access behavior on RISC-V (and
any general-purpose CPU), built for training and evaluating ML models
that predict branch-misprediction and cache-hit/miss behavior from
program structure.

500 benchmark programs, each isolating one branch-prediction pattern
crossed with one cache-access pattern, run through a common
region-of-interest measurement lifecycle and a shared trace-collection
pipeline.

## Design

**One benchmark, one architectural question.** Every program combines
exactly one of 50 branch-prediction patterns with exactly one of 50
cache-access patterns -- always-taken through Markov-chain-driven
branches; sequential streaming through Hilbert-curve traversal and
gather/scatter indirection. No file mixes unrelated behaviors.

**Deterministic, always.** Every program uses a fixed-seed linear
congruential generator for any pseudo-randomness -- no `rand()`, no
OS entropy, no wall-clock-dependent control flow. The same binary
produces the same instruction trace on every run, on every machine.

**Backend-agnostic benchmarks.** A benchmark source file never knows
or cares whether it's running natively, under QEMU, or on real
RISC-V silicon. It contains no `perf_event_open` calls, no simulator
awareness, no inline assembly, and no architecture-specific
instructions. All of that lives in `build_and_run_riscv.sh` and the
trace-parsing tooling, entirely outside the 500 generated `.c` files.

**Generator-driven, not hand-edited.** The 500 files are never edited
by hand. Five Python scripts (`gen_programs.py` through
`gen_programs_part5.py`), each a catalogue of branch/cache pattern
descriptions plus one shared template, are the single source of
truth. Changing the template and regenerating updates the entire
corpus consistently.

**Explicit lifecycle.** Every benchmark follows the same structure:
allocate (64-byte cache-line-aligned) -> initialize data -> warm-up
(untimed, primes caches/predictor/pattern state) -> region of interest
(timed, this is what gets reported) -> validate (a basic
self-consistency check) -> report -> cleanup.

**Configurable without editing source.** `SIZE` and `N` are
`#ifndef`-guarded, not hardcoded, so a full parameter sweep is
possible via `-DSIZE=... -DN=...` at compile time. The execution
backend is labeled the same way, via `-DLOWBIT_BACKEND="..."`.

## Repository structure

```
programs/                   the 500 benchmark programs (flat, no subfolders) +
                             lowbit_runtime.h (shared LCG, aligned alloc, ROI
                             timing, arch/compiler metadata -- no backend code)
gen_programs/                the generator: 5 catalogue scripts + shared template,
                             single source of truth for everything in programs/
MANIFEST.csv                 machine-readable index of all 500 programs
SCHEMA.md                    field-by-field reference for every trace/CSV field
LIMITATIONS.md               known measurement limitations, read before trusting results
LICENSE.md                   licensing terms
REPORT_AND_COMPARISONS.md    cross-backend results and comparisons for the whole corpus

-- one folder per measurement backend, each with its own script(s),
   its own results, and its own README.md + REPORT.md --
qemu/          RISC-V cross-compiled + qemu-riscv64 (functional emulation) + perf
gem5/          RISC-V cross-compiled + gem5 timing simulation (real predictor/cache model)
spike/         RISC-V cross-compiled + spike (golden-model ISA simulator)
x86-64/        native host build + perf -- real silicon, but not RISC-V
orange-pi/     native on-device build + perf on real RISC-V hardware
```

Every backend folder builds and runs the *same* `programs/*.c`
sources; nothing in `programs/` is backend-specific. Each backend's
script resolves `../programs` relative to its own location, so you
can run any of them from anywhere, and each writes its own binaries,
raw output, and CSV inside its own folder -- results are never
scattered outside the backend that produced them, and never merged
across backends without an explicit, labeled `backend` column. See
each folder's `README.md` for exact usage and requirements, and its
`REPORT.md` for that backend's results (or, for backends that need
hardware/toolchains not present in whatever environment last touched
this repo, what's needed to produce real results).

## Taxonomy

50 branch classes x 50 cache classes, organized as five batches of
10x10 (`b01`-`b10` x `c01`-`c10`, `b11`-`b20` x `c11`-`c20`, and so
on through `b41`-`b50` x `c41`-`c50`). `MANIFEST.csv` is the
authoritative index; a representative sample of what's covered:

- **Branch patterns**: always/never-taken, periodic, pseudo-random,
  nested/meta-correlated, history-correlated, switch/indirect
  dispatch, recursive and mutually-recursive call/return, Markov
  chains, saturating-counter emulation, value-dependent (parity,
  popcount, Collatz transform), phase-changing (drift, warm-up,
  bursts, hysteresis), lookup-table-driven, and more.
- **Cache patterns**: sequential/strided streaming, pseudo-random
  (small and full working set), pointer-chasing, matrix row/column/
  tiled/diagonal/Morton/Hilbert traversal, sliding windows, prime and
  geometric strides, cache-set conflict stress, false-sharing
  simulation, zipf-skewed hot sets, producer-consumer, ping-pong
  thrashing, gather/scatter indirection, binary-heap traversal, and
  more.

Four default working-set size tiers cycle across all five batches:
`small_L1` (~16 KiB), `medium_L2` (~256 KiB), `large_L3` (~4 MiB),
`xlarge_DRAM` (~32 MiB) -- all overridable per-build via `-DSIZE=...
-DN=...`.

## Requirements

- `gcc` (native builds) and, for RISC-V cross-compilation,
  `riscv64-unknown-linux-gnu-gcc` or `riscv64-linux-gnu-gcc`
- `qemu-riscv64` (user-mode emulation) for running RISC-V binaries
  without physical hardware
- `perf` (Linux `perf_events`) for hardware counter collection --
  optional; the run script falls back gracefully if unavailable (see
  `LIMITATIONS.md`)
- `objdump` for compiler-output verification (optional but recommended)
- Python 3 for the generator scripts and CSV parser (standard library
  only, no dependencies)

## Quick start

```bash
cd qemu

# Build + verify + run everything (auto-detects RISC-V cross compiler + qemu-riscv64,
# falls back to a native x86-64 build otherwise), 1 repetition per program
./build_and_run_riscv.sh

# Or step by step
./build_and_run_riscv.sh build             # compile all 500
./build_and_run_riscv.sh verify            # objdump-check a sample for cmov leakage
./build_and_run_riscv.sh run 5             # run everything, 5 repetitions per program
./build_and_run_riscv.sh env               # (re)write ENVIRONMENT.md only

# Parse the resulting traces into an ML-ready CSV
python3 parse_traces_to_csv.py traces lowbit_traces_qemu.csv
```

`build_and_run_riscv.sh run` also writes `ENVIRONMENT.md`, a snapshot
of the compiler version, host OS/CPU, and QEMU version in effect for
that run -- keep it alongside any CSV you generate.

For a deliberate, real-hardware-adjacent comparison instead of (or
alongside) the QEMU path, see `x86-64/` (native host, real perf
counters, wrong ISA), `gem5/` (RISC-V timing simulation, real
predictor/cache model), `spike/` (RISC-V golden-model ISA simulator),
and `orange-pi/` (real RISC-V silicon) -- each is a separate,
independently runnable backend in its own folder.

### Sweeping parameters

Every benchmark can be rebuilt at a different scale without touching
source:

```bash
riscv64-unknown-linux-gnu-gcc -O2 -fno-if-conversion -fno-if-conversion2 \
    -DSIZE=65536 -DN=3000000 -DLOWBIT_BACKEND=\"qemu_user_mode\" \
    -static -o custom_057 programs/057_b06_pseudo_random_50__c07_matrix_row_major.c
```

### Verifying a build

GCC can rewrite the benchmark's branch into a branchless `cmov`
sequence at higher optimization levels, which would silently defeat
the whole point of a branch-predictor benchmark.
`qemu/build_and_run_riscv.sh verify` checks a sample of binaries for
this; to check any specific one yourself:

```bash
objdump -d qemu/bin/057_... | grep cmov
```

Zero hits in the region around `main` (ignoring unrelated
library/startup code) means the benchmark branch survived as a real
conditional jump.

## Output format

See `SCHEMA.md` for the complete field-by-field reference. In short:
every program prints its own metadata and self-consistency check
(`taken`/`nottaken`/`validation`) regardless of whether hardware
counters were available; `perf stat` output (when available) is
captured above that in the same trace file; `parse_traces_to_csv.py`
turns a directory of trace files into one CSV row per program, with
derived metrics (IPC, CPI, branch/cache miss rates) computed from the
raw counters.

## Reproducibility

- Every benchmark is deterministic given its compiled-in (or
  `-D`-overridden) `SIZE`/`N`/`SEED` -- rerunning produces bit-identical
  `taken`/`nottaken`/`sink` values.
- `ENVIRONMENT.md` captures the toolchain and host state for a given
  `run` invocation.
- `predictor_stress_type` and `expected_dominant_miss_type` are
  recorded as explicit hypotheses (see `SCHEMA.md` and
  `LIMITATIONS.md`), not asserted as measured fact.
- Multiple repetitions (`run N`) are supported and aggregated
  (mean/median/stddev) by the parser, rather than relying on a single
  sample per program.

Read `LIMITATIONS.md` before using this corpus for a paper, thesis, or
any conclusion about real RISC-V hardware behavior -- in particular,
what `perf stat qemu-riscv64 ./binary` does and does not measure.

## License

See `LICENSE.md`. Copyright (c) 2026 Om Pranab Mohanty. All rights
reserved, with academic-use terms.

---

## The five measurement backends

`qemu-riscv64` (in `qemu/`) is the default, easiest-to-run RISC-V
path. It does **not** wrap `perf` around the emulated process -- an
earlier version of this repo did, and that measures the **host**
process translating and dispatching RISC-V instructions, not guest
RISC-V behavior, which is misleading rather than merely imprecise
(see `LIMITATIONS.md`). What `qemu/` gives you instead: guaranteed-
correct functional execution and wall-clock timing, plus an optional,
opt-in *exact* guest instruction count via a TCG plugin. Four other
backends exist specifically to give you what QEMU structurally
cannot -- real timing, real predictor/cache stats, or real hardware:

| Folder | What it runs | What it measures | Status in this repo |
|---|---|---|---|
| `qemu/` | RISC-V cross-compiled, under `qemu-riscv64` | Correctness + wall-clock timing (no perf wrapping -- see above) | **Run, 500/500 programs** -- see `qemu/REPORT.md` |
| `gem5/` | RISC-V cross-compiled, under gem5's timing simulator | Simulated predictor + cache stats, two CPU models (`MinorCPU`, `AtomicSimpleCPU`) | **Run, 500/500 programs, both CPU models** -- see `gem5/REPORT.md` |
| `spike/` | RISC-V cross-compiled, under spike (golden-model ISA sim) | Correctness + relative instruction-volume timing, no predictor/cache model | **Run, 500/500 programs** -- see `spike/REPORT.md` |
| `x86-64/` | Native x86-64 build, no emulation | Real hardware branch-predictor + cache counters -- right idea, wrong ISA | **Run, 500/500 programs, real hardware perf counters** -- see `x86-64/REPORT.md` |
| `orange-pi/` | Native build, on real RISC-V hardware | Real RISC-V silicon -- the ground truth every other backend approximates | Documented, not yet run (no RISC-V board available in this environment) -- see `orange-pi/REPORT.md` |

Four of five backends are fully run with real data and cross-validated
against each other (see `REPORT_AND_COMPARISONS.md` for specifics --
notably, `qemu/` and `x86-64/` agree on `taken`/`nottaken`/`sink` for
all 500 programs with zero mismatches, despite completely different
ISAs and execution models). `orange-pi/` -- real RISC-V hardware -- is
the one gap: it's the only backend that could confirm or contradict
whether `gem5/`'s simulated branch-predictor/cache numbers track real
RISC-V silicon.

Each backend lives entirely in its own folder: its own script(s), its
own `bin`/binaries, its own raw output, its own CSV, its own
`README.md` and `REPORT.md`. **Never merge CSVs across backends
without keeping the `backend` column** -- they answer different
questions and are not interchangeable. `REPORT_AND_COMPARISONS.md` at
the repo root does that comparison deliberately, with all four
backends that have real data.
