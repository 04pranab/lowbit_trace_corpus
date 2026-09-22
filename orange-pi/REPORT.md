# orange-pi/ backend -- Report

**Status: not yet run.** This backend requires real RISC-V hardware
(e.g. an Orange Pi RV2) running Linux, which the environment used to
reorganize this corpus does not have -- it's a generic x86-64
container. `run_orange_pi.sh` is documented and syntax-checked but not
execution-verified on real hardware.

## How to regenerate this report for real

```bash
cd orange-pi
./run_orange_pi.sh
python3 parse_orange_pi_traces_to_csv.py traces lowbit_traces_orange_pi.csv
```

Check `ENVIRONMENT.md`'s `uname:` line first to confirm the run
actually happened on `riscv64` hardware, not accidentally on a dev
machine.

## Why this backend matters most, and what to fill in

This is the only backend that can validate or contradict every other
one:

- **vs. `../gem5/REPORT.md`**: do real `observed_branch_miss_rate` /
  `observed_cache_miss_rate` values, per branch/cache pattern family,
  land anywhere near gem5's `MinorCPU` + default-predictor simulation?
  Large, systematic divergence (not just noise) would say something
  real about how close that gem5 configuration is to actual RISC-V
  silicon -- worth writing up explicitly here rather than assumed.
- **vs. `../qemu/REPORT.md`**: `qemu-riscv64` structurally cannot
  produce real branch/cache numbers (see `../LIMITATIONS.md`), so this
  is the backend that would have given you those numbers all along, on
  real hardware, and correctness (`taken`/`nottaken`/`validation`)
  should match `../qemu/` exactly since both execute the same
  deterministic benchmark logic.
- **vs. `../x86-64/REPORT.md`**: same source, same compiler family,
  genuinely different ISA and microarchitecture -- a legitimate
  cross-architecture comparison once both sides are real data, not a
  same-thing-measured-twice comparison. Keep the `backend` column
  (`orange_pi_native` vs `native_host`) explicit in any combined table
  so this distinction isn't lost, per `../REPORT_AND_COMPARISONS.md`.
- Board-specific detail worth capturing in `ENVIRONMENT.md`/here: exact
  SoC (e.g. which RISC-V core IP, how many pipeline stages, L1/L2
  sizes) -- gem5's simulated cache/predictor parameters in
  `../gem5/GEM5.md` should be updated to match your specific board if
  you want the gem5 comparison above to be meaningful rather than
  incidental.
