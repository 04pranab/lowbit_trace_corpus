#!/usr/bin/env python3
"""
Parse LowBit Trace Corpus gem5 stats.txt/meta.txt dumps into an
ML-ready CSV, same spirit as the other backends' parse_*_to_csv.py
scripts, but pulling in everything in stats.txt likely to be useful
for characterization -- not just the 11 counters needed for
IPC/CPI/miss-rate.

Each row is one benchmark run: one gem5_stats_<cpu>/<program>/
subfolder holding meta.txt (run config) + stats.txt (gem5's own
dumped counters). One-shot per program here, no rep-aggregation.

Two kinds of fields exist elsewhere in this corpus, and the split
still applies even though gem5's stats.txt only ever gives the second:
  - `expected_*` / `predictor_stress_type`: the benchmark's own
    self-reported HYPOTHESIS (see LIMITATIONS.md -- heuristic, not
    measured). gem5 syscall-emulation doesn't capture guest stdout,
    so these aren't in this CSV -- join MANIFEST.csv on `program`.
  - `observed_*` and everything below: measured from gem5's own
    counters. Use these as regression/ML targets or features; never
    predictor_stress_type.

Columns, in four groups:
  1. CORE (original schema, unchanged) -- run identity, IPC/CPI,
     branch/cache/icache miss rate. If any of these 11 required
     counters is missing, its name is listed in `unmatched_metrics`
     instead of being silently zero-filled as if it were measured.
  2. BRANCH PREDICTOR DETAIL -- BTB, return-address-stack, indirect
     predictor, and per-branch-type (conditional/return/indirect-call)
     commit and mispredict counts. MinorCPU only -- AtomicSimpleCPU has
     no branch predictor, so these come back blank, not zero (zero
     would wrongly claim "measured, no mispredictions").
  3. CACHE DETAIL -- read/write split, average miss latency, MSHR miss
     rate, replacements/writebacks, for L1i, L1d, and L2.
  4. EVERYTHING ELSE -- TLB (itb/dtb), DRAM controller (latency,
     bandwidth, page-hit rate), pipeline idle cycles, syscalls, and
     host-side simulation performance (wall-clock, inst/s).

Groups 2-4 are best-effort: a blank cell means gem5 didn't report that
stat for this CPU model/config, not that it was measured as zero.
They're never added to unmatched_metrics -- that column stays scoped
to the 11 CORE counters everything else in this corpus depends on, or
every AtomicSimpleCPU row would show ~20 "missing" branch-predictor
fields that were never expected to exist there in the first place.

Add --full to also write raw_stats_json: every single numeric stat
gem5 dumped for that run, as one compact JSON blob per row. Off by
default (stats.txt runs to ~1200 lines per program; at 500+ programs
this roughly doubles the CSV's size) -- turn it on when you need a
stat that isn't in groups 2-4 and don't want to re-parse stats.txt by
hand.

Usage:
    python3 parse_gem5_traces_to_csv.py gem5_stats_minorcpu lowbit_traces_gem5_minorcpu.csv
    python3 parse_gem5_traces_to_csv.py gem5_stats_atomcpu lowbit_traces_gem5_atomcpu.csv --full
"""

import re
import csv
import sys
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ARGS = [a for a in sys.argv[1:] if not a.startswith('--')]
FULL = '--full' in sys.argv[1:]
STATS_DIR = Path(ARGS[0]) if len(ARGS) > 0 else SCRIPT_DIR / "gem5_stats_minorcpu"
OUTPUT_CSV = Path(ARGS[1]) if len(ARGS) > 1 else SCRIPT_DIR / "lowbit_traces_gem5_minorcpu.csv"

STAT_LINE_RE = re.compile(r'^(\S+)\s+([0-9.eE+-]+)')

# ---------------------------------------------------------------------------
# Group 1: CORE (unchanged from the original schema)
# ---------------------------------------------------------------------------
CORE_KEYS = {
    'instructions_total': ['simInsts'],
    'cycles_total': ['system.cpu.numCycles'],
    'branches_total': ['system.cpu.branchPred.lookups_0::total',
                        'system.cpu.branchPred.BTBLookups'],
    'branch_misses_total': ['system.cpu.branchPred.mispredicted_0::total',
                             'system.cpu.branchPred.condIncorrect'],
    'dcache_accesses': ['system.cpu.dcache.overallAccesses::total'],
    'dcache_misses': ['system.cpu.dcache.overallMisses::total'],
    'icache_accesses': ['system.cpu.icache.overallAccesses::total'],
    'icache_misses': ['system.cpu.icache.overallMisses::total'],
    'l2_accesses': ['system.l2.overallAccesses::total'],
    'l2_misses': ['system.l2.overallMisses::total'],
    'sim_seconds': ['simSeconds'],
}
DIRECT_METRICS = {
    'observed_ipc': ['system.cpu.ipc'],
    'observed_cpi': ['system.cpu.cpi'],
}
CORE_FIELDNAMES = [
    'run_dir', 'program', 'backend', 'cpu_type', 'bp_type_raw',
    'l1d_size', 'l1i_size', 'l2_size', 'n_override', 'stats_available',
    'instructions_total', 'cycles_total', 'branches_total', 'branch_misses_total',
    'dcache_accesses', 'dcache_misses', 'icache_accesses', 'icache_misses',
    'l2_accesses', 'l2_misses', 'sim_seconds',
    'observed_ipc', 'observed_cpi', 'observed_branch_miss_rate',
    'observed_cache_miss_rate', 'observed_icache_miss_rate', 'unmatched_metrics',
]

# ---------------------------------------------------------------------------
# Group 2: branch predictor detail (MinorCPU only)
# ---------------------------------------------------------------------------
BRANCH_DETAIL_KEYS = {
    'btb_lookups': ['system.cpu.branchPred.BTBLookups'],
    'btb_hits': ['system.cpu.branchPred.BTBHits'],
    'btb_hit_ratio': ['system.cpu.branchPred.BTBHitRatio'],
    'btb_mispredicted': ['system.cpu.branchPred.BTBMispredicted'],
    'ras_correct': ['system.cpu.branchPred.ras.correct'],
    'ras_incorrect': ['system.cpu.branchPred.ras.incorrect'],
    'ras_pushes': ['system.cpu.branchPred.ras.pushes'],
    'ras_pops': ['system.cpu.branchPred.ras.pops'],
    'indirect_lookups': ['system.cpu.branchPred.indirectLookups'],
    'indirect_hits': ['system.cpu.branchPred.indirectHits'],
    'indirect_misses': ['system.cpu.branchPred.indirectMisses'],
    # NOTE: indirectMispredicted dropped -- gem5 never increments this
    # counter for the tagged indirect predictor used here (confirmed
    # always 0 across all 3100 rows, and no submodule stat covers it
    # either); indirect_misses above is the real, populated signal.
    'squashes_total': ['system.cpu.branchPred.squashes_0::total'],
    'corrected_total': ['system.cpu.branchPred.corrected_0::total'],
    # per-branch-type breakdown: conditional (dominant type), return and
    # indirect-call (what RAS/indirect predictor actually cover), and
    # indirect-unconditional (jump tables / dispatch_table families)
    'committed_directcond': ['system.cpu.branchPred.committed_0::DirectCond'],
    'mispredicted_directcond': ['system.cpu.branchPred.mispredicted_0::DirectCond'],
    'committed_return': ['system.cpu.branchPred.committed_0::Return'],
    'mispredicted_return': ['system.cpu.branchPred.mispredicted_0::Return'],
    'committed_callindirect': ['system.cpu.branchPred.committed_0::CallIndirect'],
    'mispredicted_callindirect': ['system.cpu.branchPred.mispredicted_0::CallIndirect'],
    'committed_indirectuncond': ['system.cpu.branchPred.committed_0::IndirectUncond'],
    'mispredicted_indirectuncond': ['system.cpu.branchPred.mispredicted_0::IndirectUncond'],
}

# ---------------------------------------------------------------------------
# Group 3: cache detail (L1i, L1d, L2 -- read/write split, latency, MSHR)
# ---------------------------------------------------------------------------
CACHE_DETAIL_KEYS = {
    'dcache_readreq_accesses': ['system.cpu.dcache.ReadReq.accesses::total'],
    'dcache_readreq_misses': ['system.cpu.dcache.ReadReq.misses::total'],
    'dcache_writereq_accesses': ['system.cpu.dcache.WriteReq.accesses::total'],
    'dcache_writereq_misses': ['system.cpu.dcache.WriteReq.misses::total'],
    'dcache_avg_miss_latency': ['system.cpu.dcache.overallAvgMissLatency::total'],
    'dcache_mshr_miss_rate': ['system.cpu.dcache.overallMshrMissRate::total'],
    'dcache_replacements': ['system.cpu.dcache.replacements'],
    'dcache_writebacks': ['system.cpu.dcache.writebacks::total'],
    'icache_avg_miss_latency': ['system.cpu.icache.overallAvgMissLatency::total'],
    'icache_mshr_miss_rate': ['system.cpu.icache.overallMshrMissRate::total'],
    'icache_replacements': ['system.cpu.icache.replacements'],
    'l2_readshared_accesses': ['system.l2.ReadSharedReq.accesses::total'],
    'l2_readshared_misses': ['system.l2.ReadSharedReq.misses::total'],
    'l2_avg_miss_latency': ['system.l2.overallAvgMissLatency::total'],
    'l2_mshr_miss_rate': ['system.l2.overallMshrMissRate::total'],
    'l2_replacements': ['system.l2.replacements'],
    'l2_writeback_dirty': ['system.l2.WritebackDirty.accesses::total'],
}

# ---------------------------------------------------------------------------
# Group 4: TLB, DRAM controller, pipeline utilization, host performance
# ---------------------------------------------------------------------------
MISC_KEYS = {
    # NOTE: itb/dtb accesses+misses (and their read/write splits) are
    # dropped -- gem5 never increments any of them in syscall-emulation
    # (SE) mode (confirmed always 0 across all 3100 rows; SE mode
    # bypasses the modeled TLB walker on every memory access). Real
    # TLB pressure numbers need gem5's full-system (FS) mode instead.
    'dram_avg_mem_acc_lat': ['system.mem_ctrls.dram.avgMemAccLat'],
    'dram_bw_total': ['system.mem_ctrls.dram.bwTotal::total'],
    'dram_page_hit_rate': ['system.mem_ctrls.dram.pageHitRate'],
    'dram_num_reads': ['system.mem_ctrls.dram.numReads::total'],
    'idle_cycles': ['system.cpu.idleCycles'],
    'num_syscalls': ['system.cpu.workload.numSyscalls'],
    # fetchStats0.numOps is always 0 for MinorCPU (gem5 doesn't populate
    # op-granularity counts at the fetch stage, only at commit) -- use
    # numInsts instead, which is real fetch-stage traffic.
    'fetch_num_insts': ['system.cpu.fetchStats0.numInsts'],
    'commit_num_ops': ['system.cpu.commitStats0.numOps'],
    'sim_ops': ['simOps'],
    'sim_ticks': ['simTicks'],
    'host_seconds': ['hostSeconds'],
    'host_inst_rate': ['hostInstRate'],
}

EXTRA_GROUPS = [BRANCH_DETAIL_KEYS, CACHE_DETAIL_KEYS, MISC_KEYS]
EXTRA_FIELDNAMES = [k for g in EXTRA_GROUPS for k in g]

FIELDNAMES = CORE_FIELDNAMES + EXTRA_FIELDNAMES + (['raw_stats_json'] if FULL else [])

META_PATTERNS = {
    r'^cpu_type=(.+)$': 'cpu_type',
    r'^bp_type=(.+)$': 'bp_type_raw',
    r'^n_override=(\d+)$': 'n_override',
}
L1_L2_SIZE_PATTERN = re.compile(r'l1d_size=(\S+)\s+l1i_size=(\S+)\s+l2_size=(\S+)')


def parse_meta_txt(filepath):
    meta = {'cpu_type': '', 'bp_type_raw': '', 'n_override': 0,
            'l1d_size': '', 'l1i_size': '', 'l2_size': ''}
    content = filepath.read_text(encoding='utf-8', errors='ignore')
    m = L1_L2_SIZE_PATTERN.search(content)
    if m:
        meta['l1d_size'], meta['l1i_size'], meta['l2_size'] = m.groups()
    for regex, key in META_PATTERNS.items():
        match = re.search(regex, content, re.MULTILINE)
        if match:
            val = match.group(1).strip()
            meta[key] = int(val) if key == 'n_override' else val
    return meta


def parse_stats_txt(filepath):
    """One pass over stats.txt -> {full_stat_name: float}. Every
    numeric line goes in, including ::-suffixed per-type breakdowns --
    every field this script surfaces (core + all three extra groups,
    plus --full's raw_stats_json) is a lookup into this one dict."""
    stats = {}
    with open(filepath, encoding='utf-8', errors='ignore') as f:
        for line in f:
            m = STAT_LINE_RE.match(line)
            if m:
                try:
                    stats[m.group(1)] = float(m.group(2))
                except ValueError:
                    pass
    return stats


def first_match(stats, keys):
    for k in keys:
        if k in stats:
            return stats[k]
    return None


def parse_one(run_dir, prog_dir):
    row = {k: '' for k in FIELDNAMES}
    row['run_dir'] = run_dir
    row['program'] = run_dir
    row['backend'] = 'gem5'

    meta_path = prog_dir / 'meta.txt'
    stats_path = prog_dir / 'stats.txt'
    if not meta_path.exists() or not stats_path.exists():
        row['stats_available'] = 0
        row['unmatched_metrics'] = 'meta.txt or stats.txt missing'
        return row

    meta = parse_meta_txt(meta_path)
    row['cpu_type'] = meta['cpu_type']
    row['bp_type_raw'] = meta['bp_type_raw']
    row['l1d_size'] = meta['l1d_size']
    row['l1i_size'] = meta['l1i_size']
    row['l2_size'] = meta['l2_size']
    row['n_override'] = meta['n_override']

    stats = parse_stats_txt(stats_path)
    if not stats:
        row['stats_available'] = 0
        row['unmatched_metrics'] = 'stats.txt empty or unparseable'
        return row
    row['stats_available'] = 1

    # --- core (required) ---
    unmatched = []
    raw = {}
    for field, keys in CORE_KEYS.items():
        v = first_match(stats, keys)
        if v is None:
            v = 0.0
            unmatched.append(field)
        raw[field] = v
        row[field] = v

    ipc = first_match(stats, DIRECT_METRICS['observed_ipc'])
    cpi = first_match(stats, DIRECT_METRICS['observed_cpi'])
    if ipc is None and raw['cycles_total']:
        ipc = raw['instructions_total'] / raw['cycles_total']
    if cpi is None and raw['instructions_total']:
        cpi = raw['cycles_total'] / raw['instructions_total']
    row['observed_ipc'] = round(ipc, 4) if ipc is not None else 0.0
    row['observed_cpi'] = round(cpi, 4) if cpi is not None else 0.0
    row['observed_branch_miss_rate'] = (
        round(raw['branch_misses_total'] / raw['branches_total'], 6) if raw['branches_total'] else 0.0
    )
    row['observed_cache_miss_rate'] = (
        round(raw['dcache_misses'] / raw['dcache_accesses'], 6) if raw['dcache_accesses'] else 0.0
    )
    row['observed_icache_miss_rate'] = (
        round(raw['icache_misses'] / raw['icache_accesses'], 6) if raw['icache_accesses'] else 0.0
    )
    row['unmatched_metrics'] = ';'.join(unmatched)

    # --- extras: best-effort, blank (not 0.0) when this CPU model/config
    # doesn't report a stat, so a blank cell never reads as "measured zero" ---
    for group in EXTRA_GROUPS:
        for field, keys in group.items():
            v = first_match(stats, keys)
            row[field] = v if v is not None else ''

    if FULL:
        row['raw_stats_json'] = json.dumps(stats, separators=(',', ':'))

    return row


def main():
    if not STATS_DIR.exists():
        print(f"Error: {STATS_DIR} directory not found!")
        return

    run_dirs = sorted(d for d in STATS_DIR.iterdir() if d.is_dir())
    print(f"Found {len(run_dirs)} run folders under {STATS_DIR}.")
    if FULL:
        print("--full: also writing raw_stats_json (every numeric stat gem5 dumped)")

    n_no_stats = 0
    n_unmatched = 0
    with open(OUTPUT_CSV, 'a+', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=FIELDNAMES)
        writer.writeheader()
        for d in run_dirs:
            row = parse_one(d.name, d)
            if not row['stats_available']:
                n_no_stats += 1
            elif row['unmatched_metrics']:
                n_unmatched += 1
            writer.writerow(row)

    print(f"Done. CSV written to: {OUTPUT_CSV}")
    print(f"   Programs: {len(run_dirs)} | Columns: {len(FIELDNAMES)} "
          f"({len(CORE_FIELDNAMES)} core + {len(EXTRA_FIELDNAMES)} extra"
          f"{' + raw_stats_json' if FULL else ''})")
    print(f"   Rows with stats_available=0 (missing/unparseable meta.txt or stats.txt): {n_no_stats}")
    print(f"   Rows with an unmatched CORE counter (e.g. no branch predictor on this CPU model): {n_unmatched}")
    if n_unmatched:
        print("   NOTE: unmatched branches_total/branch_misses_total on every row usually")
        print("   means this run used AtomicSimpleCPU -- see Table 3 in the paper /")
        print("   LIMITATIONS.md, not a parsing bug. Extra-group fields (branch predictor")
        print("   detail especially) will also come back blank for that CPU model, which")
        print("   is expected and NOT counted in unmatched_metrics.")


if __name__ == "__main__":
    main()
