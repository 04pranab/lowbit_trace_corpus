#!/usr/bin/env python3
"""
build_master_dataset.py -- consolidate all backend CSVs into one
ML-ready long-format dataset and one human-readable wide-format
comparison, with explicit provenance/semantics columns so nothing gets
silently averaged across backends that measure different things.

Lives at the repo root, alongside qemu/, gem5/, x86-64/, spike/,
orange-pi/. Run from anywhere -- paths resolve relative to this
script's own location.

Usage:
    python3 build_master_dataset.py
    python3 build_master_dataset.py --out-dir /some/other/dir

Reads (skips gracefully, with a warning, if a file doesn't exist yet):
    qemu/lowbit_traces_qemu.csv
    x86-64/lowbit_traces_native.csv
    spike/lowbit_traces_spike.csv
    gem5/lowbit_traces_gem5_minorcpu.csv
    gem5/lowbit_traces_gem5_atomcpu.csv
    orange-pi/lowbit_traces_orange_pi.csv

Writes:
    lowbit_master_long.csv   -- one row per (program, backend) pair.
                                 This is the ML-training-ready file:
                                 concatenate-friendly, `backend` as a
                                 categorical feature, missing fields
                                 are genuinely empty (not 0) when a
                                 backend structurally can't measure
                                 something.
    lowbit_master_wide.csv   -- one row per program, backend-prefixed
                                 columns for the handful of
                                 decision-relevant metrics. Meant for
                                 humans eyeballing cross-backend
                                 agreement, not for training directly
                                 (see MASTER_SCHEMA.md).
    MASTER_SCHEMA.md          -- column-by-column reference and the
                                 semantic caveats that matter before
                                 using either file.

THE ONE RULE THIS SCRIPT EXISTS TO ENFORCE: never let two backends'
numbers for a superficially-same-named column get compared, merged, or
averaged without knowing whether they mean the same thing. See
`time_metric` and `instruction_count_source` below -- those columns
exist specifically so a downstream consumer can filter/condition
before comparing, rather than the script silently deciding for them.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------
# Canonical long-format schema. Every backend loader below produces
# rows with exactly these keys; a backend that structurally cannot
# report a field leaves it as None (which csv.DictWriter renders as
# an empty cell) -- never 0, since 0 is a real, different value for
# nearly every numeric column here.
# ---------------------------------------------------------------
LONG_FIELDS = [
    # identity
    "program", "branch_pattern", "cache_pattern",
    "backend", "measurement_class", "arch", "compiler",
    # scale (see MASTER_SCHEMA.md -- qemu/x86-64 use each program's
    # real per-tier SIZE/N; gem5/spike use a capped SIZE=4096,N=20000)
    "SIZE", "N", "size_tier",
    # gem5-only cache config (None for every other backend)
    "cpu_type", "l1d_size", "l1i_size", "l2_size",
    # correctness
    "taken", "nottaken", "sink", "validation", "data_quality_ok",
    # timing -- ALWAYS check time_metric before comparing across rows
    "time_value", "time_metric",
    # instruction/branch/cache counters -- ALWAYS check
    # instruction_count_source / branch_data_available /
    # cache_data_available before comparing across rows
    "instructions_total", "instruction_count_source",
    "cycles_total",
    "observed_ipc", "observed_cpi",
    "branches_total", "branch_misses_total",
    "observed_branch_miss_rate", "branch_data_available",
    "cache_references_total", "cache_misses_total",
    "observed_cache_miss_rate", "cache_data_available",
    # provenance
    "source_csv", "source_report",
]

BRANCH_RE = re.compile(r'\d+_(b\d+_[a-z0-9_]+?)__c\d+_')
CACHE_RE = re.compile(r'__(c\d+_[a-z0-9_]+)$')


def canonical_program(name):
    """Strip any file extension / trailing .perf.txt / .spike.txt and
    leading path so every backend's program identifier matches."""
    base = Path(name).name
    for suffix in (".perf.txt", ".spike.txt", ".c", ".txt"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base


def branch_pattern_from_name(program):
    m = BRANCH_RE.match(program)
    return m.group(1) if m else None


def cache_pattern_from_name(program):
    m = CACHE_RE.search(program)
    return m.group(1) if m else None


def to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def to_int(v):
    f = to_float(v)
    return int(f) if f is not None else None


def blank_row():
    return {k: None for k in LONG_FIELDS}


def load_csv(path):
    if not path.exists():
        print(f"  SKIP (not found): {path.relative_to(SCRIPT_DIR)}")
        return None
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"  loaded {len(rows)} rows: {path.relative_to(SCRIPT_DIR)}")
    return rows


# ---------------------------------------------------------------
# Per-backend loaders. Each takes the raw CSV rows (from
# csv.DictReader) and yields fully-populated LONG_FIELDS dicts.
# ---------------------------------------------------------------

def load_qemu_or_native_or_spike(rows, backend_label, measurement_class,
                                  time_metric, source_csv, source_report,
                                  instr_source_if_present):
    """qemu, x86-64, and spike all share the exact same parser
    template/column set -- one loader covers all three, parameterized
    by what differs semantically between them."""
    for r in rows:
        row = blank_row()
        program = canonical_program(r.get("program") or r.get("filename", ""))
        row["program"] = program
        row["branch_pattern"] = r.get("branch_pattern") or branch_pattern_from_name(program)
        row["cache_pattern"] = r.get("cache_pattern") or cache_pattern_from_name(program)
        row["backend"] = backend_label
        row["measurement_class"] = measurement_class
        row["arch"] = r.get("arch") or None
        row["compiler"] = r.get("compiler") or None
        row["SIZE"] = to_int(r.get("SIZE"))
        row["N"] = to_int(r.get("N"))
        row["size_tier"] = r.get("size_tier") or None

        row["taken"] = to_int(r.get("taken"))
        row["nottaken"] = to_int(r.get("nottaken"))
        row["sink"] = to_int(r.get("sink"))
        validation = r.get("validation") or None
        row["validation"] = validation
        row["data_quality_ok"] = (validation == "PASS") if validation else None

        row["time_value"] = to_float(r.get("roi_elapsed_seconds_mean"))
        row["time_metric"] = time_metric

        perf_available = (r.get("perf_available") or "0") == "1"
        instr_source = r.get("instruction_count_source") or None

        if perf_available:
            # x86-64: real hardware perf counters, fully meaningful
            row["instructions_total"] = to_float(r.get("instructions_total"))
            row["instruction_count_source"] = "perf_hardware_counter"
            row["cycles_total"] = to_float(r.get("cycles_total"))
            row["observed_ipc"] = to_float(r.get("observed_ipc"))
            row["observed_cpi"] = to_float(r.get("observed_cpi"))
            row["branches_total"] = to_float(r.get("branches_total"))
            row["branch_misses_total"] = to_float(r.get("branch_misses_total"))
            row["observed_branch_miss_rate"] = to_float(r.get("observed_branch_miss_rate"))
            row["branch_data_available"] = True
            row["cache_references_total"] = to_float(r.get("cache_references_total"))
            row["cache_misses_total"] = to_float(r.get("cache_misses_total"))
            row["observed_cache_miss_rate"] = to_float(r.get("observed_cache_miss_rate"))
            row["cache_data_available"] = True
        elif instr_source and instr_source not in ("", "qemu_guest_none", "none"):
            # qemu with QEMU_INSN_PLUGIN set: only instructions_total
            # is real; everything else stays unavailable on purpose.
            row["instructions_total"] = to_float(r.get("instructions_total"))
            row["instruction_count_source"] = instr_source
            row["branch_data_available"] = False
            row["cache_data_available"] = False
        else:
            # qemu without a plugin, or spike: none of these are
            # measured at all -- leave everything None, not 0.
            row["instruction_count_source"] = "none"
            row["branch_data_available"] = False
            row["cache_data_available"] = False

        row["source_csv"] = source_csv
        row["source_report"] = source_report
        yield row


def load_gem5(rows, backend_label, source_csv, source_report):
    for r in rows:
        row = blank_row()
        program = canonical_program(r.get("program", ""))
        row["program"] = program
        row["branch_pattern"] = branch_pattern_from_name(program)
        row["cache_pattern"] = cache_pattern_from_name(program)
        row["backend"] = backend_label
        row["measurement_class"] = "timing_simulation"
        row["arch"] = "riscv64"
        row["compiler"] = None  # not recorded per-row by the gem5 parser
        row["SIZE"] = None  # gem5 uses size_override; see l1d/l1i/l2 below for the real config
        row["N"] = to_int(r.get("n_override"))
        row["size_tier"] = None  # stale for gem5 rows by design -- see gem5/REPORT.md
        row["cpu_type"] = r.get("cpu_type") or None
        row["l1d_size"] = r.get("l1d_size") or None
        row["l1i_size"] = r.get("l1i_size") or None
        row["l2_size"] = r.get("l2_size") or None

        # gem5's own parser doesn't capture the benchmark's own
        # taken/nottaken/validation stdout -- only gem5's stats.txt.
        # Correctness for gem5 rows is inferred from stats_available +
        # absence of unmatched_metrics, not from the benchmark's own
        # self-check (that check still runs inside the binary, gem5's
        # parser just doesn't surface it into this CSV).
        stats_ok = (r.get("stats_available") or "0") == "1"
        unmatched = (r.get("unmatched_metrics") or "").strip()
        row["data_quality_ok"] = stats_ok if not unmatched else None
        # None (not False) when unmatched_metrics is non-empty: it
        # means SOME columns for this row are unavailable (see
        # branch_data_available below), not that the whole row failed.

        row["time_value"] = to_float(r.get("sim_seconds"))
        row["time_metric"] = "gem5_sim_seconds"

        row["instructions_total"] = to_float(r.get("instructions_total"))
        row["instruction_count_source"] = "gem5_functional_sim"
        row["cycles_total"] = to_float(r.get("cycles_total"))
        row["observed_ipc"] = to_float(r.get("observed_ipc"))
        row["observed_cpi"] = to_float(r.get("observed_cpi"))

        branch_missing = "branches_total" in unmatched or "branch_misses_total" in unmatched
        if branch_missing:
            # AtomicSimpleCPU: no branch predictor unit at all. The
            # raw CSV has literal 0s here (see gem5/REPORT.md) -- that
            # is the exact trap this script exists to avoid
            # propagating. Force these to None.
            row["branches_total"] = None
            row["branch_misses_total"] = None
            row["observed_branch_miss_rate"] = None
            row["branch_data_available"] = False
        else:
            row["branches_total"] = to_float(r.get("branches_total"))
            row["branch_misses_total"] = to_float(r.get("branch_misses_total"))
            row["observed_branch_miss_rate"] = to_float(r.get("observed_branch_miss_rate"))
            row["branch_data_available"] = True

        dcache_acc = to_float(r.get("dcache_accesses"))
        dcache_miss = to_float(r.get("dcache_misses"))
        row["cache_references_total"] = dcache_acc
        row["cache_misses_total"] = dcache_miss
        row["observed_cache_miss_rate"] = to_float(r.get("observed_cache_miss_rate"))
        row["cache_data_available"] = dcache_acc is not None

        row["source_csv"] = source_csv
        row["source_report"] = source_report
        yield row


# ---------------------------------------------------------------
# Backend registry: (relative CSV path, loader-specific kwargs)
# ---------------------------------------------------------------

def build_long_rows(repo_root):
    all_rows = []

    print("Loading backend CSVs:")

    qemu_rows = load_csv(repo_root / "qemu" / "lowbit_traces_qemu.csv")
    if qemu_rows:
        all_rows.extend(load_qemu_or_native_or_spike(
            qemu_rows, "qemu_user_mode", "functional_emulation",
            "host_wallclock_qemu_emulated",
            "qemu/lowbit_traces_qemu.csv", "qemu/REPORT.md", None))

    native_rows = load_csv(repo_root / "x86-64" / "lowbit_traces_native.csv")
    if native_rows:
        all_rows.extend(load_qemu_or_native_or_spike(
            native_rows, "native_host", "real_hardware",
            "real_wallclock_hardware",
            "x86-64/lowbit_traces_native.csv", "x86-64/REPORT.md", None))

    spike_rows = load_csv(repo_root / "spike" / "lowbit_traces_spike.csv")
    if spike_rows:
        all_rows.extend(load_qemu_or_native_or_spike(
            spike_rows, "spike", "golden_model_functional",
            "roi_rdcycle_proxy_1ghz_assumed",
            "spike/lowbit_traces_spike.csv", "spike/REPORT.md", None))

    mc_rows = load_csv(repo_root / "gem5" / "lowbit_traces_gem5_minorcpu.csv")
    if mc_rows:
        all_rows.extend(load_gem5(
            mc_rows, "gem5_minorcpu",
            "gem5/lowbit_traces_gem5_minorcpu.csv", "gem5/REPORT.md"))

    ac_rows = load_csv(repo_root / "gem5" / "lowbit_traces_gem5_atomcpu.csv")
    if ac_rows:
        all_rows.extend(load_gem5(
            ac_rows, "gem5_atomcpu",
            "gem5/lowbit_traces_gem5_atomcpu.csv", "gem5/REPORT.md"))

    opi_rows = load_csv(repo_root / "orange-pi" / "lowbit_traces_orange_pi.csv")
    if opi_rows:
        all_rows.extend(load_qemu_or_native_or_spike(
            opi_rows, "orange_pi_native", "real_hardware",
            "real_wallclock_hardware",
            "orange-pi/lowbit_traces_orange_pi.csv", "orange-pi/REPORT.md", None))

    return all_rows


def write_long_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LONG_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"\nWrote long-format master: {out_path} ({len(rows)} rows)")


WIDE_METRICS = [
    ("time_value", "time"),
    ("observed_ipc", "ipc"),
    ("observed_branch_miss_rate", "bmr"),
    ("observed_cache_miss_rate", "cmr"),
    ("validation", "validation"),
]


def write_wide_csv(rows, out_path):
    by_program = {}
    backends_seen = []
    for r in rows:
        p = r["program"]
        by_program.setdefault(p, {})[r["backend"]] = r
        if r["backend"] not in backends_seen:
            backends_seen.append(r["backend"])

    fieldnames = ["program", "branch_pattern", "cache_pattern"]
    for backend in backends_seen:
        for src_field, short in WIDE_METRICS:
            fieldnames.append(f"{backend}__{short}")

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for program in sorted(by_program):
            per_backend = by_program[program]
            any_row = next(iter(per_backend.values()))
            out = {
                "program": program,
                "branch_pattern": any_row["branch_pattern"],
                "cache_pattern": any_row["cache_pattern"],
            }
            for backend in backends_seen:
                r = per_backend.get(backend)
                for src_field, short in WIDE_METRICS:
                    out[f"{backend}__{short}"] = r.get(src_field) if r else None
            w.writerow(out)

    print(f"Wrote wide-format comparison: {out_path} ({len(by_program)} programs x {len(backends_seen)} backends)")
    return backends_seen


def write_schema_doc(out_path, backends_seen):
    doc = f"""# Master dataset schema

Generated by `build_master_dataset.py`. Two files:

- **`lowbit_master_long.csv`** -- one row per (program, backend) pair.
  This is the file to use for ML training: `backend` is a plain
  categorical column, every row has the same schema, and any field a
  given backend structurally can't measure is genuinely empty (not
  `0`) so it won't silently get treated as a real zero value.
- **`lowbit_master_wide.csv`** -- one row per program, with
  `<backend>__<metric>` columns for a handful of headline metrics
  (`time`, `ipc`, `bmr` = branch miss rate, `cmr` = cache miss rate,
  `validation`). Meant for humans scanning cross-backend agreement at
  a glance (this is what backs the comparison tables in
  `REPORT_AND_COMPARISONS.md`) -- not recommended as direct ML input,
  since it mixes incompatible metric semantics side by side (see next
  section).

Backends present in this run: {", ".join(backends_seen)}

## The one rule this file exists to enforce

**Never compare `time_value`, `instructions_total`, `branches_total`,
or their derived rates across rows without first checking the
provenance columns next to them.** Same-named columns mean genuinely
different things across backends:

| `time_metric` value | Backend(s) | What it actually is |
|---|---|---|
| `real_wallclock_hardware` | `native_host`, `orange_pi_native` | Real wall-clock time on real silicon. |
| `host_wallclock_qemu_emulated` | `qemu_user_mode` | Host wall-clock while QEMU translates/dispatches RISC-V -- not RISC-V hardware time. |
| `roi_rdcycle_proxy_1ghz_assumed` | `spike` | Guest `rdcycle` count divided by an assumed 1 GHz -- an instruction-volume proxy, not a real clock. |
| `gem5_sim_seconds` | `gem5_minorcpu`, `gem5_atomcpu` | gem5's own simulated time for its configured CPU model -- comparable across gem5's own CPU types, not to real wall-clock. |

| `instruction_count_source` value | Meaning |
|---|---|
| `perf_hardware_counter` | Real, from hardware (currently only `native_host`, and `orange_pi_native` once run). |
| `gem5_functional_sim` | Real, from gem5's own functional simulation. |
| any QEMU plugin name | Real guest instruction count, opt-in only -- see `qemu/README.md`. Nothing else in that row is populated from the plugin. |
| `none` | Not measured at all for this row -- `instructions_total` is empty. |

`branch_data_available` and `cache_data_available` are explicit
booleans for the same reason -- check them before trusting
`observed_branch_miss_rate`/`observed_cache_miss_rate` in a row.
`gem5_atomcpu` rows always have `branch_data_available=False`: that CPU
model has no branch predictor unit at all (see `gem5/REPORT.md`).

## Column reference (long format)

| Column | Type | Notes |
|---|---|---|
| `program` | str | Canonical base name, no extension. Join key across backends. |
| `branch_pattern`, `cache_pattern` | str | Parsed from `program` if not present in the source CSV. |
| `backend` | str | `qemu_user_mode`, `native_host`, `spike`, `gem5_minorcpu`, `gem5_atomcpu`, `orange_pi_native`. |
| `measurement_class` | str | `functional_emulation`, `real_hardware`, `golden_model_functional`, `timing_simulation`. |
| `arch`, `compiler` | str | As reported by the source CSV; `None` where not recorded per-row. |
| `SIZE`, `N` | int | Per-program config. **Scale differs by backend** -- `qemu`/`native_host` use each program's real per-tier value (up to `N=8,000,000`); `gem5`/`spike` use a capped `SIZE=4096, N=20000`. Never compare absolute counts across that boundary without normalizing by `N`. |
| `size_tier` | str | `small_L1`/`medium_L2`/`large_L3`/`xlarge_DRAM` for `qemu`/`native_host`/`spike`; always empty for `gem5` (stale concept there, see `gem5/REPORT.md`). |
| `cpu_type`, `l1d_size`, `l1i_size`, `l2_size` | str | gem5-only cache/CPU config; empty for every other backend. |
| `taken`, `nottaken`, `sink` | int | Benchmark's own self-report. Empty for gem5 (its parser doesn't capture the binary's stdout, only `stats.txt`). |
| `validation` | str | `PASS`/`FAIL`/empty. |
| `data_quality_ok` | bool/None | `True`/`False` where determinable; `None` when the backend can't say (e.g. a gem5 row with `unmatched_metrics` -- some but not all of that row's columns are unavailable, not necessarily wrong). |
| `time_value`, `time_metric` | float, str | See table above -- always read together. |
| `instructions_total`, `instruction_count_source` | float, str | See table above -- always read together. |
| `cycles_total`, `observed_ipc`, `observed_cpi` | float | Real for `native_host`/`gem5_*`; empty otherwise. |
| `branches_total`, `branch_misses_total`, `observed_branch_miss_rate`, `branch_data_available` | float/float/float/bool | Real only where `branch_data_available=True`. |
| `cache_references_total`, `cache_misses_total`, `observed_cache_miss_rate`, `cache_data_available` | float/float/float/bool | Real only where `cache_data_available=True`. For `gem5_*`, `cache_references_total`/`cache_misses_total` are specifically **L1 data-cache** accesses/misses (`dcache_accesses`/`dcache_misses` in the source CSV); for `native_host`, they're `perf`'s generic `cache-references`/`cache-misses`, which map to **last-level cache (LLC)** on Intel -- not the same cache level, see `x86-64/REPORT.md`. |
| `source_csv`, `source_report` | str | Where this row came from, and which `REPORT.md` has the full write-up. |

## Regenerating

```bash
python3 build_master_dataset.py
```

Re-run any time a backend's CSV changes. Missing backend CSVs are
skipped with a warning, not an error -- e.g. `orange-pi/` and
`gem5`'s `DerivO3CPU` currently have no output (see
`REPORT_AND_COMPARISONS.md`), so today's master files don't include
them; re-run once they exist.
"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"Wrote schema doc: {out_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=str(SCRIPT_DIR),
                     help="Where to write the two CSVs + schema doc (default: this script's own directory)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = build_long_rows(SCRIPT_DIR)
    if not rows:
        print("\nNo backend CSVs found at all -- nothing to write. "
              "Run at least one backend's script + parser first.", file=sys.stderr)
        sys.exit(1)

    write_long_csv(rows, out_dir / "lowbit_master_long.csv")
    backends_seen = write_wide_csv(rows, out_dir / "lowbit_master_wide.csv")
    write_schema_doc(out_dir / "MASTER_SCHEMA.md", backends_seen)

    print("\nDone.")


if __name__ == "__main__":
    main()
