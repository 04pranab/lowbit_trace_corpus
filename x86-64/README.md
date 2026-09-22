# x86-64/ -- native host backend (real silicon, wrong ISA)

Builds every `*.c` program in `../programs/` directly with the host's
own `gcc` -- no cross-compilation, no emulator -- and runs the
resulting binaries directly on this machine. If `perf` is available,
this is the only backend in the corpus that measures **real hardware
branch-predictor and cache counters on real silicon** without any
translation or simulation layer in the way. The tradeoff: it's
x86-64, not RISC-V, so it answers "how does this access pattern
behave on a real out-of-order x86 core" rather than anything about
RISC-V hardware.

See `../README.md` for how this backend relates to `../qemu/`,
`../gem5/`, `../spike/`, and `../orange-pi/` -- five genuinely
different things, on purpose, kept in separate folders so their
results are never accidentally merged.

## Files

| File | Purpose |
|---|---|
| `run_x86_native.sh` | Build + verify + run all 500 programs from `../programs/`, natively. |
| `parse_x86_traces_to_csv.py` | Turn `traces/*.perf.txt` into an ML-ready CSV. |
| `bin/` | Compiled binaries. |
| `traces/` | Raw per-program output (perf counters if available, else just the program's own self-report). |
| `ENVIRONMENT.md` | Toolchain/host/perf snapshot for the run captured in this folder. |
| `lowbit_traces_native.csv` | Parsed output of `traces/`. |
| `REPORT.md` | Results and analysis of the run already captured in this folder. |

## Usage

```bash
cd x86-64
./run_x86_native.sh                  # build + verify + run everything, 1 rep
./run_x86_native.sh build            # build only
./run_x86_native.sh verify           # objdump-check a sample of builds (looks for cmov)
./run_x86_native.sh run 5            # run only, 5 repetitions per program
python3 parse_x86_traces_to_csv.py traces lowbit_traces_native.csv
```

## Requirements

- `gcc` (any recent version).
- `perf` (Linux `perf_events`) for hardware counters -- optional; the
  script degrades gracefully if it's unavailable or blocked by
  `kernel.perf_event_paranoid`, and still records every program's own
  self-reported `taken`/`nottaken`/`sink`/`roi_elapsed_seconds`/
  `validation` fields.
- `objdump` for the `verify` step (optional).

## In this environment

`bin/`, `traces/`, `ENVIRONMENT.md`, and `lowbit_traces_native.csv`
contain a completed run of all 500 programs on real hardware (Intel
13th Gen Core i5-13450HX, `perf 7.0.12`, `perf_event_paranoid=-1` --
see `ENVIRONMENT.md`). `perf_available=1` for every row: real hardware
branch-predictor and cache counters, not a wall-clock-only fallback.
Full analysis in `REPORT.md`.
