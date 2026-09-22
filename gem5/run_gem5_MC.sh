#!/usr/bin/env bash
# run_gem5.sh -- gem5 timing-simulation backend (see GEM5.md)
# ---------------------------------------------------------------
# SEPARATE from build_and_run_riscv.sh. Compiles selected benchmark
# sources with a reduced -DN=... (gem5's detailed CPU models are far
# too slow to run the corpus's default N at scale -- see GEM5.md) and
# runs them under gem5's syscall-emulation mode with a detailed CPU
# model, so branch-predictor and cache-hierarchy statistics are
# genuinely simulated rather than reflecting host-process behavior.
#
# IMPORTANT: the benchmarks take SIZE/N only as compile-time -D
# overrides (see lowbit_runtime.h / gen_programs.py) -- there is no
# argv parsing in main(), so N cannot be changed at gem5-invocation
# time via `--options`. This script rebuilds a gem5-specific binary
# per selected program instead, with -DN=$GEM5_N baked in.
#
# Usage:
#   ./run_gem5.sh sample [COUNT]     # build+run an evenly-spaced sample (default COUNT=20)
#   ./run_gem5.sh list ID [ID ...]   # build+run specific program IDs, e.g. 057 112 301
#   ./run_gem5.sh all                # build+run all 500 -- see GEM5.md speed warning
#
# Requires GEM5_ROOT / GEM5_BINARY to be set (see GEM5.md for how to
# build gem5), plus the same riscv64-*-gcc cross compiler
# build_and_run_riscv.sh uses.
#
# NOTE: this script targets gem5's documented `configs/example/se.py`
# CLI. Exact flags (especially --bp-type) vary somewhat by gem5
# version; if a run fails on an unrecognized flag, check
# `$GEM5_BINARY $GEM5_ROOT/configs/example/se.py --help` for your
# version's equivalent and adjust GEM5_BP_TYPE / the invocation below.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROGRAMS_DIR="$SCRIPT_DIR/../programs"
BUILD_DIR="$SCRIPT_DIR/bin_gem5_minorcpu"
OUT_DIR="$SCRIPT_DIR/gem5_stats_minorcpu"
# mkdir deferred to below GEM5_BP_TYPE (see there) -- output dir name
# depends on it when a non-default predictor is requested.

if [ ! -d "$PROGRAMS_DIR" ]; then
    echo "Programs directory not found at $PROGRAMS_DIR"
    echo "This script expects the corpus layout: <repo>/programs/*.c and <repo>/gem5/$(basename "${BASH_SOURCE[0]}")"
    exit 1
fi

GEM5_ROOT="${GEM5_ROOT:-}"
GEM5_BINARY="${GEM5_BINARY:-}"
GEM5_N="${GEM5_N:-20000}"
# Also caps the working-set SIZE, not just N. Several of the 500
# programs default to SIZE=1,048,576 or SIZE=8,388,608 (the large_L3 /
# xlarge_DRAM tiers) -- buffer initialization (and some cache
# patterns' O(SIZE) setup, e.g. the pointer-chase permutation shuffle)
# runs in full at that size regardless of any N override, and gem5
# simulates every one of those instructions in detail. Without also
# capping SIZE, those specific programs can take vastly longer than
# the rest -- this, not the CPU model choice, was the dominant cause
# of very long per-run times in practice. NOTE: this means the
# binary's own self-reported `size_tier` field (e.g. "xlarge_DRAM")
# is STALE for gem5 runs -- ignore it and use the actual
# GEM5_SIZE/GEM5_N recorded in meta.txt instead
# (parse_gem5_stats_to_csv.py already does this).
GEM5_SIZE="${GEM5_SIZE:-4096}"
# MinorCPU: a timing-accurate in-order pipeline model with a real
# branch predictor and real cache-hierarchy timing. This is the
# default based on DIRECT, CONFIRMED EVIDENCE (not a speed guess):
# DerivO3CPU (gem5's out-of-order model) was found to hang
# indefinitely at 100% host CPU under this project's actual usage --
# confirmed via a controlled isolation test where AtomicSimpleCPU
# completed normally and ONLY DerivO3CPU hung, with every other
# variable (binary, SIZE, N, cache config, gem5 build) held identical.
# This matches a known class of gem5 issue: DerivO3CPU + syscall-
# emulation (SE) mode has multiple open upstream issues across gem5
# versions (hangs, port-connection panics) that the simpler CPU
# models don't hit. MinorCPU still gives genuinely simulated (not
# host-measured) branch and cache behavior -- it just doesn't model
# out-of-order/superscalar execution. Override at your own risk if
# you want to keep trying DerivO3CPU on a specific program:
#   GEM5_CPU_TYPE=DerivO3CPU GEM5_JOBS=1 ./run_gem5.sh list 001
# and be ready for it to hang and need `pkill -9 -f gem5.opt`.
GEM5_CPU_TYPE="${GEM5_CPU_TYPE:-MinorCPU}"
# How many gem5 instances to run at once. gem5 barely uses more than
# one host core per simulation, so on a multi-core machine, running
# several in parallel is close to a free multiplier -- turning "500
# sequential runs" into "500/JOBS batches". Leave a few threads free
# for the OS; don't set this to your full core count.
GEM5_JOBS="${GEM5_JOBS:-6}"
# NOTE: left empty by default on purpose. On gem5 25.x, DerivO3CPU's
# branch predictor was restructured to require an explicit
# conditionalBranchPred sub-object that the legacy --bp-type flag
# (via the deprecated se.py) does not always set, causing a hard
# `fatal: system.cpu.branchPred.conditionalBranchPred without default
# or user set value` and NO stats.txt at all. Leaving GEM5_BP_TYPE
# unset uses the CPU model's own default predictor, which IS fully
# configured and will not hit this. Only set GEM5_BP_TYPE if you've
# confirmed your gem5 build/version handles it -- see GEM5.md
# "Troubleshooting" before setting this.
GEM5_BP_TYPE="${GEM5_BP_TYPE:-}"

# Predictor-aware output naming: when a non-default predictor is
# requested, suffix both BUILD_DIR and OUT_DIR with a sanitized
# version of it so back-to-back runs with different GEM5_BP_TYPE
# values (e.g. a predictor sweep) never overwrite each other's
# output. Default predictor (GEM5_BP_TYPE unset) keeps the original,
# unsuffixed directory names -- fully backward compatible with every
# existing script/doc that references gem5_stats_minorcpu/ directly.
if [ -n "$GEM5_BP_TYPE" ]; then
    BP_SUFFIX="$(echo "$GEM5_BP_TYPE" | tr -c 'A-Za-z0-9' '_')"
    BUILD_DIR="${BUILD_DIR}_${BP_SUFFIX}"
    OUT_DIR="${OUT_DIR}_${BP_SUFFIX}"
fi
mkdir -p "$BUILD_DIR" "$OUT_DIR"
GEM5_L1D_SIZE="${GEM5_L1D_SIZE:-32kB}"
GEM5_L1I_SIZE="${GEM5_L1I_SIZE:-32kB}"
GEM5_L2_SIZE="${GEM5_L2_SIZE:-256kB}"

if [ -z "$GEM5_ROOT" ] || [ -z "$GEM5_BINARY" ]; then
    echo "GEM5_ROOT and GEM5_BINARY must be set. See GEM5.md for setup."
    echo "  export GEM5_ROOT=/path/to/gem5"
    echo "  export GEM5_BINARY=\$GEM5_ROOT/build/RISCV/gem5.opt"
    exit 1
fi
if [ ! -x "$GEM5_BINARY" ]; then
    echo "GEM5_BINARY ($GEM5_BINARY) not found or not executable."
    exit 1
fi
SE_PY="$GEM5_ROOT/configs/deprecated/example/se.py"
if [ ! -f "$SE_PY" ]; then
    # Older gem5 checkouts only have the classic (non-deprecated) path.
    SE_PY="$GEM5_ROOT/configs/example/se.py"
fi
if [ ! -f "$SE_PY" ]; then
    echo "Could not find se.py at either:"
    echo "  $GEM5_ROOT/configs/deprecated/example/se.py"
    echo "  $GEM5_ROOT/configs/example/se.py"
    echo "Is GEM5_ROOT correct? ($GEM5_ROOT)"
    exit 1
fi
echo "Using se.py: $SE_PY"

RVGCC=""
for cand in riscv64-unknown-linux-gnu-gcc riscv64-linux-gnu-gcc riscv64-unknown-elf-gcc; do
    if command -v "$cand" >/dev/null 2>&1; then RVGCC="$cand"; break; fi
done
if [ -z "$RVGCC" ]; then
    echo "No RISC-V cross compiler found (riscv64-*-gcc). gem5's RISCV build needs a"
    echo "RISC-V binary to run -- a native x86-64 build won't work here."
    exit 1
fi
CFLAGS="-O2 -std=c99 -fno-tree-vectorize -ffp-contract=off -fno-if-conversion -fno-if-conversion2 -static -DLOWBIT_BACKEND=\"gem5\" -I$PROGRAMS_DIR"

# Only pass --bp-type if the user explicitly set GEM5_BP_TYPE (see the
# fatal-error note above) AND this gem5 build's se.py recognizes the flag.
BP_FLAG=""
if [ -n "$GEM5_BP_TYPE" ]; then
    if "$GEM5_BINARY" "$SE_PY" --help 2>/dev/null | grep -q -- "--bp-type"; then
        BP_FLAG="--bp-type=$GEM5_BP_TYPE"
    else
        echo "WARNING: GEM5_BP_TYPE=$GEM5_BP_TYPE was set, but this gem5 build's"
        echo "se.py does not expose --bp-type. Running with the CPU's default predictor."
    fi
fi

build_and_run_one() {
    local src="$1"
    local base
    base="$(basename "${src%.c}")"
    local bin="$BUILD_DIR/$base"
    local outdir="$OUT_DIR/$base"
    mkdir -p "$outdir"

    echo "Building for gem5 (SIZE=$GEM5_SIZE, N=$GEM5_N): $base"
    $RVGCC $CFLAGS -DSIZE="$GEM5_SIZE" -DN="$GEM5_N" -o "$bin" "$src" 2>"$outdir/build.log"
    if [ ! -f "$bin" ]; then
        echo "  BUILD FAILED -- see $outdir/build.log"
        return
    fi

    echo "Running under gem5 (cpu=$GEM5_CPU_TYPE): $base"
    "$GEM5_BINARY" --outdir="$outdir" "$SE_PY" \
        --cmd="$bin" \
        --cpu-type="$GEM5_CPU_TYPE" \
        $BP_FLAG \
        --caches --l2cache \
        --l1d_size="$GEM5_L1D_SIZE" --l1i_size="$GEM5_L1I_SIZE" --l2_size="$GEM5_L2_SIZE" \
        > "$outdir/gem5_run.log" 2>&1

    local bp_desc
    if [ -n "$GEM5_BP_TYPE" ]; then
        bp_desc="${GEM5_BP_TYPE} (applied: $([ -n "$BP_FLAG" ] && echo yes || echo "no -- flag unsupported by this gem5 build"))"
    else
        bp_desc="default (CPU model's built-in predictor; GEM5_BP_TYPE not set)"
    fi

    {
        echo "source=$src"
        echo "binary=$bin"
        echo "cpu_type=$GEM5_CPU_TYPE"
        echo "bp_type=${bp_desc}"
        echo "l1d_size=$GEM5_L1D_SIZE l1i_size=$GEM5_L1I_SIZE l2_size=$GEM5_L2_SIZE"
        echo "size_override=$GEM5_SIZE"
        echo "n_override=$GEM5_N"
        echo "note=the binary's own printed size_tier field is STALE (reflects its"
        echo "     compiled-in default tier name, not this size_override) -- use"
        echo "     size_override/n_override above as the real values for this run"
        echo "backend=gem5"
    } > "$outdir/meta.txt"

    if [ ! -f "$outdir/stats.txt" ]; then
        echo "  WARNING: no stats.txt produced for $base -- check $outdir/gem5_run.log"
    fi
}

MODE="${1:-sample}"
shift || true

# Launches build_and_run_one in the background, capped at GEM5_JOBS
# concurrent gem5 processes. gem5 barely uses more than one host core
# per run, so this is close to a free speedup on any multi-core
# machine -- just don't set GEM5_JOBS to your full core count (leave
# some headroom for the OS and everything else you're running).
run_throttled() {
    local src="$1"
    while [ "$(jobs -rp | wc -l)" -ge "$GEM5_JOBS" ]; do
        wait -n
    done
    build_and_run_one "$src" &
}

case "$MODE" in
    sample)
        COUNT="${1:-20}"
        mapfile -t ALL_SRCS < <(ls "$PROGRAMS_DIR"/*.c | sort)
        TOTAL=${#ALL_SRCS[@]}
        STEP=$(( TOTAL / COUNT ))
        [ "$STEP" -lt 1 ] && STEP=1
        i=0
        for s in "${ALL_SRCS[@]}"; do
            if [ $((i % STEP)) -eq 0 ]; then
                run_throttled "$s"
            fi
            i=$((i + 1))
        done
        wait
        ;;
    list)
        if [ "$#" -eq 0 ]; then
            echo "Usage: $0 list ID [ID ...]   e.g. $0 list 057 112 301"
            exit 1
        fi
        for id in "$@"; do
            match=$(ls "$PROGRAMS_DIR" | grep "^${id}_.*\.c$" | head -1)
            if [ -z "$match" ]; then
                echo "No source found matching ID $id in $PROGRAMS_DIR"
                continue
            fi
            run_throttled "$PROGRAMS_DIR/$match"
        done
        wait
        ;;
    all)
        for s in "$PROGRAMS_DIR"/*.c; do
            run_throttled "$s"
        done
        wait
        ;;
    *)
        echo "Usage: $0 [sample [COUNT] | list ID [ID ...] | all]"
        exit 1
        ;;
esac

echo "gem5 stats written to: $OUT_DIR"
CSV_SUGGESTION="lowbit_traces_gem5_minorcpu.csv"
[ -n "$GEM5_BP_TYPE" ] && CSV_SUGGESTION="lowbit_traces_gem5_minorcpu_${BP_SUFFIX}.csv"
echo "Parse with: python3 $SCRIPT_DIR/parse_gem5_stats_to_csv.py $OUT_DIR $SCRIPT_DIR/$CSV_SUGGESTION"