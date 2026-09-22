#!/usr/bin/env bash
# LowBit Trace Corpus -- native x86-64 backend
# ---------------------------------------------------------
# Lives in x86-64/, alongside this backend's own results. Builds every
# *.c program in ../programs directly with the HOST gcc (no
# cross-compilation, no emulator) and runs the resulting binaries
# directly on this machine. If `perf` is available, this is the only
# backend in the corpus that measures REAL hardware branch-predictor
# and cache counters on REAL silicon -- it just isn't RISC-V silicon.
#
# How this differs from the other backends (see ../README.md):
#   - qemu/build_and_run_riscv.sh cross-compiles for RISC-V and runs
#     under qemu-riscv64 (a functional emulator only); `perf` there
#     measures the HOST process running QEMU, not guest RISC-V.
#   - gem5/run_gem5.sh cross-compiles for RISC-V and runs under a
#     configurable timing *simulation* of a RISC-V core.
#   - This script (x86-64/) never touches RISC-V at all. It compiles
#     natively for whatever CPU this script runs on and measures that
#     CPU directly, with no translation or simulation layer in the
#     way. It's real silicon, but a different ISA (x86-64, not
#     RISC-V) -- useful as its own study, and as a same-source
#     cross-architecture comparison point, not as a RISC-V result.
#
# Usage (run from anywhere -- paths resolve relative to this script's
# own location):
#   ./run_x86_native.sh                  # build + verify + run everything, 1 rep
#   ./run_x86_native.sh build            # build only
#   ./run_x86_native.sh verify           # objdump-check a sample of builds
#   ./run_x86_native.sh run [REPS]       # run only, REPS repetitions per binary (default 1)
#   ./run_x86_native.sh env              # (re)write ENVIRONMENT.md only
#
# Requires: gcc (native host compiler). `perf` (Linux perf_events) is
# optional -- if unavailable, or if kernel.perf_event_paranoid blocks
# it, this script still runs every program and records its own
# self-reported taken/nottaken/sink/roi_elapsed_seconds fields, just
# without hardware counters. objdump is optional, used by `verify`.

set -u

# ---------------------------------------------------------------
# Self-guard against a RISC-V cross-toolchain shadowing the system
# linker. If ~/riscv/bin (or similar) got prepended onto PATH in
# .bashrc for the qemu/spike backends, `gcc` here would still resolve
# to /usr/bin/gcc, but gcc's *internal* call to `ld` would silently
# pick up the RISC-V-only linker instead, producing:
#   ld: unrecognised emulation mode: elf_x86_64
# for every single program. Force system paths first, for this
# script's own build step only -- does not touch your interactive
# shell's PATH at all, so qemu/spike still work fine everywhere else.
export PATH="/usr/bin:/bin:$PATH"
if command -v ld >/dev/null 2>&1; then
    LD_RESOLVED="$(command -v ld)"
    if [[ "$LD_RESOLVED" != "/usr/bin/ld" && "$LD_RESOLVED" != "/bin/ld" ]]; then
        echo "WARNING: 'ld' still resolves to $LD_RESOLVED after prepending"
        echo "/usr/bin:/bin -- that's unusual (maybe /usr/bin/ld itself is"
        echo "missing on this machine?). Builds below may fail with an"
        echo "'unrecognised emulation mode' error if so; install binutils"
        echo "if that happens: sudo apt install binutils"
    fi
fi
# ---------------------------------------------------------------

MODE="${1:-all}"
# Previously defaulted to 1, meaning every run was a single unrepeated
# sample -- roi_elapsed_seconds_stddev was always 0 because there was
# no second sample to compare against, and any one-off scheduling
# hiccup (context switch, migration, page fault) landed in the data
# with nothing to flag it. Default bumped to 5 so stddev is
# meaningful and outlier reps can be seen; override with the same
# positional arg as before, e.g. `./run_x86_native.sh run 10`.
REPS="${2:-5}"
# Pin to a single core (skip core 0, which tends to catch more
# interrupt/kernel-thread traffic) and raise scheduling priority, if
# the tools are available, to cut down on the migration/context-switch
# noise the 28 "implausible" flagged rows likely came from. Set
# LOWBIT_NO_PIN=1 to disable if taskset/nice cause problems on your
# system (e.g. inside some containers).
PIN_CMD=()
if [ "${LOWBIT_NO_PIN:-0}" -ne 1 ] && command -v taskset >/dev/null 2>&1; then
    PIN_CORE="${LOWBIT_PIN_CORE:-1}"
    PIN_CMD=(taskset -c "$PIN_CORE")
fi
NICE_CMD=()
if [ "${LOWBIT_NO_PIN:-0}" -ne 1 ] && command -v nice >/dev/null 2>&1; then
    NICE_CMD=(nice -n -5)
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROGRAMS_DIR="$SCRIPT_DIR/../programs"
BIN_DIR="$SCRIPT_DIR/bin"
LOG_DIR="$SCRIPT_DIR/traces"
ENV_FILE="$SCRIPT_DIR/ENVIRONMENT.md"
mkdir -p "$BIN_DIR" "$LOG_DIR"

if [ ! -d "$PROGRAMS_DIR" ]; then
    echo "Programs directory not found at $PROGRAMS_DIR"
    echo "This script expects the corpus layout: <repo>/programs/*.c and <repo>/x86-64/$(basename "${BASH_SOURCE[0]}")"
    exit 1
fi

CC="gcc"
BACKEND_LABEL="native_host"
# -fno-if-conversion[2]: prevent GCC from turning the branch-under-test
# into a cmov, which would silently defeat the whole benchmark.
CFLAGS="-O2 -std=c99 -fno-tree-vectorize -ffp-contract=off -fno-if-conversion -fno-if-conversion2 -DLOWBIT_BACKEND=\"$BACKEND_LABEL\" -I$PROGRAMS_DIR"

write_environment() {
    {
        echo "# Environment snapshot (x86-64 native backend)"
        echo
        echo "Captured: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
        echo
        echo "## Build"
        echo '```'
        echo "compiler: $($CC --version 2>/dev/null | head -1)"
        echo "compiler_path: $(command -v "$CC")"
        echo "cflags: $CFLAGS"
        echo "backend: $BACKEND_LABEL"
        echo '```'
        echo
        echo "## Host"
        echo '```'
        echo "uname: $(uname -a 2>/dev/null)"
        if [ -f /etc/os-release ]; then
            echo "os_release:"
            sed 's/^/  /' /etc/os-release
        fi
        if command -v lscpu >/dev/null 2>&1; then
            echo "cpu:"
            lscpu 2>/dev/null | sed 's/^/  /'
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
    echo "Building with $CC (backend=$BACKEND_LABEL) from $PROGRAMS_DIR ..."
    for f in "$PROGRAMS_DIR"/*.c; do
        base="$(basename "${f%.c}")"
        $CC $CFLAGS -o "$BIN_DIR/$base" "$f" || echo "BUILD FAILED: $f"
    done
}

verify_sample() {
    command -v objdump >/dev/null 2>&1 || { echo "objdump not found; skipping verify."; return; }
    echo "Verifying a sample of binaries actually contain a real conditional branch"
    echo "(not a compiler-generated cmov) for the benchmark-under-test..."
    n=0
    for bin in "$BIN_DIR"/*; do
        n=$((n + 1))
        if [ $((n % 25)) -ne 1 ]; then continue; fi  # sample ~1 in 25
        base="$(basename "$bin")"
        cmov_count=$(objdump -d "$bin" 2>/dev/null | grep -c "cmov" || true)
        echo "  $base: cmov instructions in whole binary = $cmov_count"
        if [ "$cmov_count" != "0" ]; then
            echo "    -> inspect with: objdump -d $bin | less"
        fi
    done
}

# Same paranoia check as the qemu backend -- see build_and_run_riscv.sh.
perf_is_usable() {
    command -v perf >/dev/null 2>&1 || return 1
    perf stat -e task-clock true >/tmp/lowbit_x86_perf_check.$$ 2>&1
    local rc=$?
    if grep -q "No supported events found\|Access to performance monitoring" /tmp/lowbit_x86_perf_check.$$ 2>/dev/null; then
        rc=1
    fi
    rm -f /tmp/lowbit_x86_perf_check.$$
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
        echo "WARNING: perf cannot access hardware counters on this machine"
        echo "(kernel.perf_event_paranoid=$PARANOID_VAL), or perf is not installed."
        echo "Continuing WITHOUT perf so you still get program output (branch"
        echo "taken/not-taken counts, sink, ROI wall-clock time) -- install perf"
        echo "and/or run 'sudo sysctl kernel.perf_event_paranoid=-1' for real"
        echo "hardware branch/cache counters."
        echo "=================================================================="
    fi

    for bin in "$BIN_DIR"/*; do
        base="$(basename "$bin")"
        for rep in $(seq 1 "$REPS"); do
            if [ "$REPS" -eq 1 ]; then
                outfile="$LOG_DIR/${base}.perf.txt"
            else
                outfile="$LOG_DIR/${base}.rep${rep}.perf.txt"
            fi

            if [ "$USE_PERF" -eq 1 ]; then
                echo "Running (backend=$BACKEND_LABEL, perf, pinned=${PIN_CMD[*]:-no}, rep $rep/$REPS): $base"
                { echo "backend_declared=$BACKEND_LABEL"; \
                  echo "pinned=${PIN_CMD[*]:-none}"; \
                  "${NICE_CMD[@]}" "${PIN_CMD[@]}" perf stat -e "$PERF_EVENTS" "$bin"; } > "$outfile" 2>&1
            else
                echo "Running (backend=$BACKEND_LABEL, NO perf, pinned=${PIN_CMD[*]:-no}, rep $rep/$REPS): $base"
                { echo "backend_declared=$BACKEND_LABEL"; \
                  echo "NOTE: perf unavailable on this host."; \
                  echo "NOTE: program output below has no hardware branch/cache counters."; \
                  "${NICE_CMD[@]}" "${PIN_CMD[@]}" "$bin"; } > "$outfile" 2>&1
            fi
        done
    done
    echo "Per-program traces written to: $LOG_DIR"
    echo "Parse them into a CSV with: python3 $SCRIPT_DIR/parse_x86_traces_to_csv.py $LOG_DIR $SCRIPT_DIR/lowbit_traces_native.csv"
}

case "$MODE" in
    build)  build_all ;;
    verify) verify_sample ;;
    run)    run_all ;;
    env)    write_environment ;;
    all)    build_all; verify_sample; run_all ;;
    *) echo "Usage: $0 [build|verify|run [REPS]|env|all]"; exit 1 ;;
esac