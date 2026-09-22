# spike/ -- Spike backend (RISC-V golden-model ISA simulator)

Cross-compiles selected programs from `../programs/` for RISC-V and
runs them under `spike` (the official `riscv-isa-sim` reference
simulator), via the RISC-V proxy kernel (`pk`).

**This is a functional simulator, like `../qemu/`, not a timing
simulator like `../gem5/`.** There is no branch predictor or cache
hierarchy being modeled -- `spike` executes RISC-V instructions
correctly and reproduces each benchmark's own self-reported
`taken`/`nottaken`/`sink`/`roi_elapsed_seconds`/`validation` fields,
but has no `observed_ipc`/`observed_branch_miss_rate`/
`observed_cache_miss_rate` to offer, because nothing in this backend
computes those concepts. See the header comment in `run_spike.sh` for
what spike is actually useful for in this corpus: a second,
independent golden-model correctness check, and instruction-level
trace ground truth via `spike --log-commits` if you need it (not
wired up by the default script -- see spike's own `--help`).

## Files

| File | Purpose |
|---|---|
| `run_spike.sh` | Build + run selected programs from `../programs/` under spike. |
| `parse_spike_traces_to_csv.py` | Turn `spike_traces/*.spike.txt` into a CSV (self-reported fields only). |
| `bin_spike/` | Compiled binaries. |
| `spike_traces/` | Raw per-program spike output. |
| `REPORT.md` | Status of this backend and what to fill in once you've run it. |

## Usage

```bash
cd spike
export SPIKE_ROOT=/opt/riscv        # wherever you installed spike + pk
export PATH="$SPIKE_ROOT/bin:$PATH"
./run_spike.sh sample 20            # evenly-spaced sample of ~20 programs
./run_spike.sh list 057 112 301     # specific program IDs
./run_spike.sh all                  # all 500
python3 parse_spike_traces_to_csv.py spike_traces lowbit_traces_spike.csv
```

## Requirements

- A RISC-V cross compiler: `riscv64-unknown-elf-gcc` (preferred, for
  direct pk-hosted binaries) or `riscv64-unknown-linux-gnu-gcc`/
  `riscv64-linux-gnu-gcc`.
- `spike` and `pk`, built from
  [`riscv-software-src/riscv-isa-sim`](https://github.com/riscv-software-src/riscv-isa-sim)
  and [`riscv-software-src/riscv-pk`](https://github.com/riscv-software-src/riscv-pk)
  -- see the setup block at the top of `run_spike.sh` for the exact
  build commands. Both `bin/` and `riscv64-unknown-elf/bin/` under
  your spike install typically need to be on `PATH` -- `spike` lives
  in the former, `pk` in the latter.

## `riscv64-unknown-elf-gcc` note

`riscv64-unknown-elf-gcc` links against newlib, not glibc -- newlib
has neither `posix_memalign` nor `clock_gettime` under any
feature-test macro. `../programs/lowbit_runtime.h` has a
`__NEWLIB__`-gated fallback for both (manual aligned `malloc`, and a
RISC-V `rdcycle` CSR read in place of `clock_gettime`) that only
activates for this specific compiler -- every other backend's build is
unaffected. If you're building `spike` binaries with a different
newlib-based cross compiler and hit `implicit declaration of function
'posix_memalign'`/`'clock_gettime'`, that's the same underlying gap;
extend the `__NEWLIB__` guard in `lowbit_runtime.h` rather than adding
compiler flags -- newlib doesn't implement these regardless of flags.

## In this environment

`spike_traces/`, `bin_spike/`, and `lowbit_traces_spike.csv` contain a
completed run across all 500 programs. `REPORT.md` analyzes it,
including a cross-check against `../qemu/` for functional correctness.
