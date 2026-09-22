#!/usr/bin/env python3
"""
Parse LowBit Trace Corpus *.perf.txt files into an ML-ready CSV.

Each row is one benchmark run (or, if multiple `.repN.perf.txt` files
exist for the same base program, one row per program with per-metric
mean/median/stddev aggregated across reps).

Two kinds of fields are recorded, and deliberately kept apart:
  - `expected_*` / `predictor_stress_type`: the benchmark's own
    self-reported HYPOTHESIS about its behavior (see LIMITATIONS.md --
    these are heuristic labels, not measurements).
  - `observed_*`: metrics actually computed from measured hardware
    counters (branch_miss_rate, cache_miss_rate, ipc, cpi, etc.).
Do not conflate the two: a model should be trained against observed_*
values, using expected_*/predictor_stress_type as input features
describing how the trace was generated, not as ground-truth targets.
"""

import re
import csv
import sys
import statistics
from pathlib import Path
from collections import defaultdict

SCRIPT_DIR = Path(__file__).resolve().parent
TRACES_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else SCRIPT_DIR / "traces"
OUTPUT_CSV = Path(sys.argv[2]) if len(sys.argv) > 2 else SCRIPT_DIR / "lowbit_traces_qemu.csv"

# perf's printed event name doesn't always match the alias passed on
# the command line (e.g. requesting "branch-instructions" is what
# modern perf versions print back; older perf/aliases may print
# "branches"). Both are matched. Each pattern captures the FULL
# comma-grouped number ([\d,]+) -- capturing only \d+ before a comma
# silently truncates "3,308,280" down to "3" or "308".
COUNTER_PATTERNS = [
    (r'([\d,]+)\s+cpu_atom/instructions/', 'instructions_atom'),
    (r'([\d,]+)\s+cpu_core/instructions/', 'instructions_core'),
    (r'([\d,]+)\s+cpu_atom/cycles/', 'cycles_atom'),
    (r'([\d,]+)\s+cpu_core/cycles/', 'cycles_core'),
    (r'([\d,]+)\s+cpu_atom/(?:branches|branch-instructions)/', 'branches_atom'),
    (r'([\d,]+)\s+cpu_core/(?:branches|branch-instructions)/', 'branches_core'),
    (r'([\d,]+)\s+cpu_atom/branch-misses/', 'branch_misses_atom'),
    (r'([\d,]+)\s+cpu_core/branch-misses/', 'branch_misses_core'),
    (r'([\d,]+)\s+cpu_atom/cache-references/', 'cache_references_atom'),
    (r'([\d,]+)\s+cpu_core/cache-references/', 'cache_references_core'),
    (r'([\d,]+)\s+cpu_atom/cache-misses/', 'cache_misses_atom'),
    (r'([\d,]+)\s+cpu_core/cache-misses/', 'cache_misses_core'),
    # Non-hybrid / single-PMU-domain perf output (real RISC-V hardware,
    # non-hybrid x86, etc.) -- no "cpu_atom/"/"cpu_core/" prefix at all.
    (r'([\d,]+)\s+instructions\b(?!.*cpu_)', 'instructions_core'),
    (r'([\d,]+)\s+cycles\b(?!.*cpu_)', 'cycles_core'),
    (r'([\d,]+)\s+(?:branches|branch-instructions)\b(?!.*cpu_)', 'branches_core'),
    (r'([\d,]+)\s+branch-misses\b(?!.*cpu_)', 'branch_misses_core'),
    (r'([\d,]+)\s+cache-references\b(?!.*cpu_)', 'cache_references_core'),
    (r'([\d,]+)\s+cache-misses\b(?!.*cpu_)', 'cache_misses_core'),
    (r'([\d,]+)\s+task-clock', 'task_clock_ms'),
    (r'([\d,]+)\s+context-switches', 'context_switches'),
    (r'([\d,]+)\s+cpu-migrations', 'cpu_migrations'),
    (r'([\d,]+)\s+page-faults', 'page_faults'),
    (r'([\d.]+)\s+seconds time elapsed', 'time_elapsed'),
]

# Exact GUEST-side instruction count from build_and_run_riscv.sh's
# run_qemu_guest() (TCG plugin only -- opt-in via QEMU_INSN_PLUGIN;
# there is no singlestep fallback, see build_and_run_riscv.sh). Only
# present for backend=qemu_user_mode rows -- the native fallback path
# never writes this and keeps using perf's own instructions/cycles/etc.
GUEST_INSTRET_PATTERN = re.compile(r'qemu_guest_instructions_retired=(\d+)')
GUEST_COUNT_MODE_PATTERN = re.compile(r'guest_count_mode=(\w+)')

INT_KEYS = [
    'instructions_atom', 'instructions_core', 'cycles_atom', 'cycles_core',
    'branches_atom', 'branches_core', 'branch_misses_atom', 'branch_misses_core',
    'cache_references_atom', 'cache_references_core',
    'cache_misses_atom', 'cache_misses_core',
    'task_clock_ms', 'context_switches', 'cpu_migrations', 'page_faults',
]


def parse_perf_file(filepath):
    data = {k: 0 for k in INT_KEYS}
    data.update({
        'filename': filepath.name,
        'program': '', 'branch_pattern': '', 'cache_pattern': '',
        'predictor_stress_type': '', 'expected_dominant_miss_type': '',
        'size_tier': '', 'backend': '', 'compiler': '', 'arch': '',
        'SIZE': 0, 'N': 0, 'SEED': 0, 'warmup_iterations': 0,
        'taken': 0, 'nottaken': 0, 'sink': 0,
        'validation': '', 'roi_elapsed_seconds': 0.0,
        'time_elapsed': 0.0,
        'perf_available': 1,
    })

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    data['perf_available'] = 0 if (
        'No supported events found' in content
        or 'Access to performance monitoring' in content
        or 'perf unavailable on this host' in content
    ) else 1

    bd_match = re.search(r'backend_declared=([^\n]+)', content)
    if bd_match:
        data['backend'] = bd_match.group(1).strip()

    meta_patterns = {
        r'\bprogram=([^\n]+)': 'program',
        r'branch_pattern=([^\n]+)': 'branch_pattern',
        r'cache_pattern=([^\n]+)': 'cache_pattern',
        r'predictor_stress_type=([^\n]+)': 'predictor_stress_type',
        r'expected_dominant_miss_type=([^\n]+)': 'expected_dominant_miss_type',
        r'size_tier=([^\n]+)': 'size_tier',
        r'^backend=([^\n]+)': 'backend',
        r'compiler=([^\n]+)': 'compiler',
        r'arch=([^\n]+)': 'arch',
        r'SIZE=(\d+)': 'SIZE',
        r'N=(\d+)': 'N',
        r'SEED=(\d+)': 'SEED',
        r'warmup_iterations=(\d+)': 'warmup_iterations',
        r'taken=(\d+)': 'taken',
        r'nottaken=(\d+)': 'nottaken',
        r'sink=(\d+)': 'sink',
        r'validation=(PASS|FAIL)': 'validation',
        r'roi_elapsed_seconds=([\d.]+)': 'roi_elapsed_seconds',
    }
    for regex, key in meta_patterns.items():
        match = re.search(regex, content, re.MULTILINE)
        if match:
            val = match.group(1).strip()
            if key in ('SIZE', 'N', 'SEED', 'warmup_iterations', 'taken', 'nottaken', 'sink'):
                data[key] = int(val)
            elif key == 'roi_elapsed_seconds':
                data[key] = float(val)
            else:
                data[key] = val

    for regex, key in COUNTER_PATTERNS:
        match = re.search(regex, content)
        if match:
            raw = match.group(1).replace(',', '')
            try:
                val = float(raw)
                if key.endswith('_core') and data.get(key, 0):
                    continue  # don't let the non-hybrid fallback clobber a hybrid value
                data[key] = val
            except ValueError:
                pass

    is_qemu_guest_row = data['backend'] == 'qemu_user_mode'

    if is_qemu_guest_row:
        # QEMU user-mode has no cache/branch-predictor model no matter
        # how it's measured, and perf around the process measures the
        # HOST, not the guest -- so none of the perf-derived fields
        # above are meaningful here even if they happened to match a
        # regex (they shouldn't, since run_qemu_guest() no longer
        # calls perf at all, but this guards against stale trace files
        # from before this fix). Only the exact guest instruction
        # count (if this run's GUEST_COUNT_MODE captured one) is real.
        guest_match = GUEST_INSTRET_PATTERN.search(content)
        mode_match = GUEST_COUNT_MODE_PATTERN.search(content)
        data['instructions_total'] = int(guest_match.group(1)) if guest_match else None
        data['instruction_count_source'] = (
            f"qemu_guest_{mode_match.group(1)}" if mode_match else ''
        )
        data['cycles_total'] = None
        data['branches_total'] = None
        data['branch_misses_total'] = None
        data['cache_references_total'] = None
        data['cache_misses_total'] = None
        data['observed_ipc'] = None
        data['observed_cpi'] = None
        data['observed_branch_miss_rate'] = None
        data['observed_cache_miss_rate'] = None
        data['observed_branch_density'] = None
        data['observed_cache_refs_per_instruction'] = None
        data['branches_to_N_ratio'] = None
        data['perf_available'] = 0
        # "plausible" for qemu now means "did we get an exact guest
        # instruction count", not the old host-contaminated sanity
        # check.
        data['plausible'] = 1 if guest_match else 0
        return data

    # ---- native fallback path: real perf on real host silicon, unchanged ----
    data['instructions_total'] = data['instructions_atom'] + data['instructions_core']
    data['cycles_total'] = data['cycles_atom'] + data['cycles_core']
    data['branches_total'] = data['branches_atom'] + data['branches_core']
    data['branch_misses_total'] = data['branch_misses_atom'] + data['branch_misses_core']
    data['cache_references_total'] = data['cache_references_atom'] + data['cache_references_core']
    data['cache_misses_total'] = data['cache_misses_atom'] + data['cache_misses_core']
    data['instruction_count_source'] = 'perf_native' if data['instructions_total'] else ''

    instr = data['instructions_total']
    cyc = data['cycles_total']
    br = data['branches_total']
    brm = data['branch_misses_total']
    cr = data['cache_references_total']
    cm = data['cache_misses_total']

    data['observed_ipc'] = round(instr / cyc, 4) if cyc else 0.0
    data['observed_cpi'] = round(cyc / instr, 4) if instr else 0.0
    data['observed_branch_miss_rate'] = round(brm / br, 6) if br else 0.0
    data['observed_cache_miss_rate'] = round(cm / cr, 6) if cr else 0.0
    data['observed_branch_density'] = round(br / instr, 6) if instr else 0.0
    data['observed_cache_refs_per_instruction'] = round(cr / instr, 6) if instr else 0.0

    n = data['N'] or 1
    ratio = data['branches_total'] / n
    data['branches_to_N_ratio'] = round(ratio, 4)
    if not data['perf_available']:
        data['plausible'] = 0
    else:
        data['plausible'] = 1 if 0.1 <= ratio <= 20 else 0

    return data


FIELDNAMES = [
    'filename', 'program', 'branch_pattern', 'cache_pattern', 'size_tier',
    'predictor_stress_type', 'expected_dominant_miss_type',
    'backend', 'compiler', 'arch',
    'SIZE', 'N', 'SEED', 'warmup_iterations',
    'taken', 'nottaken', 'sink', 'validation',
    'reps',
    'roi_elapsed_seconds_mean', 'roi_elapsed_seconds_median', 'roi_elapsed_seconds_stddev',
    'perf_available',
    'instructions_total', 'cycles_total',
    'branches_total', 'branch_misses_total',
    'cache_references_total', 'cache_misses_total',
    'task_clock_ms', 'context_switches', 'cpu_migrations', 'page_faults',
    'time_elapsed',
    'observed_ipc', 'observed_cpi',
    'observed_branch_miss_rate', 'observed_cache_miss_rate',
    'observed_branch_density', 'observed_cache_refs_per_instruction',
    'branches_to_N_ratio', 'plausible', 'instruction_count_source',
]


def aggregate(rows):
    """Collapse N repetitions of the same base program into one row,
    with mean/median/stddev for the timing-sensitive fields and the
    first row's value for everything else (metadata is identical
    across reps by construction)."""
    base = dict(rows[0])
    base['reps'] = len(rows)

    times = [r['roi_elapsed_seconds'] for r in rows if r['roi_elapsed_seconds']]
    if times:
        base['roi_elapsed_seconds_mean'] = round(statistics.mean(times), 9)
        base['roi_elapsed_seconds_median'] = round(statistics.median(times), 9)
        base['roi_elapsed_seconds_stddev'] = round(statistics.stdev(times), 9) if len(times) > 1 else 0.0
    else:
        base['roi_elapsed_seconds_mean'] = 0.0
        base['roi_elapsed_seconds_median'] = 0.0
        base['roi_elapsed_seconds_stddev'] = 0.0

    is_qemu = rows[0]['backend'] == 'qemu_user_mode'
    numeric_avg_fields = [
        'instructions_total', 'cycles_total', 'branches_total', 'branch_misses_total',
        'cache_references_total', 'cache_misses_total',
        'task_clock_ms', 'context_switches', 'cpu_migrations', 'page_faults',
        'time_elapsed', 'observed_ipc', 'observed_cpi',
        'observed_branch_miss_rate', 'observed_cache_miss_rate',
        'observed_branch_density', 'observed_cache_refs_per_instruction',
        'branches_to_N_ratio',
    ]

    if is_qemu:
        # Only instructions_total can ever be non-None here; average
        # across whichever reps actually got a guest count, leave the
        # rest None (see parse_perf_file()).
        vals = [r['instructions_total'] for r in rows if r['instructions_total'] is not None]
        base['instructions_total'] = round(statistics.mean(vals), 6) if vals else None
        for f in numeric_avg_fields:
            if f != 'instructions_total':
                base[f] = None
        base['perf_available'] = 0
        base['plausible'] = 1 if vals else 0
        base['instruction_count_source'] = rows[0]['instruction_count_source']
        return base

    # --- native fallback path: unchanged behavior ---
    perf_rows = [r for r in rows if r['perf_available']]
    if perf_rows:
        for f in numeric_avg_fields:
            vals = [r[f] for r in perf_rows]
            base[f] = round(statistics.mean(vals), 6)
        base['perf_available'] = 1
        base['plausible'] = 1 if all(r['plausible'] for r in perf_rows) else 0
    else:
        base['perf_available'] = 0
        base['plausible'] = 0
    base['instruction_count_source'] = rows[0].get('instruction_count_source', '')

    return base


def main():
    if not TRACES_DIR.exists():
        print(f"Error: {TRACES_DIR} directory not found!")
        return

    trace_files = sorted(TRACES_DIR.glob("*.perf.txt"))
    print(f"Found {len(trace_files)} trace files.")

    # Group by base program name: "NNN_....perf.txt" or
    # "NNN_....repK.perf.txt" both collapse to the same base key.
    groups = defaultdict(list)
    for tf in trace_files:
        base_name = re.sub(r'\.rep\d+\.perf\.txt$', '', tf.name)
        base_name = re.sub(r'\.perf\.txt$', '', base_name)
        groups[base_name].append(parse_perf_file(tf))

    n_implausible = 0
    n_no_perf = 0
    n_validation_fail = 0
    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=FIELDNAMES, extrasaction='ignore')
        writer.writeheader()
        for base_name in sorted(groups):
            row = aggregate(groups[base_name])
            row['filename'] = base_name
            if not row['perf_available']:
                n_no_perf += 1
            elif not row['plausible']:
                n_implausible += 1
            if row.get('validation') == 'FAIL':
                n_validation_fail += 1
            writer.writerow(row)

    print(f"Done. CSV written to: {OUTPUT_CSV}")
    print(f"   Programs: {len(groups)} | Columns: {len(FIELDNAMES)}")
    print(f"   Rows where perf was UNAVAILABLE (perf_event_paranoid lockout): {n_no_perf}")
    print(f"   Rows where perf ran but counters look implausible vs. N: {n_implausible}")
    print(f"   Rows where the benchmark's own validation check FAILED: {n_validation_fail}")
    if n_no_perf:
        print("   NOTE: perf_available=0 rows mean perf refused to even launch the")
        print("   program -- fix with `sudo sysctl kernel.perf_event_paranoid=-1`")
        print("   and re-run build_and_run_riscv.sh run.")
    if n_implausible:
        print("   NOTE: implausible counter ratios (perf DID run) usually mean perf")
        print("   measured the QEMU HOST process, not guest RISC-V -- see LIMITATIONS.md.")


if __name__ == "__main__":
    main()