# orange-pi/ -- real RISC-V hardware backend

Builds every `*.c` program in `../programs/` natively (with the
board's own `gcc`) and runs the resulting binaries directly on a
RISC-V single-board computer (e.g. an Orange Pi RV2), wrapped in
`perf stat` if available. This is the **only** backend in the corpus
that measures real branch-predictor and cache-hierarchy behavior on
real RISC-V silicon -- not an emulator (`../qemu/`), not a timing
simulation (`../gem5/`), not a golden-model ISA simulator
(`../spike/`), and not a different ISA (`../x86-64/`).

## Files

| File | Purpose |
|---|---|
| `run_orange_pi.sh` | Build + verify + run all 500 programs, on-device. |
| `parse_orange_pi_traces_to_csv.py` | Turn `traces/*.perf.txt` into a CSV. |
| `bin/` | Compiled binaries (build on-device, or copy in from a cross-compile -- see below). |
| `traces/` | Raw per-program `perf stat` + self-reported output. |
| `ENVIRONMENT.md` | Toolchain/board/perf snapshot for a given run. |
| `lowbit_traces_orange_pi.csv` | Parsed output of `traces/`. |
| `REPORT.md` | Status of this backend and what to fill in once you've run it. |

## Usage

Run this **on the board itself** so `gcc`/`perf` measure real RISC-V
hardware directly, not a cross-compiled guess:

```bash
# on the Orange Pi:
sudo apt update
sudo apt install -y gcc linux-tools-common linux-tools-generic \
    linux-tools-$(uname -r) binutils
sudo sysctl kernel.perf_event_paranoid=-1   # if perf refuses to launch

cd orange-pi
./run_orange_pi.sh                  # build + verify + run everything, 1 rep
python3 parse_orange_pi_traces_to_csv.py traces lowbit_traces_orange_pi.csv
```

Alternative workflow (build elsewhere, run on-device): cross-compile
with `../qemu/`'s `riscv64-*-gcc -static` invocation (same `CFLAGS`,
swap `-DLOWBIT_BACKEND=\"qemu_user_mode\"` for
`-DLOWBIT_BACKEND=\"orange_pi_native\"`), copy the resulting binaries
into `orange-pi/bin/` on the board, then run just
`./run_orange_pi.sh run` (it uses whatever's already in `bin/` instead
of rebuilding if `../programs/` isn't present on-device).

`run_orange_pi.sh` checks `uname -m` and warns loudly if you run it
anywhere other than an actual `riscv64` host -- see the script's
header comment for why this backend deliberately does not
cross-compile or SSH out on your behalf.

## In this environment

**Not run.** The environment used to reorganize this corpus is a
generic x86-64 container, not an Orange Pi -- `run_orange_pi.sh` is
documented and syntax-checked (`bash -n run_orange_pi.sh` passes) but
not execution-verified on real hardware. See `REPORT.md`.
