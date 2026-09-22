# qemu/ -- QEMU user-mode backend

Cross-compiles the corpus's 500 programs (in `../programs/`) for
RISC-V and runs them under `qemu-riscv64` (user-mode, functional
emulation only). Falls back to a native x86-64 build automatically if
no RISC-V cross compiler / `qemu-riscv64` is found on this machine.

**Read `../LIMITATIONS.md` before trusting numbers from this
backend.** `qemu-riscv64` has no pipeline, branch predictor, or cache
model at all -- it translates and runs RISC-V instructions as fast as
possible for correctness, nothing else. For genuine simulated
branch-predictor/cache-hierarchy statistics, use `../gem5/` instead;
for real RISC-V hardware, `../orange-pi/`.

This backend deliberately does **not** wrap `perf stat` around
`qemu-riscv64` -- that would measure the **host** process translating
and dispatching RISC-V instructions, not guest RISC-V behavior, which
is misleading rather than merely imprecise. (An earlier version of
this script did wrap perf here; see the changelog note below for why
that was removed rather than kept as an option.) What this backend
does give you: guaranteed-correct functional execution (the
benchmark's own `taken`/`nottaken`/`sink`/`validation` self-report),
wall-clock timing, and -- optionally -- an *exact* guest instruction
count via a TCG plugin.

## Files

| File | Purpose |
|---|---|
| `build_and_run_riscv.sh` | Build, verify, and run all 500 programs from `../programs/`. |
| `parse_traces_to_csv.py` | Turn `traces/*.perf.txt` into an ML-ready CSV. |
| `bin/` | Compiled binaries (created by `build`). Not checked in; regenerate locally. |
| `traces/` | Raw per-program output (created by `run`). |
| `ENVIRONMENT.md` | Toolchain/host/QEMU snapshot for the most recent `run`. |
| `lowbit_traces_qemu.csv` | Parsed output of `traces/`. |
| `REPORT.md` | Results and analysis of the run already captured in this folder. |

## Usage

All paths inside the scripts are resolved relative to this folder, so
you can run them from anywhere:

```bash
cd qemu
./build_and_run_riscv.sh                  # build + verify + run everything, 1 rep
./build_and_run_riscv.sh build             # build only
./build_and_run_riscv.sh verify            # objdump-check a sample of builds
./build_and_run_riscv.sh run 5             # run only, 5 repetitions per program
python3 parse_traces_to_csv.py traces lowbit_traces_qemu.csv
```

Optional: exact guest instruction counts, via a TCG plugin (needs
`qemu-riscv64` built with `--enable-plugins` and a plugin `.so`, e.g.
`contrib/plugins/libinsn.so` from the QEMU source tree):

```bash
QEMU_INSN_PLUGIN=/path/to/libinsn.so ./build_and_run_riscv.sh run
```

Without `QEMU_INSN_PLUGIN` set, `instructions_total` and
`instruction_count_source` are simply absent/`None` in the CSV --
that's expected, not an error; every other field (timing,
correctness) is unaffected either way.

## Requirements

- `riscv64-unknown-linux-gnu-gcc` or `riscv64-linux-gnu-gcc` (cross
  compiler) and `qemu-riscv64` on `PATH` for the real RISC-V-under-
  QEMU path. Without both, the script silently falls back to a native
  x86-64 build labeled `backend=native` -- check `ENVIRONMENT.md` to
  see which path actually ran.
- `objdump` for the `verify` step (optional).
- A TCG plugin `.so` (optional, see above) for exact guest instruction
  counts.

## Changelog note: the removed `-singlestep` guest-counting path

A version of `build_and_run_riscv.sh` briefly existed that tried to
get exact guest instruction counts via `qemu-riscv64 -singlestep -d
exec` when no TCG plugin was available. It had two compounding bugs:
its per-program size cap never triggered (it checked for a `-DN=`
compile flag a normal build never sets, so the cap was always
bypassed), and `-d exec` silently produces zero log lines on a stock,
non-`--enable-debug-tcg` `qemu-user` build -- regardless of what
`qemu-riscv64 -d help` claims is available. Together, every one of the
500 programs either took an impractically long singlestepped path or
silently came back with `qemu_guest_instructions_retired=0`, and the
benchmark's own output wasn't reliably captured either. That code path
has been removed entirely, not just disabled -- see `REPORT.md`'s
"read this section before anything else" note for the full story if
you're comparing against an old CSV from that version.