#!/usr/bin/env bash
# LowBit Trace Corpus -- Orange Pi backend (real RISC-V silicon)
# ---------------------------------------------------------
# Lives in orange-pi/, alongside this backend's own results. This is
# the only backend in the corpus that runs on REAL RISC-V hardware,
# with REAL hardware branch-predictor and cache-hierarchy counters --
# not an emulator (qemu/), not a timing simulation (gem5/), not a
# golden-model ISA simulator (spike/), and not a different ISA
# (x86-64/). If you have an Orange Pi RV2 (or similar RISC-V SBC)
# running Linux, this backend gives you ground truth.
#
# NOT execution-verified in the environment that wrote this script (a
# generic x86-64 container, not the board itself) -- treat it as a
# documented starting point, same as gem5/GEM5.md and spike/run_spike.sh.
#
# This script MUST be run directly on the Orange Pi (or copied there
# and run locally) -- it does not cross-compile or SSH out on your
# behalf, on purpose: building on-device avoids any cross-compiler/
# glibc-version mismatch between your dev machine and the board, and
# every other backend already covers the cross-compiled case.
#
# Setup on the Orange Pi itself:
#   sudo apt update
#   sudo apt install -y gcc linux-tools-common linux-tools-generic \
#       linux-tools-$(uname -r) binutils
#   sudo sysctl kernel.perf_event_paranoid=-1   # if perf refuses to launch
#
# Usage (run FROM this script's own directory on the board, or
# anywhere -- paths resolve relative to this script's location):
#   ./run_orange_pi.sh                  # build + verify + run everything, 1 rep
#   ./run_orange_pi.sh build            # build only
#   ./run_orange_pi.sh verify           # objdump-check a sample of builds
#   ./run_orange_pi.sh run [REPS]       # run only, REPS repetitions per binary (default 1)
#   ./run_orange_pi.sh env              # (re)write ENVIRONMENT.md only
#
# If your workflow is "build on a fast x86-64 dev machine, copy
# binaries to the board, run there": build with the qemu/ backend's
# riscv64-*-gcc + -static invocation (same CFLAGS as below, minus
# -DLOWBIT_BACKEND), copy the resulting bin/ directory here as
# orange-pi/bin/, then run just the `run` mode of this script -- it
# will use whatever is already in bin/ rather than rebuilding.

set -u
MODE="${1:-all}"
REPS="${2:-1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROGRAMS_DIR="$SCRIPT_DIR/../programs"
BIN_DIR="$SCRIPT_DIR/bin"
LOG_DIR="$SCRIPT_DIR/traces"
ENV_FILE="$SCRIPT_DIR/ENVIRONMENT.md"
mkdir -p "$BIN_DIR" "$LOG_DIR"

HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
if [ "$HOST_ARCH" != "riscv64" ]; then
    echo "WARNING: this host reports arch='$HOST_ARCH', not riscv64."
    echo "run_orange_pi.sh is meant to be run ON the Orange Pi board itself"
    echo "so that gcc/perf measure real RISC-V hardware directly. Continuing"
    echo "anyway will produce a $HOST_ARCH build/run, which defeats the point"
    echo "of this backend -- see the header comment for the cross-compile-"
    echo "then-copy alternative workflow."
fi

if [ ! -d "$PROGRAMS_DIR" ] && [ ! -d "$BIN_DIR" ]; then
    echo "Neither $PROGRAMS_DIR (source) nor $BIN_DIR (pre-built binaries) found."
    exit 1
fi

CC="gcc"
BACKEND_LABEL="orange_pi_native"
CFLAGS="-O2 -std=c99 -fno-tree-vectorize -ffp-contract=off -fno-if-conversion -fno-if-conversion2 -DLOWBIT_BACKEND=\"$BACKEND_LABEL\" -I$PROGRAMS_DIR"

write_environment() {
    {
        echo "# Environment snapshot (Orange Pi / real RISC-V hardware backend)"
        echo
        echo "Captured: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
        echo
        echo "## Build"
        echo '```'
        echo "compiler: $($CC --version 2>/dev/null | head -1)"
        echo "compiler_path: $(command -v "$CC" 2>/dev/null)"
        echo "cflags: $CFLAGS"
        echo "backend: $BACKEND_LABEL"
        echo '```'
        echo
        echo "## Host"
        echo '```'
        echo "uname: $(uname -a 2>/dev/null)"
        if [ -f /proc/cpuinfo ]; then
            echo "cpuinfo:"
            sed 's/^/  /' /proc/cpuinfo
        fi
        echo '```'
        echo
        echo "## perf"
        echo '```'
        if command -v perf >/dev/null 2>&1; then
            echo "perf_version: $(perf --version 2>/dev/null)"
            echo "perf_event_paranoid: $(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || echo unknown)"
        else
            echo "perf: not found"
        fi
        echo '```'
    } > "$ENV_FILE"
    echo "Environment snapshot written to: $ENV_FILE"
}

build_all() {
    if [ ! -d "$PROGRAMS_DIR" ]; then
        echo "$PROGRAMS_DIR not found -- skipping build, assuming $BIN_DIR is pre-populated."
        return
    fi
    echo "Building with $CC (backend=$BACKEND_LABEL) from $PROGRAMS_DIR ..."
    for f in "$PROGRAMS_DIR"/*.c; do
        base="$(basename "${f%.c}")"
        $CC $CFLAGS -o "$BIN_DIR/$base" "$f" || echo "BUILD FAILED: $f"
    done
}

verify_sample() {
    command -v objdump >/dev/null 2>&1 || { echo "objdump not found; skipping verify."; return; }
    echo "Verifying a sample of binaries actually contain a real conditional branch"
    echo "(not a compiler-generated cmov/branchless sequence) for the benchmark-under-test..."
    n=0
    for bin in "$BIN_DIR"/*; do
        [ -f "$bin" ] || continue
        n=$((n + 1))
        if [ $((n % 25)) -ne 1 ]; then continue; fi
        base="$(basename "$bin")"
        # RISC-V has no cmov; look instead for the compiler eliminating
        # the branch entirely (i.e. zero conditional-branch opcodes).
        branch_count=$(objdump -d "$bin" 2>/dev/null | grep -Ec '\b(beq|bne|blt|bge|bltu|bgeu)\b' || true)
        echo "  $base: conditional branch instructions in whole binary = $branch_count"
        if [ "$branch_count" = "0" ]; then
            echo "    -> SUSPICIOUS: expected at least one; inspect with: objdump -d $bin | less"
        fi
    done
}

perf_is_usable() {
    command -v perf >/dev/null 2>&1 || return 1
    perf stat -e task-clock true >/tmp/lowbit_orangepi_perf_check.$$ 2>&1
    local rc=$?
    if grep -q "No supported events found\|Access to performance monitoring" /tmp/lowbit_orangepi_perf_check.$$ 2>/dev/null; then
        rc=1
    fi
    rm -f /tmp/lowbit_orangepi_perf_check.$$
    return $rc
}

PERF_EVENTS="instructions,cycles,task-clock,context-switches,cpu-migrations,page-faults,branch-instructions,branch-misses,cache-references,cache-misses"

run_all() {
    write_environment

    USE_PERF=1
    if ! perf_is_usable; then
        USE_PERF=0
        PARANOID_VAL="$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || echo unknown)"
        echo "=================================================================="
        echo "WARNING: perf cannot access hardware counters on this board"
        echo "(kernel.perf_event_paranoid=$PARANOID_VAL), or perf is not installed."
        echo "sudo sysctl kernel.perf_event_paranoid=-1 and re-run for real"
        echo "hardware branch/cache counters -- see the setup block above."
        echo "=================================================================="
    fi

    for bin in "$BIN_DIR"/*; do
        [ -f "$bin" ] || continue
        base="$(basename "$bin")"
        for rep in $(seq 1 "$REPS"); do
            if [ "$REPS" -eq 1 ]; then
                outfile="$LOG_DIR/${base}.perf.txt"
            else
                outfile="$LOG_DIR/${base}.rep${rep}.perf.txt"
            fi

            if [ "$USE_PERF" -eq 1 ]; then
                echo "Running (backend=$BACKEND_LABEL, perf, rep $rep/$REPS): $base"
                { echo "backend_declared=$BACKEND_LABEL"; \
                  perf stat -e "$PERF_EVENTS" "$bin"; } > "$outfile" 2>&1
            else
                echo "Running (backend=$BACKEND_LABEL, NO perf, rep $rep/$REPS): $base"
                { echo "backend_declared=$BACKEND_LABEL"; \
                  echo "NOTE: perf unavailable on this board."; \
                  "$bin"; } > "$outfile" 2>&1
            fi
        done
    done
    echo "Per-program traces written to: $LOG_DIR"
    echo "Parse them into a CSV with: python3 $SCRIPT_DIR/parse_orange_pi_traces_to_csv.py $LOG_DIR $SCRIPT_DIR/lowbit_traces_orange_pi.csv"
}

case "$MODE" in
    build)  build_all ;;
    verify) verify_sample ;;
    run)    run_all ;;
    env)    write_environment ;;
    all)    build_all; verify_sample; run_all ;;
    *) echo "Usage: $0 [build|verify|run [REPS]|env|all]"; exit 1 ;;
esac
