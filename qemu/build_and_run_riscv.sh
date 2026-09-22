#!/usr/bin/env bash
# LowBit Trace Corpus -- build / run / verify helper (QEMU/perf backend)
# ---------------------------------------------------------
# Lives in qemu/, alongside this backend's own results. Cross-compiles
# every *.c program in ../programs for RISC-V and executes it under
# qemu-riscv64 (dynamic). Falls back to a native x86-64 build if no
# RISC-V cross compiler is found. Every backend-specific concern
# (hardware counters, environment capture, the execution backend
# label) lives here, not in the benchmark source.
#
# Usage (run from anywhere -- paths are resolved relative to this
# script's own location, not your current directory):
#   ./build_and_run_riscv.sh                  # build + verify + run everything, 1 rep
#   ./build_and_run_riscv.sh build             # build only
#   ./build_and_run_riscv.sh verify            # objdump-check a sample of builds
#   ./build_and_run_riscv.sh run [REPS]        # run only, REPS repetitions per binary (default 1)
#   ./build_and_run_riscv.sh env               # (re)write ENVIRONMENT.md only
#
# Sweep parameters without editing source, by rebuilding manually:
#   riscv64-unknown-linux-gnu-gcc -O2 -fno-if-conversion -fno-if-conversion2 \
#       -DSIZE=65536 -DN=3000000 -DLOWBIT_BACKEND=\"qemu_user_mode\" \
#       -static -o custom_build ../programs/057_....c
#
# IMPORTANT: `perf stat qemu-riscv64 ./binary` measures the QEMU HOST
# process, not guest RISC-V execution, unless you're on a full-system
# RISC-V VM or real silicon. See ../LIMITATIONS.md before trusting
# perf output collected this way.
#
# Requires: riscv64-unknown-linux-gnu-gcc (or riscv64-linux-gnu-gcc)
# and qemu-riscv64 on PATH for the RISC-V path. gcc, perf, and
# objdump are used throughout; each step degrades gracefully if a
# given tool isn't found.

set -u
MODE="${1:-all}"
REPS="${2:-1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROGRAMS_DIR="$SCRIPT_DIR/../programs"
BIN_DIR="$SCRIPT_DIR/bin"
LOG_DIR="$SCRIPT_DIR/traces"
ENV_FILE="$SCRIPT_DIR/ENVIRONMENT.md"
mkdir -p "$BIN_DIR" "$LOG_DIR"

if [ ! -d "$PROGRAMS_DIR" ]; then
    echo "Programs directory not found at $PROGRAMS_DIR"
    echo "This script expects the corpus layout: <repo>/programs/*.c and <repo>/qemu/$(basename "${BASH_SOURCE[0]}")"
    exit 1
fi

RVGCC=""
for cand in riscv64-unknown-linux-gnu-gcc riscv64-linux-gnu-gcc riscv64-unknown-elf-gcc; do
    if command -v "$cand" >/dev/null 2>&1; then RVGCC="$cand"; break; fi
done

HAVE_QEMU=0
command -v qemu-riscv64 >/dev/null 2>&1 && HAVE_QEMU=1

if [ -n "$RVGCC" ] && [ "$HAVE_QEMU" -eq 1 ]; then
    BACKEND_LABEL="qemu_user_mode"
    CC="$RVGCC"
    EXTRA_CFLAGS="-static"
else
    BACKEND_LABEL="native"
    CC="gcc"
    EXTRA_CFLAGS=""
fi

# -fno-if-conversion[2]: prevent GCC from turning the branch-under-test
# into a cmov, which would silently defeat the whole benchmark.
CFLAGS="-O2 -std=c99 -fno-tree-vectorize -ffp-contract=off -fno-if-conversion -fno-if-conversion2 -DLOWBIT_BACKEND=\"$BACKEND_LABEL\" $EXTRA_CFLAGS"

write_environment() {
    {
        echo "# Environment snapshot"
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
        echo "## Emulation (if applicable)"
        echo '```'
        if command -v qemu-riscv64 >/dev/null 2>&1; then
            echo "qemu_version: $(qemu-riscv64 --version 2>/dev/null | head -1)"
        else
            echo "qemu_version: not found"
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
        $CC $CFLAGS -I"$PROGRAMS_DIR" -o "$BIN_DIR/$base" "$f" || echo "BUILD FAILED: $f"
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

# Checks whether `perf stat` can actually open ANY hardware counter on
# this machine. Many locked-down systems (containers, some cloud VMs,
# some distro defaults, some WSL configurations) set
# /proc/sys/kernel/perf_event_paranoid high enough that `perf stat`
# refuses to even launch the child process -- meaning the benchmark
# never runs at all, not just that the counters come back empty. This
# check catches that case up front instead of silently writing 500
# trace files that are all just an error message.
perf_is_usable() {
    command -v perf >/dev/null 2>&1 || return 1
    perf stat -e task-clock true >/tmp/lowbit_perf_check.$$ 2>&1
    local rc=$?
    if grep -q "No supported events found\|Access to performance monitoring" /tmp/lowbit_perf_check.$$ 2>/dev/null; then
        rc=1
    fi
    rm -f /tmp/lowbit_perf_check.$$
    return $rc
}

# Full event list per the design's recommendation: instruction/cycle
# counts (needed to derive IPC/CPI), scheduling-noise counters
# (context-switches, cpu-migrations, page-faults, task-clock) so
# runs affected by system noise can be identified, plus the original
# branch/cache events. ONLY used for the native fallback path below --
# see GUEST_COUNT_MODE for the qemu_user_mode path, which never wraps
# perf around qemu-riscv64 (that measures the HOST, not the guest;
# see the file header and LIMITATIONS.md).
PERF_EVENTS="instructions,cycles,task-clock,context-switches,cpu-migrations,page-faults,branch-instructions,branch-misses,cache-references,cache-misses"

# --- Guest-side instruction counting for the qemu_user_mode path (optional) ---
# QEMU has no cache/branch-predictor model in user-mode emulation, so
# IPC/branch-miss/cache-miss are not obtainable here no matter how
# this is measured -- that's a hard limitation, not a bug (see
# LIMITATIONS.md). The DEFAULT and always-reliable measurement below
# is simply running the benchmark under qemu-riscv64 and capturing its
# own self-reported taken/nottaken/sink/validation/roi_elapsed_seconds
# -- that's what the parser and every downstream report rely on, and
# it is ALWAYS attempted, unconditionally.
#
# An exact GUEST instruction count is a nice-to-have on top of that,
# not a replacement for it. It's opt-in via QEMU_INSN_PLUGIN (a TCG
# plugin .so, e.g. contrib/plugins/libinsn.so built from the QEMU
# source tree with --enable-plugins) and is attempted as a SEPARATE,
# isolated extra invocation, appended to the same trace file -- it can
# never suppress or interfere with the primary functional run above.
#
# NOTE: -singlestep + -d exec was tried here previously and removed:
# on a stock (non --enable-debug-tcg) qemu-user build, -d exec silently
# produces zero log lines regardless of what `-d help` claims is
# available, AND -singlestep is 100-1000x slower than normal execution
# with no per-program N cap in place -- together those made every one
# of the 500 runs either return a bogus 0 count or take impractically
# long. If you want exact guest counts, build a TCG plugin and point
# QEMU_INSN_PLUGIN at it; there is no singlestep fallback anymore.
QEMU_INSN_PLUGIN="${QEMU_INSN_PLUGIN:-}"
GUEST_COUNT_MODE="none"
if [ -n "$QEMU_INSN_PLUGIN" ] && [ -f "$QEMU_INSN_PLUGIN" ]; then
    GUEST_COUNT_MODE="plugin"
fi

run_qemu_guest() {
    # Runs one binary under qemu-riscv64, always capturing the
    # benchmark's own stdout (this is the measurement that matters --
    # see file header). Optionally also appends an exact GUEST
    # instruction count from a TCG plugin, as a separate invocation
    # that cannot affect the primary run above it.
    local bin="$1" outfile="$2"
    {
        echo "backend_declared=$BACKEND_LABEL"
        echo "guest_count_mode=$GUEST_COUNT_MODE"
    } > "$outfile"

    # Primary, always-attempted functional run -- this is what the
    # parser reads program=/taken=/nottaken=/sink=/validation=/
    # roi_elapsed_seconds= from. Never skipped, never gated on
    # GUEST_COUNT_MODE.
    qemu-riscv64 "$bin" >> "$outfile" 2>&1

    if [ "$GUEST_COUNT_MODE" = "plugin" ]; then
        # Separate, isolated invocation purely for the instruction
        # count -- its own stdout goes to /dev/null so it can't
        # duplicate or corrupt the primary run's output above.
        local plugin_stderr="$LOG_DIR/.plugin_stderr.$$"
        qemu-riscv64 -plugin "$QEMU_INSN_PLUGIN" -d plugin "$bin" \
            >/dev/null 2>"$plugin_stderr"
        local count
        count=$(grep -oE 'insns?: *[0-9]+' "$plugin_stderr" | grep -oE '[0-9]+' | tail -1)
        rm -f "$plugin_stderr"
        if [ -n "$count" ]; then
            echo "qemu_guest_instructions_retired=$count" >> "$outfile"
        else
            echo "NOTE: plugin ran but no instruction count found in its output; check the grep pattern in run_qemu_guest()." >> "$outfile"
        fi
    else
        echo "NOTE: no exact guest instruction count collected (QEMU_INSN_PLUGIN not set). Only wall-clock time and the program's own self-reported taken/nottaken/sink/validation are meaningful here -- see LIMITATIONS.md. Set QEMU_INSN_PLUGIN to a TCG plugin .so to add exact counts." >> "$outfile"
    fi
}

run_all() {
    write_environment

    if [ "$BACKEND_LABEL" = "qemu_user_mode" ]; then
        echo "Guest instruction counting mode: $GUEST_COUNT_MODE"
        [ "$GUEST_COUNT_MODE" = "none" ] && echo "  (no exact guest instruction count will be collected -- see NOTE written per-trace; this does NOT affect the primary functional run)"
        for bin in "$BIN_DIR"/*; do
            base="$(basename "$bin")"
            for rep in $(seq 1 "$REPS"); do
                if [ "$REPS" -eq 1 ]; then
                    outfile="$LOG_DIR/${base}.perf.txt"
                else
                    outfile="$LOG_DIR/${base}.rep${rep}.perf.txt"
                fi
                echo "Running (backend=$BACKEND_LABEL, guest-count=$GUEST_COUNT_MODE, rep $rep/$REPS): $base"
                run_qemu_guest "$bin" "$outfile"
            done
        done
        echo "Per-program traces written to: $LOG_DIR"
        echo "Parse them into a CSV with: python3 $SCRIPT_DIR/parse_traces_to_csv.py $LOG_DIR $SCRIPT_DIR/lowbit_traces_qemu.csv"
        return
    fi

    # --- native fallback path (no RISC-V cross-compiler / no qemu-riscv64 found) ---
    # This runs real binaries on real host silicon, so perf here is
    # legitimate and unaffected by the guest-counting concerns above.
    USE_PERF=1
    if ! perf_is_usable; then
        USE_PERF=0
        PARANOID_VAL="$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || echo unknown)"
        echo "=================================================================="
        echo "WARNING: perf cannot access hardware counters on this machine"
        echo "(kernel.perf_event_paranoid=$PARANOID_VAL). perf would refuse to"
        echo "even launch the benchmarks, producing trace files that are just an"
        echo "error message with ZERO actual program output."
        echo ""
        echo "TO FIX (needs root/sudo):"
        echo "    sudo sysctl kernel.perf_event_paranoid=-1"
        echo "  To make it permanent:"
        echo "    echo 'kernel.perf_event_paranoid = -1' | sudo tee -a /etc/sysctl.conf"
        echo ""
        echo "Continuing WITHOUT perf so you still get program output (branch"
        echo "taken/not-taken counts, sink, ROI wall-clock time) -- re-run 'run'"
        echo "after fixing the sysctl to get real hardware branch/cache counters."
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
                echo "Running (backend=$BACKEND_LABEL, perf, rep $rep/$REPS): $base"
                { echo "backend_declared=$BACKEND_LABEL"; \
                  perf stat -e "$PERF_EVENTS" "$bin"; } > "$outfile" 2>&1
            else
                echo "Running (backend=$BACKEND_LABEL, NO perf, rep $rep/$REPS): $base"
                { echo "backend_declared=$BACKEND_LABEL"; \
                  echo "NOTE: perf unavailable on this host (perf_event_paranoid too high)."; \
                  echo "NOTE: program output below has no hardware branch/cache counters."; \
                  "$bin"; } > "$outfile" 2>&1
            fi
        done
    done
    echo "Per-program traces written to: $LOG_DIR"
    echo "Parse them into a CSV with: python3 $SCRIPT_DIR/parse_traces_to_csv.py $LOG_DIR $SCRIPT_DIR/lowbit_traces_native.csv"
}

case "$MODE" in
    build)  build_all ;;
    verify) verify_sample ;;
    run)    run_all ;;
    env)    write_environment ;;
    all)    build_all; verify_sample; run_all ;;
    *) echo "Usage: $0 [build|verify|run [REPS]|env|all]"; exit 1 ;;
esac